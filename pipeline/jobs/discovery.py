"""
Discovery Job
해시태그 기반으로 신규 인플루언서 계정을 발굴하고 DB에 적재한다.

흐름:
  seed_hashtag_pool → Hashtag Scraper → 신규 handle 필터 → Profile Scraper → upsert
"""
import json
import uuid
from datetime import datetime, timezone

from loguru import logger

import database as db
from apify_client import ApifyClient, parse_profile
from config import settings
from keywords import (
    calculate_follower_tier,
    calculate_quality_flags,
    extract_region_tags,
    extract_treatment_tags,
)


async def run_discovery(hashtag_batch_size: int | None = None) -> dict:
    """
    Discovery 배치를 실행한다.
    반환: 실행 결과 요약 dict
    """
    batch_size = hashtag_batch_size or settings.discovery_hashtag_batch
    client = ApifyClient()
    run_log_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    # 실행 로그 시작
    await db.execute(
        """
        INSERT INTO seeding_run_logs (id, job_type, started_at)
        VALUES ($1, 'discovery', $2)
        """,
        run_log_id, started_at,
    )

    total_attempted = 0
    success_count = 0
    failed_count = 0
    new_accounts_found = 0
    apify_calls = 0

    try:
        # 1. 수집할 해시태그 선택
        hashtags = await _pick_hashtags(batch_size)
        if not hashtags:
            logger.info("Discovery: 수집할 해시태그 없음 (모두 최신 상태)")
            return {"status": "skipped", "reason": "no_hashtags"}

        logger.info(f"Discovery 시작: {len(hashtags)}개 해시태그")

        for ht in hashtags:
            try:
                # 2. Hashtag Scraper 호출
                usernames = await client.scrape_hashtag(ht["hashtag"])
                apify_calls += 1
                total_attempted += len(usernames)

                # 3. DB에 이미 있는 handle 제거
                new_usernames = await _filter_existing(usernames)
                logger.info(
                    f"#{ht['hashtag']}: {len(usernames)}개 수집 → "
                    f"{len(new_usernames)}개 신규"
                )

                if not new_usernames:
                    await _update_hashtag_pool(ht["id"], 0, len(usernames))
                    continue

                # 4. Profile Scraper (신규 handle만, 배치 단위로)
                for i in range(0, len(new_usernames), settings.profile_batch_size):
                    batch = new_usernames[i:i + settings.profile_batch_size]
                    profiles = await client.scrape_profiles(batch)
                    apify_calls += 1

                    for raw in profiles:
                        try:
                            saved = await _upsert_influencer(raw, source_value=ht["hashtag"])
                            if saved:
                                new_accounts_found += 1
                                success_count += 1
                        except Exception as e:
                            logger.warning(f"Influencer upsert 실패: {e}")
                            failed_count += 1

                # 5. 해시태그 풀 갱신
                await _update_hashtag_pool(ht["id"], len(new_usernames), len(usernames))

            except Exception as e:
                logger.error(f"Hashtag #{ht['hashtag']} 처리 실패: {e}")
                failed_count += 1

        # 6. DB 총 계정 수
        db_total = await db.fetch_one("SELECT COUNT(*) FROM influencers WHERE status != 'deleted'")
        db_total_count = db_total[0] if db_total else 0

    except Exception as e:
        logger.error(f"Discovery 배치 오류: {e}")
        failed_count += 1

    finally:
        # 실행 로그 완료
        await db.execute(
            """
            UPDATE seeding_run_logs
            SET finished_at        = NOW(),
                total_attempted    = $2,
                success_count      = $3,
                failed_count       = $4,
                new_accounts_found = $5,
                apify_calls_made   = $6,
                db_total_after     = $7
            WHERE id = $1
            """,
            run_log_id,
            total_attempted,
            success_count,
            failed_count,
            new_accounts_found,
            apify_calls,
            db_total_count if 'db_total_count' in dir() else None,
        )

    logger.info(
        f"Discovery 완료: 신규 {new_accounts_found}명, "
        f"성공 {success_count}, 실패 {failed_count}"
    )
    return {
        "status": "done",
        "new_accounts_found": new_accounts_found,
        "success_count": success_count,
        "failed_count": failed_count,
        "apify_calls": apify_calls,
    }


# ----------------------------------------------------------------
# 내부 헬퍼
# ----------------------------------------------------------------

async def _pick_hashtags(limit: int) -> list[dict]:
    """수집 대상 해시태그를 선택한다 (7일 이상 미수집 + 미고갈)."""
    rows = await db.fetch_all(
        """
        SELECT id, hashtag, domain
        FROM seed_hashtag_pool
        WHERE is_exhausted = FALSE
          AND (last_crawled_at IS NULL OR last_crawled_at < NOW() - INTERVAL '7 days')
        ORDER BY last_crawled_at NULLS FIRST, new_accounts_found_last DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def _filter_existing(usernames: list[str]) -> list[str]:
    """DB에 이미 존재하는 handle을 제거하고 신규 handle만 반환한다."""
    if not usernames:
        return []
    existing = await db.fetch_all(
        "SELECT handle FROM influencers WHERE platform = 'instagram' AND handle = ANY($1)",
        usernames,
    )
    existing_set = {r["handle"] for r in existing}
    return [u for u in usernames if u not in existing_set]


async def _upsert_influencer(raw: dict, source_value: str) -> bool:
    """프로필 데이터를 influencers 테이블에 upsert한다."""
    parsed = parse_profile(raw)
    handle = parsed.get("handle")
    if not handle:
        return False

    instagram_user_id = parsed.get("instagram_user_id") or None
    bio = parsed.get("bio") or ""
    followers = parsed.get("followers") or 0
    following = parsed.get("following") or 0

    # 파생 필드 계산
    treatment_tags = extract_treatment_tags(bio)
    region_tags = extract_region_tags(bio)
    follower_tier = calculate_follower_tier(followers)
    quality_flags = calculate_quality_flags(
        followers=followers,
        following=following,
        engagement_rate=parsed.get("engagement_rate"),
        posts_count=parsed.get("posts_count"),
        avg_reel_plays=parsed.get("avg_reel_plays"),
    )
    status = "low_quality" if len(quality_flags) >= 2 else "active"

    # upsert (instagram_user_id 우선, 없으면 handle fallback)
    row = await db.fetch_one(
        """
        INSERT INTO influencers (
          platform, instagram_user_id, handle, full_name, bio,
          profile_url, profile_pic_url, external_url,
          is_verified, is_business,
          followers, following, posts_count,
          avg_likes, avg_comments, avg_reel_plays, engagement_rate,
          treatment_tags, region_tags, follower_tier,
          quality_flags, status, discovered_via, last_scraped_at
        ) VALUES (
          $1, $2, $3, $4, $5,
          $6, $7, $8,
          $9, $10,
          $11, $12, $13,
          $14, $15, $16, $17,
          $18::jsonb, $19::jsonb, $20,
          $21::jsonb, $22, $23, NOW()
        )
        ON CONFLICT (platform, handle) DO UPDATE SET
          instagram_user_id = COALESCE(EXCLUDED.instagram_user_id, influencers.instagram_user_id),
          full_name         = COALESCE(EXCLUDED.full_name, influencers.full_name),
          bio               = COALESCE(EXCLUDED.bio, influencers.bio),
          followers         = EXCLUDED.followers,
          following         = EXCLUDED.following,
          posts_count       = EXCLUDED.posts_count,
          avg_likes         = EXCLUDED.avg_likes,
          avg_comments      = EXCLUDED.avg_comments,
          avg_reel_plays    = EXCLUDED.avg_reel_plays,
          engagement_rate   = EXCLUDED.engagement_rate,
          treatment_tags    = EXCLUDED.treatment_tags,
          region_tags       = EXCLUDED.region_tags,
          follower_tier     = EXCLUDED.follower_tier,
          quality_flags     = EXCLUDED.quality_flags,
          status            = CASE
                                WHEN influencers.status = 'deleted' THEN influencers.status
                                ELSE EXCLUDED.status
                              END,
          last_scraped_at   = NOW(),
          updated_at        = NOW()
        RETURNING id, (xmax = 0) AS is_insert
        """,
        parsed["platform"],
        instagram_user_id,
        handle,
        parsed.get("full_name"),
        bio,
        parsed.get("profile_url"),
        parsed.get("profile_pic_url"),
        parsed.get("external_url"),
        parsed.get("is_verified", False),
        parsed.get("is_business", False),
        followers,
        following,
        parsed.get("posts_count"),
        parsed.get("avg_likes"),
        parsed.get("avg_comments"),
        parsed.get("avg_reel_plays"),
        parsed.get("engagement_rate"),
        json.dumps(treatment_tags, ensure_ascii=False),
        json.dumps(region_tags, ensure_ascii=False),
        follower_tier,
        json.dumps(quality_flags, ensure_ascii=False),
        status,
        f"hashtag:{source_value}",
    )

    influencer_id = str(row["id"])
    is_new = row["is_insert"]

    # discovery event 저장
    await db.execute(
        """
        INSERT INTO influencer_discovery_events (influencer_id, source_type, source_value)
        VALUES ($1, 'hashtag', $2)
        """,
        influencer_id, source_value,
    )

    # enrichment 큐 등록 (신규만)
    if is_new:
        await db.execute(
            """
            INSERT INTO influencer_seed_queue (influencer_id, platform, handle, job_type, priority)
            VALUES ($1, 'instagram', $2, 'posts_refresh', 5)
            ON CONFLICT DO NOTHING
            """,
            influencer_id, handle,
        )

    # 스냅샷 저장
    await _save_snapshot(influencer_id, parsed)

    return is_new


async def _save_snapshot(influencer_id: str, parsed: dict):
    await db.execute(
        """
        INSERT INTO influencer_metrics_snapshots
          (influencer_id, followers, following, posts_count,
           avg_likes, avg_comments, avg_reel_plays, engagement_rate)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        influencer_id,
        parsed.get("followers"),
        parsed.get("following"),
        parsed.get("posts_count"),
        parsed.get("avg_likes"),
        parsed.get("avg_comments"),
        parsed.get("avg_reel_plays"),
        parsed.get("engagement_rate"),
    )


async def _update_hashtag_pool(hashtag_id: str, new_found: int, total_collected: int):
    """해시태그 풀의 수집 결과를 갱신한다."""
    is_exhausted = total_collected > 0 and (new_found / total_collected) < 0.05

    await db.execute(
        """
        UPDATE seed_hashtag_pool
        SET last_crawled_at          = NOW(),
            crawl_count              = crawl_count + 1,
            new_accounts_found_last  = $2,
            total_accounts_found     = total_accounts_found + $2,
            is_exhausted             = $3
        WHERE id = $1
        """,
        hashtag_id, new_found, is_exhausted,
    )
