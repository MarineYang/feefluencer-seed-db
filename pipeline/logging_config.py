"""
Loguru 기반 로깅 설정 — 일별 로테이션.

사용법:
  진입점(test_run.py / api.py / scheduler.py) 상단에서 한 번만 import.
    from logging_config import setup_logging
    setup_logging()

로그 파일 구조:
  pipeline/logs/pipeline_2026-04-22.log     (오늘자)
  pipeline/logs/pipeline_2026-04-21.log.gz  (하루 지난 로그, gzip 압축)
  ...
  30일 이후 자동 삭제.
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}:{function}:{line}</cyan> - "
    "<level>{message}</level>"
)
_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} - "
    "{message}"
)

_initialized = False


def setup_logging(level: str = "INFO", retention_days: int = 30) -> None:
    """
    로깅을 초기화한다.

    - 콘솔: 색상 있는 INFO 이상
    - 파일: pipeline/logs/pipeline_{YYYY-MM-DD}.log, 자정마다 로테이션, gzip 압축
    - 보관: retention_days 일 후 자동 삭제 (기본 30일)
    """
    global _initialized
    if _initialized:
        return

    logger.remove()  # 기본 stderr 핸들러 제거

    logger.add(
        sys.stderr,
        level=level,
        format=_CONSOLE_FORMAT,
        colorize=True,
    )

    logger.add(
        LOG_DIR / "pipeline_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention=f"{retention_days} days",
        compression="gz",
        level=level,
        format=_FILE_FORMAT,
        encoding="utf-8",
        enqueue=True,   # 멀티스레드/멀티프로세스 안전
    )

    _initialized = True
    logger.info(f"로깅 초기화 완료 (레벨={level}, 보관={retention_days}일, 경로={LOG_DIR})")
