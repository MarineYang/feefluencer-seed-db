"""
Instaloader + Webshare 프록시 기반 무료 리프레시 잡.
기존 refresh.py (Apify 기반)를 건드리지 않고 독립적으로 동작한다.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from loguru import logger

import database as db
from apify_client import parse_profile
from instaloader_proxy_client import ProxyInstaloaderClient
from keywords import calculate_follower_tier, calculate_quality_flags

PROXY_FILE = Path(__file__).resolve().parent.parent.parent / "Webshare 10 proxies.txt"

_TIER_INTERVALS = {
    "hot":  "1 day",
    "warm": "7 days",
    "cold": "30 days",
}


async def run_refresh_free(tier: str = "hot", limit: int = 50, force: bool = False) -> dict:
    """
    Instaloader + 프록시 풀 기반 무료 리프레시.

    tier: "hot" | "warm" | "cold"
    limit: 1회 배치 최대 처리 수 (프록시 10개 기준 50 이하 권장)
    force: True 이면 last_scraped_at 시간 조건 무시하고 강제 실행
    """
    client = ProxyInstaloaderClient(PROXY_FILE)
    run_log_id = str(uuid.uuid4())

    await db.execute(
        "INSERT INTO seeding_run_logs (id, job_type, started_at) VALUES ($1, 'profile_refresh_free', NOW())",
        run_log_id,
    )

    accounts = await _pick_accounts(tier, limit, force=force)
    logger.info(f"[RefreshFree] {tier} 티어 {len(accounts)}개 계정 리프레시 시작 (force={force})")

    handles = [a["handle"] for a in accounts]
    handle_to_id = {a["handle"]: str(a["id"]) for a in accounts}

    profiles = await client.scrape_profiles(handles)

    total = len(accounts)
    success = failed = 0

    for raw in profiles:
        handle = (raw.get("username") or "").lower()
        influencer_id = handle_to_id.get(handle)
        if not influencer_id:
            continue
        try:
            await _refresh_influencer(influencer_id, raw)
            success += 1
        except Exception as e:
            logger.warning(f"[RefreshFree] 갱신 실패 @{handle}: {e}")
            failed += 1

    db_total = await db.fetch_one("SELECT COUNT(*) FROM influencers WHERE status != 'deleted'")
    db_total_count = db_total[0] if db_total else 0

    await db.execute(
        """
        UPDATE seeding_run_logs
        SET finished_at      = NOW(),
            total_attempted  = $2,
            success_count    = $3,
            failed_count     = $4,
            apify_calls_made = 0,
            db_total_after   = $5,
            metadata         = $6::jsonb
        WHERE id = $1
        """,
        run_log_id, total, success, failed, db_total_count,
        json.dumps({"tier": tier, "method": "instaloader_proxy"}),
    )

    logger.info(f"[RefreshFree] 완료: 성공 {success}, 실패 {failed}")
    return {
        "status": "done",
        "tier": tier,
        "method": "instaloader_proxy",
        "total": total,
        "success": success,
        "failed": failed,
    }


async def _pick_accounts(tier: str, limit: int) -> list[dict]:
    interval = _TIER_INTERVALS.get(tier, "7 days")
    rows = await db.fetch_all(
        f"""
        SELECT id, handle, followers
        FROM influencers
        WHERE seed_priority = $1
          AND status NOT IN ('deleted', 'blocked')
          AND (last_scraped_at IS NULL OR last_scraped_at < NOW() - INTERVAL '{interval}')
        ORDER BY last_scraped_at NULLS FIRST
        LIMIT $2
        """,
        tier, limit,
    )
    return [dict(r) for r in rows]


async def _refresh_influencer(influencer_id: str, raw: dict):
    parsed = parse_profile(raw)
    new_followers = parsed.get("followers") or 0

    snap_7d = await db.fetch_one(
        """
        SELECT followers FROM influencer_metrics_snapshots
        WHERE influencer_id = $1 AND captured_at <= NOW() - INTERVAL '7 days'
        ORDER BY captured_at DESC LIMIT 1
        """,
        influencer_id,
    )
    snap_30d = await db.fetch_one(
        """
        SELECT followers FROM influencer_metrics_snapshots
        WHERE influencer_id = $1 AND captured_at <= NOW() - INTERVAL '30 days'
        ORDER BY captured_at DESC LIMIT 1
        """,
        influencer_id,
    )

    change_7d = (new_followers - snap_7d["followers"]) if snap_7d else None
    change_30d = (new_followers - snap_30d["followers"]) if snap_30d else None

    anomaly = False
    if snap_7d and snap_7d["followers"] > 0:
        if change_7d and change_7d / snap_7d["followers"] < -0.20:
            anomaly = True
    if snap_30d and snap_30d["followers"] > 0:
        if change_30d and change_30d / snap_30d["followers"] > 2.0:
            anomaly = True

    follower_tier = calculate_follower_tier(new_followers)
    quality_flags = calculate_quality_flags(
        followers=new_followers,
        following=parsed.get("following"),
        engagement_rate=parsed.get("engagement_rate"),
        posts_count=parsed.get("posts_count"),
        avg_reel_plays=parsed.get("avg_reel_plays"),
    )
    new_status = "low_quality" if len(quality_flags) >= 2 else None

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
