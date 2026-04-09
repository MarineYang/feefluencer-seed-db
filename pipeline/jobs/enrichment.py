from __future__ import annotations
"""
Enrichment Job
큐에서 posts_refresh / deep_enrich 작업을 처리한다.
게시물 수집 → treatment_tags / region_tags / match_score 계산 → DB 갱신
"""
import json
import uuid
from datetime import datetime, timezone

from loguru import logger

import database as db
from apify_client import ApifyClient, parse_post
from config import settings
from keywords import (
    calculate_match_score,
    calculate_quality_flags,
    extract_clinic_brands,
    extract_region_tags,
    extract_treatment_tags,
    has_medical_risk,
    is_sponsored,
)


async def run_enrichment(batch_size: int | None = None) -> dict:
    """
    Enrichment 배치를 실행한다.
    큐에서 posts_refresh 작업을 꺼내 게시물 수집 및 분석을 수행한다.
    """
    batch_size = batch_size or settings.enrichment_batch_size
    client = ApifyClient()
    run_log_id = str(uuid.uuid4())

    await db.execute(
        "INSERT INTO seeding_run_logs (id, job_type, started_at) VALUES ($1, 'enrichment', NOW())",
        run_log_id,
    )

    total = success = failed = skipped = apify_calls = 0

    jobs = await _pick_queue_jobs(batch_size)
    logger.info(f"Enrichment 시작: {len(jobs)}개 작업")

    for job in jobs:
        job_id = str(job["id"])
        handle = job["handle"]
        influencer_id = str(job["influencer_id"]) if job["influencer_id"] else None

        # 실행 중 표시
        await db.execute(
            "UPDATE influencer_seed_queue SET status='running', started_at=NOW(), attempt_count=attempt_count+1 WHERE id=$1",
            job_id,
        )
        total += 1

        try:
            # 게시물 수집
            posts_raw = await client.scrape_posts(handle)
            apify_calls += 1

            if not posts_raw:
                await _mark_job(job_id, "done")
                skipped += 1
                continue

            # influencer_id 확인 (없으면 handle로 조회)
            if not influencer_id:
                row = await db.fetch_one(
                    "SELECT id FROM influencers WHERE platform='instagram' AND handle=$1",
                    handle,
                )
                if not row:
                    await _mark_job(job_id, "failed_deleted")
                    failed += 1
                    continue
                influencer_id = str(row["id"])

            # 게시물 분석
            analysis = _analyze_posts(posts_raw)

            # 게시물 upsert
            await _upsert_posts(posts_raw, influencer_id)

            # 활동성 체크: 90일 이상 미활동 → stale
            last_posted = analysis.get("last_posted_at")
            if last_posted:
                from datetime import datetime, timezone, timedelta
                try:
                    if isinstance(last_posted, str):
                        last_dt = datetime.fromisoformat(last_posted.replace("Z", "+00:00"))
                    else:
                        last_dt = last_posted
                    if datetime.now(timezone.utc) - last_dt > timedelta(days=90):
                        await db.execute(
                            "UPDATE influencers SET status='stale', updated_at=NOW() WHERE id=$1",
                            influencer_id,
                        )
                        await _mark_job(job_id, "done")
                        skipped += 1
                        logger.debug(f"활동 없음(90일) → stale: @{handle}")
                        continue
                except Exception:
                    pass

            # influencer 필드 갱신
            await _update_influencer_enrichment(influencer_id, analysis)

            # 협찬 시그널 저장
            await _save_sponsorship_signals(influencer_id, posts_raw)

            await _mark_job(job_id, "done")
            success += 1
            logger.info(f"Enrichment 완료: @{handle} (게시물 {len(posts_raw)}개)")

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Enrichment 실패 @{handle}: {error_msg}")

            status = "failed_api"
            if "private" in error_msg.lower():
                status = "failed_private"
            elif "not found" in error_msg.lower() or "deleted" in error_msg.lower():
                status = "failed_deleted"
                await db.execute(
                    "UPDATE influencers SET status='deleted' WHERE id=$1",
                    influencer_id,
                )

            await _mark_job(job_id, status, error_msg)
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
            skipped_count    = $5,
            apify_calls_made = $6,
            db_total_after   = $7
        WHERE id = $1
        """,
        run_log_id, total, success, failed, skipped, apify_calls, db_total_count,
    )

    logger.info(f"Enrichment 완료: 성공 {success}, 실패 {failed}, 스킵 {skipped}")
    return {"status": "done", "success": success, "failed": failed, "skipped": skipped}


# ----------------------------------------------------------------
# 내부 헬퍼
# ----------------------------------------------------------------

async def _pick_queue_jobs(limit: int) -> list[dict]:
    rows = await db.fetch_all(
        """
        SELECT id, influencer_id, platform, handle, job_type, attempt_count
        FROM influencer_seed_queue
        WHERE status = 'pending'
          AND job_type IN ('posts_refresh', 'deep_enrich')
          AND scheduled_at <= NOW()
          AND attempt_count < 3
        ORDER BY priority ASC, scheduled_at ASC
        LIMIT $1
        FOR UPDATE SKIP LOCKED
        """,
        limit,
    )
    return [dict(r) for r in rows]


def _analyze_posts(posts_raw: list[dict]) -> dict:
    """게시물 목록을 분석해 인플루언서 수준의 집계 지표를 반환한다."""
    total = len(posts_raw)
    if total == 0:
        return {}

    sponsored_count = 0
    treatment_count = 0
    has_risk = False
    all_treatment_tags: set[str] = set()
    all_region_tags: set[str] = set()
    last_posted_at = None

    for post in posts_raw:
        caption = post.get("caption") or post.get("text") or ""
        hashtags = post.get("hashtags") or []
        if isinstance(hashtags, list):
            full_text = caption + " " + " ".join(hashtags)
        else:
            full_text = caption

        # 협찬 감지
        if is_sponsored(full_text):
            sponsored_count += 1

        # 시술 키워드
        tags = extract_treatment_tags(full_text)
        if tags:
            treatment_count += 1
            all_treatment_tags.update(tags)

        # 지역
        regions = extract_region_tags(full_text)
        all_region_tags.update(regions)

        # 의료 리스크
        if not has_risk and has_medical_risk(full_text):
            has_risk = True

        # 최근 게시 날짜
        posted = post.get("timestamp") or post.get("postedAt")
        if posted and (last_posted_at is None or posted > last_posted_at):
            last_posted_at = posted

    return {
        "treatment_tags": list(all_treatment_tags),
        "region_tags": list(all_region_tags),
        "treatment_content_ratio": round(treatment_count / total, 3),
        "sponsorship_ratio": round(sponsored_count / total, 3),
        "has_medical_risk_flag": has_risk,
        "last_posted_at": last_posted_at,
    }


async def _update_influencer_enrichment(influencer_id: str, analysis: dict):
    """분석 결과를 influencers 테이블에 반영하고 match_score를 계산한다."""
    if not analysis:
        return

    # 현재 influencer 정보 조회
    inf = await db.fetch_one(
        """
        SELECT followers, following, engagement_rate, posts_count,
               avg_reel_plays, follower_tier, quality_flags, anomaly_flag
        FROM influencers WHERE id = $1
        """,
        influencer_id,
    )
    if not inf:
        return

    treatment_tags = analysis.get("treatment_tags", [])
    region_tags = analysis.get("region_tags", [])
    follower_tier = inf["follower_tier"]
    treatment_content_ratio = analysis.get("treatment_content_ratio", 0.0)
    sponsorship_ratio = analysis.get("sponsorship_ratio", 0.0)
    has_risk = analysis.get("has_medical_risk_flag", False)
    quality_flags = json.loads(inf["quality_flags"]) if isinstance(inf["quality_flags"], str) else (inf["quality_flags"] or [])
    anomaly_flag = inf["anomaly_flag"] or False

    # match_score 계산
    common_args = dict(
        treatment_tags=treatment_tags,
        region_tags=region_tags,
        follower_tier=follower_tier,
        treatment_content_ratio=treatment_content_ratio,
        sponsorship_ratio=sponsorship_ratio,
        has_risk=has_risk,
        quality_flags=quality_flags,
        anomaly_flag=anomaly_flag,
    )
    score_skin = calculate_match_score(domain="skin_clinic", **common_args)
    score_plastic = calculate_match_score(domain="plastic_surgery", **common_args)
    score_obesity = calculate_match_score(domain="obesity_clinic", **common_args)

    await db.execute(
        """
        UPDATE influencers SET
          treatment_tags              = $2::jsonb,
          region_tags                 = $3::jsonb,
          treatment_content_ratio     = $4,
          sponsorship_ratio           = $5,
          has_medical_risk_flag       = $6,
          last_posted_at              = $7,
          match_score_skin_clinic     = $8,
          match_score_plastic_surgery = $9,
          match_score_obesity_clinic  = $10,
          updated_at                  = NOW()
        WHERE id = $1
        """,
        influencer_id,
        json.dumps(treatment_tags, ensure_ascii=False),
        json.dumps(region_tags, ensure_ascii=False),
        treatment_content_ratio,
        sponsorship_ratio,
        has_risk,
        analysis.get("last_posted_at"),
        score_skin,
        score_plastic,
        score_obesity,
    )


async def _upsert_posts(posts_raw: list[dict], influencer_id: str):
    """게시물 목록을 일괄 upsert한다."""
    for raw in posts_raw:
        parsed = parse_post(raw, influencer_id)
        if not parsed.get("post_url"):
            continue

        caption = parsed.get("caption") or ""
        hashtags_list = json.loads(parsed["hashtags"]) if isinstance(parsed["hashtags"], str) else []
        full_text = caption + " " + " ".join(hashtags_list)

        treatment_mentions = extract_treatment_tags(full_text)
        sponsored = is_sponsored(full_text)

        await db.execute(
            """
            INSERT INTO influencer_posts (
              influencer_id, platform, external_post_id, post_url,
              post_type, caption, likes, comments, plays,
              hashtags, mentions, posted_at,
              is_sponsored, treatment_mentions
            ) VALUES (
              $1, $2, $3, $4,
              $5, $6, $7, $8, $9,
              $10::jsonb, $11::jsonb, $12,
              $13, $14::jsonb
            )
            ON CONFLICT (post_url) DO UPDATE SET
              likes              = EXCLUDED.likes,
              comments           = EXCLUDED.comments,
              plays              = EXCLUDED.plays,
              is_sponsored       = EXCLUDED.is_sponsored,
              treatment_mentions = EXCLUDED.treatment_mentions
            """,
            influencer_id,
            parsed["platform"],
            parsed.get("external_post_id") or None,
            parsed["post_url"],
            parsed.get("post_type"),
            parsed.get("caption"),
            parsed.get("likes", 0),
            parsed.get("comments", 0),
            parsed.get("plays", 0),
            parsed["hashtags"],
            parsed["mentions"],
            parsed.get("posted_at"),
            sponsored,
            json.dumps(treatment_mentions, ensure_ascii=False),
        )


async def _save_sponsorship_signals(influencer_id: str, posts_raw: list[dict]):
    """게시물에서 클리닉/브랜드 협찬 시그널을 감지해 저장한다."""
    for post in posts_raw:
        caption = post.get("caption") or ""
        post_url = post.get("url") or post.get("postUrl") or ""
        brands = extract_clinic_brands(caption)

        for brand in brands:
            await db.execute(
                """
                INSERT INTO influencer_sponsorship_signals
                  (influencer_id, detected_brand, signal_type, post_url)
                VALUES ($1, $2, 'caption_mention', $3)
                ON CONFLICT DO NOTHING
                """,
                influencer_id, brand, post_url,
            )


async def _mark_job(job_id: str, status: str, error: str | None = None):
    await db.execute(
        """
        UPDATE influencer_seed_queue
        SET status = $2, finished_at = NOW(), last_error = $3
        WHERE id = $1
        """,
        job_id, status, error,
    )
