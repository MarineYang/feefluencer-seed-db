"""
스크래핑 API 라우터 — SSE 진행률 스트리밍 포함.
"""
import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.schemas.scrape import ScrapeRequest
from app.services.firecrawl import crawl_and_build_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["scrape"])


def _save_to_db(req: ScrapeRequest, report: dict) -> int | None:
    """DB에 리포트 저장. DB 미연결 시 None 반환."""
    try:
        from app.core.database import SessionLocal
        from app.models.clinic import Clinic, ScrapeResult, Report

        db = SessionLocal()
        try:
            clinic = db.query(Clinic).filter(Clinic.url == req.url).first()
            if not clinic:
                clinic = Clinic(
                    url=req.url,
                    name=report.get("clinic_snapshot", {}).get("name", ""),
                    name_en=report.get("clinic_snapshot", {}).get("name_en", ""),
                    phone=report.get("clinic_snapshot", {}).get("phone", ""),
                    address=report.get("clinic_snapshot", {}).get("location", ""),
                )
                db.add(clinic)
                db.flush()

            scrape_result = ScrapeResult(
                clinic_id=clinic.id,
                scrape_type="website",
                raw_data=report,
            )
            db.add(scrape_result)

            report_record = Report(
                clinic_id=clinic.id,
                report_data=report,
                overall_score=report.get("overall_score", 0),
            )
            db.add(report_record)
            db.commit()
            logger.info("Report saved: clinic=%s, report_id=%d", clinic.name, report_record.id)
            return report_record.id
        except Exception as e:
            logger.error("DB save error: %s", e)
            db.rollback()
            return None
        finally:
            db.close()
    except Exception as e:
        logger.warning("DB not available, skipping save: %s", e)
        return None


@router.post("/scrape")
async def scrape_website(req: ScrapeRequest):
    """
    병원 웹사이트 스크래핑 — SSE 스트림으로 진행률 + 결과 반환.
    """

    async def event_generator():
        final_report = None

        async for event in crawl_and_build_report(req.url, req.clinic_name):
            if event["type"] == "progress":
                yield {
                    "event": "progress",
                    "data": json.dumps(event, ensure_ascii=False),
                }
            elif event["type"] == "error":
                yield {
                    "event": "error",
                    "data": json.dumps({"message": event["message"]}, ensure_ascii=False),
                }
                return
            elif event["type"] == "complete":
                final_report = event["report"]

        if final_report:
            db_id = _save_to_db(req, final_report)
            if db_id:
                final_report["db_report_id"] = db_id

            yield {
                "event": "complete",
                "data": json.dumps(final_report, ensure_ascii=False, default=str),
            }

    return EventSourceResponse(event_generator())


@router.post("/scrape/sync")
async def scrape_website_sync(req: ScrapeRequest):
    """
    동기 버전 — SSE 없이 JSON으로 최종 결과만 반환.
    테스트/디버깅용.
    """
    final_report = None

    async for event in crawl_and_build_report(req.url, req.clinic_name):
        if event["type"] == "error":
            return JSONResponse(status_code=500, content={"success": False, "message": event["message"]})
        if event["type"] == "complete":
            final_report = event["report"]

    if not final_report:
        return JSONResponse(status_code=500, content={"success": False, "message": "리포트 생성 실패"})

    db_id = _save_to_db(req, final_report)
    if db_id:
        final_report["db_report_id"] = db_id

    return {"success": True, "report": final_report}
