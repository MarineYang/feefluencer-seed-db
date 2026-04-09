from __future__ import annotations
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    apify_api_token: str

    # Apify actors
    hashtag_scraper_actor: str = "apify/instagram-hashtag-scraper"
    profile_scraper_actor: str = "apify/instagram-profile-scraper"
    post_scraper_actor: str = "apify/instagram-post-scraper"

    # 수집 설정
    hashtag_results_limit: int = 300
    post_results_limit: int = 30
    profile_batch_size: int = 50
    discovery_hashtag_batch: int = 5
    enrichment_batch_size: int = 20

    # 스케줄 (cron 표현식)
    discovery_cron: str = "0 9 * * 1"
    enrichment_cron: str = "0 10 * * 3"
    refresh_cron: str = "0 8 * * *"

    # API 서버
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


settings = Settings()
