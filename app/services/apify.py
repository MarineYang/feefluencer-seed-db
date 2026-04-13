"""
Apify API 클라이언트 — Instagram Profile + Google Maps 스크래퍼.
핸들 미발견 시 Firecrawl search로 자동 탐색.
"""
import asyncio
import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

APIFY_API = "https://api.apify.com/v2"
FIRECRAWL_API = "https://api.firecrawl.dev/v1"

# Apify Actor IDs (slug 대신 ID 사용 — slug는 404 반환)
ACTOR_IG_PROFILE = "dSCLg0C3YEZ83HzYX"    # apify/instagram-profile-scraper
ACTOR_IG_POST = "nH2AHrwxeTRJoN5hX"       # apify/instagram-post-scraper
ACTOR_IG_REEL = "xMc5Ga1oCONPmWJIa"       # apify/instagram-reel-scraper
ACTOR_IG_HASHTAG = "reGe1ST3OBgYZSsZJ"    # apify/instagram-hashtag-scraper
ACTOR_FB_PAGES = "4Hv5RhChiaDk6iwad"      # apify/facebook-pages-scraper
ACTOR_GOOGLE_MAPS = "nwua9Gu5YrADL7ZDj"   # compass/crawler-google-places


async def _run_actor(
    client: httpx.AsyncClient,
    actor_id: str,
    run_input: dict,
    timeout: float = 60.0,
) -> list[dict]:
    """Apify Actor를 동기 실행하고 dataset items를 반환."""
    url = f"{APIFY_API}/acts/{actor_id}/run-sync-get-dataset-items"
    try:
        resp = await client.post(
            url,
            json=run_input,
            params={"token": settings.APIFY_API_TOKEN},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        if resp.status_code not in (200, 201):
            logger.warning("Apify %s failed: %s %s", actor_id, resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Apify %s error: %s", actor_id, e)
        return []


async def scrape_instagram_profile(
    client: httpx.AsyncClient,
    handle: str,
) -> dict | None:
    """
    Apify instagram-profile-scraper로 Instagram 프로필 데이터 수집.
    handle: "viewplastic" 또는 "@viewplastic" (@ 자동 제거)
    """
    handle = handle.lstrip("@").strip().rstrip("/")
    if not handle:
        return None

    items = await _run_actor(
        client,
        actor_id=ACTOR_IG_PROFILE,
        run_input={
            "usernames": [handle],
        },
        timeout=30.0,
    )

    if not items:
        logger.info("Instagram scrape returned no data for @%s", handle)
        return None

    item = items[0]
    return {
        "handle": f"@{handle}",
        "profile_link": f"https://www.instagram.com/{handle}/",
        "followers": item.get("followersCount", 0),
        "following": item.get("followsCount", 0),
        "posts": item.get("postsCount", 0),
        "bio": item.get("biography", ""),
        "full_name": item.get("fullName", ""),
        "category": item.get("businessCategoryName") or item.get("categoryName", ""),
        "is_verified": item.get("verified", False),
        "is_business": item.get("isBusinessAccount", False),
        "profile_pic_url": item.get("profilePicUrlHD") or item.get("profilePicUrl", ""),
        "external_url": item.get("externalUrl", ""),
    }


def _parse_profile(item: dict) -> dict:
    """Apify Profile Scraper 결과를 표준 dict로 변환."""
    handle = item.get("username", "")
    return {
        "handle": f"@{handle}",
        "profile_link": f"https://www.instagram.com/{handle}/",
        "followers": item.get("followersCount", 0),
        "following": item.get("followsCount", 0),
        "posts": item.get("postsCount", 0),
        "bio": item.get("biography", ""),
        "full_name": item.get("fullName", ""),
        "category": item.get("businessCategoryName") or item.get("categoryName", ""),
        "is_verified": item.get("verified", False),
        "is_business": item.get("isBusinessAccount", False),
        "profile_pic_url": item.get("profilePicUrlHD") or item.get("profilePicUrl", ""),
        "external_url": item.get("externalUrl", ""),
    }


async def scrape_instagram_profiles_batch(
    client: httpx.AsyncClient,
    handles: list[str],
) -> list[dict]:
    """
    여러 핸들을 한 번에 수집 (1회 API 호출).
    개별 호출보다 훨씬 빠름.
    """
    clean = list(set(h.lstrip("@").strip().rstrip("/") for h in handles if h))
    if not clean:
        return []

    logger.info("Batch profile scrape: %d handles", len(clean))
    items = await _run_actor(
        client,
        actor_id=ACTOR_IG_PROFILE,
        run_input={"usernames": clean},
        timeout=max(60.0, len(clean) * 3.0),  # 핸들 수에 비례한 타임아웃
    )

    return [_parse_profile(item) for item in items]


async def scrape_instagram_posts(
    client: httpx.AsyncClient,
    handle: str,
    limit: int = 12,
) -> list[dict]:
    """Apify instagram-post-scraper로 최근 게시물 수집."""
    handle = handle.lstrip("@").strip().rstrip("/")
    if not handle:
        return []

    items = await _run_actor(
        client,
        actor_id=ACTOR_IG_POST,
        run_input={
            "username": [handle],
            "resultsLimit": limit,
        },
        timeout=60.0,
    )

    return [
        {
            "id": item.get("id", ""),
            "type": item.get("type", ""),
            "caption": (item.get("caption", "") or "")[:200],
            "likes": item.get("likesCount", 0),
            "comments": item.get("commentsCount", 0),
            "timestamp": item.get("timestamp", ""),
            "url": item.get("url", ""),
            "image_url": item.get("displayUrl") or item.get("imageUrl", ""),
            "video_views": item.get("videoViewCount", 0),
        }
        for item in items[:limit]
    ]


async def scrape_instagram_reels(
    client: httpx.AsyncClient,
    handle: str,
    limit: int = 12,
) -> list[dict]:
    """Apify instagram-reel-scraper로 최근 릴스 수집."""
    handle = handle.lstrip("@").strip().rstrip("/")
    if not handle:
        return []

    items = await _run_actor(
        client,
        actor_id=ACTOR_IG_REEL,
        run_input={
            "username": [handle],
            "resultsLimit": limit,
        },
        timeout=60.0,
    )

    return [
        {
            "id": item.get("id", ""),
            "caption": (item.get("caption", "") or "")[:200],
            "likes": item.get("likesCount", 0),
            "comments": item.get("commentsCount", 0),
            "plays": item.get("playsCount") or item.get("videoPlayCount", 0),
            "timestamp": item.get("timestamp", ""),
            "url": item.get("url", ""),
            "duration": item.get("videoDuration", 0),
        }
        for item in items[:limit]
    ]


async def scrape_google_maps(
    client: httpx.AsyncClient,
    clinic_name: str,
    address: str = "",
) -> dict | None:
    """
    Apify crawler-google-places로 Google Maps 장소 데이터 수집.
    """
    search_query = f"{clinic_name} {address}".strip()
    if not search_query:
        return None

    items = await _run_actor(
        client,
        actor_id=ACTOR_GOOGLE_MAPS,
        run_input={
            "searchStringsArray": [search_query],
            "maxCrawledPlacesPerSearch": 1,
            "language": "ko",
            "countryCode": "kr",
        },
        timeout=60.0,
    )

    if not items:
        logger.info("Google Maps scrape returned no data for '%s'", search_query)
        return None

    item = items[0]
    return {
        "place_name": item.get("title", ""),
        "address": item.get("address", ""),
        "phone": item.get("phone", ""),
        "website": item.get("website", ""),
        "rating": item.get("totalScore", 0),
        "total_reviews": item.get("reviewsCount", 0),
        "category": item.get("categoryName", ""),
        "url": item.get("url", ""),
        "image_url": item.get("imageUrl", ""),
        "opening_hours": item.get("openingHours", []),
        "location": {
            "lat": item.get("location", {}).get("lat"),
            "lng": item.get("location", {}).get("lng"),
        },
        "place_id": item.get("placeId", ""),
        "reviews_distribution": item.get("reviewsDistribution", {}),
    }


async def _firecrawl_search(
    client: httpx.AsyncClient,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """Firecrawl search API 호출."""
    if not settings.FIRECRAWL_API_KEY:
        return []
    try:
        resp = await client.post(
            f"{FIRECRAWL_API}/search",
            json={"query": query, "limit": limit},
            headers={
                "Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        data = resp.json()
        return data.get("data", []) if data.get("success") else []
    except Exception as e:
        logger.warning("Firecrawl search error for '%s': %s", query, e)
        return []


async def _firecrawl_scrape(
    client: httpx.AsyncClient,
    url: str,
    formats: list[str] | None = None,
) -> dict | None:
    """Firecrawl scrape API 호출."""
    if not settings.FIRECRAWL_API_KEY:
        return None
    try:
        resp = await client.post(
            f"{FIRECRAWL_API}/scrape",
            json={"url": url, "formats": formats or ["markdown", "links"]},
            headers={
                "Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        data = resp.json()
        return data.get("data") if data.get("success") else None
    except Exception as e:
        logger.warning("Firecrawl scrape error for '%s': %s", url, e)
        return None


def _extract_channels_from_text(text: str, links: list[str]) -> dict:
    """텍스트 + 링크 목록에서 모든 SNS 채널 URL/핸들 추출."""
    all_text = text + " " + " ".join(links)
    result = {}

    # Instagram
    ig_skip = {"p", "reel", "reels", "explore", "accounts", "about",
               "developer", "legal", "terms", "stories", "tv", "directory", "static"}
    ig_handles = set()
    for m in re.finditer(r"instagram\.com/([\w.]{2,30})/?", all_text):
        handle = m.group(1).lower()
        if handle not in ig_skip:
            ig_handles.add(handle)
    if ig_handles:
        result["instagram"] = list(ig_handles)[:5]

    # YouTube
    yt_urls = set()
    for m in re.finditer(r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|@|user/)([\w@-]+)", all_text):
        yt_urls.add(f"https://www.youtube.com/{'' if m.group(1).startswith('@') else 'channel/'}{m.group(1)}")
    if yt_urls:
        result["youtube"] = list(yt_urls)[:3]

    # Facebook
    fb_skip = {"sharer", "share", "dialog", "plugins", "login", "pages", "groups", "watch", "marketplace"}
    fb_urls = set()
    for m in re.finditer(r"https?://(?:www\.)?facebook\.com/([\w.]+)/?", all_text):
        page = m.group(1).lower()
        if page not in fb_skip:
            fb_urls.add(f"https://www.facebook.com/{m.group(1)}/")
    if fb_urls:
        result["facebook"] = list(fb_urls)[:3]

    # 네이버 블로그
    naver_blogs = set()
    for m in re.finditer(r"https?://blog\.naver\.com/([\w]+)", all_text):
        blog_id = m.group(1)
        if blog_id not in {"MyBlog", "PostList", "PostView"}:
            naver_blogs.add(f"https://blog.naver.com/{blog_id}")
    if naver_blogs:
        result["naver_blog"] = list(naver_blogs)[:5]

    # 네이버 플레이스
    for m in re.finditer(r"https?://(?:m\.)?place\.naver\.com/[\w/]+", all_text):
        result["naver_place"] = m.group(0)
        break
    if "naver_place" not in result:
        for m in re.finditer(r"https?://(?:m\.)?map\.naver\.com/[\w/]+", all_text):
            result["naver_place"] = m.group(0)
            break

    # 카카오
    for m in re.finditer(r"https?://pf\.kakao\.com/([\w_]+)", all_text):
        result["kakao"] = f"https://pf.kakao.com/{m.group(1)}"
        break

    # TikTok
    for m in re.finditer(r"https?://(?:www\.)?tiktok\.com/@([\w.]+)", all_text):
        result["tiktok"] = f"https://www.tiktok.com/@{m.group(1)}"
        break

    # Twitter/X
    x_skip = {"intent", "share", "search", "i", "home"}
    for m in re.finditer(r"https?://(?:www\.)?(?:twitter|x)\.com/([\w]+)", all_text):
        handle = m.group(1).lower()
        if handle not in x_skip:
            result["twitter"] = f"https://x.com/{m.group(1)}"
            break

    return result


async def discover_missing_channels(
    client: httpx.AsyncClient,
    clinic_name: str,
    existing_channels: dict[str, list[str]],
) -> dict:
    """
    3단계 채널 디스커버리:
    1) 네이버 검색 결과 스크래핑 — 한 번에 대부분의 채널 발견
    2) 크로스 플랫폼 — Instagram 바이오, YouTube 설명에서 추가 채널 발견
    3) 개별 검색 폴백 — 아직 못 찾은 채널만
    """
    if not clinic_name:
        return {}

    discovered = {}

    # ── 1단계: 네이버 검색 결과 스크래핑 ──
    naver_search_url = f"https://search.naver.com/search.naver?query={clinic_name}"
    logger.info("Channel discovery step 1: Naver search for '%s'", clinic_name)

    naver_result = await _firecrawl_scrape(client, naver_search_url)
    if naver_result:
        md = naver_result.get("markdown", "")
        links = naver_result.get("links", [])
        naver_channels = _extract_channels_from_text(md, links)
        logger.info("Naver search found: %s", list(naver_channels.keys()))

        # 기존 채널에 없는 것만 discovered에 추가
        if naver_channels.get("instagram") and not existing_channels.get("instagram"):
            discovered["instagram"] = naver_channels["instagram"]
        if naver_channels.get("youtube") and not existing_channels.get("youtube"):
            discovered["youtube"] = naver_channels["youtube"][0]
        if naver_channels.get("facebook") and not existing_channels.get("facebook"):
            discovered["facebook"] = naver_channels["facebook"][0]

        has_blog = any("blog.naver.com" in u for u in existing_channels.get("naver", []))
        if naver_channels.get("naver_blog") and not has_blog:
            discovered["naver_blog"] = naver_channels["naver_blog"][0]

        has_place = any("place.naver.com" in u or "map.naver.com" in u for u in existing_channels.get("naver", []))
        if naver_channels.get("naver_place") and not has_place:
            discovered["naver_place"] = naver_channels["naver_place"]

        if naver_channels.get("kakao") and not existing_channels.get("kakao"):
            discovered["kakao"] = naver_channels["kakao"]
        if naver_channels.get("tiktok") and not existing_channels.get("tiktok"):
            discovered["tiktok"] = naver_channels["tiktok"]
        if naver_channels.get("twitter") and not existing_channels.get("twitter"):
            discovered["twitter"] = naver_channels["twitter"]

    # ── 2단계: 아직 못 찾은 채널만 개별 검색 ──
    still_missing = {}
    if not existing_channels.get("instagram") and "instagram" not in discovered:
        still_missing["instagram"] = _firecrawl_search(client, f"{clinic_name} 인스타그램 instagram")
    if not existing_channels.get("youtube") and "youtube" not in discovered:
        still_missing["youtube"] = _firecrawl_search(client, f"{clinic_name} 유튜브 youtube")

    if still_missing:
        logger.info("Channel discovery step 2: individual search for %s", list(still_missing.keys()))
        keys = list(still_missing.keys())
        results = await asyncio.gather(*still_missing.values(), return_exceptions=True)

        for key, result in zip(keys, results):
            if isinstance(result, Exception) or not result:
                continue
            if key == "instagram":
                ig_skip = {"p", "reel", "reels", "explore", "accounts", "about",
                           "developer", "legal", "terms", "stories", "tv", "directory"}
                handles = set()
                for item in result:
                    match = re.search(r"instagram\.com/([\w.]{2,30})/?", item.get("url", ""))
                    if match and match.group(1).lower() not in ig_skip:
                        handles.add(match.group(1).lower())
                if handles:
                    discovered["instagram"] = list(handles)[:3]
            elif key == "youtube":
                for item in result:
                    url = item.get("url", "")
                    if re.search(r"youtube\.com/(?:c/|channel/|@|user/)", url):
                        discovered["youtube"] = url
                        break

    logger.info("Channel discovery complete for '%s': %s",
                clinic_name,
                {k: (v if isinstance(v, str) else f"{len(v)} items") for k, v in discovered.items()})
    return discovered


async def enrich_channels(
    client: httpx.AsyncClient,
    instagram_handles: list[str],
    clinic_name: str,
    address: str = "",
) -> dict:
    """
    Apify 4개 Actor를 병렬 실행하여 채널 데이터 보강.
    - Instagram 프로필 (apify/instagram-profile-scraper)
    - Instagram 게시물 (apify/instagram-post-scraper)
    - Instagram 릴스 (apify/instagram-reel-scraper)
    - Google Maps (compass/crawler-google-places)
    """
    primary_handle = instagram_handles[0] if instagram_handles else None

    # 모든 Apify Actor를 병렬 실행
    tasks = {}

    # Instagram 프로필 (여러 핸들)
    for h in instagram_handles[:3]:
        tasks[f"ig_profile_{h}"] = scrape_instagram_profile(client, h)

    # Instagram 게시물 + 릴스 (대표 핸들만)
    if primary_handle:
        tasks["ig_posts"] = scrape_instagram_posts(client, primary_handle, limit=12)
        tasks["ig_reels"] = scrape_instagram_reels(client, primary_handle, limit=12)

    # Google Maps
    tasks["google_maps"] = scrape_google_maps(client, clinic_name, address)

    # 병렬 실행
    keys = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    result_map = {}
    for k, r in zip(keys, results):
        if isinstance(r, Exception):
            logger.warning("Apify task '%s' exception: %s", k, r)
            result_map[k] = None
        else:
            result_map[k] = r

    # Instagram 프로필 결과 조립
    ig_profiles = []
    for h in instagram_handles[:3]:
        profile = result_map.get(f"ig_profile_{h}")
        if profile:
            ig_profiles.append(profile)

    # Instagram 게시물/릴스 분석 통계
    ig_posts = result_map.get("ig_posts") or []
    ig_reels = result_map.get("ig_reels") or []

    ig_content_analysis = None
    if ig_posts or ig_reels:
        post_likes = [p.get("likes", 0) for p in ig_posts if p.get("likes")]
        post_comments = [p.get("comments", 0) for p in ig_posts if p.get("comments")]
        reel_plays = [r.get("plays", 0) for r in ig_reels if r.get("plays")]
        reel_likes = [r.get("likes", 0) for r in ig_reels if r.get("likes")]

        ig_content_analysis = {
            "posts_analyzed": len(ig_posts),
            "reels_analyzed": len(ig_reels),
            "avg_post_likes": round(sum(post_likes) / len(post_likes)) if post_likes else 0,
            "avg_post_comments": round(sum(post_comments) / len(post_comments)) if post_comments else 0,
            "avg_reel_plays": round(sum(reel_plays) / len(reel_plays)) if reel_plays else 0,
            "avg_reel_likes": round(sum(reel_likes) / len(reel_likes)) if reel_likes else 0,
            "top_posts": sorted(ig_posts, key=lambda x: x.get("likes", 0), reverse=True)[:5],
            "top_reels": sorted(ig_reels, key=lambda x: x.get("plays", 0), reverse=True)[:5],
        }

    # Google Maps
    gmap_result = result_map.get("google_maps")

    # 크로스 플랫폼: Instagram 바이오에서 추가 채널 발견
    cross_platform_links = {}
    for profile in ig_profiles:
        ext_url = profile.get("external_url", "")
        if ext_url:
            cross_platform_links["instagram_bio_link"] = ext_url

    return {
        "instagram": ig_profiles,
        "instagram_content": ig_content_analysis,
        "google_maps": gmap_result,
        "cross_platform_links": cross_platform_links,
        "discovered_handles": instagram_handles,
    }
