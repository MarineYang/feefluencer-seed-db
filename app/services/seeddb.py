"""
feefluencer-seed-db API 클라이언트.
사전 수집된 인플루언서 DB에서 키워드 검색을 수행한다.
DB에 데이터가 충분하면 Apify 호출 없이 즉시 결과를 반환한다.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "뷰티": ["뷰티", "메이크업", "화장품", "스킨케어", "beauty", "makeup", "skincare"],
    "성형/피부": ["성형", "피부과", "시술", "리프팅", "필러", "보톡스", "레이저", "plastic"],
    "건강/다이어트": ["건강", "운동", "헬스", "다이어트", "피트니스", "비만", "체중"],
    "패션": ["패션", "ootd", "룩북", "코디", "fashion"],
    "일상": ["일상", "데일리", "브이로그", "vlog"],
}


def _infer_categories(treatment_tags: list, bio: str) -> list[str]:
    """시술 태그 + 바이오에서 카테고리를 추론한다."""
    text = " ".join(treatment_tags or []) + " " + (bio or "")
    matched = []
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            matched.append(cat)
    return matched[:3] if matched else ["일상"]


def _to_poc_format(item: dict) -> dict:
    """
    seeddb 응답 형식을 PoC 기존 응답 형식으로 변환한다.
    기존 Apify 검색 결과와 동일한 구조를 유지해 프론트엔드 변경 최소화.
    """
    followers = item.get("followers") or 1
    avg_likes = item.get("avg_likes") or 0
    avg_comments = item.get("avg_comments") or 0

    engagement_rate = item.get("recent_engagement_rate") or item.get("engagement_rate")
    if engagement_rate:
        engagement_rate = round(float(engagement_rate) * 100, 2)  # 소수 → 퍼센트
    else:
        engagement_rate = round((avg_likes + avg_comments) / followers * 100, 2)

    # 예상 유효 팔로워 (ER 기반)
    if engagement_rate > 0.5:
        real_ratio = min(1.0, engagement_rate / 3.0 * 0.8 + 0.2)
    else:
        real_ratio = 0.3
    estimated_real_followers = round(followers * min(1.0, real_ratio))
    estimated_reach = round(avg_likes * 8) if avg_likes else round(followers * 0.15)

    handle = item.get("handle", "")
    categories = _infer_categories(item.get("treatment_tags") or [], item.get("bio") or "")

    return {
        # 기본 프로필
        "handle": f"@{handle}" if handle and not handle.startswith("@") else handle,
        "profile_link": item.get("profile_url") or f"https://www.instagram.com/{handle}/",
        "profile_pic_url": item.get("profile_pic_url"),
        "full_name": item.get("full_name"),
        "bio": item.get("bio"),
        "followers": followers,
        "following": item.get("following"),
        "posts": item.get("posts_count"),
        "follower_tier": item.get("follower_tier"),
        # 지표
        "engagement_rate": engagement_rate,
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "estimated_real_followers": estimated_real_followers,
        "estimated_reach": estimated_reach,
        # 분류 & 매칭
        "categories": categories,
        "treatment_tags": item.get("treatment_tags") or [],
        "region_tags": item.get("region_tags") or [],
        "match_type": item.get("match_source", "post_content"),
        "matched_posts": item.get("matched_post_count", 0),
        "sample_captions": item.get("sample_captions") or [],
        "last_upload": item.get("last_upload"),
        # B2B 전용 필드
        "match_score_skin_clinic": item.get("match_score_skin_clinic"),
        "match_score_plastic_surgery": item.get("match_score_plastic_surgery"),
        "match_score_obesity_clinic": item.get("match_score_obesity_clinic"),
        "has_contact_info": item.get("has_contact_info", False),
        "contact_email": item.get("contact_email"),
        "contact_kakao": item.get("contact_kakao"),
        "contact_linktree": item.get("contact_linktree"),
        "sponsorship_intent_signal": item.get("sponsorship_intent_signal"),
        "is_recently_active": item.get("is_recently_active", False),
        "content_consistency_score": item.get("content_consistency_score"),
        # 출처 표시
        "data_source": "seeddb",
    }


async def search_from_seeddb(
    keywords: list[str],
    min_followers: int = 1_000,
    max_followers: int = 0,
    limit: int = 30,
) -> list[dict]:
    """
    feefluencer-seed-db API에서 키워드 검색을 수행한다.
    여러 키워드를 순차 검색하고 handle 기준으로 중복 제거한다.
    """
    if not settings.SEEDDB_API_URL:
        return []

    base_url = settings.SEEDDB_API_URL.rstrip("/")
    seen_handles: set[str] = set()
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for kw in keywords:
            try:
                params: dict[str, Any] = {
                    "keyword": kw,
                    "min_followers": min_followers,
                    "limit": limit,
                }
                if max_followers > 0:
                    params["max_followers"] = max_followers

                resp = await client.get(f"{base_url}/api/influencers/search", params=params)
                resp.raise_for_status()
                data = resp.json()

                for item in data.get("items", []):
                    handle = item.get("handle", "")
                    if handle and handle not in seen_handles:
                        seen_handles.add(handle)
                        results.append(_to_poc_format(item))

            except httpx.ConnectError:
                logger.warning("seeddb API 연결 실패: %s (서버가 실행 중인지 확인)", base_url)
                break
            except Exception as e:
                logger.warning("seeddb 키워드 '%s' 검색 실패: %s", kw, e)

    # 매칭 게시물 수 → ER 순으로 정렬
    results.sort(key=lambda x: (x.get("matched_posts", 0), x.get("followers", 0)), reverse=True)
    return results[:limit]
