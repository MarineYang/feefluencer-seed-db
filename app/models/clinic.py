from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, Enum, ForeignKey
from sqlalchemy.orm import relationship

from app.core.database import Base


class Clinic(Base):
    __tablename__ = "clinics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String(500), nullable=False, unique=True)
    name = Column(String(200))
    name_en = Column(String(200))
    phone = Column(String(50))
    address = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    scrape_results = relationship("ScrapeResult", back_populates="clinic")
    reports = relationship("Report", back_populates="clinic")


class ScrapeResult(Base):
    __tablename__ = "scrape_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    scrape_type = Column(
        Enum("website", "youtube", "instagram", "facebook", "naver", "google_maps",
             name="scrape_type_enum"),
        nullable=False,
    )
    raw_data = Column(JSON, nullable=False)
    scraped_at = Column(DateTime, default=datetime.utcnow)

    clinic = relationship("Clinic", back_populates="scrape_results")


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    clinic_id = Column(Integer, ForeignKey("clinics.id"), nullable=False)
    report_data = Column(JSON, nullable=False)
    scrape_data = Column(JSON)
    analysis_data = Column(JSON)
    overall_score = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    clinic = relationship("Clinic", back_populates="reports")
