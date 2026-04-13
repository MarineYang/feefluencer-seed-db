"""
시장 분석 + AI 콘텐츠 생성 API 라우터.
"""
import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import httpx

from app.core.config import settings
from app.services.perplexity import run_market_analysis, synthesize_report
from app.services.gemini import (
    generate_blog_posts,
    generate_social_captions,
    generate_ad_copy,
    generate_content_calendar,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ─── Request Schemas ───

class MarketAnalysisRequest(BaseModel):
    clinic_name: str
    services: list[str] = []
    address: str = ""


class ContentRequest(BaseModel):
    clinic_name: str
    services: list[str] = []
    keywords: list[str] = []
    platform: str = "instagram"
    count: int = 5


class AdCopyRequest(BaseModel):
    clinic_name: str
    services: list[str] = []
    target_audience: str = ""
    platforms: list[str] = ["naver_search", "google_ads", "instagram_ads"]


class CalendarRequest(BaseModel):
    clinic_name: str
    services: list[str] = []
    channels: list[str] = ["blog", "instagram", "youtube"]
    weeks: int = 4


class FullReportRequest(BaseModel):
    clinic_name: str
    services: list[str] = []
    address: str = ""
    scrape_data: dict = {}


# ─── 시장 분석 (Perplexity) ───

@router.post("/market")
async def market_analysis(req: MarketAnalysisRequest):
    """
    Perplexity sonar로 시장 분석 실행 (4개 쿼리 병렬).
    경쟁사, 키워드, 시장 트렌드, 타겟 오디언스 분석.
    """
    if not settings.PERPLEXITY_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "PERPLEXITY_API_KEY not configured"})

    async with httpx.AsyncClient() as client:
        result = await run_market_analysis(
            client,
            clinic_name=req.clinic_name,
            services=req.services,
            address=req.address,
        )

    return {"success": True, "data": result}


@router.post("/market/stream")
async def market_analysis_stream(req: MarketAnalysisRequest):
    """시장 분석 SSE 스트림 — 각 분석이 완료될 때마다 이벤트 전송."""
    if not settings.PERPLEXITY_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "PERPLEXITY_API_KEY not configured"})

    from app.services.perplexity import (
        analyze_competitors, analyze_keywords,
        analyze_market, analyze_target_audience,
    )

    async def event_generator():
        async with httpx.AsyncClient() as client:
            steps = [
                ("competitors", "경쟁사 분석 중...", analyze_competitors),
                ("keywords", "키워드 트렌드 분석 중...", analyze_keywords),
                ("market", "시장 분석 중...", analyze_market),
                ("target_audience", "타겟 오디언스 분석 중...", analyze_target_audience),
            ]

            results = {}
            for i, (key, message, func) in enumerate(steps, 1):
                yield {
                    "event": "progress",
                    "data": json.dumps({"step": i, "total": 4, "message": message}, ensure_ascii=False),
                }
                result = await func(client, req.clinic_name, req.services, req.address)
                results[key] = result
                yield {
                    "event": "step_complete",
                    "data": json.dumps({"step": i, "key": key, "data": result}, ensure_ascii=False, default=str),
                }

            yield {
                "event": "complete",
                "data": json.dumps({"data": results}, ensure_ascii=False, default=str),
            }

    return EventSourceResponse(event_generator())


# ─── AI 리포트 합성 (Perplexity) ───

@router.post("/synthesize")
async def synthesize(req: FullReportRequest):
    """스크래핑 데이터 + 시장 분석을 종합하여 최종 전략 리포트 생성."""
    if not settings.PERPLEXITY_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "PERPLEXITY_API_KEY not configured"})

    async with httpx.AsyncClient() as client:
        # 시장 분석 먼저 실행
        market_data = await run_market_analysis(
            client,
            clinic_name=req.clinic_name,
            services=req.services,
            address=req.address,
        )
        # 종합 리포트 합성
        report = await synthesize_report(
            client,
            clinic_name=req.clinic_name,
            scrape_summary=req.scrape_data,
            market_analysis=market_data,
        )

    return {"success": True, "market_analysis": market_data, "report": report}


# ─── 콘텐츠 생성 (Gemini) ───

@router.post("/content/blog")
async def blog_content(req: ContentRequest):
    """블로그 포스트 아이디어 + 초안 생성."""
    if not settings.GEMINI_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "GEMINI_API_KEY not configured"})

    async with httpx.AsyncClient() as client:
        result = await generate_blog_posts(
            client,
            clinic_name=req.clinic_name,
            services=req.services,
            keywords=req.keywords,
            count=req.count,
        )

    return {"success": True, "data": result}


@router.post("/content/social")
async def social_content(req: ContentRequest):
    """SNS 게시물 캡션 생성."""
    if not settings.GEMINI_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "GEMINI_API_KEY not configured"})

    async with httpx.AsyncClient() as client:
        result = await generate_social_captions(
            client,
            clinic_name=req.clinic_name,
            services=req.services,
            platform=req.platform,
            count=req.count,
        )

    return {"success": True, "data": result}


@router.post("/content/ads")
async def ad_content(req: AdCopyRequest):
    """광고 카피 생성."""
    if not settings.GEMINI_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "GEMINI_API_KEY not configured"})

    async with httpx.AsyncClient() as client:
        result = await generate_ad_copy(
            client,
            clinic_name=req.clinic_name,
            services=req.services,
            target_audience=req.target_audience,
            platforms=req.platforms,
        )

    return {"success": True, "data": result}


@router.post("/content/calendar")
async def content_calendar(req: CalendarRequest):
    """콘텐츠 캘린더 생성."""
    if not settings.GEMINI_API_KEY:
        return JSONResponse(status_code=500, content={"success": False, "message": "GEMINI_API_KEY not configured"})

    async with httpx.AsyncClient() as client:
        result = await generate_content_calendar(
            client,
            clinic_name=req.clinic_name,
            services=req.services,
            channels=req.channels,
            weeks=req.weeks,
        )

    return {"success": True, "data": result}
