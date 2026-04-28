"""
Instagram 모바일 API 기반 포스트 수집 클라이언트.
Apify instagram-post-scraper를 대체한다. (비용 0)

인증: 브라우저에서 복사한 sessionid 쿠키 (.env의 INSTAGRAM_SESSION_ID)
프록시: Webshare 프록시 풀 로테이션
HTTP: requests 라이브러리 (thread executor로 async 호환)
"""
from __future__ import annotations

import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import requests
from loguru import logger
from config import settings

PROXY_FILE = Path(__file__).resolve().parent.parent / "Webshare 10 proxies.txt"
POST_LIMIT_DEFAULT = 50

_MEDIA_TYPE = {1: "photo", 2: "video", 8: "carousel"}
_executor = ThreadPoolExecutor(max_workers=4)

_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-IG-App-ID": "936619743392459",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
}

_MOBILE_HEADERS = {
    "User-Agent": (
        "Instagram 275.0.0.27.98 Android "
        "(33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458229258)"
    ),
    "X-IG-App-ID": "936619743392459",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def _get_session_id() -> str:
    raw = settings.instagram_session_id
    if not raw:
        raise RuntimeError(
            "INSTAGRAM_SESSION_ID가 .env에 없습니다. "
            "브라우저 instagram.com 쿠키에서 sessionid를 복사해 추가하세요."
        )
    return unquote(raw)


def _load_proxies() -> list[str]:
    proxies = []
    for line in PROXY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, password = parts
            proxies.append(f"http://{user}:{password}@{ip}:{port}")
    return proxies


_PROXIES: list[str] = []


def _pick_proxy() -> str:
    global _PROXIES
    if not _PROXIES:
        _PROXIES = _load_proxies()
    return random.choice(_PROXIES)


def _parse_item(item: dict) -> dict:
    """Instagram 모바일 API 응답 아이템을 parse_post() 호환 형식으로 변환."""
    caption_text = ""
    if item.get("caption"):
        caption_text = item["caption"].get("text", "")

    taken_at = item.get("taken_at")
    posted_at = (
        datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat()
        if taken_at else None
    )

    image_url = None
    candidates = (item.get("image_versions2") or {}).get("candidates", [])
    if candidates:
        image_url = candidates[0].get("url")

    video_url = None
    if item.get("video_versions"):
        video_url = item["video_versions"][0].get("url")

    hashtags = [w[1:] for w in caption_text.split() if w.startswith("#")]

    return {
        "id":        str(item.get("pk", "")),
        "url":       f"https://www.instagram.com/p/{item.get('code', '')}/",
        "type":      _MEDIA_TYPE.get(item.get("media_type"), "photo"),
        "caption":   caption_text,
        "hashtags":  hashtags,
        "timestamp": posted_at,
        "likes":     item.get("like_count", 0),
        "comments":  item.get("comment_count", 0),
        "plays":     item.get("view_count") or item.get("play_count") or 0,
        "image_url": image_url,
        "video_url": video_url,
    }


def _scrape_posts_sync(username: str, limit: int) -> list[dict]:
    """동기 방식으로 포스트 수집. thread executor에서 실행된다."""
    session_id = _get_session_id()
    proxy = _pick_proxy()
    proxy_dict = {"http": proxy, "https": proxy}

    logger.info(f"[IGPostClient] @{username} 수집 시작 (limit={limit}, proxy={proxy.split('@')[1]})")

    # 1. user_id 조회 (프록시 없이 직접 — 세션 IP 불일치 방지)
    try:
        resp = requests.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers={**_WEB_HEADERS, "Referer": f"https://www.instagram.com/{username}/"},
            cookies={"sessionid": session_id},
            timeout=15,
        )
        resp.raise_for_status()
        user_id = str(resp.json()["data"]["user"]["id"])
        logger.debug(f"[IGPostClient] @{username} user_id={user_id}")
    except Exception as e:
        logger.warning(f"[IGPostClient] user_id 조회 실패 @{username}: {e}")
        return []

    # 2. 포스트 수집
    posts: list[dict] = []
    max_id = None

    while len(posts) < limit:
        params: dict = {"count": min(12, limit - len(posts))}
        if max_id:
            params["max_id"] = max_id

        try:
            resp = requests.get(
                f"https://i.instagram.com/api/v1/feed/user/{user_id}/",
                headers=_MOBILE_HEADERS,
                cookies={"sessionid": session_id},
                params=params,
                proxies=proxy_dict,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[IGPostClient] 포스트 요청 실패 @{username}: {e}")
            break

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            posts.append(_parse_item(item))

        if not data.get("more_available"):
            break
        max_id = items[-1]["pk"]

    logger.info(f"[IGPostClient] @{username} 수집 완료: {len(posts)}개")
    return posts[:limit]


async def scrape_posts(username: str, limit: int = POST_LIMIT_DEFAULT) -> list[dict]:
    """
    username의 최근 포스트를 Instagram 모바일 API로 수집한다.
    Apify scrape_posts()와 동일한 호출 인터페이스.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _scrape_posts_sync, username, limit)
