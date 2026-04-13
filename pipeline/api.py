from __future__ import annotations
"""
FastAPI 모니터링 API
대시보드에서 실시간 현황을 조회하는 엔드포인트를 제공한다.
"""
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone
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

    # 스케줄러는 자동으로 시작하지 않음 — 대시보드 재생 버튼으로 수동 시작
    logger.info("스케줄러 준비 완료 (대기 중) — /api/scheduler/start 로 시작하세요")

    yield

    if _scheduler.running:
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


# ============================================================
# 키워드 기반 인플루언서 검색 (피처링 방식)
# ============================================================
@app.get("/api/influencers/search")
async def search_influencers(
    keyword: str = Query(..., min_length=1, description="검색 키워드"),
    domain: str | None = Query(default=None, description="skin_clinic | plastic_surgery | obesity_clinic"),
    min_followers: int = Query(default=1_000, ge=0),
    max_followers: int = Query(default=0, ge=0, description="0=무제한"),
    tier: str | None = Query(default=None, description="nano | micro | mid | macro"),
    has_contact: bool = Query(default=False, description="연락처 있는 계정만"),
    intent: str | None = Query(default=None, description="explicit_dm | explicit_email | has_experience"),
    recently_active: bool = Query(default=False, description="최근 30일 활동 계정만"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """
    키워드로 인플루언서를 검색한다.
    - 게시물 캡션 / 해시태그 / 바이오 / 시술 태그에서 키워드 매칭
    - 매칭 게시물 수 기준으로 정렬 (피처링 방식)
    """
    keyword_pattern = f"%{keyword}%"

    # 도메인별 match_score 컬럼 선택
    score_col = {
        "skin_clinic": "i.match_score_skin_clinic",
        "plastic_surgery": "i.match_score_plastic_surgery",
        "obesity_clinic": "i.match_score_obesity_clinic",
    }.get(domain or "", "GREATEST(COALESCE(i.match_score_skin_clinic,0), COALESCE(i.match_score_plastic_surgery,0), COALESCE(i.match_score_obesity_clinic,0))")

    # 동적 WHERE 조건
    extra_conditions = []
    extra_args: list = [keyword_pattern, keyword_pattern, keyword_pattern, min_followers]
    arg_idx = 5  # $5부터 시작

    if max_followers > 0:
        extra_conditions.append(f"AND i.followers <= ${arg_idx}")
        extra_args.append(max_followers)
        arg_idx += 1

    if tier:
        extra_conditions.append(f"AND i.follower_tier = ${arg_idx}")
        extra_args.append(tier)
        arg_idx += 1

    if has_contact:
        extra_conditions.append("AND i.has_contact_info = TRUE")

    if intent:
        extra_conditions.append(f"AND i.sponsorship_intent_signal = ${arg_idx}")
        extra_args.append(intent)
        arg_idx += 1

    if recently_active:
        extra_conditions.append("AND i.is_recently_active = TRUE")

    extra_where = " ".join(extra_conditions)

    rows = await db.fetch_all(
        f"""
        WITH matching_posts AS (
          SELECT
            influencer_id,
            COUNT(*) AS matched_post_count,
            ARRAY_REMOVE(
              ARRAY_AGG(
                CASE WHEN caption IS NOT NULL AND caption != ''
                     THEN LEFT(caption, 150) END
                ORDER BY posted_at DESC NULLS LAST
              ),
              NULL
            ) AS sample_captions
          FROM influencer_posts
          WHERE caption ILIKE $1
             OR hashtags::text ILIKE $2
          GROUP BY influencer_id
        )
        SELECT
          i.id, i.handle, i.full_name, i.bio,
          i.followers, i.following, i.posts_count,
          i.follower_tier, i.engagement_rate, i.avg_likes, i.avg_comments,
          i.profile_url, i.profile_pic_url,
          i.treatment_tags, i.region_tags,
          i.match_score_skin_clinic, i.match_score_plastic_surgery, i.match_score_obesity_clinic,
          i.has_contact_info, i.contact_email, i.contact_kakao, i.contact_linktree,
          i.sponsorship_intent_signal, i.is_recently_active, i.last_posted_at,
          i.recent_engagement_rate, i.content_consistency_score,
          COALESCE(mp.matched_post_count, 0) AS matched_post_count,
          mp.sample_captions,
          CASE
            WHEN i.bio ILIKE $3 THEN 'bio'
            WHEN i.treatment_tags::text ILIKE $3 THEN 'treatment_tag'
            ELSE 'post_content'
          END AS match_source,
          {score_col} AS domain_score
        FROM influencers i
        LEFT JOIN matching_posts mp ON mp.influencer_id = i.id
        WHERE i.status = 'active'
          AND i.followers >= $4
          AND (
            mp.influencer_id IS NOT NULL
            OR i.bio ILIKE $3
            OR i.treatment_tags::text ILIKE $3
          )
          {extra_where}
        ORDER BY
          matched_post_count DESC,
          domain_score DESC NULLS LAST,
          i.followers DESC
        LIMIT {limit} OFFSET {offset}
        """,
        *extra_args,
    )

    now = datetime.now(timezone.utc)

    def _format_last_upload(last_posted_at) -> str | None:
        if not last_posted_at:
            return None
        try:
            dt = last_posted_at if hasattr(last_posted_at, "tzinfo") else None
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days = (now - dt).days
            if days == 0:
                return "오늘"
            if days < 7:
                return f"{days}일 전"
            if days < 30:
                return f"{days // 7}주 전"
            return f"{days // 30}개월 전"
        except Exception:
            return None

    items = []
    for r in rows:
        items.append({
            "handle": r["handle"],
            "full_name": r["full_name"],
            "bio": r["bio"],
            "profile_url": r["profile_url"],
            "profile_pic_url": r["profile_pic_url"],
            "followers": r["followers"],
            "following": r["following"],
            "posts_count": r["posts_count"],
            "follower_tier": r["follower_tier"],
            "engagement_rate": float(r["engagement_rate"]) if r["engagement_rate"] else None,
            "recent_engagement_rate": float(r["recent_engagement_rate"]) if r["recent_engagement_rate"] else None,
            "avg_likes": float(r["avg_likes"]) if r["avg_likes"] else None,
            "avg_comments": float(r["avg_comments"]) if r["avg_comments"] else None,
            "treatment_tags": r["treatment_tags"] or [],
            "region_tags": r["region_tags"] or [],
            "match_score_skin_clinic": float(r["match_score_skin_clinic"]) if r["match_score_skin_clinic"] else None,
            "match_score_plastic_surgery": float(r["match_score_plastic_surgery"]) if r["match_score_plastic_surgery"] else None,
            "match_score_obesity_clinic": float(r["match_score_obesity_clinic"]) if r["match_score_obesity_clinic"] else None,
            "content_consistency_score": float(r["content_consistency_score"]) if r["content_consistency_score"] else None,
            "has_contact_info": r["has_contact_info"],
            "contact_email": r["contact_email"],
            "contact_kakao": r["contact_kakao"],
            "contact_linktree": r["contact_linktree"],
            "sponsorship_intent_signal": r["sponsorship_intent_signal"],
            "is_recently_active": r["is_recently_active"],
            "last_posted_at": r["last_posted_at"].isoformat() if r["last_posted_at"] else None,
            "last_upload": _format_last_upload(r["last_posted_at"]),
            "matched_post_count": r["matched_post_count"],
            "sample_captions": (r["sample_captions"] or [])[:3],
            "match_source": r["match_source"],
        })

    return {
        "keyword": keyword,
        "source": "seeddb",
        "count": len(items),
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@app.get("/api/influencers/{handle}")
async def get_influencer(handle: str):
    """핸들로 인플루언서 상세 정보를 조회한다."""
    handle = handle.lstrip("@")
    row = await db.fetch_one(
        """
        SELECT id, handle, full_name, bio, profile_url, profile_pic_url, external_url,
               followers, following, posts_count, engagement_rate, avg_likes, avg_comments,
               avg_reel_plays, follower_tier, treatment_tags, region_tags,
               match_score_skin_clinic, match_score_plastic_surgery, match_score_obesity_clinic,
               match_score_breakdown, has_contact_info, contact_email, contact_kakao,
               contact_phone, contact_linktree, sponsorship_intent_signal,
               is_recently_active, posts_last_30d, recent_engagement_rate,
               content_consistency_score, status, last_posted_at, last_scraped_at
        FROM influencers
        WHERE platform = 'instagram' AND handle = $1
        """,
        handle,
    )
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"@{handle} not found")

    r = dict(row)
    r["last_posted_at"] = r["last_posted_at"].isoformat() if r["last_posted_at"] else None
    r["last_scraped_at"] = r["last_scraped_at"].isoformat() if r["last_scraped_at"] else None
    return r


# ============================================================
# 스케줄러 수동 제어
# ============================================================
@app.get("/api/scheduler/status")
async def scheduler_status():
    """스케줄러 실행 상태를 반환한다."""
    running = _scheduler is not None and _scheduler.running
    jobs = []
    if running:
        for job in _scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append({
                "id": job.id,
                "next_run": next_run.isoformat() if next_run else None,
            })
    return {"running": running, "jobs": jobs}


@app.post("/api/scheduler/start")
async def scheduler_start():
    """스케줄러를 시작한다."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return {"ok": True, "message": "이미 실행 중입니다"}
    _scheduler.start()
    logger.info("스케줄러 수동 시작 — Discovery 5분 / Enrichment 2분 / Refresh 매일")
    return {"ok": True, "message": "스케줄러 시작됨"}


@app.post("/api/scheduler/stop")
async def scheduler_stop():
    """스케줄러를 정지한다."""
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        return {"ok": True, "message": "이미 정지 상태입니다"}
    _scheduler.shutdown(wait=False)
    logger.info("스케줄러 수동 정지")
    return {"ok": True, "message": "스케줄러 정지됨"}


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
