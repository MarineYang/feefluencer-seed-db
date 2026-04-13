from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, JSON, ForeignKey,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Influencer(Base):
    __tablename__ = "influencers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, default="instagram")  # instagram, youtube, tiktok
    handle = Column(String(100), nullable=False)
    profile_url = Column(String(500))
    full_name = Column(String(200))
    bio = Column(Text)
    category = Column(String(100))       # 뷰티, 성형, 피부, 치과, 라이프스타일 ...
    followers = Column(Integer, default=0)
    following = Column(Integer, default=0)
    posts_count = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)  # (avg_likes + avg_comments) / followers * 100
    avg_likes = Column(Integer, default=0)
    avg_comments = Column(Integer, default=0)
    avg_reel_plays = Column(Integer, default=0)
    is_verified = Column(Boolean, default=False)
    is_business = Column(Boolean, default=False)
    profile_pic_url = Column(Text)
    external_url = Column(String(500))
    extra_data = Column(JSON)             # 기타 raw 데이터
    discovered_via = Column(String(50))   # handle_search, hashtag, competitor, manual
    last_scraped_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    posts = relationship("InfluencerPost", back_populates="influencer", cascade="all, delete-orphan")


class InfluencerPost(Base):
    __tablename__ = "influencer_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    influencer_id = Column(Integer, ForeignKey("influencers.id"), nullable=False)
    post_type = Column(String(20))       # post, reel, video
    post_url = Column(String(500))
    caption = Column(Text)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    plays = Column(Integer, default=0)
    hashtags = Column(JSON)
    posted_at = Column(String(50))
    scraped_at = Column(DateTime, default=datetime.utcnow)

    influencer = relationship("Influencer", back_populates="posts")
