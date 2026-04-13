"""
INFINITH Backend — FastAPI Application.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import scrape, reports, analysis, influencers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="INFINITH Marketing Intelligence API",
    version="1.0.0",
    description="병원 마케팅 인텔리전스 — 웹사이트 스크래핑 + 시장 분석 + 리포트 생성",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scrape.router)
app.include_router(reports.router)
app.include_router(analysis.router)
app.include_router(influencers.router)


@app.on_event("startup")
def startup():
    from app.core.database import engine, Base
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.warning("DB connection failed (tables not created): %s", e)
        logger.warning("Scraping API will work but DB storage will be skipped")


@app.get("/health")
def health():
    return {"status": "ok"}
