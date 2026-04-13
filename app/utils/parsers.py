"""
병원 웹사이트 데이터 파싱 유틸리티.
기존 firecrawl.ts의 파싱 로직을 Python으로 재구현.
"""
import re
import math
from urllib.parse import urlparse


# ─── 숫자 파싱 ("103K", "1.5M", "14,000", "14만") → int ───

def parse_metric_number(s: str) -> int:
    if not s:
        return 0
    cleaned = s.replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*([KkMm만억])?", cleaned)
    if not m:
        try:
            return int(cleaned)
        except ValueError:
            return 0
    num = float(m.group(1))
    mult_map = {"k": 1e3, "m": 1e6, "만": 1e4, "억": 1e8}
    suffix = m.group(2)
    multiplier = mult_map.get(suffix.lower(), 1) if suffix else 1
    return round(num * multiplier)


# ─── 웹사이트 파싱 ───

def parse_website(result: dict, source_url: str) -> dict:
    meta = result.get("metadata", {}) or {}
    markdown: str = result.get("markdown", "") or ""
    links: list = result.get("links", []) or []
    domain = urlparse(source_url).hostname or ""
    if domain.startswith("www."):
        domain = domain[4:]

    # 병원명
    og_title = meta.get("og:title") or meta.get("ogTitle") or meta.get("title") or ""
    app_name = meta.get("application-name") or meta.get("apple-mobile-web-app-title") or ""
    copyright_val = meta.get("copyright") or ""
    name = copyright_val or app_name or re.sub(r"[-|–].*$", "", og_title).strip() or domain

    og_desc = meta.get("og:description") or meta.get("ogDescription") or meta.get("description") or ""
    og_image = meta.get("og:image") or meta.get("ogImage") or ""

    # 전화번호
    combined = og_desc + " " + markdown
    phone_match = re.search(r"(?:📞|☎|전화|TEL|tel)[:\s]*([0-9\-+(). ]{8,20})", combined, re.I)
    if not phone_match:
        phone_match = re.search(r"(0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4})", combined)
    phone = phone_match.group(1).strip() if phone_match else ""

    # 주소
    addr_pattern = r"(?:📢|주소|소재지)?[:\s]*((?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n,]{5,50})"
    addr_match = re.search(addr_pattern, combined)
    location = addr_match.group(1).strip() if addr_match else ""

    # 영문명
    eng_match = re.search(r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,5})", og_title)
    name_en = eng_match.group(0).strip() if eng_match else ""

    # 트래킹 픽셀
    tracking_pixels = [
        {"name": "Facebook Pixel", "installed": "fbq(" in markdown or "facebook.com/tr" in markdown or bool(meta.get("facebook-domain-verification"))},
        {"name": "Google Tag Manager", "installed": "googletagmanager.com" in markdown or "gtm.js" in markdown},
        {"name": "Google Analytics", "installed": "google-analytics.com" in markdown or "gtag('config" in markdown or "gtag.js" in markdown},
        {"name": "Kakao Pixel", "installed": "kakaoPixel" in markdown or "kpx-pixel" in markdown},
        {"name": "Naver Analytics", "installed": "wcs_do" in markdown or "naver.com/wcslog" in markdown or bool(meta.get("naver-site-verification"))},
    ]

    # SNS 링크 존재 여부
    sns_patterns = ["instagram.com", "youtube.com", "facebook.com", "tiktok.com", "twitter.com", "x.com"]
    sns_links_on_site = any(p in link for link in links for p in sns_patterns)

    # 추가 도메인
    additional_domains = []
    main_base = domain.split(".")[0]
    for link in links:
        try:
            ld = urlparse(link).hostname or ""
            if ld.startswith("www."):
                ld = ld[4:]
            if ld and ld != domain and main_base in ld:
                if not any(d["domain"] == ld for d in additional_domains):
                    purpose = "영문 사이트" if ld.startswith("eng") or ld.startswith("en") else "관련 도메인"
                    additional_domains.append({"domain": ld, "purpose": purpose})
        except Exception:
            pass

    # 메인 CTA
    cta_match = re.search(r"(?:상담|예약|문의|카카오톡?|카톡|전화)[^\n]{0,20}(?:신청|하기|접수|상담|문의)", markdown)
    main_cta = cta_match.group(0).strip() if cta_match else (f"전화 상담 {phone}" if phone else "확인 필요")

    # 브랜드 컬러
    color_match = re.search(r"(?:--(?:primary|brand|main)[-\w]*)\s*:\s*(#[0-9a-fA-F]{3,8})", markdown)
    theme_color = meta.get("theme-color", "")
    brand_colors = {}
    if theme_color:
        brand_colors = {"primary": theme_color, "accent": color_match.group(1) if color_match else "", "text": "#333333"}

    # 운영시간
    hours_match = re.search(r"(?:진료|영업|운영)\s*시간[:\s]*([\d:~\-\s오전오후APMapm]+[^\n]{0,50})", markdown)
    operating_hours = hours_match.group(1).strip() if hours_match else ""

    # 시술/서비스
    service_pattern = r"(?:눈성형|코성형|안면윤곽|가슴성형|지방흡입|리프팅|동안성형|피부관리|쌍꺼풀|양악수술|임플란트|치아교정|라식|라섹|보톡스|필러|레이저|모발이식|주름|턱끝|광대|사각턱)"
    services = list(set(re.findall(service_pattern, markdown)))

    # 인증/자격
    cert_pattern = r"(?:보건복지부|대한[^\s]+학회|JCI|CCTV|전담\s*마취|응급|무사고|수상|인증|특허|ISO)[^\n,]{0,40}"
    certifications = list(set(m.strip() for m in re.findall(cert_pattern, markdown, re.I)))[:15]

    return {
        "website_audit": {
            "primary_domain": domain,
            "additional_domains": additional_domains,
            "sns_links_on_site": sns_links_on_site,
            "tracking_pixels": tracking_pixels,
            "main_cta": main_cta,
        },
        "clinic_snapshot": {
            "name": name,
            "name_en": name_en,
            "domain": domain,
            "phone": phone,
            "location": location,
            "logo_url": og_image,
            "brand_colors": brand_colors,
        },
        "keywords": meta.get("keywords", ""),
        "og_desc": og_desc,
        "og_image": og_image,
        "links": links,
        "services": services,
        "certifications": certifications,
        "operating_hours": operating_hours,
        "markdown": markdown,
    }


# ─── SNS 채널 발견 ───

def find_social_channels(links: list[str]) -> dict[str, list[str]]:
    channels: dict[str, list[str]] = {
        "youtube": [], "instagram": [], "facebook": [],
        "tiktok": [], "kakao": [], "naver": [], "twitter": [],
    }
    seen: set[str] = set()

    for link in links:
        norm = link.rstrip("/").split("?")[0].lower()
        if norm in seen:
            continue
        seen.add(norm)

        if any(p in link for p in ["youtube.com/c/", "youtube.com/channel/", "youtube.com/@", "youtube.com/user/"]):
            channels["youtube"].append(link)
        if "instagram.com/" in link and "/p/" not in link and "/reel/" not in link:
            channels["instagram"].append(link)
        if "facebook.com/" in link and "/posts/" not in link and "/photos/" not in link:
            channels["facebook"].append(link)
        if "tiktok.com/@" in link:
            channels["tiktok"].append(link)
        if "pf.kakao.com" in link or ("kakao" in link and "chat" in link):
            channels["kakao"].append(link)
        if any(p in link for p in ["blog.naver.com", "place.naver.com", "naver.me"]):
            channels["naver"].append(link)
        if "twitter.com/" in link or "x.com/" in link:
            channels["twitter"].append(link)

    return channels


# ─── YouTube 파싱 ───

def parse_youtube(result: dict, url: str) -> dict:
    meta = result.get("metadata", {}) or {}
    markdown: str = result.get("markdown", "") or ""
    links: list = result.get("links", []) or []

    channel_name = (meta.get("og:title") or meta.get("ogTitle") or "").replace(" - YouTube", "").strip()
    description = meta.get("og:description") or meta.get("ogDescription") or meta.get("description") or ""
    full_desc = meta.get("description", "") if isinstance(meta.get("description"), str) and "\n" in meta.get("description", "") else description
    channel_image = meta.get("og:image") or meta.get("ogImage") or ""

    # 핸들
    handle_match = re.search(r"@[\w]+", url) or re.search(r"@[\w]+", markdown)
    handle = handle_match.group(0) if handle_match else ""

    # 구독자
    sub_match = re.search(r"([\d,.]+[KkMm]?)\s*(?:subscribers|구독자)", markdown, re.I)
    subscribers = parse_metric_number(sub_match.group(1)) if sub_match else 0

    # 영상 수
    vid_match = re.search(r"([\d,.]+)\s*(?:videos|개의?\s*동영상)", markdown, re.I)
    total_videos = int(vid_match.group(1).replace(",", "")) if vid_match else 0

    # 인기 영상
    top_videos = []
    video_pattern = re.compile(r"\[([^\]]{5,100})\]\(https://www\.youtube\.com/watch\?v=([^)]+)\)")
    for vm in video_pattern.finditer(markdown):
        if len(top_videos) >= 15:
            break
        title = vm.group(1).strip()
        skip_words = ["Sign in", "Home", "Shorts", "Skip"]
        if any(w in title for w in skip_words):
            continue
        after = markdown[vm.start():vm.start() + 300]
        view_match = re.search(r"([\d,.]+[KkMm]?)\s*(?:views|회)", after, re.I)
        views = parse_metric_number(view_match.group(1)) if view_match else 0
        ago_match = re.search(r"(\d+\s*(?:년|개월|일|시간|분|주|year|month|day|hour|week)s?\s*(?:전|ago))", after, re.I)
        top_videos.append({
            "title": title,
            "views": views,
            "uploaded_ago": ago_match.group(1) if ago_match else "",
            "type": "Short" if len(title) < 30 else "Long",
        })

    # 외부 링크
    linked_urls = []
    for u in re.findall(r"https?://[^\s\"<>]+", full_desc):
        if "youtube.com" not in u:
            linked_urls.append({"label": u, "url": u})

    # 플레이리스트
    playlists = []
    for plm in re.finditer(r"(?:playlist|재생목록)[:\s]*([^\n]{3,50})", markdown, re.I):
        if len(playlists) < 15:
            playlists.append(plm.group(1).strip())

    # 키워드
    og_tags = meta.get("og:video:tag")
    if isinstance(og_tags, list):
        keywords = og_tags
    elif isinstance(meta.get("keywords"), str):
        keywords = [k.strip() for k in meta["keywords"].split(",") if k.strip()]
    else:
        keywords = []

    # YT 설명에서 전화/주소 추출
    phone_match = re.search(r"📞[:\s]*([+\d\-().\s]{8,20})", full_desc)
    yt_phone = phone_match.group(1).strip() if phone_match else ""
    addr_match = re.search(r"📢[:\s]*([^\n📞💫⭐#]{5,80})", full_desc)
    yt_address = addr_match.group(1).strip() if addr_match else ""

    return {
        "channel_name": channel_name,
        "handle": handle,
        "subscribers": subscribers,
        "total_videos": total_videos,
        "total_views": 0,
        "description": full_desc,
        "top_videos": top_videos,
        "playlists": playlists,
        "linked_urls": linked_urls,
        "keywords": keywords,
        "_extra": {
            "yt_phone": yt_phone,
            "yt_address": yt_address,
            "channel_image": channel_image,
        },
    }


# ─── Instagram (Firecrawl 차단 — 링크 기반 스텁) ───

def build_instagram_from_link(url: str) -> dict:
    handle_match = re.search(r"instagram\.com/([\w.]+)", url)
    handle = f"@{handle_match.group(1)}" if handle_match else ""
    return {
        "handle": handle,
        "profile_link": url,
        "followers": 0,
        "posts": 0,
        "following": 0,
        "category": "Health/beauty",
        "bio": "Instagram Graph API 연동 필요",
    }


# ─── Facebook 파싱 ───

def parse_facebook(result: dict, url: str) -> dict:
    meta = result.get("metadata", {}) or {}
    markdown: str = result.get("markdown", "") or ""

    page_name = (meta.get("og:title") or meta.get("ogTitle") or "")
    page_name = re.sub(r"\s*[|–-]\s*Facebook\s*$", "", page_name).strip()
    desc = meta.get("og:description") or meta.get("ogDescription") or ""

    combined = desc + " " + markdown
    followers_match = re.search(r"([\d,.]+[KkMm]?)\s*(?:followers|팔로워|people follow|likes)", combined, re.I)
    followers = parse_metric_number(followers_match.group(1)) if followers_match else 0

    review_match = re.search(r"([\d,.]+)\s*(?:reviews?|리뷰)", combined, re.I)
    reviews = int(review_match.group(1).replace(",", "")) if review_match else 0

    cat_match = re.search(r"(?:Category|카테고리)[:\s]*([^\n,]{3,30})", desc, re.I)
    category = cat_match.group(1).strip() if cat_match else ""

    post_match = re.search(r"(\d+\s*(?:분|시간|일|주|개월|년|min|hour|day|week|month|year)s?\s*(?:전|ago))", markdown, re.I)
    recent_post_age = post_match.group(1) if post_match else "확인 필요"

    return {
        "page_name": page_name,
        "url": re.sub(r"^https?://", "", url),
        "followers": followers,
        "reviews": reviews,
        "category": category,
        "bio": desc[:300],
        "logo": meta.get("og:image", ""),
        "recent_post_age": recent_post_age,
        "has_whatsapp": "whatsapp" in markdown.lower(),
    }


# ─── 네이버 블로그 파싱 ───

def parse_naver_blog(result: dict, url: str) -> dict:
    meta = result.get("metadata", {}) or {}
    markdown: str = result.get("markdown", "") or ""

    blog_name = (meta.get("og:title") or meta.get("ogTitle") or meta.get("title") or "")
    blog_name = blog_name.replace(" : 네이버 블로그", "").strip()

    post_match = re.search(r"(?:게시글|글)\s*([\d,]+)", markdown)
    post_count = int(post_match.group(1).replace(",", "")) if post_match else 0

    visitor_match = re.search(r"(?:방문자|조회)\s*([\d,]+)", markdown)
    visitor_count = int(visitor_match.group(1).replace(",", "")) if visitor_match else 0

    neighbor_match = re.search(r"(?:이웃|서로이웃)\s*([\d,]+)", markdown)
    neighbor_count = int(neighbor_match.group(1).replace(",", "")) if neighbor_match else 0

    return {
        "blog_name": blog_name,
        "url": url,
        "post_count": post_count,
        "visitor_count": visitor_count,
        "neighbor_count": neighbor_count,
    }


# ─── 의료진 파싱 ───

def parse_doctors(markdown: str) -> list[dict]:
    doctors: list[dict] = []
    seen_names: set[str] = set()

    # Pattern 1: DR. 이름
    for m in re.finditer(r"DR\.?\s*([\uAC00-\uD7A3]{2,4})", markdown, re.I):
        if len(doctors) >= 20:
            break
        name = m.group(1)
        if name in seen_names:
            continue
        seen_names.add(name)

        after = markdown[m.start():m.start() + 300]
        cred_match = re.search(
            r"(?:성형외과|피부과|외과|치과|안과|마취통증의학과|내과|산부인과|정형외과|이비인후과|비뇨의학과|재활의학과)\s*(?:전문의|세부전문의)",
            after,
        )
        univ_match = re.search(
            r"(서울대|연세대|고려대|경북대|성균관대|울산대|한양대|중앙대|이화여대|가톨릭대|경희대|전남대|부산대|충남대|원광대)[^\n]{0,30}",
            after,
        )
        creds = ", ".join(filter(None, [cred_match.group(0) if cred_match else None, univ_match.group(0) if univ_match else None]))

        img_match = re.search(r"!\[.*?\]\((https?://[^\s)]+)\)", after)
        doctors.append({
            "name": name,
            "credentials": creds or "전문의",
            "image_url": img_match.group(1) if img_match else "",
        })

    # Fallback: 이름 + 원장/교수
    if not doctors:
        for m in re.finditer(r"([\uAC00-\uD7A3]{2,4})\s*(?:원장|교수|의사|대표원장)\s*(?:님)?", markdown):
            if len(doctors) >= 20:
                break
            name = m.group(1)
            if name in seen_names:
                continue
            seen_names.add(name)
            after = markdown[m.start():m.start() + 200]
            cred_match = re.search(r"(?:성형외과|피부과|외과|치과|안과|마취|내과)\s*(?:전문의|세부전문의)", after)
            doctors.append({"name": name, "credentials": cred_match.group(0) if cred_match else "전문의", "image_url": ""})

    return doctors


# ─── 서브페이지 분류 ───

def categorize_sub_pages(links: list[str], base_domain: str) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {
        "doctor": [], "about": [], "service": [], "review": [],
        "contact": [], "event": [], "blog": [],
    }
    patterns = {
        "doctor": re.compile(r"(?:doctor|staff|의료진|원장|sub07|team|specialists)", re.I),
        "about": re.compile(r"(?:about|소개|introduction|company|sub01|greeting)", re.I),
        "service": re.compile(r"(?:service|시술|surgery|procedure|treatment|sub0[2-6]|menu|program)", re.I),
        "review": re.compile(r"(?:review|후기|before.?after|gallery|photo|board)", re.I),
        "contact": re.compile(r"(?:contact|오시는|map|location|directions|access|찾아)", re.I),
        "event": re.compile(r"(?:event|이벤트|promotion|special|offer)", re.I),
        "blog": re.compile(r"(?:blog|column|칼럼|magazine|story|news)", re.I),
    }
    main_base = base_domain.split(".")[0]

    for link in links:
        try:
            ld = urlparse(link).hostname or ""
            if ld.startswith("www."):
                ld = ld[4:]
            if main_base not in ld:
                continue
            for cat, regex in patterns.items():
                if regex.search(link):
                    if link not in categories[cat]:
                        categories[cat].append(link)
                    break
        except Exception:
            pass

    return categories


# ─── 채널 점수 계산 ───

def build_channel_scores(yt_data: dict, ig_accounts: list[dict], fb_pages: list[dict], website_audit: dict, google_maps_data: dict | None = None) -> list[dict]:
    scores = []

    # YouTube
    if yt_data.get("subscribers", 0) > 0 or yt_data.get("channel_name"):
        subs = yt_data.get("subscribers", 0)
        score = min(100, round(math.log10(subs + 1) * 20)) if subs > 0 else 10
        status = "good" if score >= 70 else ("warning" if score >= 40 else "critical")
        headline = (
            f"{subs:,} 구독자, {yt_data.get('total_videos', 0):,}개 영상"
            if subs > 0
            else f"{yt_data.get('channel_name', '')} — 구독자 수 확인 필요"
        )
        scores.append({"channel": "YouTube", "icon": "youtube", "score": score, "max_score": 100, "status": status, "headline": headline})

    # Instagram
    for ig in ig_accounts:
        followers = ig.get("followers", 0)
        score = min(100, round(math.log10(followers + 1) * 20)) if followers > 0 else 10
        has_data = followers > 0
        status = "good" if score >= 70 else ("warning" if score >= 40 else ("unknown" if not has_data else "critical"))
        headline = (
            f"{ig.get('handle', '')} — {followers:,} 팔로워, {ig.get('posts', 0):,}개 게시물"
            if has_data
            else f"{ig.get('handle', '')} — 데이터 수집 실패"
        )
        scores.append({"channel": f"Instagram {ig.get('handle', '')}", "icon": "instagram", "score": score, "max_score": 100, "status": status, "headline": headline})

    # Facebook
    for fb in fb_pages:
        followers = fb.get("followers", 0)
        score = min(100, round(math.log10(followers + 1) * 20)) if followers > 0 else 10
        status = "good" if score >= 70 else ("warning" if score >= 40 else "critical")
        headline = f"{followers:,} 팔로워" if followers else f"{fb.get('page_name', '')} — 확인 필요"
        scores.append({"channel": "Facebook", "icon": "facebook", "score": score, "max_score": 100, "status": status, "headline": headline})

    # Google Maps
    if google_maps_data and google_maps_data.get("rating"):
        rating = google_maps_data["rating"]
        reviews = google_maps_data.get("total_reviews", 0)
        score = min(100, round(rating * 20))
        status = "good" if rating >= 4.0 else ("warning" if rating >= 3.0 else "critical")
        headline = f"평점 {rating}/5 ({reviews:,}개 리뷰)"
        scores.append({"channel": "Google Maps", "icon": "map-pin", "score": score, "max_score": 100, "status": status, "headline": headline})

    # Website
    pixel_count = sum(1 for p in website_audit.get("tracking_pixels", []) if p.get("installed"))
    web_score = (30 if website_audit.get("sns_links_on_site") else 0) + pixel_count * 15
    web_score = min(100, web_score)
    status = "good" if web_score >= 70 else ("warning" if web_score >= 40 else "critical")
    sns_status = "SNS 연결 있음" if website_audit.get("sns_links_on_site") else "SNS 연결 없음"
    scores.append({
        "channel": "Website", "icon": "globe", "score": web_score, "max_score": 100, "status": status,
        "headline": f"{website_audit.get('primary_domain', '')} — {sns_status}, 트래킹 {pixel_count}개",
    })

    return scores


# ─── 문제 진단 ───

def build_diagnosis(website_audit: dict, ig_accounts: list[dict], social_channels: dict) -> list[dict]:
    items = []

    if not website_audit.get("sns_links_on_site"):
        items.append({"category": "웹사이트-SNS 연결 부재", "detail": f"{website_audit.get('primary_domain', '')}에 소셜미디어 링크가 발견되지 않음", "severity": "critical"})

    pixel_count = sum(1 for p in website_audit.get("tracking_pixels", []) if p.get("installed"))
    if pixel_count == 0:
        items.append({"category": "트래킹 미설치", "detail": "주요 트래킹 픽셀(FB Pixel, GTM, GA)이 감지되지 않음", "severity": "critical"})

    if not ig_accounts and not social_channels.get("instagram"):
        items.append({"category": "Instagram 부재", "detail": "Instagram 계정이 발견되지 않음", "severity": "warning"})

    if not social_channels.get("tiktok"):
        items.append({"category": "TikTok 부재", "detail": "TikTok 계정이 발견되지 않음 — MZ세대 도달 채널 부재", "severity": "warning"})

    if not social_channels.get("naver"):
        items.append({"category": "네이버 채널 부재", "detail": "네이버 블로그/플레이스 미발견 — 한국 검색 SEO 기회 미활용", "severity": "warning"})

    if 0 < pixel_count < 3:
        missing = [p["name"] for p in website_audit.get("tracking_pixels", []) if not p.get("installed")]
        items.append({"category": "트래킹 일부 미설치", "detail": f"미설치: {', '.join(missing)}", "severity": "warning"})

    if not items:
        items.append({"category": "기본 채널 구축 완료", "detail": "주요 소셜 채널이 연결됨", "severity": "good"})

    return items
