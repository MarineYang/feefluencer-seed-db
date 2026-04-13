"""
Perplexity API 클라이언트 — 시장 분석 + AI 리포트 합성.
모델: sonar (웹 검색 기반 응답)
"""
import asyncio
import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PERPLEXITY_API = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar"


async def _query(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
) -> dict:
    """Perplexity sonar 모델에 단일 쿼리 실행."""
    try:
        resp = await client.post(
            PERPLEXITY_API,
            headers={
                "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            logger.warning("Perplexity API error: %s %s", resp.status_code, resp.text[:300])
            return {"data": None, "citations": []}

        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        citations = result.get("citations", [])

        # JSON 파싱 시도
        parsed = _try_parse_json(content)
        return {"data": parsed if parsed else content, "citations": citations}

    except Exception as e:
        logger.warning("Perplexity query error: %s", e)
        return {"data": None, "citations": []}


def _try_parse_json(text: str) -> dict | list | None:
    """텍스트에서 JSON 추출 시도."""
    text = text.strip()
    # ```json ... ``` 블록 추출
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


# ─── 4개 병렬 시장 분석 쿼리 ───

SYSTEM_PROMPT = (
    "당신은 한국 의료/뷰티 마케팅 시장 분석 전문가입니다. "
    "정확한 데이터와 최신 트렌드를 기반으로 분석하세요. "
    "반드시 한국어로 답변하고, 요청된 JSON 형식으로만 응답하세요."
)


async def analyze_competitors(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    address: str,
) -> dict:
    """경쟁 병원 분석."""
    services_str = ", ".join(services[:5]) if services else "성형외과"
    prompt = f"""
{address} 지역에서 "{clinic_name}"과 경쟁하는 병원/클리닉 5곳을 분석해주세요.
주요 시술: {services_str}

다음 JSON 형식으로 응답:
{{
  "competitors": [
    {{
      "name": "병원명",
      "address": "주소",
      "specialties": ["주력 시술1", "주력 시술2"],
      "online_reputation": "온라인 평판 요약 (별점, 리뷰 등)",
      "marketing_channels": ["블로그", "인스타그램", "유튜브 등"],
      "strengths": ["강점1", "강점2"],
      "weaknesses": ["약점1"]
    }}
  ],
  "market_position": "{clinic_name}의 경쟁 포지션 요약 (2-3문장)"
}}
"""
    return await _query(client, SYSTEM_PROMPT, prompt)


async def analyze_keywords(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    address: str,
) -> dict:
    """키워드 트렌드 분석."""
    services_str = ", ".join(services[:5]) if services else "성형외과"
    prompt = f"""
"{clinic_name}" ({address})의 마케팅에 활용할 네이버/구글 검색 키워드를 분석해주세요.
주요 시술: {services_str}

다음 JSON 형식으로 응답:
{{
  "primary_keywords": [
    {{
      "keyword": "검색 키워드",
      "monthly_searches": "예상 월간 검색량 (숫자)",
      "competition": "high/medium/low",
      "intent": "informational/transactional/navigational"
    }}
  ],
  "long_tail_keywords": [
    {{
      "keyword": "롱테일 키워드",
      "monthly_searches": "예상 월간 검색량",
      "opportunity": "기회 설명"
    }}
  ],
  "trending_keywords": ["최근 급상승 키워드1", "키워드2"],
  "content_topics": ["블로그/콘텐츠 주제 추천1", "주제2", "주제3"]
}}

primary_keywords 10개, long_tail_keywords 10개, trending_keywords 5개, content_topics 5개를 포함해주세요.
"""
    return await _query(client, SYSTEM_PROMPT, prompt)


async def analyze_market(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    address: str,
) -> dict:
    """시장 분석."""
    services_str = ", ".join(services[:5]) if services else "성형외과"
    prompt = f"""
한국 {services_str} 시장을 분석해주세요. 특히 {address} 지역 중심으로.

다음 JSON 형식으로 응답:
{{
  "market_size": "시장 규모 (금액 또는 설명)",
  "growth_rate": "연간 성장률",
  "trends": [
    {{
      "trend": "트렌드명",
      "description": "설명",
      "impact": "high/medium/low"
    }}
  ],
  "opportunities": ["기회1", "기회2"],
  "threats": ["위협1", "위협2"],
  "seasonal_patterns": "계절별 수요 패턴 설명"
}}

최소 5개 트렌드, 3개 기회, 3개 위협을 포함해주세요.
"""
    return await _query(client, SYSTEM_PROMPT, prompt)


async def analyze_target_audience(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    address: str,
) -> dict:
    """타겟 오디언스 분석."""
    services_str = ", ".join(services[:5]) if services else "성형외과"
    prompt = f"""
"{clinic_name}" ({address})의 {services_str} 시술에 대한 타겟 고객층을 분석해주세요.

다음 JSON 형식으로 응답:
{{
  "primary_audience": {{
    "age_range": "주 연령대",
    "gender": "성별 비율",
    "income_level": "소득 수준",
    "interests": ["관심사1", "관심사2"],
    "pain_points": ["고민1", "고민2"],
    "decision_factors": ["결정 요인1", "요인2"],
    "preferred_channels": ["정보 탐색 채널1", "채널2"]
  }},
  "secondary_audience": {{
    "age_range": "연령대",
    "gender": "성별 비율",
    "interests": ["관심사1"],
    "preferred_channels": ["채널1"]
  }},
  "customer_journey": [
    {{
      "stage": "인지/관심/비교/결정/후기",
      "description": "단계 설명",
      "key_touchpoints": ["접점1", "접점2"]
    }}
  ]
}}
"""
    return await _query(client, SYSTEM_PROMPT, prompt)


async def run_market_analysis(
    client: httpx.AsyncClient,
    clinic_name: str,
    services: list[str],
    address: str,
) -> dict:
    """4개 시장 분석 쿼리를 병렬 실행."""
    competitors, keywords, market, audience = await asyncio.gather(
        analyze_competitors(client, clinic_name, services, address),
        analyze_keywords(client, clinic_name, services, address),
        analyze_market(client, clinic_name, services, address),
        analyze_target_audience(client, clinic_name, services, address),
    )

    return {
        "competitors": competitors,
        "keywords": keywords,
        "market": market,
        "target_audience": audience,
    }


# ─── AI 리포트 합성 ───

async def synthesize_report(
    client: httpx.AsyncClient,
    clinic_name: str,
    scrape_summary: dict,
    market_analysis: dict,
) -> dict:
    """스크래핑 데이터 + 시장 분석을 종합하여 최종 마케팅 리포트 합성."""
    system = (
        "당신은 병원 마케팅 전략 컨설턴트입니다. "
        "수집된 데이터와 시장 분석을 종합하여 실행 가능한 마케팅 전략을 제시하세요. "
        "반드시 한국어로, 요청된 JSON 형식으로만 응답하세요."
    )

    prompt = f"""
다음은 "{clinic_name}" 병원에 대해 수집한 데이터입니다.

## 병원 현황
{json.dumps(scrape_summary, ensure_ascii=False, default=str)[:3000]}

## 시장 분석
{json.dumps(market_analysis, ensure_ascii=False, default=str)[:3000]}

위 데이터를 종합하여 마케팅 전략 리포트를 작성해주세요.

다음 JSON 형식으로 응답:
{{
  "executive_summary": "3-5문장 경영진 요약",
  "swot": {{
    "strengths": ["강점1", "강점2"],
    "weaknesses": ["약점1", "약점2"],
    "opportunities": ["기회1", "기회2"],
    "threats": ["위협1", "위협2"]
  }},
  "recommendations": [
    {{
      "priority": 1,
      "category": "SNS/SEO/광고/콘텐츠/브랜딩",
      "title": "추천 제목",
      "description": "구체적 실행 방안 (3-5문장)",
      "expected_impact": "기대 효과",
      "timeline": "예상 소요 기간",
      "budget_estimate": "예상 비용 범위"
    }}
  ],
  "content_strategy": {{
    "blog_topics": ["블로그 주제1", "주제2"],
    "social_media_plan": "SNS 운영 전략 요약",
    "video_ideas": ["영상 콘텐츠 아이디어1", "아이디어2"]
  }},
  "kpi_targets": [
    {{
      "metric": "KPI 지표명",
      "current": "현재 수치 (추정)",
      "target_3m": "3개월 목표",
      "target_6m": "6개월 목표"
    }}
  ]
}}

recommendations는 우선순위 순으로 최소 5개, kpi_targets는 최소 4개를 포함해주세요.
"""
    return await _query(client, system, prompt, temperature=0.4)
