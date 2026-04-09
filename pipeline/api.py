from __future__ import annotations
"""
FastAPI 모니터링 API
대시보드에서 실시간 현황을 조회하는 엔드포인트를 제공한다.
"""
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

import database as db
from config import settings
from jobs.discovery import run_discovery
from jobs.enrichment import run_enrichment
from jobs.refresh import run_refresh

_scheduler: AsyncIOScheduler | None = None


async def _job_discovery():
    logger.info("=== [스케줄] Discovery 시작 ===")
    try:
        result = await run_discovery()
        logger.info(f"=== [스케줄] Discovery 완료: {result} ===")
    except Exception as e:
        logger.error(f"=== [스케줄] Discovery 오류: {e} ===")


async def _job_enrichment():
    logger.info("=== [스케줄] Enrichment 시작 ===")
    try:
        result = await run_enrichment()
        logger.info(f"=== [스케줄] Enrichment 완료: {result} ===")
    except Exception as e:
        logger.error(f"=== [스케줄] Enrichment 오류: {e} ===")


async def _job_refresh_hot():
    try:
        await run_refresh("hot")
    except Exception as e:
        logger.error(f"=== [스케줄] Refresh(hot) 오류: {e} ===")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    await db.get_pool()

    _scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # Discovery: 5분마다 (해시태그 → 프로필 → Triage)
    _scheduler.add_job(
        _job_discovery,
        CronTrigger.from_crontab("*/5 * * * *", timezone="Asia/Seoul"),
        id="discovery",
        max_instances=1,
        misfire_grace_time=60,
    )
    # Enrichment: 2분마다 (큐 우선순위 1번 계정 즉시 처리)
    _scheduler.add_job(
        _job_enrichment,
        CronTrigger.from_crontab("*/2 * * * *", timezone="Asia/Seoul"),
        id="enrichment",
        max_instances=1,
        misfire_grace_time=60,
    )
    # Refresh(hot): 매일 오전 8시
    _scheduler.add_job(
        _job_refresh_hot,
        CronTrigger.from_crontab(settings.refresh_cron, timezone="Asia/Seoul"),
        id="refresh_hot",
        max_instances=1,
        misfire_grace_time=1800,
    )

    _scheduler.start()
    logger.info("스케줄러 시작: Discovery 5분 / Enrichment 10분 / Refresh(hot) 매일")

    yield

    _scheduler.shutdown()
    await db.close_pool()


app = FastAPI(title="Feefluencer Seeding Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 전체 통계
# ============================================================
@app.get("/api/stats")
async def get_stats():
    """대시보드 상단 핵심 지표를 반환한다."""
    total = await db.fetch_one("SELECT COUNT(*) FROM influencers WHERE status != 'deleted'")
    new_today = await db.fetch_one(
        "SELECT COUNT(*) FROM influencers WHERE created_at >= CURRENT_DATE"
    )
    queue_pending = await db.fetch_one(
        "SELECT COUNT(*) FROM influencer_seed_queue WHERE status = 'pending'"
    )
    queue_running = await db.fetch_one(
        "SELECT COUNT(*) FROM influencer_seed_queue WHERE status = 'running'"
    )
    last_run = await db.fetch_one(
        "SELECT started_at, job_type FROM seeding_run_logs ORDER BY started_at DESC LIMIT 1"
    )
    anomaly_count = await db.fetch_one(
        "SELECT COUNT(*) FROM influencers WHERE anomaly_flag = TRUE AND status != 'deleted'"
    )

    return {
        "total_influencers": total[0],
        "new_today": new_today[0],
        "queue_pending": queue_pending[0],
        "queue_running": queue_running[0],
        "anomaly_count": anomaly_count[0],
        "last_run": {
            "started_at": last_run["started_at"].isoformat() if last_run else None,
            "job_type": last_run["job_type"] if last_run else None,
        },
    }


# ============================================================
# 티어 & 도메인 분포
# ============================================================
@app.get("/api/stats/distribution")
async def get_distribution():
    """팔로워 티어 및 도메인별 분포를 반환한다."""
    tiers = await db.fetch_all(
        """
        SELECT follower_tier, COUNT(*) as count
        FROM influencers
        WHERE status NOT IN ('deleted', 'low_quality', 'business')
          AND follower_tier IS NOT NULL
        GROUP BY follower_tier
        ORDER BY count DESC
        """
    )

    status_dist = await db.fetch_all(
        """
        SELECT status, COUNT(*) as count
        FROM influencers
        GROUP BY status
        ORDER BY count DESC
        """
    )

    domain_scores = await db.fetch_one(
        """
        SELECT
          COUNT(*) FILTER (WHERE match_score_skin_clinic >= 0.3)      AS skin_clinic,
          COUNT(*) FILTER (WHERE match_score_plastic_surgery >= 0.3)  AS plastic_surgery,
          COUNT(*) FILTER (WHERE match_score_obesity_clinic >= 0.3)   AS obesity_clinic
        FROM influencers
        WHERE status NOT IN ('deleted', 'business')
        """
    )

    return {
        "tiers": [{"tier": r["follower_tier"], "count": r["count"]} for r in tiers],
        "status": [{"status": r["status"], "count": r["count"]} for r in status_dist],
        "domain_scores": dict(domain_scores) if domain_scores else {},
    }


# ============================================================
# 성장 차트 (일별 누적)
# ============================================================
@app.get("/api/stats/growth")
async def get_growth(days: int = Query(default=30, le=90)):
    """최근 N일간 일별 신규 인플루언서 수 및 누적 수를 반환한다."""
    rows = await db.fetch_all(
        """
        SELECT
          DATE(created_at) AS date,
          COUNT(*)         AS new_count
        FROM influencers
        WHERE created_at >= NOW() - ($1 || ' days')::INTERVAL
        GROUP BY DATE(created_at)
        ORDER BY date
        """,
        str(days),
    )

    # 누적 합산
    cumulative = 0
    base = await db.fetch_one(
        "SELECT COUNT(*) FROM influencers WHERE created_at < NOW() - ($1 || ' days')::INTERVAL",
        str(days),
    )
    cumulative = base[0] if base else 0

    result = []
    for r in rows:
        cumulative += r["new_count"]
        result.append({
            "date": r["date"].isoformat(),
            "new_count": r["new_count"],
            "cumulative": cumulative,
        })
    return result


# ============================================================
# 배치 실행 로그
# ============================================================
@app.get("/api/runs")
async def get_runs(limit: int = Query(default=20, le=100)):
    """최근 배치 실행 이력을 반환한다."""
    rows = await db.fetch_all(
        """
        SELECT id, job_type, started_at, finished_at,
               total_attempted, success_count, failed_count,
               new_accounts_found, apify_calls_made, db_total_after
        FROM seeding_run_logs
        ORDER BY started_at DESC
        LIMIT $1
        """,
        limit,
    )
    result = []
    for r in rows:
        duration = None
        if r["finished_at"] and r["started_at"]:
            duration = int((r["finished_at"] - r["started_at"]).total_seconds())
        result.append({
            **dict(r),
            "started_at": r["started_at"].isoformat(),
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            "duration_seconds": duration,
        })
    return result


# ============================================================
# 큐 현황
# ============================================================
@app.get("/api/queue")
async def get_queue():
    """큐 상태별 집계를 반환한다."""
    rows = await db.fetch_all(
        """
        SELECT status, job_type, COUNT(*) as count
        FROM influencer_seed_queue
        GROUP BY status, job_type
        ORDER BY status, count DESC
        """
    )
    return [dict(r) for r in rows]


# ============================================================
# 해시태그 풀
# ============================================================
@app.get("/api/hashtags")
async def get_hashtag_pool():
    """해시태그 풀 현황을 반환한다."""
    rows = await db.fetch_all(
        """
        SELECT hashtag, domain, last_crawled_at, crawl_count,
               new_accounts_found_last, total_accounts_found, is_exhausted, source
        FROM seed_hashtag_pool
        ORDER BY is_exhausted ASC, total_accounts_found DESC
        """
    )
    result = []
    for r in rows:
        result.append({
            **dict(r),
            "last_crawled_at": r["last_crawled_at"].isoformat() if r["last_crawled_at"] else None,
        })
    return result


# ============================================================
# 인플루언서 목록
# ============================================================
@app.get("/api/influencers")
async def get_influencers(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, le=200),
    tier: str | None = None,
    domain: str | None = None,
    status: str = "active",
):
    """인플루언서 목록을 반환한다. domain: skin_clinic | plastic_surgery | obesity_clinic"""
    offset = (page - 1) * size

    score_col = {
        "skin_clinic": "match_score_skin_clinic",
        "plastic_surgery": "match_score_plastic_surgery",
        "obesity_clinic": "match_score_obesity_clinic",
    }.get(domain, "match_score_skin_clinic")

    tier_filter = "AND follower_tier = $3" if tier else ""
    tier_args = [tier] if tier else []

    rows = await db.fetch_all(
        f"""
        SELECT id, handle, full_name, followers, follower_tier,
               engagement_rate, match_score_skin_clinic,
               match_score_plastic_surgery, match_score_obesity_clinic,
               treatment_tags, region_tags, status, last_scraped_at,
               anomaly_flag, quality_flags
        FROM influencers
        WHERE status = $1
          {tier_filter}
        ORDER BY {score_col} DESC NULLS LAST
        LIMIT $2 OFFSET {offset}
        """,
        status,
        size,
        *tier_args,
    )

    total = await db.fetch_one(
        f"SELECT COUNT(*) FROM influencers WHERE status = $1 {tier_filter}",
        status, *tier_args,
    )

    return {
        "total": total[0],
        "page": page,
        "size": size,
        "items": [
            {
                **dict(r),
                "last_scraped_at": r["last_scraped_at"].isoformat() if r["last_scraped_at"] else None,
            }
            for r in rows
        ],
    }


# ============================================================
# 수동 배치 트리거 (관리용)
# ============================================================
@app.post("/api/jobs/discovery")
async def trigger_discovery():
    result = await run_discovery()
    return result


@app.post("/api/jobs/enrichment")
async def trigger_enrichment():
    result = await run_enrichment()
    return result


@app.post("/api/jobs/refresh/{tier}")
async def trigger_refresh(tier: str):
    if tier not in ("hot", "warm", "cold"):
        return {"error": "tier must be hot | warm | cold"}
    result = await run_refresh(tier)
    return result


@app.get("/api/stats/goal")
async def get_goal():
    """
    1차 목표(5,000명) 진행률을 반환한다.
    '의미있는 인플루언서' 기준:
      - status = 'active'
      - match_score_skin_clinic OR plastic_surgery OR obesity_clinic > 0
      - last_posted_at > 90일 이내
    """
    GOAL = 5_000

    meaningful = await db.fetch_one(
        """
        SELECT COUNT(*) FROM influencers
        WHERE status = 'active'
          AND last_posted_at >= NOW() - INTERVAL '90 days'
          AND (
            match_score_skin_clinic     > 0 OR
            match_score_plastic_surgery > 0 OR
            match_score_obesity_clinic  > 0
          )
        """
    )
    enriched = await db.fetch_one(
        "SELECT COUNT(*) FROM influencers WHERE status = 'active' AND last_posted_at IS NOT NULL"
    )
    pending_enrichment = await db.fetch_one(
        "SELECT COUNT(*) FROM influencer_seed_queue WHERE status = 'pending' AND job_type = 'posts_refresh'"
    )
    total_collected = await db.fetch_one(
        "SELECT COUNT(*) FROM influencers"
    )
    discarded = await db.fetch_one(
        "SELECT COUNT(*) FROM influencers WHERE status IN ('low_quality', 'business', 'stale')"
    )

    meaningful_count = meaningful[0]
    progress_pct = round(meaningful_count / GOAL * 100, 1)

    # 도메인별 breakdown
    domain_breakdown = await db.fetch_one(
        """
        SELECT
          COUNT(*) FILTER (WHERE match_score_skin_clinic > 0.3
                             AND status = 'active'
                             AND last_posted_at >= NOW() - INTERVAL '90 days') AS skin_clinic,
          COUNT(*) FILTER (WHERE match_score_plastic_surgery > 0.3
                             AND status = 'active'
                             AND last_posted_at >= NOW() - INTERVAL '90 days') AS plastic_surgery,
          COUNT(*) FILTER (WHERE match_score_obesity_clinic > 0.3
                             AND status = 'active'
                             AND last_posted_at >= NOW() - INTERVAL '90 days') AS obesity_clinic
        FROM influencers
        """
    )

    return {
        "goal": GOAL,
        "meaningful": meaningful_count,
        "progress_pct": progress_pct,
        "enriched": enriched[0],
        "pending_enrichment": pending_enrichment[0],
        "total_collected": total_collected[0],
        "discarded": discarded[0],
        "domain_breakdown": dict(domain_breakdown) if domain_breakdown else {},
    }


@app.post("/api/admin/reclassify-business")
async def reclassify_business():
    """기존 적재된 계정 중 업체 계정을 재분류한다."""
    from keywords import is_business_account
    rows = await db.fetch_all(
        "SELECT id, handle, full_name, bio, is_business FROM influencers WHERE status = 'active'"
    )
    updated = 0
    for r in rows:
        if is_business_account(
            bio=r["bio"] or "",
            handle=r["handle"] or "",
            full_name=r["full_name"] or "",
            is_business=r["is_business"] or False,
        ):
            await db.execute(
                "UPDATE influencers SET status = 'business', updated_at = NOW() WHERE id = $1",
                r["id"],
            )
            updated += 1
    return {"reclassified": updated}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
