#!/usr/bin/env python3
"""
테스트 파이프라인: 10개 수집 → 7개 의미있는 인플루언서 검증

사전 조건:
  docker-compose up db  (PostgreSQL 컨테이너가 localhost:5433에 떠 있어야 함)

사용법:
  python pipeline/test_run.py                         # 기본 해시태그(피부과후기)
  python pipeline/test_run.py 강남피부과               # 해시태그 직접 지정
  python pipeline/test_run.py 피부과후기 --limit 10   # 수집 상한 지정 (기본 10)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── 환경변수 세팅 (다른 모듈 import 전에 먼저) ──────────────────────────
# 루트 .env에서 API 토큰 로드 (test_instaloader.py 동일 패턴)
from dotenv import load_dotenv
_root = Path(__file__).parent.parent
load_dotenv(_root / ".env", override=True)

# 파이프라인 전용 PostgreSQL (Docker Compose: host:5433 → container:5432)
os.environ["DATABASE_URL"] = "postgresql://feefluencer:feefluencer@localhost:5433/feefluencer"

# config.py 기본값(10/10/1/10)을 그대로 사용 — 별도 오버라이드 불필요

# ── 경로 설정 ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from logging_config import setup_logging
setup_logging()

import database as db
from jobs.discovery import run_discovery
from jobs.enrichment import run_enrichment

MEANINGFUL_THRESHOLD = 0.35  # 이 점수 이상을 "의미있는 인플루언서"로 판단


# ─────────────────────────────────────────────────────────────────────────
# 출력 헬퍼
# ─────────────────────────────────────────────────────────────────────────

def _tier_label(tier: str | None) -> str:
    return {"nano": "nano ", "micro": "micro", "mid": "mid  ", "macro": "macro"}.get(tier or "", "?    ")


async def _print_results(handles: list[str]) -> int:
    """수집된 계정 결과를 테이블로 출력하고 의미있는 계정 수를 반환한다."""
    if not handles:
        print("\n결과 없음: 수집된 계정이 없습니다.")
        return 0

    rows = await db.fetch_all(
        """
        SELECT
            handle, followers, follower_tier, status,
            COALESCE(match_score_skin_clinic, 0)        AS skin,
            COALESCE(match_score_plastic_surgery, 0)    AS plastic,
            COALESCE(match_score_obesity_clinic, 0)     AS obesity,
            has_contact_info,
            sponsorship_intent_signal,
            is_recently_active,
            treatment_tags,
            quality_flags
        FROM influencers
        WHERE platform = 'instagram' AND handle = ANY($1)
        ORDER BY GREATEST(
            COALESCE(match_score_skin_clinic, 0),
            COALESCE(match_score_plastic_surgery, 0),
            COALESCE(match_score_obesity_clinic, 0)
        ) DESC NULLS LAST
        """,
        handles,
    )

    active_rows = [r for r in rows if r["status"] == "active"]
    other_rows  = [r for r in rows if r["status"] != "active"]

    print()
    print("─" * 82)
    print(f"  {'핸들':<22} {'팔로워':>7}  {'티어'}  {'피부과':>6} {'성형':>6} {'비만':>6}  {'연락처'}  {'결과'}")
    print("─" * 82)

    meaningful = 0
    for r in active_rows:
        skin    = float(r["skin"])
        plastic = float(r["plastic"])
        obesity = float(r["obesity"])
        best    = max(skin, plastic, obesity)

        contact = "✓" if r["has_contact_info"] else "-"

        if best >= MEANINGFUL_THRESHOLD:
            meaningful += 1
            domain_scores = {"피부과": skin, "성형": plastic, "비만": obesity}
            best_domain = max(domain_scores, key=lambda k: domain_scores[k])
            mark = f"✓ {best_domain}"
        else:
            mark = "-"

        print(
            f"  @{r['handle']:<21} {r['followers']:>7,}  "
            f"{_tier_label(r['follower_tier'])}  "
            f"{skin:>6.2f} {plastic:>6.2f} {obesity:>6.2f}  "
            f"  {contact:<4}  {mark}"
        )

    if other_rows:
        print()
        print("  ── Triage 탈락 ─────────────────────────────────────────────────────────")
        for r in other_rows:
            status = r["status"]
            flags = list(r["quality_flags"] or [])
            note = f"flags={flags}" if flags else ""
            print(f"  @{r['handle']:<22} → {status}  {note}")

    print("─" * 82)
    return meaningful


# ─────────────────────────────────────────────────────────────────────────
# 사전 점검
# ─────────────────────────────────────────────────────────────────────────

async def preflight_check() -> bool:
    """DB 연결과 Apify 토큰을 확인한다."""
    # DB 연결
    try:
        await db.get_pool()
        await db.fetch_one("SELECT 1")
        print("  DB 연결: ✓")
    except Exception as e:
        print(f"  DB 연결: ✗  ({e})")
        print("  → docker-compose up db 를 먼저 실행하세요")
        return False

    # Apify 토큰
    token = os.environ.get("APIFY_API_TOKEN", "")
    if token and not token.startswith("apify_api_xxxx"):
        print(f"  Apify 토큰: ✓  ({token[:20]}...)")
    else:
        print("  Apify 토큰: ✗  (.env에 APIFY_API_TOKEN이 없습니다)")
        return False

    return True


# ─────────────────────────────────────────────────────────────────────────
# 해시태그 풀 준비
# ─────────────────────────────────────────────────────────────────────────

async def ensure_hashtag_ready(hashtag: str):
    """테스트 해시태그가 풀에 있고 즉시 수집 가능한 상태인지 확인한다."""
    row = await db.fetch_one("SELECT id FROM seed_hashtag_pool WHERE hashtag = $1", hashtag)
    if row:
        await db.execute(
            "UPDATE seed_hashtag_pool SET last_crawled_at = NULL, is_exhausted = FALSE WHERE hashtag = $1",
            hashtag,
        )
        print(f"  #{hashtag}: 쿨다운 초기화 ✓")
    else:
        await db.execute(
            "INSERT INTO seed_hashtag_pool (hashtag, domain, source) VALUES ($1, 'skin_clinic', 'manual') ON CONFLICT DO NOTHING",
            hashtag,
        )
        print(f"  #{hashtag}: 풀에 추가 ✓")


# ─────────────────────────────────────────────────────────────────────────
# 최근 수집된 핸들 조회
# ─────────────────────────────────────────────────────────────────────────

async def get_recent_handles() -> list[str]:
    rows = await db.fetch_all(
        """
        SELECT DISTINCT i.handle
        FROM influencers i
        JOIN influencer_discovery_events e ON e.influencer_id = i.id
        WHERE e.discovered_at > NOW() - INTERVAL '15 minutes'
        ORDER BY i.handle
        """
    )
    return [r["handle"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────

async def main(hashtag: str, limit: int):
    print(f"\n{'=' * 60}")
    print(f"  인플루언서 수집 테스트")
    print(f"  대상: #{hashtag}  |  수집 상한: {limit}개")
    print(f"  의미있는 기준: match_score ≥ {MEANINGFUL_THRESHOLD}")
    print(f"{'=' * 60}\n")

    # HASHTAG_RESULTS_LIMIT은 메인에서 설정 (argparse 후)
    os.environ["HASHTAG_RESULTS_LIMIT"] = str(limit)

    # ── 사전 점검 ──
    print("[사전 점검]")
    ok = await preflight_check()
    if not ok:
        await db.close_pool()
        sys.exit(1)

    # ── STEP 1: 해시태그 준비 ──
    print("\n[1/3] 해시태그 준비")
    await ensure_hashtag_ready(hashtag)

    # ── STEP 2: Discovery ──
    print(f"\n[2/3] Discovery  (#{hashtag}, 최대 {limit}개 계정)")
    result = await run_discovery(target_hashtags=[hashtag])

    if result.get("status") == "skipped":
        reason = result.get("reason", "unknown")
        print(f"  수집 실패 — 사유: {reason}")
        if reason == "target_not_found":
            print(f"  #{hashtag} 이 seed_hashtag_pool에 없습니다. INSERT 먼저 필요.")
        await db.close_pool()
        sys.exit(1)

    print(f"  신규 발굴: {result.get('new_accounts_found', 0)}개")
    print(f"  Triage 통과: {result.get('success_count', 0)}개  |  탈락/실패: {result.get('failed_count', 0)}개")

    # ── STEP 3: Enrichment ──
    print(f"\n[3/3] Enrichment  (게시물 수집 + match_score 계산)")
    enrich = await run_enrichment(batch_size=limit)
    print(f"  완료: {enrich.get('success_count', 0)}개  |  실패: {enrich.get('failed_count', 0)}개")

    # ── 결과 출력 ──
    handles = await get_recent_handles()
    if not handles:
        print("\n최근 15분 내 수집된 계정이 없습니다. DB를 직접 확인하세요.")
        await db.close_pool()
        return

    print(f"\n[결과]  수집 계정 {len(handles)}개")
    meaningful = await _print_results(handles)

    # ── 최종 요약 ──
    print()
    print(f"  수집된 계정       : {len(handles)}개")
    print(f"  의미있는 인플루언서: {meaningful}개  (match_score ≥ {MEANINGFUL_THRESHOLD})")
    print()

    if meaningful >= 7:
        print("  ✓ 목표 달성  (10명 중 7명 이상)")
    elif meaningful >= 5:
        print(f"  △ 목표 근접  ({meaningful}/7)  —  해시태그 변경 또는 Triage 기준 완화 검토")
    else:
        print(f"  ✗ 목표 미달  ({meaningful}/7)  —  해시태그 또는 수집 상한 조정 필요")

    await db.close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="인플루언서 수집 테스트")
    parser.add_argument("hashtag", nargs="?", default="피부과후기", help="해시태그 (# 없이, 기본: 피부과후기)")
    parser.add_argument("--limit", type=int, default=10, help="수집 상한 계정 수 (기본 10)")
    args = parser.parse_args()

    asyncio.run(main(args.hashtag, args.limit))
