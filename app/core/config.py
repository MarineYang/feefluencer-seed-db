from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "mysql+pymysql://root:password@localhost:3306/infinith"
    FIRECRAWL_API_KEY: str = ""
    PERPLEXITY_API_KEY: str = ""
    APIFY_API_TOKEN: str = ""
    GEMINI_API_KEY: str = ""
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # feefluencer-seed-db API (DB 기반 키워드 검색)
    SEEDDB_API_URL: str = "http://localhost:8000"
    # DB 결과가 이 수 미만이면 Apify 폴백
    SEEDDB_MIN_RESULTS: int = 5

    class Config:
        env_file = ".env"


settings = Settings()
