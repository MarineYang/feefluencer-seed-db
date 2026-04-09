"""
Apify API 클라이언트 래퍼
- Hashtag Scraper
- Profile Scraper
- Post Scraper
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 5   # seconds
MAX_WAIT = 600      # 최대 10분 대기


class ApifyError(Exception):
    pass


class ApifyClient:
    def __init__(self):
        self.token = settings.apify_api_token
        self.headers = {"Authorization": f"Bearer {self.token}"}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
    async def _run_actor(self, actor_id: str, input_data: dict) -> list[dict]:
        """Actor를 실행하고 결과를 반환한다."""
        # Apify API는 경로에 '~' 사용 (슬래시 불가)
        actor_path = actor_id.replace("/", "~")
        async with httpx.AsyncClient(timeout=30) as client:
            run_res = await client.post(
                f"{APIFY_BASE}/acts/{actor_path}/runs",
                headers=self.headers,
                json=input_data,   # input을 직접 body로 전송
            )
            if not run_res.is_success:
                logger.error(
                    f"Apify API 오류 [{run_res.status_code}] actor={actor_id}\n"
                    f"응답: {run_res.text[:500]}"
                )
            run_res.raise_for_status()
            run_id = run_res.json()["data"]["id"]
            logger.info(f"Apify run started: {actor_id} / run_id={run_id}")

        # 완료 대기
        return await self._wait_for_run(run_id)

    async def _wait_for_run(self, run_id: str) -> list[dict]:
        """Run이 완료될 때까지 폴링하고 결과를 반환한다."""
        elapsed = 0
        async with httpx.AsyncClient(timeout=30) as client:
            while elapsed < MAX_WAIT:
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL

                status_res = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=self.headers,
                )
                status_res.raise_for_status()
                status = status_res.json()["data"]["status"]

                if status == "SUCCEEDED":
                    dataset_id = status_res.json()["data"]["defaultDatasetId"]
                    return await self._fetch_dataset(dataset_id)

                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    raise ApifyError(f"Apify run {run_id} ended with status: {status}")

                logger.debug(f"Apify run {run_id} status: {status} ({elapsed}s)")

        raise ApifyError(f"Apify run {run_id} timed out after {MAX_WAIT}s")

    async def _fetch_dataset(self, dataset_id: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=60) as client:
            res = await client.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items",
                headers=self.headers,
                params={"format": "json", "clean": True},
            )
            res.raise_for_status()
            return res.json()

    # ----------------------------------------------------------------
    # 공개 메서드
    # ----------------------------------------------------------------

    async def scrape_hashtag(self, hashtag: str, limit: Optional[int] = None) -> list[str]:
        """
        해시태그에서 게시물 작성자 username 목록을 반환한다.
        반환: ["username1", "username2", ...]
        """
        limit = limit or settings.hashtag_results_limit
        # # 제거
        tag = hashtag.lstrip("#")
        logger.info(f"Hashtag scrape: #{tag} (limit={limit})")

        results = await self._run_actor(
            settings.hashtag_scraper_actor,
            {
                "hashtags": [tag],
                "resultsLimit": limit,
            },
        )

        usernames = []
        for item in results:
            username = (
                item.get("ownerUsername")
                or item.get("username")
                or (item.get("owner") or {}).get("username")
            )
            if username:
                usernames.append(username.lower())

        logger.info(f"Hashtag #{tag}: {len(usernames)}개 username 수집")
        return list(set(usernames))

    async def scrape_profiles(self, usernames: list[str]) -> list[dict]:
        """
        username 목록으로 프로필 정보를 일괄 수집한다.
        반환: Apify 응답 raw 데이터 리스트
        """
        if not usernames:
            return []
        logger.info(f"Profile scrape: {len(usernames)}개 계정")

        results = await self._run_actor(
            settings.profile_scraper_actor,
            {"usernames": usernames},
        )
        logger.info(f"Profile scrape 완료: {len(results)}개 결과")
        return results

    async def scrape_posts(self, username: str, limit: Optional[int] = None) -> list[dict]:
        """
        계정의 최근 게시물을 수집한다.
        반환: Apify 응답 raw 데이터 리스트
        """
        limit = limit or settings.post_results_limit
        logger.info(f"Post scrape: @{username} (limit={limit})")

        results = await self._run_actor(
            settings.post_scraper_actor,
            {
                "username": [username],
                "resultsLimit": limit,
            },
        )
        logger.info(f"Post scrape @{username}: {len(results)}개 게시물")
        return results


# ----------------------------------------------------------------
# Apify 응답 → 내부 모델 변환
# ----------------------------------------------------------------

def parse_profile(raw: dict) -> dict:  # noqa: C901
    """Apify 프로필 응답을 DB 저장용 dict로 변환한다."""
    followers = raw.get("followersCount") or raw.get("followers") or 0
    following = raw.get("followingCount") or raw.get("following") or 0
    posts_count = raw.get("postsCount") or raw.get("mediaCount") or 0
    avg_likes = raw.get("avgLikes") or raw.get("averageLikes")
    avg_comments = raw.get("avgComments") or raw.get("averageComments")

    engagement_rate = None
    if avg_likes and followers and followers > 0:
        engagement_rate = round((avg_likes + (avg_comments or 0)) / followers, 6)

    return {
        "platform": "instagram",
        "instagram_user_id": str(raw.get("id") or raw.get("userId") or ""),
        "handle": (raw.get("username") or raw.get("handle") or "").lower(),
        "full_name": raw.get("fullName") or raw.get("name"),
        "bio": raw.get("biography") or raw.get("bio"),
        "profile_url": raw.get("url") or raw.get("profileUrl"),
        "profile_pic_url": raw.get("profilePicUrl") or raw.get("profilePicUrlHD"),
        "external_url": raw.get("externalUrl") or raw.get("website"),
        "is_verified": bool(raw.get("verified") or raw.get("isVerified")),
        "is_business": bool(raw.get("businessCategoryName") or raw.get("isBusinessAccount")),
        "followers": followers,
        "following": following,
        "posts_count": posts_count,
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "avg_reel_plays": raw.get("avgVideoViews") or raw.get("avgReelPlays"),
        "engagement_rate": engagement_rate,
    }


def parse_post(raw: dict, influencer_id: str) -> dict:
    """Apify 게시물 응답을 DB 저장용 dict로 변환한다."""
    caption = raw.get("caption") or raw.get("text") or ""
    hashtags = raw.get("hashtags") or []
    mentions = raw.get("mentions") or []

    # 타입 정규화
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    if isinstance(mentions, str):
        mentions = [mentions]

    return {
        "influencer_id": influencer_id,
        "platform": "instagram",
        "external_post_id": str(raw.get("id") or raw.get("postId") or ""),
        "post_url": raw.get("url") or raw.get("postUrl") or "",
        "post_type": raw.get("type") or raw.get("mediaType") or "photo",
        "caption": caption,
        "likes": raw.get("likesCount") or raw.get("likes") or 0,
        "comments": raw.get("commentsCount") or raw.get("comments") or 0,
        "plays": raw.get("videoViewCount") or raw.get("plays") or 0,
        "hashtags": json.dumps(hashtags, ensure_ascii=False),
        "mentions": json.dumps(mentions, ensure_ascii=False),
        "posted_at": raw.get("timestamp") or raw.get("postedAt"),
    }
