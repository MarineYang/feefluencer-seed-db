from __future__ import annotations
"""
도메인 특화 키워드 사전
- treatment_tags 추출 (시술 키워드)
- region_tags 추출 (지역명)
- sponsorship 감지 (협찬 표시)
- medical_risk 감지 (의료광고법 위반 소지)
- clinic_brand 감지 (클리닉/브랜드명 패턴)
- contact_info 추출 (이메일/카카오/전화/링크트리)
- sponsorship_intent 감지 (협찬 의향 신호)
- content_consistency 계산 (컨텐츠 일관성 점수)
"""

import re
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
# 협찬 의향 신호 키워드 (바이오 문구 감지용)
# ============================================================
SPONSORSHIP_INTENT_KEYWORDS: dict[str, list[str]] = {
    # DM으로 협찬 문의를 명시한 경우 (가장 강력한 신호)
    "explicit_dm": [
        "협찬문의dm", "협찬 문의 dm", "협찬문의는dm", "협찬문의 dm",
        "광고문의dm", "광고 문의 dm", "제안은dm", "제안 dm",
        "collab dm", "collaboration dm", "비즈니스dm",
        "business dm", "광고dm", "협업dm",
    ],
    # 이메일로 협찬 문의를 명시한 경우
    "explicit_email": [
        "비즈니스문의", "business inquiry", "광고문의",
        "협찬문의", "제안문의", "collab inquiry",
        "협업문의",
    ],
    # 협찬 경험/의향을 바이오에 표시한 경우
    "has_experience": [
        "광고계정", "협찬계정", "체험단진행중",
        "서포터즈활동중", "브랜드앰배서더",
        "brand ambassador", "협찬가능", "광고가능", "홍보가능",
    ],
}

# ============================================================
# 연락처 추출 정규식 패턴
# ============================================================
CONTACT_PATTERNS: dict[str, str] = {
    "email":    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "kakao":    r"(?:카카오|카톡|kakao|KakaoTalk)[:\s]*([a-zA-Z0-9_\-\.가-힣]{2,20})",
    "phone":    r"0[1789][0-9]\s*[-.]?\s*[0-9]{3,4}\s*[-.]?\s*[0-9]{4}",
    "linktree": r"linktr\.ee/([a-zA-Z0-9_\-\.]+)",
}


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


def extract_contact_info(bio: str, external_url: str | None = None) -> dict:
    """
    바이오와 외부 URL에서 연락처 정보를 추출한다.
    Returns: {"email": str|None, "kakao": str|None, "phone": str|None,
              "linktree": str|None, "has_contact": bool}
    """
    text = (bio or "") + " " + (external_url or "")
    result: dict = {"email": None, "kakao": None, "phone": None, "linktree": None}

    for field, pattern in CONTACT_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            # kakao/linktree는 캡처 그룹(group 1), 나머지는 전체 매치
            result[field] = m.group(1) if field in ("kakao", "linktree") else m.group(0)

    result["has_contact"] = any(v for k, v in result.items() if k != "has_contact" and v)
    return result


def detect_sponsorship_intent(bio: str) -> tuple[str, str | None]:
    """
    바이오에서 협찬 의향 신호를 감지한다.
    Returns: (signal_type, matched_phrase)
    signal_type: 'explicit_dm' | 'explicit_email' | 'has_experience' | 'none'
    """
    if not bio:
        return "none", None
    bio_normalized = bio.lower().replace(" ", "")

    for signal_type in ("explicit_dm", "explicit_email", "has_experience"):
        for kw in SPONSORSHIP_INTENT_KEYWORDS[signal_type]:
            kw_normalized = kw.lower().replace(" ", "")
            if kw_normalized in bio_normalized:
                return signal_type, kw
    return "none", None


def calculate_content_consistency(post_treatment_flags: list[bool]) -> float:
    """
    게시물별 시술 관련 여부 플래그 리스트를 받아 일관성 점수를 반환한다.
    단순 비율(70%) + 최대 연속 스트릭 보너스(30%) 합산.
    Returns: 0.0 ~ 1.0
    """
    if not post_treatment_flags:
        return 0.0
    ratio = sum(post_treatment_flags) / len(post_treatment_flags)

    max_streak = current_streak = 0
    for flag in post_treatment_flags:
        if flag:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    streak_bonus = min(max_streak / len(post_treatment_flags), 0.3)

    return round(min(ratio * 0.7 + streak_bonus, 1.0), 3)


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
    # B2B 신규 지표
    has_contact_info: bool = False,
    sponsorship_intent_signal: str = "none",
    content_consistency_score: float = 0.0,
    is_recently_active: bool = False,
    recent_engagement_rate: float | None = None,
) -> tuple[float, dict]:
    """
    도메인별 협찬 매칭 점수를 계산한다 (0.0 ~ 1.0).
    Returns: (score, breakdown_dict)
    """
    score = 0.0
    breakdown: dict = {}
    domain_kws = TREATMENT_KEYWORDS.get(domain, [])

    # 시술 태그 매칭 (최대 0.30)
    matched = sum(1 for tag in treatment_tags if tag in domain_kws)
    tag_score = min(matched * 0.06, 0.30)
    score += tag_score
    breakdown["treatment_tag_score"] = tag_score

    # 컨텐츠 일관성 점수 (최대 0.20)
    # content_consistency_score가 있으면 우선 사용, 없으면 ratio 구간화
    if content_consistency_score > 0:
        content_score = content_consistency_score * 0.20
    else:
        # 20% 이하는 0점, 그 이상부터 선형 비례
        content_score = max(0.0, (treatment_content_ratio - 0.20) / 0.80) * 0.20
    score += content_score
    breakdown["content_score"] = round(content_score, 4)

    # 팔로워 티어 가중치 (최대 0.20)
    tier_scores = {
        "skin_clinic":      {"nano": 0.12, "micro": 0.20, "mid": 0.14, "macro": 0.08},
        "plastic_surgery":  {"nano": 0.10, "micro": 0.20, "mid": 0.18, "macro": 0.10},
        "obesity_clinic":   {"nano": 0.20, "micro": 0.18, "mid": 0.12, "macro": 0.06},
    }
    tier_score = 0.0
    if follower_tier and domain in tier_scores:
        tier_score = tier_scores[domain].get(follower_tier, 0.0)
    score += tier_score
    breakdown["tier_score"] = tier_score

    # 지역 태그 존재 여부 (최대 0.10)
    region_score = 0.10 if region_tags else 0.0
    score += region_score
    breakdown["region_score"] = region_score

    # [신규] 연락처 존재 보너스 (최대 0.08) — B2B에서 연락 가능 여부 핵심
    contact_score = 0.08 if has_contact_info else 0.0
    score += contact_score
    breakdown["contact_score"] = contact_score

    # [신규] 협찬 의향 신호 보너스 (최대 0.07)
    intent_score_map = {"explicit_dm": 0.07, "explicit_email": 0.05, "has_experience": 0.03, "none": 0.0}
    intent_score = intent_score_map.get(sponsorship_intent_signal, 0.0)
    score += intent_score
    breakdown["intent_score"] = intent_score

    # [신규] 최근 활동 보너스 (최대 0.05) — 30일 내 활동 없으면 협찬 의미 없음
    active_score = 0.05 if is_recently_active else 0.0
    score += active_score
    breakdown["active_score"] = active_score

    # 협찬 과다 감점 (sponsorship_ratio > 0.5이면 감점)
    if sponsorship_ratio > 0.5:
        penalty = round((sponsorship_ratio - 0.5) * 0.20, 4)
        score -= penalty
        breakdown["sponsorship_penalty"] = -penalty

    # 의료광고법 리스크 감점
    if has_risk:
        score -= 0.10
        breakdown["risk_penalty"] = -0.10

    # quality_flags 감점
    if quality_flags:
        flag_penalty = len(quality_flags) * 0.05
        score -= flag_penalty
        breakdown["flag_penalty"] = -flag_penalty

    # anomaly 감점
    if anomaly_flag:
        score -= 0.10
        breakdown["anomaly_penalty"] = -0.10

    # [신규] 최근 engagement 매우 낮으면 추가 감점
    if recent_engagement_rate is not None and recent_engagement_rate < 0.01:
        score -= 0.05
        breakdown["low_engagement_penalty"] = -0.05

    final_score = round(max(0.0, min(1.0, score)), 3)
    breakdown["total"] = final_score
    return final_score, breakdown


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


# ============================================================
# 업체 계정 감지
# ============================================================

# bio에 이 키워드가 있으면 업체로 분류
_BUSINESS_BIO_KEYWORDS: list[str] = [
    # 직함/운영
    "원장", "대표", "점장", "원장님", "실장",
    # 공간 유형
    "에스테틱", "피부관리실", "피부관리샵", "샵", "스파",
    "뷰티샵", "네일샵", "헤어샵",
    # 예약/문의
    "예약문의", "예약은", "상담문의", "카톡아이디", "카카오",
    "문의는", "예약링크", "링크트리", "운영시간", "영업시간",
    "오픈", "휴무",
    # 주소/위치 영업 표현
    "점 운영", "지점", "본점", "신규오픈",
]

# handle에 이 패턴이 있으면 업체로 분류
_BUSINESS_HANDLE_KEYWORDS: list[str] = [
    "aesthetic", "clinic", "skincare", "skin_care",
    "beauty", "shop", "salon", "spa",
    "에스테틱", "클리닉", "피부과", "성형외과",
]


def passes_triage(
    followers: int,
    following: int,
    posts_count: int,
    quality_flags: list,
    bio: str,
    handle: str,
    full_name: str,
    is_business: bool,
    engagement_rate: float | None = None,
) -> tuple:
    """
    Enrichment 대상 여부를 판단한다.
    Returns: (passes: bool, reason: str)
    """
    if followers < 1_000:
        return False, "followers_too_low"
    # nano(1k~3k) 계정은 engagement_rate 2% 미만이면 제외 (유령 계정 방지)
    if followers < 3_000 and engagement_rate is not None and engagement_rate < 0.02:
        return False, "nano_low_engagement"
    if posts_count is not None and posts_count < 6:
        return False, "too_few_posts"
    if len(quality_flags) >= 2:
        return False, "low_quality"
    if is_business_account(bio=bio, handle=handle, full_name=full_name, is_business=is_business):
        return False, "business"
    return True, "ok"


def is_business_account(
    bio: str,
    handle: str,
    full_name: str,
    is_business: bool,
) -> bool:
    """
    업체/샵 계정 여부를 판단한다.
    True이면 status='business'로 분류되어 검색에서 제외된다.
    """
    bio_lower = (bio or "").lower()
    handle_lower = (handle or "").lower()
    name_lower = (full_name or "").lower()

    # Apify가 비즈니스 계정으로 표시한 경우
    # (단독으로는 너무 광범위하므로 다른 조건과 조합)
    bio_hit = sum(1 for kw in _BUSINESS_BIO_KEYWORDS if kw in bio_lower)
    handle_hit = any(kw in handle_lower for kw in _BUSINESS_HANDLE_KEYWORDS)
    name_hit = any(kw in name_lower for kw in _BUSINESS_BIO_KEYWORDS[:6])  # 직함/공간 유형만

    # 판단 기준:
    # - bio에 업체 키워드 2개 이상
    # - handle에 업체 키워드 + bio에 업체 키워드 1개 이상
    # - is_business=True + bio에 업체 키워드 1개 이상
    # - full_name에 직함/공간 유형 포함
    if bio_hit >= 2:
        return True
    if handle_hit and bio_hit >= 1:
        return True
    if is_business and bio_hit >= 1:
        return True
    if name_hit:
        return True

    return False


# ============================================================
# 해시태그 자동 발굴 (게시물 → seed_hashtag_pool 자동 확장)
# ============================================================

# 너무 일반적이라 수집 가치 없는 해시태그 제외 목록
_GENERIC_HASHTAG_BLOCKLIST: set[str] = {
    "일상", "데일리", "일상스타그램", "데일리룩", "오늘의일상",
    "맛집", "맛스타그램", "카페", "카페스타그램", "음식",
    "여행", "여행스타그램", "여행사진",
    "패션", "ootd", "outfit",
    "selfie", "셀카", "셀피", "얼스타그램",
    "선팔", "맞팔", "팔로우", "좋아요", "like4like", "follow",
    "photo", "photography", "instagood", "instagram",
    "beautiful", "cute", "love", "happy", "good",
    "korea", "korean", "seoul", "서울", "한국",
}

# 해시태그에서 관련성 판단할 패턴
_HASHTAG_RELEVANCE_PATTERNS: dict[str, list[str]] = {
    "skin_clinic": [
        "피부", "레이저", "리프팅", "보톡스", "필러", "시술", "스킨",
        "미백", "기미", "주름", "모공", "탄력", "재생", "관리",
        "울쎄라", "써마지", "슈링크", "인모드", "피코", "이피엘",
    ],
    "plastic_surgery": [
        "성형", "쌍꺼풀", "코수술", "지방흡입", "눈성형", "코성형",
        "양악", "광대", "사각턱", "윤곽", "가슴",
    ],
    "obesity_clinic": [
        "다이어트", "비만", "체중", "삭센다", "위고비", "오젬픽",
        "살빼", "감량", "지방분해", "다이어트주사",
    ],
}

_HASHTAG_RE = re.compile(r"#([가-힣a-zA-Z0-9_]{2,25})")


def _infer_hashtag_domain(tag: str) -> str | None:
    """해시태그에서 도메인을 추론한다. 관련 없으면 None."""
    tag_lower = tag.lower()

    for domain, patterns in _HASHTAG_RELEVANCE_PATTERNS.items():
        if any(p in tag_lower for p in patterns):
            return domain

    # 지역 + 의료기관 패턴 (예: 강남피부과, 부산성형)
    has_region = any(r in tag_lower for r in REGION_KEYWORDS)
    if has_region:
        if any(p in tag_lower for p in _HASHTAG_RELEVANCE_PATTERNS["skin_clinic"]):
            return "skin_clinic"
        if any(p in tag_lower for p in _HASHTAG_RELEVANCE_PATTERNS["plastic_surgery"]):
            return "plastic_surgery"
        if any(p in tag_lower for p in _HASHTAG_RELEVANCE_PATTERNS["obesity_clinic"]):
            return "obesity_clinic"
        if any(kw in tag_lower for kw in ["클리닉", "병원", "의원", "한의원"]):
            return "general"

    return None


def extract_new_hashtags(posts_raw: list[dict]) -> list[tuple[str, str]]:
    """
    게시물 목록에서 관련 해시태그를 추출하고 도메인을 추론한다.
    seed_hashtag_pool 자동 확장에 사용된다.

    반환: [(hashtag, domain), ...]  — 중복 제거, 관련 없는 태그 제외
    """
    found: dict[str, str] = {}  # hashtag → domain

    for post in posts_raw:
        caption = post.get("caption") or post.get("text") or ""
        raw_hashtags = post.get("hashtags") or []
        if isinstance(raw_hashtags, str):
            raw_hashtags = [raw_hashtags]

        # 캡션에서 해시태그 추출 + 이미 파싱된 hashtags 필드 합산
        from_caption = _HASHTAG_RE.findall(caption)
        all_tags = [t.lower() for t in from_caption] + [
            t.lstrip("#").lower() for t in raw_hashtags if t
        ]

        for tag in all_tags:
            tag = tag.strip()
            if not tag or len(tag) < 2 or len(tag) > 25:
                continue
            if tag in _GENERIC_HASHTAG_BLOCKLIST:
                continue
            if tag in found:
                continue

            domain = _infer_hashtag_domain(tag)
            if domain:
                found[tag] = domain

    return list(found.items())
