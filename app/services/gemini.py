"""
Google Gemini API 클라이언트 — AI 마케팅 콘텐츠 생성 + 추천.
"""
import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL = "gemini-2.0-flash"


async def _generate(
    client: httpx.AsyncClient,
    system_instruction: str,
    prompt: str,
    temperature: float = 0.7,
) -> dict:
    """Gemini generateContent 호출."""
    url = f"{GEMINI_API}/{MODEL}:generateContent"
    try:
        resp = await client.post(
            url,
            params={"key": settings.GEMINI_API_KEY},
            json={
                "system_instruction": {
                    "parts": [{"text": system_instruction}],
                },
                "contents": [
                    {"parts": [{"text": prompt}]},
                ],
                "generationConfig": {
                    "temperature": temperature,
                    "responseMimeType": "application/json",
                },
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            logger.warning("Gemini API error: %s %s", resp.status_code, resp.text[:300])
            return {"data": None, "error": resp.text[:200]}

        result = resp.json()
        text = (
            result.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        parsed = _try_parse_json(text)
        return {"data": parsed if parsed else text}

    except Exception as e:
        logger.warning("Gemini error: %s", e)
        return {"data": None, "error": str(e)}


def _try_parse_json(text: str) -> dict | list | None:
    text = text.strip()
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ─── 마케팅 콘텐츠 생성 ───

async def generate_blog_posts(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    keywords: list[str],
    count: int = 5,
) -> dict:
    """블로그 포스트 아이디어 + 초안 생성."""
    system = (
        "당신은 한국 병원/클리닉 전문 콘텐츠 마케터입니다. "
        "SEO에 최적화된 블로그 콘텐츠를 기획하세요. "
        "반드시 한국어로 응답하세요."
    )
    services_str = ", ".join(services[:5]) if services else "성형외과"
    keywords_str = ", ".join(keywords[:10]) if keywords else ""

    prompt = f"""
"{clinic_name}" 병원의 블로그 콘텐츠 {count}개를 기획해주세요.
주요 시술: {services_str}
타겟 키워드: {keywords_str}

다음 JSON 형식으로 응답:
{{
  "posts": [
    {{
      "title": "SEO 최적화된 블로그 제목",
      "slug": "url-friendly-slug",
      "target_keyword": "메인 타겟 키워드",
      "meta_description": "검색 결과에 표시될 메타 설명 (150자 이내)",
      "outline": ["소제목1", "소제목2", "소제목3"],
      "hook": "도입부 1-2문장 (독자 관심 유도)",
      "cta": "행동 유도 문구",
      "estimated_word_count": 1500,
      "category": "시술정보/후기/FAQ/트렌드/비교"
    }}
  ]
}}
"""
    return await _generate(client, system, prompt)


async def generate_social_captions(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    platform: str = "instagram",
    count: int = 10,
) -> dict:
    """SNS 게시물 캡션 생성."""
    system = (
        "당신은 한국 병원/뷰티 분야 SNS 마케터입니다. "
        "각 플랫폼에 최적화된 캡션을 작성하세요. "
        "반드시 한국어로 응답하세요."
    )
    services_str = ", ".join(services[:5]) if services else "성형외과"

    prompt = f"""
"{clinic_name}" 병원의 {platform} 게시물 캡션 {count}개를 작성해주세요.
주요 시술: {services_str}

다음 JSON 형식으로 응답:
{{
  "captions": [
    {{
      "type": "before_after/tip/promotion/behind_scenes/faq/testimonial",
      "caption": "게시물 캡션 (이모지 포함, {platform}에 적합한 길이)",
      "hashtags": ["해시태그1", "해시태그2"],
      "best_posting_time": "추천 게시 시간",
      "visual_suggestion": "이미지/영상 제안"
    }}
  ]
}}
"""
    return await _generate(client, system, prompt)


async def generate_ad_copy(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    target_audience: str = "",
    platforms: list[str] | None = None,
) -> dict:
    """광고 카피 생성 (네이버, 구글, 인스타그램 등)."""
    if platforms is None:
        platforms = ["naver_search", "google_ads", "instagram_ads"]

    system = (
        "당신은 한국 의료 마케팅 광고 카피라이터입니다. "
        "각 플랫폼 규격에 맞는 광고 카피를 작성하세요. "
        "반드시 한국어로 응답하세요."
    )
    services_str = ", ".join(services[:5]) if services else "성형외과"
    platforms_str = ", ".join(platforms)

    prompt = f"""
"{clinic_name}" 병원의 광고 카피를 작성해주세요.
주요 시술: {services_str}
타겟: {target_audience or '20-40대 여성'}
플랫폼: {platforms_str}

다음 JSON 형식으로 응답:
{{
  "ads": [
    {{
      "platform": "플랫폼명",
      "headline": "광고 헤드라인 (글자수 제한 준수)",
      "description": "광고 설명문",
      "display_url": "표시 URL (해당 시)",
      "call_to_action": "CTA 버튼 텍스트",
      "target_keyword": "타겟 키워드 (검색광고의 경우)",
      "variation": "A/B 테스트용 변형 헤드라인"
    }}
  ]
}}

각 플랫폼별 2개씩 작성해주세요.
"""
    return await _generate(client, system, prompt)


async def generate_content_calendar(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    channels: list[str] | None = None,
    weeks: int = 4,
) -> dict:
    """콘텐츠 캘린더 생성."""
    if channels is None:
        channels = ["blog", "instagram", "youtube"]

    system = (
        "당신은 한국 병원 마케팅 전략가입니다. "
        "실행 가능한 콘텐츠 캘린더를 작성하세요. "
        "반드시 한국어로 응답하세요."
    )
    services_str = ", ".join(services[:5]) if services else "성형외과"
    channels_str = ", ".join(channels)

    prompt = f"""
"{clinic_name}" 병원의 {weeks}주간 콘텐츠 캘린더를 작성해주세요.
주요 시술: {services_str}
운영 채널: {channels_str}

다음 JSON 형식으로 응답:
{{
  "calendar": [
    {{
      "week": 1,
      "theme": "주간 테마",
      "posts": [
        {{
          "day": "월요일",
          "channel": "채널명",
          "type": "콘텐츠 유형",
          "title": "콘텐츠 제목",
          "description": "간략 설명",
          "hashtags": ["태그1", "태그2"]
        }}
      ]
    }}
  ],
  "monthly_goals": ["목표1", "목표2"],
  "key_dates": ["활용 가능한 기념일/이벤트"]
}}
"""
    return await _generate(client, system, prompt, temperature=0.8)
