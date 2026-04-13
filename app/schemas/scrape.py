from __future__ import annotations

from pydantic import BaseModel, HttpUrl


class ScrapeRequest(BaseModel):
    url: str
    clinic_name: str | None = None


class ScrapeProgress(BaseModel):
    step: int
    total_steps: int
    label: str
    detail: str | None = None


class ClinicInfo(BaseModel):
    name: str = ""
    name_en: str = ""
    phone: str = ""
    address: str = ""
    location: str = ""
    domain: str = ""
    services: list[str] = []
    certifications: list[str] = []
    doctors: list[DoctorInfo] = []
    sns_links: dict[str, str] = {}
    tracking_pixels: list[TrackingPixel] = []
    brand_colors: dict[str, str] = {}
    main_cta: str = ""
    operating_hours: str = ""
    established: str = ""
    logo_url: str = ""
    screenshot_url: str = ""


class DoctorInfo(BaseModel):
    name: str
    credentials: str = ""
    image_url: str = ""


class TrackingPixel(BaseModel):
    name: str
    installed: bool
    details: str = ""


class SiteMapResult(BaseModel):
    all_links: list[str] = []
    categorized: dict[str, list[str]] = {}


class SocialChannels(BaseModel):
    youtube: list[str] = []
    instagram: list[str] = []
    facebook: list[str] = []
    tiktok: list[str] = []
    kakao: list[str] = []
    naver_blog: list[str] = []
    naver_place: list[str] = []
    twitter: list[str] = []


class YouTubeChannel(BaseModel):
    channel_name: str = ""
    handle: str = ""
    subscribers: int = 0
    total_videos: int = 0
    total_views: int = 0
    description: str = ""
    top_videos: list[dict] = []
    playlists: list[str] = []
    linked_urls: list[dict] = []
    keywords: list[str] = []


class FacebookPage(BaseModel):
    page_name: str = ""
    url: str = ""
    followers: int = 0
    reviews: int = 0
    category: str = ""
    bio: str = ""
    logo: str = ""
    recent_post_age: str = ""
    has_whatsapp: bool = False
    post_frequency: str = ""


class NaverBlog(BaseModel):
    url: str = ""
    post_count: int = 0
    visitor_count: int = 0
    neighbor_count: int = 0


class ScrapeResponse(BaseModel):
    success: bool
    clinic: ClinicInfo | None = None
    site_map: SiteMapResult | None = None
    social_channels: SocialChannels | None = None
    youtube: YouTubeChannel | None = None
    facebook: list[FacebookPage] = []
    naver_blog: NaverBlog | None = None
    screenshots: list[dict] = []


# Rebuild models that have forward references
ClinicInfo.model_rebuild()
