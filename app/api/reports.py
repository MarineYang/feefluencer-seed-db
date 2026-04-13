"""
리포트 조회 API.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.clinic import Report, Clinic

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다")
    return {"success": True, "report": report.report_data, "report_id": report.id, "created_at": report.created_at}


@router.get("")
def list_reports(url: str | None = None, limit: int = 20, db: Session = Depends(get_db)):
    query = db.query(Report).order_by(Report.created_at.desc())
    if url:
        clinic = db.query(Clinic).filter(Clinic.url.contains(url)).first()
        if clinic:
            query = query.filter(Report.clinic_id == clinic.id)
        else:
            return {"success": True, "reports": []}
    reports = query.limit(limit).all()
    return {
        "success": True,
        "reports": [
            {
                "id": r.id,
                "clinic_name": r.report_data.get("clinic_snapshot", {}).get("name", "") if isinstance(r.report_data, dict) else "",
                "overall_score": r.overall_score,
                "created_at": r.created_at,
            }
            for r in reports
        ],
    }
