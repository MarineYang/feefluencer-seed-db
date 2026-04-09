from __future__ import annotations
"""
Refresh Job
Hot/Warm/Cold 티어 기준으로 기존 계정의 프로필을 주기적으로 갱신한다.
팔로워 이상 감지 (anomaly_flag) 및 스냅샷 저장 포함.
"""
import json
import uuid
from datetime import datetime, timezone

from loguru import logger

import database as db
from apify_client import ApifyClient, parse_profile
from config import settings
from keywords import calculate_follower_tier, calculate_quality_flags


async def run_refresh(tier: str = "hot") -> dict:
    """
    프로필 Refresh 배치를 실행한다.
    tier: "hot" | "warm" | "cold"
    """
    client = ApifyClient()
    run_log_id = str(uuid.uuid4())

    await db.execute(
        "INSERT INTO seeding_run_logs (id, job_type, started_at) VALUES ($1, 'profile_refresh', NOW())",
        run_log_id,
    )

    total = success = failed = 0
    apify_calls = 0

    accounts = await _pick_accounts(tier)
    logger.info(f"Refresh({tier}) 시작: {len(accounts)}개 계정")

    # 배치 단위로 처리
    for i in range(0, len(accounts), settings.profile_batch_size):
        batch = accounts[i:i + settings.profile_batch_size]
        handles = [a["handle"] for a in batch]
        handle_to_id = {a["handle"]: str(a["id"]) for a in batch}

        try:
            profiles = await client.scrape_profiles(handles)
            apify_calls += 1

            for raw in profiles:
                handle = (raw.get("username") or "").lower()
                influencer_id = handle_to_id.get(handle)
                if not influencer_id:
                    continue

                try:
                    await _refresh_influencer(influencer_id, raw)
                    success += 1
                except Exception as e:
                    logger.warning(f"Refresh 실패 @{handle}: {e}")
                    failed += 1

            total += len(batch)

        except Exception as e:
            logger.error(f"Refresh 배치 오류: {e}")
            failed += len(batch)
            total += len(batch)

    db_total = await db.fetch_one("SELECT COUNT(*) FROM influencers WHERE status != 'deleted'")
    db_total_count = db_total[0] if db_total else 0

    await db.execute(
        """
        UPDATE seeding_run_logs
        SET finished_at      = NOW(),
            total_attempted  = $2,
            success_count    = $3,
            failed_count     = $4,
            apify_calls_made = $5,
            db_total_after   = $6,
            metadata         = $7::jsonb
        WHERE id = $1
        """,
        run_log_id, total, success, failed, apify_calls, db_total_count,
        json.dumps({"tier": tier}),
    )

    logger.info(f"Refresh({tier}) 완료: 성공 {success}, 실패 {failed}")
    return {"status": "done", "tier": tier, "success": success, "failed": failed}


# ----------------------------------------------------------------
# 내부 헬퍼
# ----------------------------------------------------------------

_TIER_INTERVALS = {
    "hot":  "1 day",
    "warm": "7 days",
    "cold": "30 days",
}


async def _pick_accounts(tier: str) -> list[dict]:
    """갱신 대상 계정을 선택한다."""
    interval = _TIER_INTERVALS.get(tier, "7 days")
    rows = await db.fetch_all(
        f"""
        SELECT id, handle, followers
        FROM influencers
        WHERE seed_priority = $1
          AND status NOT IN ('deleted', 'blocked')
          AND (last_scraped_at IS NULL OR last_scraped_at < NOW() - INTERVAL '{interval}')
        ORDER BY last_scraped_at NULLS FIRST
        LIMIT 200
        """,
        tier,
    )
    return [dict(r) for r in rows]


async def _refresh_influencer(influencer_id: str, raw: dict):
    """프로필 갱신, 스냅샷 저장, anomaly 감지를 수행한다."""
    parsed = parse_profile(raw)
    new_followers = parsed.get("followers") or 0

    # 기존 정보 조회
    old = await db.fetch_one(
        "SELECT followers, follower_change_7d, follower_change_30d FROM influencers WHERE id=$1",
        influencer_id,
    )

    # 팔로워 변화 계산 (스냅샷에서 7일/30일 전 값 조회)
    snap_7d = await db.fetch_one(
        """
        SELECT followers FROM influencer_metrics_snapshots
        WHERE influencer_id = $1
          AND captured_at <= NOW() - INTERVAL '7 days'
        ORDER BY captured_at DESC LIMIT 1
        """,
        influencer_id,
    )
    snap_30d = await db.fetch_one(
        """
        SELECT followers FROM influencer_metrics_snapshots
        WHERE influencer_id = $1
          AND captured_at <= NOW() - INTERVAL '30 days'
        ORDER BY captured_at DESC LIMIT 1
        """,
        influencer_id,
    )

    change_7d = (new_followers - snap_7d["followers"]) if snap_7d else None
    change_30d = (new_followers - snap_30d["followers"]) if snap_30d else None

    # anomaly 감지
    anomaly = False
    if snap_7d and snap_7d["followers"] > 0:
        if change_7d and change_7d / snap_7d["followers"] < -0.20:
            anomaly = True  # 7일 내 -20% 이상 감소
    if snap_30d and snap_30d["followers"] > 0:
        if change_30d and change_30d / snap_30d["followers"] > 2.0:
            anomaly = True  # 30일 내 +200% 급증

    follower_tier = calculate_follower_tier(new_followers)
    quality_flags = calculate_quality_flags(
        followers=new_followers,
        following=parsed.get("following"),
        engagement_rate=parsed.get("engagement_rate"),
        posts_count=parsed.get("posts_count"),
        avg_reel_plays=parsed.get("avg_reel_plays"),
    )
    new_status = "low_quality" if len(quality_flags) >= 2 else None

    # 계정 갱신
    await db.execute(
        """
        UPDATE influencers SET
          followers           = $2,
          following           = $3,
          posts_count         = $4,
          avg_likes           = $5,
          avg_comments        = $6,
          avg_reel_plays      = $7,
          engagement_rate     = $8,
          follower_tier       = $9,
          quality_flags       = $10::jsonb,
          anomaly_flag        = $11,
          follower_change_7d  = $12,
          follower_change_30d = $13,
          status              = CASE
                                  WHEN status = 'deleted' THEN status
                                  WHEN $14 IS NOT NULL THEN $14
                                  ELSE status
                                END,
          last_scraped_at     = NOW(),
          updated_at          = NOW()
        WHERE id = $1
        """,
        influencer_id,
        new_followers,
        parsed.get("following"),
        parsed.get("posts_count"),
        parsed.get("avg_likes"),
        parsed.get("avg_comments"),
        parsed.get("avg_reel_plays"),
        parsed.get("engagement_rate"),
        follower_tier,
        json.dumps(quality_flags, ensure_ascii=False),
        anomaly,
        change_7d,
        change_30d,
        new_status,
    )

    # 스냅샷 저장
    await db.execute(
        """
        INSERT INTO influencer_metrics_snapshots
          (influencer_id, followers, following, posts_count,
           avg_likes, avg_comments, avg_reel_plays, engagement_rate)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        influencer_id,
        new_followers,
        parsed.get("following"),
        parsed.get("posts_count"),
        parsed.get("avg_likes"),
        parsed.get("avg_comments"),
        parsed.get("avg_reel_plays"),
        parsed.get("engagement_rate"),
    )
