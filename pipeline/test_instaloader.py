"""
Instaloader 독립 테스트 스크립트
- Apify 해시태그 1회 → username 확보
- Instaloader로 프로필 수집
- keywords.py로 분석
- JSON 저장 + 통계 출력

DB 연결 불필요. 독립 실행 가능.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

# ── 환경변수를 다른 모듈 import 전에 먼저 로드 ──
from dotenv import load_dotenv

_root = Path(__file__).parent.parent
load_dotenv(_root / ".env", override=True)
# pipeline/config.py에 필요한 database_url (테스트에서는 미사용)
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite:///dummy.db"

from loguru import logger

# pipeline/ 디렉토리를 import path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from instagrapi import Client as InstaClient

from apify_client import ApifyClient
from keywords import (
    extract_treatment_tags,
    extract_region_tags,
    extract_contact_info,
    detect_sponsorship_intent,
    calculate_follower_tier,
    calculate_quality_flags,
    passes_triage,
    is_business_account,
)

# 안전한 딜레이 설정 (초)
DELAY_MIN = 10.0
DELAY_MAX = 15.0

# 결과 저장 경로
RESULTS_FILE = Path(__file__).parent / "test_results.json"

# 수집할 해시태그 (도메인별 대표 3개)
SEED_HASHTAGS = [
    "피부과후기",
    "성형후기",
    "다이어트일기",
]

# 목표 수집 수 (신규 계정이므로 안전하게 소량 테스트)
TARGET_COUNT = 10


async def step1_get_usernames() -> list[str]:
    """Apify 해시태그 스크래퍼 1회 호출로 username 확보"""
    logger.info("=" * 60)
    logger.info("Step 1: Apify 해시태그 스크래핑 (1회)")
    logger.info("=" * 60)

    client = ApifyClient()
    all_usernames: set[str] = set()

    for hashtag in SEED_HASHTAGS:
        try:
            usernames = await client.scrape_hashtag(hashtag, limit=100)
            logger.info(f"  #{hashtag}: {len(usernames)}명 발견")
            all_usernames.update(usernames)
        except Exception as e:
            logger.error(f"  #{hashtag} 실패: {e}")

    unique_list = list(all_usernames)
    logger.info(f"  → 총 고유 username: {len(unique_list)}명")

    # username 목록 캐시 저장 (재실행 시 Apify 호출 생략용)
    cache_file = Path(__file__).parent / "test_usernames_cache.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(unique_list, f, ensure_ascii=False)
    logger.info(f"  → 캐시 저장: {cache_file}")

    return unique_list


async def step2_scrape_profiles(usernames: list[str]) -> list[dict]:
    """instagrapi 로그인 세션으로 프로필 수집 (차단 최소화)"""
    logger.info("=" * 60)
    logger.info(f"Step 2: instagrapi 프로필 수집 (목표: {TARGET_COUNT}명)")
    logger.info(f"  딜레이: {DELAY_MIN}~{DELAY_MAX}초 (안전 모드)")
    logger.info("=" * 60)

    ig_user = os.environ.get("INSTAGRAM_USERNAME", "")
    ig_pass = os.environ.get("INSTAGRAM_PASSWORD", "")
    if not ig_user or not ig_pass:
        logger.error("INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD 환경변수가 없습니다.")
        return []

    # 로그인
    cl = InstaClient()
    cl.delay_range = [int(DELAY_MIN), int(DELAY_MAX)]

    logger.info(f"  Instagram 로그인 중: {ig_user}")
    try:
        cl.login(ig_user, ig_pass)
        logger.info("  로그인 성공")
    except Exception as e:
        logger.error(f"  로그인 실패: {e}")
        return []

    # 수집
    target_usernames = usernames[:TARGET_COUNT + 30]
    results: list[dict] = []
    consecutive_errors = 0
    max_errors = 5
    start_time = time.time()

    logger.info(f"  → {len(target_usernames)}명 시도 (목표: {TARGET_COUNT}명)")

    for i, username in enumerate(target_usernames):
        if len(results) >= TARGET_COUNT:
            logger.info(f"  → 목표 {TARGET_COUNT}명 달성, 수집 종료")
            break

        try:
            info = cl.user_info_by_username(username)
            raw = {
                "username":          info.username,
                "id":                str(info.pk),
                "fullName":          info.full_name,
                "biography":         info.biography,
                "followers":         info.follower_count,
                "following":         info.following_count,
                "mediaCount":        info.media_count,
                "profilePicUrl":     str(info.profile_pic_url) if info.profile_pic_url else None,
                "externalUrl":       info.external_url,
                "verified":          info.is_verified,
                "isBusinessAccount": info.is_business,
            }
            results.append(raw)
            consecutive_errors = 0

            if len(results) % 10 == 0:
                elapsed = time.time() - start_time
                logger.info(f"  진행: {len(results)}명 수집 ({i+1}/{len(target_usernames)}) [{elapsed:.0f}초]")

        except Exception as e:
            error_msg = str(e)
            logger.debug(f"  @{username}: {error_msg[:80]}")
            consecutive_errors += 1

            # Rate limit / challenge 감지 시 장시간 대기
            if any(kw in error_msg.lower() for kw in ["429", "rate", "challenge", "checkpoint", "login_required"]):
                wait = 180
                logger.warning(f"  Rate limit/Challenge 감지! {wait}초 대기...")
                await asyncio.sleep(wait)
                consecutive_errors = 0
                continue

        if consecutive_errors >= max_errors:
            logger.error(f"  연속 {max_errors}회 실패 — 차단 가능성, 수집 중단")
            break

        # 딜레이 (instagrapi의 delay_range와 별개로 추가 딜레이)
        if i < len(target_usernames) - 1:
            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            await asyncio.sleep(delay)

    elapsed = time.time() - start_time
    logger.info(f"  → 수집 완료: {len(results)}명 (소요: {elapsed:.0f}초)")
    return results


def step3_analyze(raw_profiles: list[dict]) -> list[dict]:
    """프로필 분석 (keywords.py 함수 적용)"""
    logger.info("=" * 60)
    logger.info("Step 3: 프로필 분석")
    logger.info("=" * 60)

    results = []
    for raw in raw_profiles:
        handle = (raw.get("username") or "").lower()
        bio = raw.get("biography") or ""
        external_url = raw.get("externalUrl") or ""
        followers = raw.get("followers") or 0
        following = raw.get("following") or 0
        posts_count = raw.get("mediaCount") or 0
        full_name = raw.get("fullName") or ""
        is_business = raw.get("isBusinessAccount", False)

        # 분석
        treatment_tags = extract_treatment_tags(bio)
        region_tags = extract_region_tags(bio)
        contact_info = extract_contact_info(bio, external_url)
        intent_signal, intent_raw = detect_sponsorship_intent(bio)
        follower_tier = calculate_follower_tier(followers)
        quality_flags = calculate_quality_flags(
            followers=followers,
            following=following,
            engagement_rate=None,
            posts_count=posts_count,
            avg_reel_plays=None,
        )
        is_biz = is_business_account(
            bio=bio,
            handle=handle,
            full_name=full_name,
            is_business=is_business,
        )
        triage_pass, triage_reason = passes_triage(
            followers=followers,
            following=following,
            posts_count=posts_count,
            quality_flags=quality_flags,
            bio=bio,
            handle=handle,
            full_name=full_name,
            is_business=is_business,
            engagement_rate=None,
        )

        results.append({
            # 기본 프로필
            "handle": handle,
            "full_name": full_name,
            "bio": bio[:200] if bio else None,
            "followers": followers,
            "following": following,
            "posts_count": posts_count,
            "is_verified": raw.get("verified", False),
            "is_business": is_business,
            "profile_url": f"https://www.instagram.com/{handle}/",
            "external_url": external_url or None,
            "follower_tier": follower_tier,
            # 분석 결과
            "treatment_tags": treatment_tags,
            "region_tags": region_tags,
            "contact_email": contact_info["email"],
            "contact_kakao": contact_info["kakao"],
            "contact_phone": contact_info["phone"],
            "contact_linktree": contact_info["linktree"],
            "has_contact_info": contact_info["has_contact"],
            "sponsorship_intent_signal": intent_signal,
            "sponsorship_intent_raw": intent_raw,
            "quality_flags": quality_flags,
            "is_business_account": is_biz,
            "triage_pass": triage_pass,
            "triage_reason": triage_reason,
        })

    logger.info(f"  → 분석 완료: {len(results)}명")
    return results


def step4_report(results: list[dict]):
    """결과 저장 + 통계 출력"""
    logger.info("=" * 60)
    logger.info("Step 4: 결과 저장 + 통계")
    logger.info("=" * 60)

    # JSON 저장
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"  → 저장 완료: {RESULTS_FILE}")

    total = len(results)
    if total == 0:
        logger.warning("  수집된 데이터가 없습니다.")
        return

    # ── 통계 ──
    print("\n" + "=" * 60)
    print(f"  📊 수집 결과 통계 ({total}명)")
    print("=" * 60)

    # 팔로워 티어 분포
    tier_counter = Counter(r["follower_tier"] for r in results if r["follower_tier"])
    print(f"\n  ▸ 팔로워 티어 분포:")
    for tier in ["nano", "micro", "mid", "macro"]:
        count = tier_counter.get(tier, 0)
        pct = count / total * 100
        bar = "█" * int(pct / 2)
        print(f"    {tier:6s}: {count:4d}명 ({pct:5.1f}%) {bar}")

    under_1k = sum(1 for r in results if (r["followers"] or 0) < 1000)
    print(f"    <1K   : {under_1k:4d}명 ({under_1k/total*100:5.1f}%)")

    # Triage 결과
    triage_pass = sum(1 for r in results if r["triage_pass"])
    triage_fail = total - triage_pass
    print(f"\n  ▸ Triage 결과:")
    print(f"    통과: {triage_pass}명 ({triage_pass/total*100:.1f}%)")
    print(f"    탈락: {triage_fail}명 ({triage_fail/total*100:.1f}%)")

    # Triage 탈락 사유
    fail_reasons = Counter(r["triage_reason"] for r in results if not r["triage_pass"])
    if fail_reasons:
        print(f"    탈락 사유:")
        for reason, count in fail_reasons.most_common():
            print(f"      {reason}: {count}명")

    # 업체 계정
    biz_count = sum(1 for r in results if r["is_business_account"])
    print(f"\n  ▸ 업체 계정: {biz_count}명 ({biz_count/total*100:.1f}%)")

    # 시술 태그 분포 (Triage 통과 계정만)
    passed = [r for r in results if r["triage_pass"]]
    all_tags: list[str] = []
    for r in passed:
        all_tags.extend(r["treatment_tags"])
    tag_counter = Counter(all_tags)
    print(f"\n  ▸ 시술 태그 TOP 10 (Triage 통과 {len(passed)}명):")
    for tag, count in tag_counter.most_common(10):
        print(f"    {tag}: {count}명")

    has_tags = sum(1 for r in passed if r["treatment_tags"])
    print(f"    시술 태그 보유율: {has_tags}/{len(passed)} ({has_tags/max(len(passed),1)*100:.1f}%)")

    # 지역 태그
    region_tags_all: list[str] = []
    for r in passed:
        region_tags_all.extend(r["region_tags"])
    region_counter = Counter(region_tags_all)
    if region_counter:
        print(f"\n  ▸ 지역 태그 TOP 5:")
        for region, count in region_counter.most_common(5):
            print(f"    {region}: {count}명")

    # 연락처
    has_contact = sum(1 for r in passed if r["has_contact_info"])
    print(f"\n  ▸ 연락처 보유 (Triage 통과): {has_contact}/{len(passed)} ({has_contact/max(len(passed),1)*100:.1f}%)")

    contact_types = {
        "email": sum(1 for r in passed if r["contact_email"]),
        "kakao": sum(1 for r in passed if r["contact_kakao"]),
        "phone": sum(1 for r in passed if r["contact_phone"]),
        "linktree": sum(1 for r in passed if r["contact_linktree"]),
    }
    for ctype, count in contact_types.items():
        if count > 0:
            print(f"    {ctype}: {count}명")

    # 협찬 의향 신호
    intent_counter = Counter(r["sponsorship_intent_signal"] for r in passed)
    has_intent = sum(v for k, v in intent_counter.items() if k != "none")
    print(f"\n  ▸ 협찬 의향 신호 (Triage 통과): {has_intent}/{len(passed)} ({has_intent/max(len(passed),1)*100:.1f}%)")
    for signal, count in intent_counter.most_common():
        if signal != "none":
            print(f"    {signal}: {count}명")

    # 품질 플래그
    flagged = sum(1 for r in results if r["quality_flags"])
    print(f"\n  ▸ 품질 플래그 있는 계정: {flagged}/{total} ({flagged/total*100:.1f}%)")

    # 샘플 출력 (Triage 통과 + 시술태그 있는 상위 5명)
    good_samples = [r for r in passed if r["treatment_tags"]]
    good_samples.sort(key=lambda x: x["followers"] or 0, reverse=True)
    print(f"\n  ▸ 우수 후보 샘플 (시술태그 보유, 팔로워 순):")
    for r in good_samples[:5]:
        tags_str = ", ".join(r["treatment_tags"][:3])
        intent = f" 💬{r['sponsorship_intent_signal']}" if r["sponsorship_intent_signal"] != "none" else ""
        contact = " 📧" if r["has_contact_info"] else ""
        print(f"    @{r['handle']:20s} | {r['followers']:>8,}명 | {r['follower_tier']:5s} | [{tags_str}]{intent}{contact}")

    print("\n" + "=" * 60)


async def main():
    logger.info("Instaloader 독립 테스트 시작")
    logger.info(f"목표: {TARGET_COUNT}명 수집")

    # Step 1: username 확보 (캐시 있으면 재사용)
    cache_file = Path(__file__).parent / "test_usernames_cache.json"
    if cache_file.exists():
        logger.info("캐시된 username 목록 발견 — Apify 호출 생략")
        with open(cache_file, "r", encoding="utf-8") as f:
            usernames = json.load(f)
        logger.info(f"  → 캐시에서 {len(usernames)}명 로드")
    else:
        usernames = await step1_get_usernames()

    if len(usernames) < 10:
        logger.error("username이 너무 적습니다. 종료합니다.")
        return

    # Step 2: Instaloader 프로필 수집
    raw_profiles = await step2_scrape_profiles(usernames)

    if not raw_profiles:
        logger.error("프로필 수집 실패. 종료합니다.")
        return

    # Step 3: 분석
    results = step3_analyze(raw_profiles)

    # Step 4: 결과 저장 + 통계
    step4_report(results)

    logger.info("테스트 완료!")


if __name__ == "__main__":
    asyncio.run(main())
