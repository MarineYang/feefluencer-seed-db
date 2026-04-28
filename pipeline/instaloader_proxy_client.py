"""
Webshare 프록시 풀 기반 Instaloader 클라이언트.
기존 instaloader_client.py를 건드리지 않고 독립적으로 동작한다.
"""
from __future__ import annotations

import asyncio
import itertools
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import instaloader
from loguru import logger

DELAY_MIN = 10.0
DELAY_MAX = 25.0
MAX_CONSECUTIVE_ERRORS = 3

_executor = ThreadPoolExecutor(max_workers=3)


def _load_proxies(proxy_file: str | Path) -> list[str]:
    """IP:PORT:USER:PASS 형식 파일을 읽어 http://USER:PASS@IP:PORT 형식으로 변환"""
    proxies = []
    for line in Path(proxy_file).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, password = parts
            proxies.append(f"http://{user}:{password}@{ip}:{port}")
    if not proxies:
        raise ValueError(f"프록시 파일에서 유효한 항목을 찾을 수 없습니다: {proxy_file}")
    return proxies


class ProxyInstaloaderClient:
    def __init__(self, proxy_file: str | Path):
        self._proxies = _load_proxies(proxy_file)
        self._proxy_cycle = itertools.cycle(self._proxies)
        logger.info(f"[ProxyInstaloader] 프록시 풀 로드: {len(self._proxies)}개")

    def _next_proxy(self) -> str:
        return next(self._proxy_cycle)

    def _fetch_profile_sync(self, username: str, proxy: str) -> dict | None:
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
        L.context._session.proxies = {"http": proxy, "https": proxy}
        try:
            profile = instaloader.Profile.from_username(L.context, username)
            return {
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
            logger.debug(f"[ProxyInstaloader] @{username} 계정 없음 (비공개 or 삭제됨)")
            return None
        except instaloader.exceptions.ConnectionException as e:
            logger.warning(f"[ProxyInstaloader] 연결 오류 (@{username}, proxy={proxy}) — {e}")
            return None
        except Exception as e:
            logger.warning(f"[ProxyInstaloader] @{username} 수집 실패 — {e}")
            return None

    async def scrape_profiles(self, usernames: list[str]) -> list[dict]:
        if not usernames:
            return []

        loop = asyncio.get_event_loop()
        results: list[dict] = []
        consecutive_errors = 0

        logger.info(f"[ProxyInstaloader] 프로필 수집 시작: {len(usernames)}개")

        for i, username in enumerate(usernames):
            proxy = self._next_proxy()
            raw = await loop.run_in_executor(
                _executor,
                self._fetch_profile_sync,
                username,
                proxy,
            )

            if raw:
                results.append(raw)
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.warning(
                        f"[ProxyInstaloader] 연속 오류 {consecutive_errors}회 — "
                        f"수집 중단 ({i + 1}/{len(usernames)})"
                    )
                    break

            if i < len(usernames) - 1:
                delay = random.uniform(DELAY_MIN, DELAY_MAX)
                await asyncio.sleep(delay)

        logger.info(f"[ProxyInstaloader] 수집 완료: {len(results)}/{len(usernames)}개")
        return results
