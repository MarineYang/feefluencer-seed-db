"""
인플루언서 검색 API (피쳐링 방식 POC).

검색 전략 (하이브리드):
  1. feefluencer-seed-db DB 검색 (빠름, 무료)
  2. 결과가 SEEDDB_MIN_RESULTS 미만이면 Apify 실시간 검색으로 보완 (느림, 유료)
"""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import httpx

from app.core.config import settings
from app.services.influencer import search_by_keywords, search_by_handle
from app.services.seeddb import search_from_seeddb

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/influencers", tags=["influencers"])


class KeywordSearchRequest(BaseModel):
    keywords: list[str]                         # 포함 키워드 (최대 3개)
    exclude_keywords: list[str] = []            # 제외 키워드 (최대 3개)
    exclude_accounts: list[str] = []            # 계정명 제외 (최대 5개)
    search_in_content: bool = True              # 콘텐츠에서 검색
    search_in_profile: bool = True              # 프로필에서 검색
    min_followers: int = 1000
    max_followers: int = 0                      # 0 = 무제한
    results_limit: int = 30                     # 수집할 게시물 수


class HandleSearchRequest(BaseModel):
    handle: str


@router.post("/search")
async def keyword_search(req: KeywordSearchRequest):
    """
    키워드 기반 인플루언서 검색 (하이브리드).

    1차) feefluencer-seed-db DB 검색 (즉시 응답)
    2차) DB 결과 부족 시 Apify 실시간 검색으로 보완 (~70초)
    """
    if not req.keywords:
        return JSONResponse(status_code=400, content={"success": False, "message": "keywords required"})

    # ── 1단계: DB 기반 검색 ──
    db_results = await search_from_seeddb(
        keywords=req.keywords[:3],
        min_followers=req.min_followers,
        max_followers=req.max_followers,
        limit=req.results_limit,
    )

    search_source = "seeddb"

    # ── 2단계: DB 결과 부족 시 Apify 폴백 ──
    if len(db_results) < settings.SEEDDB_MIN_RESULTS:
        if not settings.APIFY_API_TOKEN:
            logger.warning("DB 결과 부족(%d개)이지만 APIFY_API_TOKEN 미설정 — DB 결과만 반환", len(db_results))
        else:
            logger.info(
                "DB 결과 %d개 (최소 %d개 미만) → Apify 폴백 실행",
                len(db_results), settings.SEEDDB_MIN_RESULTS,
            )
            try:
                async with httpx.AsyncClient() as client:
                    apify_results = await search_by_keywords(
                        client,
                        keywords=req.keywords[:3],
                        exclude_keywords=req.exclude_keywords[:3],
                        exclude_accounts=req.exclude_accounts[:5],
                        search_in_content=req.search_in_content,
                        search_in_profile=req.search_in_profile,
                        min_followers=req.min_followers,
                        max_followers=req.max_followers,
                        results_limit=req.results_limit,
                    )
                # DB 결과와 중복 제거 후 합산
                db_handles = {r.get("handle", "").lstrip("@") for r in db_results}
                for ar in apify_results:
                    h = ar.get("handle", "").lstrip("@")
                    if h not in db_handles:
                        ar["data_source"] = "apify"
                        db_results.append(ar)
                        db_handles.add(h)
                search_source = "hybrid"
            except Exception as e:
                logger.warning("Apify 폴백 실패: %s", e)

    return {
        "success": True,
        "keywords": req.keywords,
        "search_source": search_source,
        "filters": {
            "exclude_keywords": req.exclude_keywords,
            "exclude_accounts": req.exclude_accounts,
            "search_in_content": req.search_in_content,
            "search_in_profile": req.search_in_profile,
            "min_followers": req.min_followers,
            "max_followers": req.max_followers,
        },
        "count": len(db_results),
        "data": db_results,
    }


@router.post("/profile")
async def profile_detail(req: HandleSearchRequest):
    """
    인플루언서 프로필 상세 (핸들 직접 조회).
    프로필 + 최근 게시물 12개 + 릴스 12개.
    """
    if not settings.APIFY_API_TOKEN:
        return JSONResponse(status_code=500, content={"success": False, "message": "APIFY_API_TOKEN not configured"})

    async with httpx.AsyncClient() as client:
        result = await search_by_handle(client, req.handle)

    if not result:
        return JSONResponse(status_code=404, content={"success": False, "message": f"@{req.handle} not found"})

    return {"success": True, "data": result}


@router.get("/proxy/image")
async def proxy_image(url: str):
    """Instagram 프로필 사진 CORS 프록시."""
    from fastapi.responses import Response
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10.0, follow_redirects=True)
            return Response(
                content=resp.content,
                media_type=resp.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "public, max-age=3600"},
            )
    except Exception:
        return Response(status_code=404)
