"""
인플루언서 키워드 기반 검색 서비스 (피쳐링 방식 POC).

핵심 최적화:
- Hashtag Scraper: 키워드별 병렬 실행 → 많은 게시물 수집
- Profile Scraper: 배치 호출 (50명을 1회 호출) → 빠른 프로필 수집
- 총 2~3회 API 호출로 30~50명 결과 반환 (~70초)
"""
import asyncio
import logging
import re
from datetime import datetime

import httpx

from app.services.apify import (
    _run_actor, scrape_instagram_profiles_batch,
    scrape_instagram_posts, scrape_instagram_reels,
    ACTOR_IG_HASHTAG,
)

logger = logging.getLogger(__name__)

# ─── 카테고리 자동 분류 ───

CATEGORY_KEYWORDS = {
    "뷰티": ["뷰티", "메이크업", "화장품", "스킨케어", "코스메틱", "beauty", "makeup", "skincare"],
    "패션": ["패션", "ootd", "룩북", "코디", "스타일링", "fashion", "outfit", "style"],
    "성형/피부": ["성형", "피부과", "시술", "리프팅", "필러", "보톡스", "눈성형", "코성형", "plastic"],
    "F&B": ["맛집", "카페", "요리", "레시피", "먹방", "맛스타그램", "food", "cafe", "cooking"],
    "건강/다이어트": ["건강", "운동", "헬스", "다이어트", "피트니스", "fitness", "gym", "workout"],
    "일상": ["일상", "데일리", "브이로그", "daily", "vlog"],
    "육아": ["육아", "아기", "맘", "임신", "아이", "baby", "mom"],
    "홈/리빙": ["홈", "인테리어", "리빙", "집꾸미기", "home", "interior"],
    "여행": ["여행", "travel", "trip", "관광"],
    "반려동물": ["반려", "강아지", "고양이", "펫", "dog", "cat", "pet"],
}


def _classify_category(bio: str, captions: list[str]) -> list[str]:
    """바이오 + 캡션에서 카테고리 자동 분류."""
    text = (bio + " " + " ".join(captions)).lower()
    matched = []
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matched.append(cat)
    return matched[:3] if matched else ["일상"]


def _estimate_metrics(profile: dict, post_data: dict) -> dict:
    """프로필 + 게시물 데이터로 지표 계산."""
    followers = profile.get("followers", 0) or 1
    avg_likes = post_data.get("avg_likes", 0)
    avg_comments = post_data.get("avg_comments", 0)

    # 인게이지먼트율
    engagement_rate = round((avg_likes + avg_comments) / followers * 100, 2) if followers > 1 else 0

    # 예상 유효 팔로워 (ER 기반 추정)
    # ER 1~5%: 정상, <0.5%: 의심, >10%: 매우 활발한 소규모 계정
    if engagement_rate > 0.5:
        real_ratio = min(1.0, engagement_rate / 3.0 * 0.8 + 0.2)
    else:
        real_ratio = 0.3
    estimated_real_followers = round(followers * min(1.0, real_ratio))

    # 예상 평균 도달 수 (좋아요 × 7~10배 추정)
    estimated_reach = round(avg_likes * 8) if avg_likes else round(followers * 0.15)

    return {
        "engagement_rate": engagement_rate,
        "estimated_real_followers": estimated_real_followers,
        "estimated_reach": estimated_reach,
    }


# ─── 키워드 → 인플루언서 해시태그 확장 ───

INFLUENCER_HASHTAG_MAP = {
    # 성형 관련 키워드 → 인플루언서가 쓰는 태그
    "성형": ["성형후기", "뷰티브이로그", "성형브이로그", "뷰티인플루언서"],
    "성형후기": ["성형브이로그", "뷰티브이로그", "뷰티인플루언서", "성형리뷰"],
    "코성형": ["코성형후기", "코수술후기", "뷰티브이로그", "성형브이로그"],
    "눈성형": ["눈성형후기", "쌍수후기", "뷰티브이로그", "성형브이로그"],
    "리프팅": ["리프팅후기", "피부관리", "동안피부", "뷰티인플루언서"],
    "필러": ["필러후기", "뷰티브이로그", "피부관리"],
    "보톡스": ["보톡스후기", "뷰티브이로그", "피부관리"],
    # 뷰티 관련
    "뷰티": ["뷰티인플루언서", "뷰티브이로그", "뷰티리뷰", "메이크업"],
    "화장품": ["화장품리뷰", "뷰티인플루언서", "코스메틱리뷰", "스킨케어"],
    "스킨케어": ["스킨케어루틴", "피부관리", "뷰티인플루언서", "화장품추천"],
    "메이크업": ["메이크업룩", "뷰티인플루언서", "메이크업추천", "데일리메이크업"],
    # 패션
    "패션": ["ootd", "데일리룩", "패션인플루언서", "코디추천"],
    "룩북": ["룩북", "ootd", "데일리룩", "패션인플루언서"],
    # 다이어트/건강
    "다이어트": ["다이어트브이로그", "운동브이로그", "바디프로필", "헬스타그램"],
    "피부관리": ["피부관리루틴", "스킨케어", "뷰티인플루언서", "동안피부"],
    # 일반
    "일상": ["일상브이로그", "데일리", "브이로그"],
    "브이로그": ["일상브이로그", "데일리브이로그"],
    "맛집": ["맛집추천", "맛스타그램", "카페추천", "푸드인플루언서"],
    "육아": ["육아브이로그", "육아일기", "맘스타그램"],
}


def _expand_to_influencer_hashtags(keywords: list[str], max_tags: int = 6) -> list[str]:
    """
    유저 키워드를 인플루언서가 실제로 쓰는 해시태그로 확장.
    예: "성형후기" → ["성형브이로그", "뷰티브이로그", "뷰티인플루언서", "성형리뷰"]
    """
    result = []
    for kw in keywords:
        kw_clean = kw.replace("#", "").replace(" ", "").lower()
        # 매핑된 인플루언서 해시태그 추가
        if kw_clean in INFLUENCER_HASHTAG_MAP:
            result.extend(INFLUENCER_HASHTAG_MAP[kw_clean])
        else:
            # 매핑 없으면 원본 키워드 + 일반 인플루언서 태그 조합
            result.append(kw_clean)
            result.append(f"{kw_clean}후기")
            result.append(f"{kw_clean}리뷰")

    # 중복 제거 + 최대 개수 제한
    seen = set()
    unique = []
    for tag in result:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)
    return unique[:max_tags]


# ─── 메인 키워드 검색 ───

async def search_by_keywords(
    client: httpx.AsyncClient,
    keywords: list[str],
    exclude_keywords: list[str] | None = None,
    exclude_accounts: list[str] | None = None,
    search_in_content: bool = True,
    search_in_profile: bool = True,
    min_followers: int = 1000,
    max_followers: int = 0,
    results_limit: int = 50,
) -> list[dict]:
    """
    피쳐링 방식 키워드 검색.

    1) 키워드별 Hashtag Scraper 병렬 실행 → 대량 게시물 수집
    2) 유니크 핸들 추출 + 게시물 데이터 집계
    3) Profile Scraper 배치 호출 (1회로 전체 프로필 수집)
    4) 필터 적용 + 지표 계산 → 결과 반환
    """
    if not keywords:
        return []

    exclude_kw = [k.lower() for k in (exclude_keywords or [])]
    exclude_acc = [k.lower() for k in (exclude_accounts or [])]

    # 기본 제외: 공식 계정/병원 계정 패턴
    default_exclude_acc = ["clinic", "hospital", "official", "surgery", "의원", "성형외과", "피부과"]
    exclude_acc = list(set(exclude_acc + default_exclude_acc))

    # 유저 키워드 + 인플루언서가 쓰는 연관 해시태그를 조합
    search_hashtags = _expand_to_influencer_hashtags(keywords)

    logger.info("=== Influencer search: keywords=%s → hashtags=%s, limit=%d ===",
                keywords, search_hashtags, results_limit)

    # ── 1단계: 해시태그 게시물 병렬 수집 ──
    per_tag_limit = max(20, results_limit // len(search_hashtags))
    hashtag_tasks = [
        _run_actor(
            client,
            actor_id=ACTOR_IG_HASHTAG,
            run_input={"hashtags": [tag], "resultsLimit": per_tag_limit},
            timeout=120.0,
        )
        for tag in search_hashtags
    ]

    all_results = await asyncio.gather(*hashtag_tasks, return_exceptions=True)

    # 게시물 합치기
    all_posts = []
    for i, result in enumerate(all_results):
        if isinstance(result, list):
            logger.info("  #%s: %d posts", search_hashtags[i], len(result))
            all_posts.extend(result)
        elif isinstance(result, Exception):
            logger.warning("  #%s: error %s", search_hashtags[i], result)

    if not all_posts:
        logger.info("No posts found")
        return []

    logger.info("Total posts collected: %d", len(all_posts))

    # ── 2단계: 유니크 핸들 추출 + 게시물 데이터 집계 ──
    owner_data: dict[str, dict] = {}

    for post in all_posts:
        owner = post.get("ownerUsername") or ""
        if not owner:
            continue

        owner_lower = owner.lower()
        caption = (post.get("caption") or "").lower()

        # 계정명 제외
        if _matches_any(owner_lower, exclude_acc):
            continue

        # 제외 키워드
        if exclude_kw and any(ek in caption for ek in exclude_kw):
            continue

        if owner not in owner_data:
            owner_data[owner] = {
                "post_count": 0,
                "total_likes": 0,
                "total_comments": 0,
                "captions": [],
                "content_match": False,
                "latest_timestamp": "",
            }

        od = owner_data[owner]
        od["post_count"] += 1
        od["total_likes"] += post.get("likesCount", 0) or 0
        od["total_comments"] += post.get("commentsCount", 0) or 0
        if len(od["captions"]) < 5:
            od["captions"].append(caption[:200])

        ts = post.get("timestamp", "")
        if ts > od["latest_timestamp"]:
            od["latest_timestamp"] = ts

        # 콘텐츠 키워드 매칭
        if search_in_content and any(k.lower() in caption for k in keywords):
            od["content_match"] = True

    logger.info("Unique accounts extracted: %d", len(owner_data))

    if not owner_data:
        return []

    # ── 3단계: 배치 프로필 수집 (1회 API 호출!) ──
    handle_list = list(owner_data.keys())[:50]  # 최대 50명
    logger.info("Batch profile scraping: %d handles in 1 call", len(handle_list))

    profiles = await scrape_instagram_profiles_batch(client, handle_list)
    logger.info("Profiles received: %d", len(profiles))

    # ── 4단계: 필터 적용 + 지표 계산 ──
    results = []

    for profile in profiles:
        handle = profile.get("handle", "").lstrip("@")
        followers = profile.get("followers", 0)
        bio = (profile.get("bio") or "").lower()
        full_name = (profile.get("full_name") or "").lower()

        # 팔로워 필터
        if followers < min_followers:
            continue
        if max_followers > 0 and followers > max_followers:
            continue

        # 계정명/바이오 제외 필터
        if _matches_any(handle.lower(), exclude_acc):
            continue
        if _matches_any(full_name, exclude_acc):
            continue
        if exclude_kw and any(ek in bio for ek in exclude_kw):
            continue

        # 프로필 키워드 매칭
        profile_match = False
        if search_in_profile:
            profile_match = any(k.lower() in bio or k.lower() in full_name for k in keywords)

        od = owner_data.get(handle, {})
        content_match = od.get("content_match", False)

        # 매칭 유형 결정
        if not content_match and not profile_match:
            # 해시태그에서 발견되었으므로 기본 매칭으로 처리
            match_type = "hashtag"
        elif content_match and profile_match:
            match_type = "content+profile"
        elif content_match:
            match_type = "content"
        elif profile_match:
            match_type = "profile"
        else:
            match_type = "hashtag"

        # 게시물 데이터에서 평균 계산
        pc = od.get("post_count", 1) or 1
        avg_likes = round(od.get("total_likes", 0) / pc)
        avg_comments = round(od.get("total_comments", 0) / pc)

        post_metrics = {"avg_likes": avg_likes, "avg_comments": avg_comments}
        metrics = _estimate_metrics(profile, post_metrics)

        # 카테고리 분류
        categories = _classify_category(
            profile.get("bio", ""),
            od.get("captions", []),
        )

        # 최근 업로드 일
        latest = od.get("latest_timestamp", "")
        last_upload = ""
        if latest:
            try:
                dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                days_ago = (datetime.now(dt.tzinfo) - dt).days
                last_upload = f"{days_ago}일 전" if days_ago >= 0 else latest[:10]
            except Exception:
                last_upload = latest[:10]

        results.append({
            **profile,
            **metrics,
            "categories": categories,
            "match_type": match_type,
            "matched_posts": od.get("post_count", 0),
            "avg_likes": avg_likes,
            "avg_comments": avg_comments,
            "last_upload": last_upload,
            "sample_captions": od.get("captions", [])[:3],
        })

    # 팔로워 순 정렬
    results.sort(key=lambda x: x.get("followers", 0), reverse=True)
    logger.info("=== Search complete: %d influencers returned ===", len(results))
    return results


async def search_by_handle(
    client: httpx.AsyncClient,
    handle: str,
) -> dict | None:
    """핸들로 인플루언서 프로필 + 콘텐츠 실시간 수집."""
    handle = handle.lstrip("@").strip().rstrip("/")
    if not handle:
        return None

    profile_result, posts, reels = await asyncio.gather(
        scrape_instagram_profiles_batch(client, [handle]),
        scrape_instagram_posts(client, handle, limit=12),
        scrape_instagram_reels(client, handle, limit=12),
        return_exceptions=True,
    )

    profiles = profile_result if isinstance(profile_result, list) else []
    if not profiles:
        return None

    profile = profiles[0]
    posts_list = posts if isinstance(posts, list) else []
    reels_list = reels if isinstance(reels, list) else []

    post_likes = [p.get("likes", 0) for p in posts_list if p.get("likes")]
    post_comments = [p.get("comments", 0) for p in posts_list if p.get("comments")]
    reel_plays = [r.get("plays", 0) for r in reels_list if r.get("plays")]

    avg_likes = round(sum(post_likes) / len(post_likes)) if post_likes else 0
    avg_comments = round(sum(post_comments) / len(post_comments)) if post_comments else 0
    avg_reel_plays = round(sum(reel_plays) / len(reel_plays)) if reel_plays else 0

    metrics = _estimate_metrics(profile, {"avg_likes": avg_likes, "avg_comments": avg_comments})
    categories = _classify_category(profile.get("bio", ""), [])

    return {
        **profile,
        **metrics,
        "avg_likes": avg_likes,
        "avg_comments": avg_comments,
        "avg_reel_plays": avg_reel_plays,
        "categories": categories,
        "recent_posts": posts_list[:6],
        "recent_reels": reels_list[:6],
    }


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(p in text for p in patterns) if patterns else False
