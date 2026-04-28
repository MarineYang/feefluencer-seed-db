"""
Webshare 프록시 + Instagram 모바일 API 포스트 수집 테스트.

Instaloader의 GraphQL(doc_id) 방식이 Instagram에 의해 차단되므로
세션 쿠키만 Instaloader에서 빌려와 모바일 API를 직접 호출한다.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from instaloader_proxy_client import _load_proxies
import instaloader
import random
import requests

PROXY_FILE = Path(__file__).parent.parent / "Webshare 10 proxies.txt"
TEST_USERNAME = "kimninna"
POST_LIMIT = 5
IG_SESSION_ID = "35371545333%3Ai8MEdYeEgvqPO5%3A17%3AAYhG9dEnRGNlnB8PjiQVqUVDWlriXgY_I1j7vvMEbg"

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "X-IG-App-ID": "936619743392459",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://www.instagram.com/",
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
}

MOBILE_HEADERS = {
    "User-Agent": (
        "Instagram 275.0.0.27.98 Android "
        "(33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458229258)"
    ),
    "X-IG-App-ID": "936619743392459",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


def load_session_id() -> str:
    """브라우저에서 복사한 sessionid를 URL 디코딩해서 반환한다."""
    from urllib.parse import unquote
    return unquote(IG_SESSION_ID)


def get_user_id(username: str, session_id: str, proxy: str) -> str:
    """username → user_id 변환 (Instagram 웹 API)"""
    resp = requests.get(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
        headers={**WEB_HEADERS, "Referer": f"https://www.instagram.com/{username}/"},
        cookies={"sessionid": session_id},
        proxies={"http": proxy, "https": proxy},
        timeout=15,
    )
    resp.raise_for_status()
    return str(resp.json()["data"]["user"]["id"])


def get_posts(user_id: str, session_id: str, proxy: str, limit: int = 30) -> list[dict]:
    """Instagram 모바일 API로 포스트를 수집한다. GraphQL 미사용."""
    posts = []
    max_id = None

    while len(posts) < limit:
        params = {"count": min(12, limit - len(posts))}
        if max_id:
            params["max_id"] = max_id

        resp = requests.get(
            f"https://i.instagram.com/api/v1/feed/user/{user_id}/",
            headers=MOBILE_HEADERS,
            cookies={"sessionid": session_id},
            params=params,
            proxies={"http": proxy, "https": proxy},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            caption_text = ""
            if item.get("caption"):
                caption_text = item["caption"].get("text", "")
            taken_at = item.get("taken_at")
            posted_at = (
                datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat()
                if taken_at else None
            )
            # 이미지 URL (최고해상도)
            image_url = None
            candidates = (item.get("image_versions2") or {}).get("candidates", [])
            if candidates:
                image_url = candidates[0].get("url")

            # 영상 URL
            video_url = None
            video_versions = item.get("video_versions")
            if video_versions:
                video_url = video_versions[0].get("url")

            posts.append({
                "id":          str(item.get("pk", "")),
                "url":         f"https://www.instagram.com/p/{item.get('code', '')}/",
                "media_type":  item.get("media_type"),  # 1=사진, 2=영상, 8=캐러셀
                "caption":     caption_text,
                "likes":       item.get("like_count", 0),
                "likes_hidden": item.get("like_and_view_counts_disabled", False),
                "comments":    item.get("comment_count", 0),
                "plays":       item.get("view_count") or item.get("play_count") or 0,
                "posted_at":   posted_at,
                "hashtags":    [w[1:] for w in caption_text.split() if w.startswith("#")],
                "image_url":   image_url,
                "video_url":   video_url,
            })

        if not data.get("more_available"):
            break
        max_id = items[-1]["pk"]

    return posts[:limit]


def main():
    proxies = _load_proxies(PROXY_FILE)
    proxy = random.choice(proxies)

    print("[세션 로드] 브라우저 쿠키 사용")
    session_id = load_session_id()
    print(f"[세션] sessionid={session_id[:20]}...\n")

    print(f"[프록시] {proxy}")
    print(f"[대상]   @{TEST_USERNAME}\n")

    print("[user_id 조회 중...]")
    user_id = get_user_id(TEST_USERNAME, session_id, proxy)
    print(f"[user_id] {user_id}\n")

    print(f"[포스트 수집 중... (최대 {POST_LIMIT}개)]")
    posts = get_posts(user_id, session_id, proxy, limit=POST_LIMIT)

    print(f"\n=== 최근 포스트 ({len(posts)}개) ===")
    for i, p in enumerate(posts):
        caption = p["caption"].replace("\n", " ")[:80]
        print(f"  [{i+1}] {p['posted_at']}")
        print(f"       likes={p['likes']} | comments={p['comments']} | plays={p['plays']}")
        print(f"       likes_hidden={p['likes_hidden']} | type={p['media_type']}")
        print(f"       image={p['image_url'][:80] if p['image_url'] else 'None'}...")
        print(f"       video={p['video_url'][:80] if p['video_url'] else 'None'}...")
        print(f"       caption={caption}")
        print()

    print("완료!")


if __name__ == "__main__":
    main()
