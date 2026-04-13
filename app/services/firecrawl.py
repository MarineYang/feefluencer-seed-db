"""
Firecrawl API 클라이언트 + 5단계 스크래핑 파이프라인.
"""
import asyncio
import logging
import re
from datetime import datetime, date
from urllib.parse import urlparse
from typing import AsyncGenerator

import httpx

from app.core.config import settings
from app.services.apify import enrich_channels as apify_enrich_channels, discover_missing_channels
from app.services.perplexity import run_market_analysis, synthesize_report
from app.utils.parsers import (
    parse_website, find_social_channels, parse_youtube,
    build_instagram_from_link, parse_facebook, parse_naver_blog,
    parse_doctors, categorize_sub_pages,
    build_channel_scores, build_diagnosis, parse_metric_number,
)

logger = logging.getLogger(__name__)

FIRECRAWL_API = "https://api.firecrawl.dev/v1"


def _build_ig_diagnosis(ig_accounts: list[dict]) -> list[dict]:
    if not ig_accounts:
        return [{"category": "Instagram 미발견", "detail": "웹사이트에서 Instagram 링크 미발견", "severity": "warning"}]
    has_real_data = any(a.get("followers", 0) > 0 for a in ig_accounts)
    if has_real_data:
        total = sum(a.get("followers", 0) for a in ig_accounts)
        return [{"category": f"Instagram {len(ig_accounts)}개 계정", "detail": f"총 {total:,} 팔로워", "severity": "good" if total >= 1000 else "warning"}]
    return [{"category": f"Instagram {len(ig_accounts)}개 계정 발견", "detail": "Apify 데이터 수집 실패 — 핸들 확인 필요", "severity": "warning"}]


# ─── Firecrawl REST 호출 ───

async def firecrawl_scrape(
    client: httpx.AsyncClient,
    url: str,
    formats: list[str] | None = None,
) -> dict | None:
    if formats is None:
        formats = ["markdown", "links"]
    try:
        resp = await client.post(
            f"{FIRECRAWL_API}/scrape",
            json={"url": url, "formats": formats},
            headers={
                "Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        data = resp.json()
        if not data.get("success"):
            logger.warning("Scrape failed for %s: %s", url, data.get("error"))
            return None
        return data.get("data")
    except Exception as e:
        logger.warning("Scrape error for %s: %s", url, e)
        return None


async def firecrawl_map(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        resp = await client.post(
            f"{FIRECRAWL_API}/map",
            json={"url": url},
            headers={
                "Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        data = resp.json()
        if not data.get("success"):
            return None
        return data
    except Exception as e:
        logger.warning("Map error for %s: %s", url, e)
        return None


# ─── 5단계 스크래핑 파이프라인 ───

async def crawl_and_build_report(
    target_url: str,
    clinic_name: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Generator that yields progress events and finally the report.
    Each yield is either:
      {"type": "progress", "step": N, "total": 5, "message": "..."}
      {"type": "complete", "report": {...}}
      {"type": "error", "message": "..."}
    """
    total_steps = 7
    screenshots = []
    website_audit = {"primary_domain": "", "additional_domains": [], "sns_links_on_site": False, "tracking_pixels": [], "main_cta": ""}
    clinic_info: dict = {}
    social_channels: dict = {"youtube": [], "instagram": [], "facebook": [], "tiktok": [], "kakao": [], "naver": [], "twitter": []}
    yt_data: dict = {}
    ig_accounts: list[dict] = []
    fb_pages: list[dict] = []
    all_keywords: list[str] = []
    doctors: list[dict] = []
    all_services: list[str] = []
    all_certifications: list[str] = []
    naver_blog_info: dict | None = None
    sub_page_data: dict = {}
    google_maps_data: dict | None = None
    ig_content_analysis: dict | None = None

    async with httpx.AsyncClient() as client:
        try:
            # ── Step 1: 메인 웹사이트 + 사이트맵 ──
            yield {"type": "progress", "step": 1, "total": total_steps, "message": "웹사이트 스캔 중..."}

            web_result, map_result = await asyncio.gather(
                firecrawl_scrape(client, target_url, ["markdown", "links", "screenshot"]),
                firecrawl_map(client, target_url),
            )

            if not web_result:
                yield {"type": "error", "message": f"웹사이트 스크래핑 실패: {target_url}"}
                return

            parsed = parse_website(web_result, target_url)
            website_audit = parsed["website_audit"]
            clinic_info = parsed["clinic_snapshot"]
            all_services = parsed["services"]
            all_certifications = parsed["certifications"]
            if parsed["keywords"]:
                all_keywords = [k.strip() for k in re.split(r"[,，]\s*", parsed["keywords"]) if k.strip()]

            social_channels = find_social_channels(parsed["links"])

            # Map API 결과 병합
            if map_result and map_result.get("links"):
                map_social = find_social_channels(map_result["links"])
                for key, urls in map_social.items():
                    for u in urls:
                        if u not in social_channels.get(key, []):
                            social_channels.setdefault(key, []).append(u)

            # 서브페이지 분류
            all_links = parsed["links"] + (map_result.get("links", []) if map_result else [])
            sub_pages = categorize_sub_pages(all_links, website_audit["primary_domain"])

            # 홈페이지 스크린샷
            if web_result.get("screenshot"):
                screenshots.append({
                    "id": "website-homepage",
                    "url": web_result["screenshot"],
                    "channel": "Website",
                    "captured_at": datetime.utcnow().isoformat(),
                    "caption": f"{website_audit['primary_domain']} 홈페이지",
                    "source_url": target_url,
                })

            # ── 채널 자동 탐색: 네이버 검색 + 개별 검색으로 미발견 채널 보충 ──
            resolved_name = clinic_name or clinic_info.get("name", "")
            if resolved_name:
                discovered = await discover_missing_channels(client, resolved_name, social_channels)

                # discovered 결과를 social_channels에 병합
                channel_map = {
                    "youtube": "youtube",
                    "facebook": "facebook",
                    "kakao": "kakao",
                    "tiktok": "tiktok",
                    "twitter": "twitter",
                }
                for disc_key, ch_key in channel_map.items():
                    if discovered.get(disc_key):
                        val = discovered[disc_key]
                        if val not in social_channels.get(ch_key, []):
                            social_channels.setdefault(ch_key, []).append(val)

                # Instagram (핸들 → URL 변환)
                if discovered.get("instagram"):
                    for h in discovered["instagram"]:
                        url = f"https://www.instagram.com/{h}/"
                        if url not in social_channels.get("instagram", []):
                            social_channels.setdefault("instagram", []).append(url)

                # 네이버 (블로그/플레이스)
                for nv_key in ("naver_blog", "naver_place"):
                    if discovered.get(nv_key):
                        if discovered[nv_key] not in social_channels.get("naver", []):
                            social_channels.setdefault("naver", []).append(discovered[nv_key])

            # ── Step 2: YouTube + 서브페이지 병렬 ──
            yield {"type": "progress", "step": 2, "total": total_steps, "message": "채널 스크린샷 캡처 중..."}

            step2_tasks = []

            # YouTube
            async def scrape_youtube():
                nonlocal yt_data, clinic_info, all_keywords
                if not social_channels.get("youtube"):
                    return
                result = await firecrawl_scrape(client, social_channels["youtube"][0], ["markdown", "links", "screenshot"])
                if not result:
                    return
                yt_data = parse_youtube(result, social_channels["youtube"][0])
                extra = yt_data.get("_extra", {})
                if extra.get("yt_phone") and not clinic_info.get("phone"):
                    clinic_info["phone"] = extra["yt_phone"]
                if extra.get("yt_address") and not clinic_info.get("location"):
                    clinic_info["location"] = extra["yt_address"]
                if extra.get("channel_image") and not clinic_info.get("logo_url"):
                    clinic_info["logo_url"] = extra["channel_image"]
                if result.get("screenshot"):
                    screenshots.append({
                        "id": "yt-channel", "url": result["screenshot"], "channel": "YouTube",
                        "captured_at": datetime.utcnow().isoformat(),
                        "caption": f"YouTube {yt_data.get('channel_name', '')}",
                        "source_url": social_channels["youtube"][0],
                    })

            step2_tasks.append(scrape_youtube())

            # 의료진 페이지
            async def scrape_doctors():
                nonlocal doctors
                doctor_url = sub_pages["doctor"][0] if sub_pages["doctor"] else None
                if not doctor_url:
                    return
                result = await firecrawl_scrape(client, doctor_url)
                if result:
                    doctors = parse_doctors(result.get("markdown", ""))

            step2_tasks.append(scrape_doctors())

            # 시술 페이지
            async def scrape_services():
                nonlocal all_services
                svc_url = sub_pages["service"][0] if sub_pages["service"] else None
                if not svc_url:
                    return
                result = await firecrawl_scrape(client, svc_url)
                if result:
                    svc_pattern = r"(?:눈성형|코성형|안면윤곽|가슴성형|지방흡입|리프팅|동안성형|피부관리|쌍꺼풀|양악수술|임플란트|치아교정|라식|라섹|보톡스|필러|레이저|모발이식|주름|턱끝|광대|사각턱|지방이식|실리프팅|울쎄라|써마지|브이라인)"
                    more = list(set(re.findall(svc_pattern, result.get("markdown", ""))))
                    all_services = list(set(all_services + more))

            step2_tasks.append(scrape_services())

            # 네이버 블로그 (채널 디스커버리에서 이미 보충됨)
            async def scrape_naver():
                nonlocal naver_blog_info
                naver_url = next((u for u in social_channels.get("naver", []) if "blog.naver.com" in u), None)
                if not naver_url:
                    return
                result = await firecrawl_scrape(client, naver_url)
                if result:
                    naver_blog_info = parse_naver_blog(result, naver_url)

            step2_tasks.append(scrape_naver())

            await asyncio.gather(*step2_tasks)

            # 의료진 폴백
            if not doctors and not sub_pages["doctor"]:
                base_url = f"{urlparse(target_url).scheme}://{urlparse(target_url).hostname}"
                staff_paths = ["/page/sub07_01", "/about", "/doctor", "/staff", "/page/doctors", "/introduction", "/team"]
                for path in staff_paths:
                    result = await firecrawl_scrape(client, f"{base_url}{path}")
                    if result:
                        md = result.get("markdown", "")
                        if any(kw in md for kw in ["DR.", "전문의", "의료진", "원장"]):
                            doctors = parse_doctors(md)
                            if doctors:
                                break

            # ── Step 3: Instagram(Apify) + Google Maps(Apify) + Facebook 병렬 ──
            yield {"type": "progress", "step": 3, "total": total_steps, "message": "소셜 미디어 분석 중..."}

            # Instagram 핸들 추출
            ig_handles = []
            for ig_url in social_channels.get("instagram", [])[:3]:
                handle_match = re.search(r"instagram\.com/([\w.]+)", ig_url)
                if handle_match:
                    ig_handles.append(handle_match.group(1))

            # Apify: Instagram + Google Maps 병렬 실행
            apify_data: dict = {}
            if settings.APIFY_API_TOKEN and (ig_handles or clinic_name):
                try:
                    apify_data = await apify_enrich_channels(
                        client,
                        instagram_handles=ig_handles,
                        clinic_name=clinic_name or clinic_info.get("name", ""),
                        address=clinic_info.get("location", ""),
                    )
                except Exception as e:
                    logger.warning("Apify enrich failed: %s", e)

            # Apify Instagram 결과 → ig_accounts
            if apify_data.get("instagram"):
                ig_accounts = apify_data["instagram"]
            else:
                # 폴백: 링크 기반 스텁
                for ig_url in social_channels.get("instagram", [])[:3]:
                    ig_accounts.append(build_instagram_from_link(ig_url))

            # Instagram 콘텐츠 분석 (게시물 + 릴스)
            ig_content_analysis = apify_data.get("instagram_content")

            # Apify Google Maps 결과
            google_maps_data = apify_data.get("google_maps")
            if google_maps_data:
                # Google Maps에서 보강 가능한 데이터 병합
                if google_maps_data.get("rating"):
                    clinic_info["google_rating"] = google_maps_data["rating"]
                if google_maps_data.get("total_reviews"):
                    clinic_info["google_reviews"] = google_maps_data["total_reviews"]
                if google_maps_data.get("phone") and not clinic_info.get("phone"):
                    clinic_info["phone"] = google_maps_data["phone"]
                if google_maps_data.get("address") and not clinic_info.get("location"):
                    clinic_info["location"] = google_maps_data["address"]

            step3_tasks = []

            # Facebook
            async def scrape_fb(fb_url: str):
                result = await firecrawl_scrape(client, fb_url, ["markdown", "links", "screenshot"])
                if not result:
                    return
                fb_data = parse_facebook(result, fb_url)
                fb_pages.append(fb_data)
                if result.get("screenshot"):
                    screenshots.append({
                        "id": f"fb-{fb_data.get('page_name', '').replace(' ', '-')}",
                        "url": result["screenshot"], "channel": "Facebook",
                        "captured_at": datetime.utcnow().isoformat(),
                        "caption": f"Facebook {fb_data.get('page_name', '')}",
                        "source_url": fb_url,
                    })

            for fb_url in social_channels.get("facebook", [])[:3]:
                step3_tasks.append(scrape_fb(fb_url))

            # 리뷰 페이지
            async def scrape_reviews():
                review_url = sub_pages["review"][0] if sub_pages["review"] else None
                if not review_url:
                    return
                result = await firecrawl_scrape(client, review_url)
                if result:
                    md = result.get("markdown", "")
                    review_match = re.search(r"(?:리뷰|후기|수술후기)\s*(?:총\s*)?([\d,]+)\s*(?:개|건)", md)
                    if review_match:
                        sub_page_data["reviews"] = review_match.group(1).replace(",", "")

            step3_tasks.append(scrape_reviews())
            await asyncio.gather(*step3_tasks)

            # ── Step 4: 추가 보강 ──
            yield {"type": "progress", "step": 4, "total": total_steps, "message": "브랜드 일관성 평가 중..."}

            # 소개 페이지
            about_url = sub_pages["about"][0] if sub_pages["about"] else None
            if about_url:
                about_result = await firecrawl_scrape(client, about_url)
                if about_result:
                    md = about_result.get("markdown", "")
                    year_match = re.search(r"(?:설립|개원|since|SINCE)\s*[:\s]*(\d{4})", md, re.I)
                    if year_match:
                        clinic_info["established"] = year_match.group(1)
                        clinic_info["years_in_business"] = datetime.now().year - int(year_match.group(1))

                    cert_pattern = r"(?:보건복지부|대한[^\s]+학회|JCI|CCTV|전담\s*마취|응급|무사고|수상|인증|특허|ISO|렛미인|표창)[^\n,]{0,40}"
                    more_certs = [s.strip() for s in re.findall(cert_pattern, md, re.I)]
                    all_certifications = list(set(all_certifications + more_certs))

                    media_pattern = r"(?:TV|방송|출연|언론|보도|KBS|SBS|MBC|JTBC|tvN|채널A|MBN)[^\n,]{0,40}"
                    media_matches = list(set(s.strip() for s in re.findall(media_pattern, md, re.I)))
                    if media_matches:
                        clinic_info["media_appearances"] = media_matches

            # 연락처 페이지
            contact_url = sub_pages["contact"][0] if sub_pages["contact"] else None
            if contact_url and not clinic_info.get("location"):
                contact_result = await firecrawl_scrape(client, contact_url)
                if contact_result:
                    md = contact_result.get("markdown", "")
                    addr_match = re.search(r"((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n]{5,80})", md)
                    if addr_match:
                        clinic_info["location"] = addr_match.group(1).strip()
                    station_match = re.search(r"(\d+호선\s*[^\s]+역\s*(?:\d+번\s*출구)?[^\n]{0,20})", md)
                    if station_match:
                        clinic_info["nearest_station"] = station_match.group(1).strip()
                    if not clinic_info.get("phone"):
                        phone_match = re.search(r"(0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4})", md)
                        if phone_match:
                            clinic_info["phone"] = phone_match.group(1).strip()

            # ── Step 5: 시장 분석 (Perplexity) ──
            market_analysis: dict = {}
            if settings.PERPLEXITY_API_KEY:
                yield {"type": "progress", "step": 5, "total": total_steps, "message": "시장 분석 중... (경쟁사·키워드·트렌드·타겟)"}
                try:
                    resolved_name = clinic_name or clinic_info.get("name", "")
                    resolved_address = clinic_info.get("location", "")
                    market_analysis = await run_market_analysis(
                        client,
                        clinic_name=resolved_name,
                        services=all_services,
                        address=resolved_address,
                    )
                except Exception as e:
                    logger.warning("Market analysis failed: %s", e)
            else:
                yield {"type": "progress", "step": 5, "total": total_steps, "message": "시장 분석 건너뜀 (API 키 미설정)"}

            # ── Step 6: AI 리포트 합성 (Perplexity) ──
            ai_report: dict = {}
            if settings.PERPLEXITY_API_KEY and market_analysis:
                yield {"type": "progress", "step": 6, "total": total_steps, "message": "AI 전략 리포트 합성 중..."}
                try:
                    scrape_summary = {
                        "clinic_name": clinic_name or clinic_info.get("name", ""),
                        "services": all_services,
                        "address": clinic_info.get("location", ""),
                        "youtube_subscribers": yt_data.get("subscribers", 0),
                        "instagram_accounts": len(ig_accounts),
                        "instagram_followers": sum(a.get("followers", 0) for a in ig_accounts),
                        "facebook_pages": len(fb_pages),
                        "google_rating": clinic_info.get("google_rating", 0),
                        "google_reviews": clinic_info.get("google_reviews", 0),
                        "website_domain": website_audit.get("primary_domain", ""),
                        "tracking_pixels": sum(1 for p in website_audit.get("tracking_pixels", []) if p.get("installed")),
                        "certifications": all_certifications[:10],
                        "doctors_count": len(doctors),
                    }
                    ai_report = await synthesize_report(
                        client,
                        clinic_name=clinic_name or clinic_info.get("name", ""),
                        scrape_summary=scrape_summary,
                        market_analysis=market_analysis,
                    )
                except Exception as e:
                    logger.warning("Report synthesis failed: %s", e)
            else:
                yield {"type": "progress", "step": 6, "total": total_steps, "message": "AI 리포트 합성 건너뜀"}

            # ── Step 7: 최종 리포트 조립 ──
            yield {"type": "progress", "step": 7, "total": total_steps, "message": "인텔리전스 리포트 생성 중..."}

        except Exception as e:
            logger.error("Pipeline error: %s", e, exc_info=True)
            yield {"type": "error", "message": str(e)}
            return

    # _extra 제거
    yt_clean = {k: v for k, v in yt_data.items() if k != "_extra"}

    # 채널 점수 & 진단
    channel_scores = build_channel_scores(yt_data, ig_accounts, fb_pages, website_audit, google_maps_data)
    overall_score = round(sum(s["score"] for s in channel_scores) / len(channel_scores)) if channel_scores else 0
    diagnosis = build_diagnosis(website_audit, ig_accounts, social_channels)

    total_reviews = int(sub_page_data.get("reviews", 0))

    # 기타 채널
    other_channels = [
        {"name": "카카오톡", "status": "active" if social_channels.get("kakao") else "not_found",
         "details": "카카오톡 채널 발견" if social_channels.get("kakao") else "미발견",
         "url": (social_channels.get("kakao") or [None])[0]},
        {"name": "네이버 블로그",
         "status": "active" if any("blog.naver.com" in u for u in social_channels.get("naver", [])) else "not_found",
         "details": f"{naver_blog_info['blog_name']} — 게시글 {naver_blog_info['post_count']}개" if naver_blog_info else "미발견",
         "url": next((u for u in social_channels.get("naver", []) if "blog.naver.com" in u), None)},
        {"name": "네이버 플레이스",
         "status": "active" if any("place.naver.com" in u or "map.naver.com" in u for u in social_channels.get("naver", [])) else "not_found",
         "details": "네이버 플레이스/지도 발견" if any("place.naver.com" in u for u in social_channels.get("naver", [])) else "미발견",
         "url": next((u for u in social_channels.get("naver", []) if "place.naver.com" in u or "map.naver.com" in u), None)},
        {"name": "TikTok", "status": "active" if social_channels.get("tiktok") else "not_found",
         "details": "TikTok 계정 발견" if social_channels.get("tiktok") else "미발견",
         "url": (social_channels.get("tiktok") or [None])[0]},
        {"name": "Twitter/X", "status": "active" if social_channels.get("twitter") else "not_found",
         "details": "계정 발견" if social_channels.get("twitter") else "미발견",
         "url": (social_channels.get("twitter") or [None])[0]},
    ]

    report = {
        "id": f"report-{int(datetime.utcnow().timestamp())}",
        "created_at": date.today().isoformat(),
        "target_url": target_url,
        "overall_score": overall_score,
        "clinic_snapshot": {
            "name": clinic_name or clinic_info.get("name", website_audit["primary_domain"]),
            "name_en": clinic_info.get("name_en", ""),
            "established": clinic_info.get("established", ""),
            "years_in_business": clinic_info.get("years_in_business", 0),
            "staff_count": len(doctors),
            "lead_doctor": {
                "name": doctors[0]["name"] if doctors else "",
                "credentials": doctors[0]["credentials"] if doctors else "",
            },
            "overall_rating": clinic_info.get("google_rating", 0),
            "google_reviews": clinic_info.get("google_reviews", 0),
            "total_reviews": total_reviews,
            "certifications": all_certifications or all_keywords[:10],
            "media_appearances": clinic_info.get("media_appearances", []),
            "medical_tourism": ["영문 사이트 운영"] if any(d.get("purpose", "").startswith("영문") for d in website_audit.get("additional_domains", [])) else [],
            "location": clinic_info.get("location", ""),
            "nearest_station": clinic_info.get("nearest_station", ""),
            "phone": clinic_info.get("phone", ""),
            "domain": clinic_info.get("domain", website_audit["primary_domain"]),
            "logo_url": clinic_info.get("logo_url", ""),
            "brand_colors": clinic_info.get("brand_colors", {}),
            "services": all_services,
            "doctors": [{"name": d["name"], "credentials": d["credentials"], "image_url": d.get("image_url", "")} for d in doctors],
        },
        "channel_scores": channel_scores,
        "youtube_audit": {
            "channel_name": yt_clean.get("channel_name", ""),
            "handle": yt_clean.get("handle", ""),
            "subscribers": yt_clean.get("subscribers", 0),
            "total_videos": yt_clean.get("total_videos", 0),
            "total_views": 0,
            "description": yt_clean.get("description", ""),
            "top_videos": yt_clean.get("top_videos", []),
            "playlists": yt_clean.get("playlists", []),
            "linked_urls": yt_clean.get("linked_urls", []),
        },
        "instagram_audit": {
            "accounts": ig_accounts,
            "content_analysis": ig_content_analysis or {},
            "diagnosis": _build_ig_diagnosis(ig_accounts),
        },
        "google_maps_audit": google_maps_data or {},
        "facebook_audit": {
            "pages": fb_pages,
            "diagnosis": [{"category": f"Facebook {len(fb_pages)}개 페이지 발견", "detail": ", ".join(p.get("page_name", "") for p in fb_pages), "severity": "unknown"}] if fb_pages else [],
        },
        "other_channels": other_channels,
        "website_audit": website_audit,
        "problem_diagnosis": diagnosis,
        "social_channels_raw": social_channels,
        "naver_blog": naver_blog_info,
        "screenshots": screenshots,
        "market_analysis": market_analysis or {},
        "ai_report": ai_report.get("data") if isinstance(ai_report, dict) else {},
        "ai_citations": ai_report.get("citations", []) if isinstance(ai_report, dict) else [],
    }

    yield {"type": "complete", "report": report}
