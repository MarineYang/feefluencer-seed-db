"""
APScheduler 기반 배치 스케줄러
cron 표현식으로 Discovery / Enrichment / Refresh 배치를 자동 실행한다.

실행: python scheduler.py
"""
import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

import database as db
from config import settings
from jobs.discovery import run_discovery
from jobs.enrichment import run_enrichment
from jobs.refresh import run_refresh


async def job_discovery():
    logger.info("=== Discovery Job 시작 ===")
    try:
        result = await run_discovery()
        logger.info(f"=== Discovery Job 완료: {result} ===")
    except Exception as e:
        logger.error(f"=== Discovery Job 오류: {e} ===")


async def job_enrichment():
    logger.info("=== Enrichment Job 시작 ===")
    try:
        result = await run_enrichment()
        logger.info(f"=== Enrichment Job 완료: {result} ===")
    except Exception as e:
        logger.error(f"=== Enrichment Job 오류: {e} ===")


async def job_refresh_hot():
    logger.info("=== Refresh(hot) Job 시작 ===")
    try:
        result = await run_refresh("hot")
        logger.info(f"=== Refresh(hot) 완료: {result} ===")
    except Exception as e:
        logger.error(f"=== Refresh(hot) 오류: {e} ===")


async def job_refresh_warm():
    logger.info("=== Refresh(warm) Job 시작 ===")
    try:
        result = await run_refresh("warm")
        logger.info(f"=== Refresh(warm) 완료: {result} ===")
    except Exception as e:
        logger.error(f"=== Refresh(warm) 오류: {e} ===")


async def main():
    await db.get_pool()

    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # Discovery: 매주 월요일 오전 9시
    scheduler.add_job(
        job_discovery,
        CronTrigger.from_crontab(settings.discovery_cron, timezone="Asia/Seoul"),
        id="discovery",
        name="Discovery Job",
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Enrichment: 매주 수요일 오전 10시
    scheduler.add_job(
        job_enrichment,
        CronTrigger.from_crontab(settings.enrichment_cron, timezone="Asia/Seoul"),
        id="enrichment",
        name="Enrichment Job",
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Refresh(hot): 매일 오전 8시
    scheduler.add_job(
        job_refresh_hot,
        CronTrigger.from_crontab(settings.refresh_cron, timezone="Asia/Seoul"),
        id="refresh_hot",
        name="Refresh Hot Job",
        max_instances=1,
        misfire_grace_time=1800,
    )

    # Refresh(warm): 매주 목요일 오전 7시
    scheduler.add_job(
        job_refresh_warm,
        CronTrigger.from_crontab("0 7 * * 4", timezone="Asia/Seoul"),
        id="refresh_warm",
        name="Refresh Warm Job",
        max_instances=1,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("스케줄러 시작됨. Ctrl+C로 종료.")
    logger.info(f"  Discovery  : {settings.discovery_cron}")
    logger.info(f"  Enrichment : {settings.enrichment_cron}")
    logger.info(f"  Refresh    : {settings.refresh_cron}")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        await db.close_pool()
        logger.info("스케줄러 종료됨.")


if __name__ == "__main__":
    asyncio.run(main())
