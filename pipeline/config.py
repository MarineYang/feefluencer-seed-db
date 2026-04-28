from __future__ import annotations
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 루트 .env → pipeline/.env 순으로 로드 (뒤쪽이 앞쪽을 덮어씀)
_PIPELINE_DIR = Path(__file__).resolve().parent
_ROOT_ENV = _PIPELINE_DIR.parent / ".env"
_LOCAL_ENV = _PIPELINE_DIR / ".env"


class Settings(BaseSettings):
    """파이프라인 설정.

    모든 값은 .env 파일의 환경변수로 override 할 수 있다.
    우선순위: pipeline/.env > 루트 .env > 코드 기본값
    """

    model_config = SettingsConfigDict(
        env_file=(str(_ROOT_ENV), str(_LOCAL_ENV)),
        env_file_encoding="utf-8",
        extra="ignore",  # 루트 .env의 app/ 전용 변수 무시
        case_sensitive=False,
    )

    # ── DB 연결 ──────────────────────────────────
    # 루트 .env의 DATABASE_URL은 app/용 MySQL이므로 pipeline은 PIPELINE_DATABASE_URL 우선 사용.
    # Docker(env var DATABASE_URL) 환경에서는 fallback으로 DATABASE_URL 사용.
    database_url: str = Field(
        ..., validation_alias=AliasChoices("PIPELINE_DATABASE_URL", "DATABASE_URL")
    )

    # ── Apify 인증 ───────────────────────────────
    apify_api_token: str = Field(..., validation_alias="APIFY_API_TOKEN")

    # ── Apify Actor IDs ─────────────────────────
    hashtag_scraper_actor: str = Field(
        default="apify/instagram-hashtag-scraper",
        validation_alias="HASHTAG_SCRAPER_ACTOR",
    )
    profile_scraper_actor: str = Field(
        default="apify/instagram-profile-scraper",
        validation_alias="PROFILE_SCRAPER_ACTOR",
    )
    post_scraper_actor: str = Field(
        default="apify/instagram-post-scraper",
        validation_alias="POST_SCRAPER_ACTOR",
    )

    # ── 수집 설정 ──────────────────────────────
    hashtag_results_limit: int = Field(
        default=100, validation_alias="HASHTAG_RESULTS_LIMIT"
    )
    post_results_limit: int = Field(
        default=30, validation_alias="POST_RESULTS_LIMIT"
    )
    profile_batch_size: int = Field(
        default=50, validation_alias="PROFILE_BATCH_SIZE"
    )
    discovery_hashtag_batch: int = Field(
        default=5, validation_alias="DISCOVERY_HASHTAG_BATCH"
    )
    enrichment_batch_size: int = Field(
        default=20, validation_alias="ENRICHMENT_BATCH_SIZE"
    )

    # ── 스케줄 (cron 표현식) ──────────────────────
    discovery_cron: str = Field(
        default="0 9 * * 1", validation_alias="DISCOVERY_CRON"
    )
    enrichment_cron: str = Field(
        default="0 10 * * 3", validation_alias="ENRICHMENT_CRON"
    )
    refresh_cron: str = Field(
        default="0 8 * * *", validation_alias="REFRESH_CRON"
    )

    # ── API 서버 ───────────────────────────────
    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")
    cors_origins: str = Field(
        default="http://localhost:5173", validation_alias="CORS_ORIGINS"
    )

    # ── Instagram 세션 ─────────────────────────
    instagram_session_id: str = Field(
        default="", validation_alias="INSTAGRAM_SESSION_ID"
    )


settings = Settings()
