from __future__ import annotations
"""
도메인 특화 키워드 사전
- treatment_tags 추출 (시술 키워드)
- region_tags 추출 (지역명)
- sponsorship 감지 (협찬 표시)
- medical_risk 감지 (의료광고법 위반 소지)
- clinic_brand 감지 (클리닉/브랜드명 패턴)
"""

from typing import Optional

# ============================================================
# 시술 키워드 사전
# ============================================================
TREATMENT_KEYWORDS: dict[str, list[str]] = {
    "skin_clinic": [
        # 레이저
        "레이저토닝", "피코레이저", "엑셀브이", "루트로닉", "포토나",
        "레이저", "IPL", "아이피엘",
        # 리프팅
        "울쎄라", "써마지", "슈링크", "인모드", "엑스포텐셜",
        "리프팅", "피부리프팅", "안면거상",
        # 보톡스/필러
        "보톡스", "필러", "쁘띠성형", "쁘띠", "히알루론산",
        # 스킨케어
        "피부과", "피부관리", "피부개선", "피부재생",
        "미백", "기미", "주근깨", "색소침착", "잡티",
        "아쿠아필링", "물광주사", "피부트러블", "모공",
        "주름개선", "탄력", "수분공급",
        # 기타 시술
        "실리프팅", "실", "PDO실", "콜라겐",
    ],
    "plastic_surgery": [
        # 눈
        "쌍꺼풀", "눈성형", "앞트임", "뒷트임", "눈밑지방재배치",
        "눈밑지방", "눈매교정", "상안검", "하안검",
        # 코
        "코성형", "코필러", "코뼈", "매부리코", "콧대", "콧볼",
        # 지방
        "지방흡입", "지방이식", "지방분해",
        # 윤곽
        "양악", "광대축소", "사각턱", "윤곽주사", "윤곽",
        # 가슴
        "가슴성형", "가슴확대", "가슴축소",
        # 전체
        "성형", "성형수술", "성형외과",
        "성형후기", "성형일기", "성형변신",
    ],
    "obesity_clinic": [
        # 약물
        "삭센다", "위고비", "오젬픽", "마운자로", "GLP-1",
        # 주사
        "다이어트주사", "지방분해주사", "PPC주사", "비만주사",
        "카복시", "카르니틴주사",
        # 일반
        "비만클리닉", "비만치료", "비만",
        "체중감량", "체중관리", "다이어트",
        "살빼기", "살빼는법", "다이어트성공", "다이어트일기",
        # 수술
        "위절제", "위밴드", "위소매절제",
    ],
}

# 모든 시술 키워드를 flat 리스트로 (도메인 무관 빠른 검색용)
ALL_TREATMENT_KEYWORDS: set[str] = {
    kw for kws in TREATMENT_KEYWORDS.values() for kw in kws
}


# ============================================================
# 지역 키워드 사전
# ============================================================
REGION_KEYWORDS: list[str] = [
    # 서울 주요 지역
    "강남", "서초", "송파", "강동", "강북", "노원", "도봉",
    "성북", "동대문", "중랑", "광진", "성동", "중구", "종로",
    "용산", "마포", "홍대", "서대문", "은평", "강서",
    "양천", "목동", "구로", "금천", "영등포", "동작", "관악",
    # 서울 상권
    "압구정", "청담", "신사", "이태원", "명동", "신촌",
    "건대", "혜화", "잠실", "수서", "방이",
    # 경기/수도권
    "판교", "분당", "일산", "수원", "인천", "용인", "성남",
    "안양", "부천", "평촌", "의정부", "안산", "화성",
    # 지방 광역시
    "부산", "대구", "광주", "대전", "울산",
    "해운대", "동래", "서면",
    # 지방
    "제주", "전주", "춘천", "청주",
]


# ============================================================
# 협찬 표시 키워드
# ============================================================
SPONSORSHIP_KEYWORDS: list[str] = [
    "#ad", "#협찬", "#유료광고", "#광고", "#제공",
    "#협찬받음", "#체험단", "#서포터즈", "#홍보",
    "#pr", "#sponsored", "#gifted",
    "유료광고포함", "광고포함",
]


# ============================================================
# 의료광고법 리스크 감지 키워드
# ============================================================
MEDICAL_RISK_KEYWORDS: list[str] = [
    # 전후 비교
    "전후", "비포애프터", "before after", "변화", "달라진",
    # 효능 주장
    "확실히", "완벽하게", "100%", "효과보장", "보장",
    "완치", "치료", "치유", "완전제거",
    # 과장 표현
    "기적", "신세계", "인생템", "인생시술",
]


# ============================================================
# 클리닉 브랜드 감지 패턴 (일반적인 패턴)
# ============================================================
CLINIC_BRAND_PATTERNS: list[str] = [
    "피부과", "성형외과", "의원", "클리닉", "병원",
    "비만클리닉", "다이어트클리닉",
]


# ============================================================
# 키워드 추출 함수
# ============================================================
def extract_treatment_tags(text: str) -> list[str]:
    """텍스트에서 시술 키워드를 추출한다."""
    if not text:
        return []
    text_lower = text.lower()
    found = []
    for kw in ALL_TREATMENT_KEYWORDS:
        if kw in text_lower or kw in text:
            found.append(kw)
    return list(set(found))


def get_treatment_domains(tags: list[str]) -> list[str]:
    """시술 태그 목록에서 해당 도메인을 반환한다."""
    domains = set()
    for domain, kws in TREATMENT_KEYWORDS.items():
        if any(tag in kws for tag in tags):
            domains.add(domain)
    return list(domains)


def extract_region_tags(text: str) -> list[str]:
    """텍스트에서 지역명을 추출한다."""
    if not text:
        return []
    found = []
    for region in REGION_KEYWORDS:
        if region in text:
            found.append(region)
    return list(set(found))


def is_sponsored(text: str) -> bool:
    """협찬 게시물인지 판단한다."""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in SPONSORSHIP_KEYWORDS)


def has_medical_risk(text: str) -> bool:
    """의료광고법 리스크 표현이 있는지 감지한다."""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in MEDICAL_RISK_KEYWORDS)


def extract_clinic_brands(text: str) -> list[str]:
    """텍스트에서 클리닉 브랜드 언급을 감지한다."""
    if not text:
        return []
    found = []
    for pattern in CLINIC_BRAND_PATTERNS:
        # 앞에 한글 단어가 붙은 경우 감지 (e.g. "강남OO피부과")
        idx = 0
        while True:
            pos = text.find(pattern, idx)
            if pos == -1:
                break
            # 앞 3~10글자를 브랜드명으로 추정
            start = max(0, pos - 8)
            brand_snippet = text[start:pos + len(pattern)].strip()
            if brand_snippet:
                found.append(brand_snippet)
            idx = pos + 1
    return list(set(found))


# ============================================================
# 스코어 계산
# ============================================================
def calculate_follower_tier(followers: int | None) -> str | None:
    if followers is None:
        return None
    if followers < 10_000:
        return "nano"
    if followers < 100_000:
        return "micro"
    if followers < 500_000:
        return "mid"
    return "macro"


def calculate_match_score(
    domain: str,
    treatment_tags: list[str],
    region_tags: list[str],
    follower_tier: str | None,
    treatment_content_ratio: float,
    sponsorship_ratio: float,
    has_risk: bool,
    quality_flags: list[str],
    anomaly_flag: bool,
) -> float:
    """도메인별 협찬 매칭 점수를 계산한다 (0.0 ~ 1.0)."""
    score = 0.0
    domain_kws = TREATMENT_KEYWORDS.get(domain, [])

    # 시술 태그 매칭 (최대 0.35)
    matched = sum(1 for tag in treatment_tags if tag in domain_kws)
    score += min(matched * 0.07, 0.35)

    # 시술 콘텐츠 비율 (최대 0.20)
    score += treatment_content_ratio * 0.20

    # 팔로워 티어 가중치 (최대 0.20)
    tier_scores = {
        "skin_clinic":      {"nano": 0.12, "micro": 0.20, "mid": 0.14, "macro": 0.08},
        "plastic_surgery":  {"nano": 0.10, "micro": 0.20, "mid": 0.18, "macro": 0.10},
        "obesity_clinic":   {"nano": 0.20, "micro": 0.18, "mid": 0.12, "macro": 0.06},
    }
    if follower_tier and domain in tier_scores:
        score += tier_scores[domain].get(follower_tier, 0.0)

    # 지역 태그 존재 여부 (최대 0.10)
    if region_tags:
        score += 0.10

    # 협찬 과다 감점 (sponsorship_ratio > 0.5이면 감점)
    if sponsorship_ratio > 0.5:
        score -= (sponsorship_ratio - 0.5) * 0.20

    # 의료광고법 리스크 감점
    if has_risk:
        score -= 0.10

    # quality_flags 감점
    score -= len(quality_flags) * 0.05

    # anomaly 감점
    if anomaly_flag:
        score -= 0.10

    return round(max(0.0, min(1.0, score)), 3)


def calculate_quality_flags(
    followers: int | None,
    following: int | None,
    engagement_rate: float | None,
    posts_count: int | None,
    avg_reel_plays: float | None,
) -> list[str]:
    """봇/저품질 계정 감지 플래그를 반환한다."""
    flags = []

    if followers and following and followers > 0:
        if following / followers > 1.5:
            flags.append("high_following_ratio")

    if engagement_rate is not None and engagement_rate < 0.003:
        flags.append("low_engagement")

    if posts_count is not None and posts_count < 9:
        flags.append("low_post_count")

    if avg_reel_plays and followers and followers > 0:
        if avg_reel_plays / followers < 0.01:
            flags.append("low_reel_reach")

    return flags
