"""
Instaloader 기반 프로필 스크래퍼 (무료, 로그인 불필요).
Discovery 단계에서 Apify Profile Scraper를 대체한다.

반환 형식은 apify_client.parse_profile()이 읽을 수 있는 구조로 맞춰져 있어
_upsert_influencer()를 수정하지 않아도 된다.
"""
from __future__ import annotations

import asyncio
import random
from concurrent.futures import ThreadPoolExecutor

import instaloader
from loguru import logger

# 동시 요청 수 제한 (IP 차단 방지)
_executor = ThreadPoolExecutor(max_workers=2)

# 요청 간 딜레이 (초)
DELAY_MIN = 3.0
DELAY_MAX = 8.0

# 계정당 연속 실패 허용 횟수 초과 시 일시 중단
_consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5


def _fetch_profile_sync(username: str) -> dict | None:
    """
    Instaloader로 단일 계정 프로필을 동기 방식으로 수집한다.
    스레드 풀에서 실행된다.
    """
    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )
    try:
        profile = instaloader.Profile.from_username(L.context, username)
        return {
            # parse_profile()의 fallback 키 사용
            "username":          profile.username,
            "id":                str(profile.userid),
            "fullName":          profile.full_name,
            "biography":         profile.biography,
            "followers":         profile.followers,
            "following":         profile.followees,
            "mediaCount":        profile.mediacount,
            "profilePicUrl":     profile.profile_pic_url,
            "externalUrl":       profile.external_url,
            "verified":          profile.is_verified,
            "isBusinessAccount": profile.is_business_account,
        }
    except instaloader.exceptions.ProfileNotExistsException:
        logger.debug(f"Instaloader: @{username} 계정 없음 (비공개 or 삭제됨)")
        return None
    except instaloader.exceptions.ConnectionException as e:
        logger.warning(f"Instaloader: 연결 오류 (@{username}) — {e}")
        return None
    except Exception as e:
        logger.warning(f"Instaloader: @{username} 수집 실패 — {e}")
        return None


async def scrape_profiles(usernames: list[str]) -> list[dict]:
    """
    Instaloader로 프로필 목록을 수집한다.

    - 로그인 불필요
    - 요청 간 랜덤 딜레이로 IP 차단 방지
    - 연속 오류 MAX_CONSECUTIVE_ERRORS 초과 시 조기 종료
    """
    global _consecutive_errors

    if not usernames:
        return []

    loop = asyncio.get_event_loop()
    results: list[dict] = []

    logger.info(f"Instaloader 프로필 수집 시작: {len(usernames)}개")

    for i, username in enumerate(usernames):
        raw = await loop.run_in_executor(_executor, _fetch_profile_sync, username)

        if raw:
            results.append(raw)
            _consecutive_errors = 0
        else:
            _consecutive_errors += 1
            if _consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.warning(
                    f"Instaloader: 연속 오류 {_consecutive_errors}회 — "
                    f"IP 차단 가능성, 수집 중단 ({i+1}/{len(usernames)})"
                )
                _consecutive_errors = 0
                break

        # 마지막 계정이 아니면 딜레이
        if i < len(usernames) - 1:
            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            await asyncio.sleep(delay)

    logger.info(f"Instaloader 프로필 수집 완료: {len(results)}/{len(usernames)}개")
    return results
