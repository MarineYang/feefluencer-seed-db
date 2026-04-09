# INFINITH — Developer Handoff Document
**Version:** 2.0 | **Updated:** 2026-04-01 | **Status:** Phase 1 Backend Complete, Frontend Integration Pending

---

## 1. Architecture Overview

```
[Frontend: React + Vite + TailwindCSS]
        │
        ├── src/lib/supabase.ts (API client)
        │
        ▼
[Supabase Edge Functions (Deno)]
        │
        ├── generate-report ──── Orchestrator (Pipeline Entry)
        │       │
        │       ├── 1) scrape-website ─── Firecrawl API
        │       │       ├── /v1/scrape (구조화 JSON 추출)
        │       │       ├── /v1/map (사이트맵 탐색)
        │       │       └── /v1/search (리뷰 검색)
        │       │
        │       ├── 2) analyze-market ─── Perplexity API (sonar)
        │       │       ├── 경쟁 병원 분석
        │       │       ├── 키워드 트렌드
        │       │       ├── 시장 분석
        │       │       └── 타겟 오디언스
        │       │
        │       └── 3) AI 리포트 합성 ─── Perplexity API (sonar)
        │
        ├── enrich-channels ──── Phase 2 (Background Enrichment)
        │       ├── Instagram ─── Apify (instagram-profile-scraper)
        │       ├── Google Maps ── Apify (crawler-google-places)
        │       └── YouTube ───── Apify (youtube-channel-scraper) ⚠️ 수정 필요
        │
        ▼
[Supabase PostgreSQL]
        ├── scrape_results (스크래핑 캐시)
        └── marketing_reports (최종 리포트)
```

---

## 2. Supabase Project

| Item | Value |
|------|-------|
| **Project Ref** | `wkvjclkkonoxqtjxiwcw` |
| **Region** | Seoul (ap-northeast-2) |
| **Dashboard** | `https://supabase.com/dashboard/project/wkvjclkkonoxqtjxiwcw` |
| **API URL** | `https://wkvjclkkonoxqtjxiwcw.supabase.co` |
| **Edge Functions** | `https://wkvjclkkonoxqtjxiwcw.supabase.co/functions/v1/{function-name}` |
| **CLI** | `npx supabase` (글로벌 설치 불필요) |
| **Access Token** | `infinith-cli` (Supabase Dashboard > Account > Access Tokens) |

### Database Tables

```sql
-- supabase/migrations/20260330_create_tables.sql

scrape_results (
  id          UUID PRIMARY KEY,
  url         TEXT NOT NULL,
  clinic_name TEXT,
  data        JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ DEFAULT NOW()
)

marketing_reports (
  id            UUID PRIMARY KEY,
  url           TEXT NOT NULL,
  clinic_name   TEXT,
  report        JSONB NOT NULL DEFAULT '{}',    -- 최종 AI 리포트
  scrape_data   JSONB DEFAULT '{}',             -- 원본 스크래핑 데이터
  analysis_data JSONB DEFAULT '{}',             -- 시장 분석 데이터
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
)
```

- RLS 활성화됨. `service_role` key로 Edge Function 호출 시 전체 접근 가능
- `anon` role은 `marketing_reports` SELECT만 가능

### Supabase Secrets (Edge Functions 환경변수)

Edge Function에서 `Deno.env.get()`으로 접근:

```bash
# 시크릿 설정 명령어
npx supabase secrets set FIRECRAWL_API_KEY=<YOUR_FIRECRAWL_API_KEY>
npx supabase secrets set PERPLEXITY_API_KEY=<YOUR_PERPLEXITY_API_KEY>
npx supabase secrets set APIFY_API_TOKEN=<YOUR_APIFY_API_TOKEN>
npx supabase secrets set GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>

# 자동 제공 (설정 불필요)
# SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY
```

---

## 3. Edge Functions — 상세 스펙

### 3.1 `scrape-website`

| Item | Detail |
|------|--------|
| **파일** | `supabase/functions/scrape-website/index.ts` |
| **엔드포인트** | `POST /functions/v1/scrape-website` |
| **Auth** | `--no-verify-jwt` (인증 불필요) |
| **외부 API** | Firecrawl (`api.firecrawl.dev`) |
| **소요시간** | ~15-20s |

**Request:**
```json
{ "url": "https://viewclinic.com", "clinicName": "뷰성형외과" }
```

**Response:**
```json
{
  "success": true,
  "data": {
    "clinic": { "clinicName", "address", "phone", "services[]", "doctors[]", "socialMedia{}" },
    "siteLinks": ["url1", "url2"],
    "siteMap": ["url1", "url2"],
    "reviews": [{ "title", "url", "description" }],
    "scrapedAt": "ISO timestamp",
    "sourceUrl": "https://viewclinic.com"
  }
}
```

**처리 흐름:**
1. Firecrawl `/v1/scrape` — 메인 URL에서 병원 정보 구조화 추출 (JSON schema 정의됨)
2. Firecrawl `/v1/map` — 사이트 전체 페이지 URL 수집 (limit: 50)
3. Firecrawl `/v1/search` — "{병원명} 리뷰 평점 후기 강남언니 바비톡" 검색

---

### 3.2 `analyze-market`

| Item | Detail |
|------|--------|
| **파일** | `supabase/functions/analyze-market/index.ts` |
| **엔드포인트** | `POST /functions/v1/analyze-market` |
| **외부 API** | Perplexity (`api.perplexity.ai`, model: `sonar`) |
| **소요시간** | ~10-15s (4개 쿼리 병렬) |

**Request:**
```json
{
  "clinicName": "뷰성형외과",
  "services": ["코성형", "눈성형", "리프팅"],
  "address": "강남구",
  "scrapeData": { ... }
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "clinicName": "뷰성형외과",
    "services": [...],
    "address": "강남구",
    "analysis": {
      "competitors": { "data": {...}, "citations": [...] },
      "keywords": { "data": {...}, "citations": [...] },
      "market": { "data": {...}, "citations": [...] },
      "targetAudience": { "data": {...}, "citations": [...] }
    },
    "analyzedAt": "ISO timestamp"
  }
}
```

**4개 병렬 Perplexity 쿼리:**
1. `competitors` — 주변 경쟁 병원 5곳 (이름, 시술, 온라인 평판, 마케팅 채널)
2. `keywords` — 네이버/구글 검색 키워드 트렌드 20개
3. `market` — 시장 규모, 성장률, 트렌드 분석
4. `targetAudience` — 연령/성별/관심사/채널 분석

---

### 3.3 `generate-report` (Orchestrator)

| Item | Detail |
|------|--------|
| **파일** | `supabase/functions/generate-report/index.ts` |
| **엔드포인트** | `POST /functions/v1/generate-report` |
| **외부 API** | 내부적으로 `scrape-website` + `analyze-market` + Perplexity 호출 |
| **소요시간** | ~45s (전체 파이프라인) |

**Request:**
```json
{ "url": "https://viewclinic.com", "clinicName": "뷰성형외과" }
```

**Response:**
```json
{
  "success": true,
  "reportId": "uuid",
  "report": {
    "clinicInfo": { ... },
    "executiveSummary": "경영진 요약",
    "overallScore": 72,
    "channelAnalysis": {
      "naverBlog": { "score", "status", "posts", "recommendation" },
      "instagram": { "score", "status", "followers", "recommendation" },
      "youtube": { "score", "status", "subscribers", "recommendation" },
      "naverPlace": { "score", "rating", "reviews", "recommendation" },
      "gangnamUnni": { "score", "rating", "reviews", "recommendation" },
      "website": { "score", "issues[]", "recommendation" }
    },
    "competitors": [{ "name", "strengths[]", "weaknesses[]", "marketingChannels[]" }],
    "keywords": {
      "primary": [{ "keyword", "monthlySearches", "competition" }],
      "longTail": [{ "keyword", "monthlySearches" }]
    },
    "targetAudience": { "primary": {...}, "secondary": {...} },
    "recommendations": [{ "priority", "category", "title", "description", "expectedImpact" }],
    "marketTrends": [...]
  },
  "metadata": {
    "url": "...",
    "clinicName": "...",
    "generatedAt": "ISO timestamp",
    "dataSources": { "scraping": true, "marketAnalysis": true, "aiGeneration": true }
  }
}
```

**파이프라인 순서:**
1. `scrape-website` 호출 → 병원 데이터 수집
2. `analyze-market` 호출 → 시장 분석
3. Perplexity `sonar` 모델로 최종 리포트 JSON 합성
4. `marketing_reports` 테이블에 저장

---

### 3.4 `enrich-channels` (Phase 2 — Background)

| Item | Detail |
|------|--------|
| **파일** | `supabase/functions/enrich-channels/index.ts` |
| **엔드포인트** | `POST /functions/v1/enrich-channels` |
| **외부 API** | Apify Actors (3개 병렬) |
| **소요시간** | ~27s |

**Request:**
```json
{
  "reportId": "uuid",
  "clinicName": "뷰성형외과",
  "instagramHandle": "viewplastic",
  "youtubeChannelId": "@viewplastic",
  "address": "강남구"
}
```

**Apify Actors 사용:**

| Actor | Actor ID | 용도 | 검증 |
|-------|----------|------|------|
| Instagram Profile | `apify~instagram-profile-scraper` | 팔로워, 게시물, 바이오 | ✅ 정상 작동 (6s) |
| Google Maps | `compass~crawler-google-places` | 평점, 리뷰, 영업시간 | ✅ 정상 작동 (10s) |
| YouTube Channel | `streamers~youtube-channel-scraper` | 영상 목록, 조회수 | ⚠️ 빈 데이터 반환 — 다른 Actor 또는 YouTube Data API v3 필요 |

**동작:** 기존 `marketing_reports`의 `report` 필드에 `channelEnrichment` 객체를 추가 저장

---

## 4. Frontend 통합 가이드

### 현재 상태

| 파일 | 상태 | 설명 |
|------|------|------|
| `src/lib/supabase.ts` | ✅ 완성 | `generateMarketingReport()`, `scrapeWebsite()` 함수 |
| `src/pages/AnalysisLoadingPage.tsx` | ✅ 완성 | 실제 API 호출 + 프로그레스 UI |
| `src/hooks/useReport.ts` | ⚠️ **Mock 데이터** | `mockReport` 반환 중 — 실제 API 연동 필요 |
| `src/pages/ReportPage.tsx` (or similar) | ⚠️ 확인 필요 | `useReport()` 결과 렌더링 |

### 프론트엔드 환경변수 (`.env`)

```env
VITE_SUPABASE_URL=https://wkvjclkkonoxqtjxiwcw.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIs...
```

### API 호출 방식

Edge Functions는 `--no-verify-jwt`로 배포되어 있어 Authorization 헤더 불필요:

```typescript
// src/lib/supabase.ts
const response = await fetch(
  `${supabaseUrl}/functions/v1/generate-report`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, clinicName }),
  }
);
```

### TODO: `useReport()` 실제 연동

`AnalysisLoadingPage`에서 `/report/view-clinic`으로 navigate할 때 `location.state`에 report 데이터를 전달 중:

```typescript
navigate('/report/view-clinic', {
  replace: true,
  state: { report: result.report, metadata: result.metadata },
});
```

`useReport()` 훅에서 이 state를 받아 사용하도록 변경 필요.

### TODO: Progressive Loading (Phase 2)

```
Phase 1 (~45s): generate-report → 즉시 리포트 표시
Phase 2 (~27s): enrich-channels → 백그라운드에서 채널 데이터 보강
                → 완료 시 리포트 UI에 실시간 반영
```

구현 방식: Phase 1 리포트 렌더 후 `enrich-channels` 비동기 호출 → Supabase Realtime 또는 polling으로 업데이트 감지

---

## 5. API & Service 연결 현황

### Connected (연결 완료, 코드 구현됨)

| # | Service | 용도 | API Key 위치 | Dashboard |
|---|---------|------|-------------|-----------|
| 1 | **Firecrawl** | 웹사이트 스크래핑 (scrape/map/search) | `.env` + Supabase Secret | [Dashboard](https://www.firecrawl.dev/app) |
| 2 | **Perplexity** | 시장 분석 + 리포트 생성 (sonar) | `.env` + Supabase Secret | [API Settings](https://www.perplexity.ai/settings/api) |
| 3 | **Apify** | Instagram/Google Maps 채널 데이터 | `.env` + Supabase Secret | [Console](https://console.apify.com/) |
| 4 | **Supabase** | DB + Edge Functions + Auth | `.env` (VITE_*) | [Dashboard](https://supabase.com/dashboard/project/wkvjclkkonoxqtjxiwcw) |

### Connected (MCP로 연결, 코드 미사용)

| # | Service | 용도 | MCP Config |
|---|---------|------|-----------|
| 5 | **Gemini (nano-banana-pro)** | AI 이미지 생성/편집 | `claude_desktop_config.json` |
| 6 | **Figma MCP** | 디자인 에셋 읽기, 브랜드 변수 | VS Code Extension |
| 7 | **Slack MCP** | 팀 알림, 채널 메시징 | `claude_desktop_config.json` |
| 8 | **Notion MCP** | 문서/DB 관리 | VS Code Extension |
| 9 | **Google Drive** | 파일 저장/공유 | VS Code Extension |
| 10 | **Ahrefs** | SEO/키워드 분석 (GSC 연동) | VS Code Extension |
| 11 | **Claude in Chrome** | 브라우저 자동화 | Chrome Extension |
| 12 | **Firebase** | (미사용, 연결만) | VS Code Extension |

### Not Connected (연동 필요)

| # | Service | 용도 | 우선순위 | 문서 |
|---|---------|------|---------|------|
| 13 | **YouTube Data API v3** | 채널 통계, 영상 분석 | P0 | [Docs](https://developers.google.com/youtube/v3/getting-started) |
| 14 | **Naver Search API** | 블로그/카페/뉴스 검색 | P0 | [Developers](https://developers.naver.com/) |
| 15 | **Claude/Anthropic API** | AI 리포트 생성 (Perplexity 대체 가능) | P1 | [Docs](https://docs.anthropic.com/) |
| 16 | **Creatomate** | 템플릿 기반 영상/이미지 생성 | P1 | [API Docs](https://creatomate.com/docs/api/introduction) |
| 17 | **Instagram Graph API** | 공식 게시/인사이트 | P1 | [Docs](https://developers.facebook.com/docs/instagram-platform/) |
| 18 | **Google Search Console** | SEO 성과 추적 | P1 | [Docs](https://developers.google.com/webmaster-tools) |
| 19 | **Google Analytics 4** | 웹 트래픽 분석 | P1 | [Docs](https://developers.google.com/analytics/devguides/reporting/data/v1) |
| 20 | **Naver Place/Map API** | 플레이스 리뷰/위치 | P2 | [Naver Cloud](https://www.ncloud.com/) |
| 21 | **Google Maps Places API** | 구글 리뷰/평점 (Apify 대체중) | P2 | [Docs](https://developers.google.com/maps/documentation/places/web-service) |
| 22 | **TikTok API** | 숏폼 게시/분석 | P2 | [Docs](https://developers.tiktok.com/) |
| 23 | **Canva Connect API** | 템플릿 Autofill (Enterprise) | P2 | [Docs](https://www.canva.dev/docs/connect/) |
| 24 | **Brandfetch** | 브랜드 로고/컬러 추출 | P2 | [Docs](https://docs.brandfetch.com/) |

---

## 6. 측정된 파이프라인 타이밍

```
Phase 1: generate-report 전체 (~45초)
  ├── scrape-website   ~15-20s
  ├── analyze-market   ~10-15s (4 Perplexity 병렬)
  └── AI report 합성   ~10-15s

Phase 2: enrich-channels (~27초, 백그라운드)
  ├── Instagram (Apify)     ~6s
  ├── Google Maps (Apify)   ~10s
  └── YouTube (Apify)       ~11s (⚠️ 빈 데이터)

Total: ~72초 (사용자 체감: 45초 → 리포트 표시)
```

---

## 7. 알려진 이슈 & 해결 필요

| # | 이슈 | 상세 | 해결 방향 |
|---|------|------|---------|
| 1 | **Gemini API 429** | 프로젝트 spending cap $10 초과. `generate-report`에서 Perplexity로 대체 완료 | AI Studio에서 한도 증가 또는 Perplexity 유지 |
| 2 | **YouTube Apify 빈 데이터** | `streamers~youtube-channel-scraper` Actor가 빈 배열 반환 | YouTube Data API v3 연동 또는 다른 Apify Actor 탐색 |
| 3 | **useReport() Mock** | `useReport()` 훅이 mockReport 반환 중 | `location.state`에서 실제 데이터 읽도록 변경 |
| 4 | **JWT 미검증** | Edge Functions가 `--no-verify-jwt`로 배포 | 프로덕션 전에 Supabase Auth 연동 + JWT 검증 활성화 |
| 5 | **Instagram 핸들 자동 감지** | `scrape-website`에서 추출한 socialMedia.instagram을 `enrich-channels`에 자동 전달 필요 | `generate-report` orchestrator에서 연결 |

---

## 8. 프로젝트 구조

```
remix_-infinith---infinite-marketing/
├── src/
│   ├── components/
│   │   ├── Hero.tsx              # 랜딩 히어로 (URL 입력)
│   │   ├── icons/                # 커스텀 아이콘
│   │   └── ...
│   ├── hooks/
│   │   └── useReport.ts          # ⚠️ Mock 데이터 → 실제 연동 필요
│   ├── lib/
│   │   └── supabase.ts           # ✅ Supabase 클라이언트 + API 함수
│   ├── pages/
│   │   ├── AnalysisLoadingPage.tsx # ✅ 분석 로딩 (실제 API 호출)
│   │   └── ...
│   └── ...
├── supabase/
│   ├── functions/
│   │   ├── scrape-website/       # ✅ Firecrawl 스크래핑
│   │   ├── analyze-market/       # ✅ Perplexity 시장 분석
│   │   ├── generate-report/      # ✅ 파이프라인 오케스트레이터
│   │   └── enrich-channels/      # ✅ Apify 채널 enrichment
│   └── migrations/
│       └── 20260330_create_tables.sql  # DB 스키마
├── docs/
│   ├── API_CONNECTORS.md         # API 레지스트리 (v1.0)
│   ├── DESIGN_SYSTEM.md          # 디자인 시스템
│   └── DEVELOPER_HANDOFF.md      # 이 문서
├── .env                          # 환경변수 (Git 제외)
└── package.json
```

---

## 9. Edge Functions 배포 가이드

```bash
# 1. Supabase CLI 로그인
npx supabase login

# 2. 프로젝트 연결
npx supabase link --project-ref wkvjclkkonoxqtjxiwcw

# 3. Secrets 설정 (최초 1회)
npx supabase secrets set FIRECRAWL_API_KEY=<YOUR_FIRECRAWL_API_KEY>
npx supabase secrets set PERPLEXITY_API_KEY=<YOUR_PERPLEXITY_API_KEY>
npx supabase secrets set APIFY_API_TOKEN=<YOUR_APIFY_API_TOKEN>

# 4. 개별 함수 배포
npx supabase functions deploy scrape-website --no-verify-jwt
npx supabase functions deploy analyze-market --no-verify-jwt
npx supabase functions deploy generate-report --no-verify-jwt
npx supabase functions deploy enrich-channels --no-verify-jwt

# 5. DB 마이그레이션
npx supabase db push

# 6. 로컬 테스트
npx supabase functions serve --env-file .env
```

---

## 10. 개발 우선순위 로드맵

### Phase 1 — 즉시 (Frontend ↔ Backend 연결)

- [ ] `useReport()` 훅을 실제 API 데이터로 교체
- [ ] `generate-report` 호출 후 `enrich-channels` 자동 호출 연결
- [ ] 리포트 페이지에서 `channelEnrichment` 데이터 렌더링
- [ ] YouTube Data API v3 연동 (Apify 대체)
- [ ] 에러 핸들링 강화 (재시도, timeout, 사용자 피드백)

### Phase 2 — Content Studio

- [ ] Naver Search API 연동 (블로그/카페 검색)
- [ ] 콘텐츠 캘린더 생성 로직 (리포트 기반)
- [ ] Creatomate API 연동 (이미지/영상 생성)
- [ ] Gemini 이미지 생성 연동 (nano-banana-pro MCP)

### Phase 3 — 자동화 & 배포

- [ ] Instagram Graph API (자동 게시)
- [ ] Supabase Auth 연동 (사용자 인증)
- [ ] Edge Function JWT 검증 활성화
- [ ] Google Analytics / Search Console 연동
- [ ] 자동 리포트 스케줄링

---

## 11. 테스트 데이터

검증에 사용한 실제 병원:

| 병원 | URL | Instagram | 비고 |
|------|-----|-----------|------|
| 뷰성형외과 | `https://viewclinic.com` | `viewplastic` (14,094 followers) | 전체 파이프라인 테스트 완료 |

---

*Last updated: 2026-04-01*
