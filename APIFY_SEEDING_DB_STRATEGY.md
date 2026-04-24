# Apify 기반 인플루언서 시딩 DB 전략

**목표:** 피부과/성형외과/비만클리닉 협찬 인플루언서 5만~10만 계정을 내부 DB에 누적해, 검색·추천·CRM을 외부 API 의존 없이 운영한다.

---

## 전체 파이프라인 흐름

```
[해시태그 풀]
     │
     ▼
[Discovery]  Apify Hashtag Scraper → 핸들 목록 추출
     │        DB에 이미 있는 핸들 제거 (비용 최적화)
     │        신규 핸들만 Instaloader로 프로필 수집 (무료, Apify 폴백)
     ▼
[Triage]     팔로워 1,000+  /  게시물 6+  /  quality_flags < 2
     │        is_business=FALSE (원장/샵 계정 자동 분류 → 검색 제외)
     │
     ├── 탈락 → status='low_quality' or 'business' 로 저장 (하드 삭제 안 함)
     │
     ▼
[Enrichment] Apify Post Scraper → 최근 30개 게시물 수집
     │        시술태그 / 지역태그 / 협찬시그널 / 연락처 / ER 계산
     │        match_score 산출 (피부과/성형/비만 3개 도메인)
     │        새 해시태그 자동 발굴 → 해시태그 풀 확장
     ▼
[influencers DB]  status='active', match_score 기준 정렬
     │
     ├── [검색 API]  DB 우선 응답 → 부족 시 Apify 실시간 폴백
     │
     └── [Refresh]  hot(매일) / warm(주1회) / cold(월1회) 주기 갱신
                    프로필 스냅샷 저장 → 팔로워 증감 추적
```

---

## 현재 구현 상태 (2026-04)

| 항목 | 상태 | 위치 |
|------|------|------|
| DB 스키마 (8개 테이블) | ✅ | `db/schema.sql` |
| Discovery Job | ✅ | `pipeline/jobs/discovery.py` |
| Enrichment Job | ✅ | `pipeline/jobs/enrichment.py` |
| Refresh Job | ✅ | `pipeline/jobs/refresh.py` |
| Apify Client | ✅ | `pipeline/apify_client.py` |
| Instaloader Client (무료 폴백) | ✅ | `pipeline/instaloader_client.py` |
| 키워드 사전 + 스코어링 | ✅ | `pipeline/keywords.py` |
| APScheduler 배치 runner | ✅ | `pipeline/scheduler.py` + `api.py` |
| 검색 API (DB 우선 → Apify 폴백) | ✅ | `app/api/influencers.py` + `services/seeddb.py` |
| AI 콘텐츠 분석 (`comment_quality_score`, `ai_content_label`) | 🔴 미구현 | 컬럼만 존재 |
| 경쟁 클리닉 협찬 이력 감점 | 🔴 미구현 | 데이터 수집은 되나 match_score 미반영 |
| `follower_change_7d/30d` + `anomaly_flag` 자동 계산 | 🟡 부분 구현 | Refresh 배치에 일부만 반영 |

**스택:** PostgreSQL 16 (JSONB/GIN/조건부 UNIQUE) · Apify + Instaloader · APScheduler · Gemini 2.0 Flash · Docker Compose

---

## 데이터 모델 (8개 테이블)

### 핵심 테이블 역할

| 테이블 | 역할 |
|--------|------|
| `influencers` | 마스터 프로필. 모든 계산 결과가 여기에 집약됨 |
| `influencer_metrics_snapshots` | 시점별 팔로워/ER 스냅샷. 성장률·이상 감지에 사용 |
| `influencer_posts` | 최근 게시물. Enrichment의 분석 원본 |
| `influencer_seed_queue` | 배치 큐. Discovery → Enrichment → Refresh 작업 관리 |
| `influencer_discovery_events` | 어느 해시태그/경로로 발견됐는지 로그 |
| `seed_hashtag_pool` | 해시태그 수집 상태 관리 (고갈 감지, 자동 확장) |
| `seeding_run_logs` | 배치 실행 이력. `apify_calls_made`로 비용 추적 |
| `influencer_sponsorship_signals` | 게시물에서 감지된 클리닉/브랜드 협찬 이력 |

### influencers 주요 컬럼 구조

**기본 프로필**
`platform` · `handle` · `instagram_user_id` (upsert 실키) · `followers` · `following` · `posts_count` · `engagement_rate` · `bio` · `is_verified` · `last_posted_at`

**도메인 분석 (Enrichment에서 계산)**
- `treatment_tags` — 시술 키워드 JSON 배열 (레이저토닝, 삭센다, 쌍꺼풀 등)
- `region_tags` — 지역명 JSON 배열 (강남, 홍대 등)
- `treatment_content_ratio` — 최근 30개 중 시술 관련 게시물 비율
- `content_consistency_score` — 시술 콘텐츠 비율(70%) + 최대 연속 스트릭(30%)
- `sponsorship_ratio` — 협찬 표시 게시물 비율 (과다 시 감점)
- `has_medical_risk_flag` — 의료광고법 위반 소지 감지

**B2B 특화**
- `contact_email` / `contact_kakao` / `contact_phone` / `contact_linktree` — 바이오 파싱
- `has_contact_info` — 연락처 존재 여부 (협찬 제안 즉시 가능 여부)
- `sponsorship_intent_signal` — `explicit_dm` / `explicit_email` / `has_experience` / `none`
- `match_score_skin_clinic` / `match_score_plastic_surgery` / `match_score_obesity_clinic`
- `match_score_breakdown` — JSONB 점수 세부 분해

**운영 상태**
- `status` — `active` / `low_quality` / `business` / `stale` / `private` / `deleted`
- `follower_tier` — `nano`(1k~1만) / `micro`(1만~10만) / `mid`(10만~50만) / `macro`(50만+)
- `seed_priority` — `hot` / `warm` / `cold`
- `quality_flags` — 봇/저품질 신호 JSON 배열
- `anomaly_flag` — 팔로워 급변 감지 (7일 내 -20% 또는 30일 내 +200%)

### Queue 상태 흐름

```
pending → running → done
                 → failed_api      (재시도, attempt_count < 3)
                 → failed_private  (30일 후 재시도)
                 → failed_deleted  (수집 중단, influencer.status='deleted')
                 → skipped_fresh   (최근 수집 완료, 갱신 불필요)
```

중복 방지: `UNIQUE(platform, handle, job_type) WHERE status='pending'` — DB 레벨에서 race condition 차단

---

## 수집 전략

### A. Discovery — 신규 계정 발굴

**핵심 원칙: Apify 비용은 Hashtag Scraper에만 쓴다**

```
Hashtag Scraper (Apify, 저렴)
    → 핸들 목록 추출
    → DB에 이미 있는 핸들 제거
    → 신규 핸들만 Instaloader로 수집 (무료)
    → 실패 시 Apify Profile Scraper로 폴백
```

예시: 해시태그에서 1,000개 핸들 → 950개 기존 → **신규 50개만 프로필 수집**

**해시태그 풀 운영 규칙**
- 같은 해시태그는 7일 내 재수집 금지 (`seed_hashtag_pool.last_crawled_at` 기준)
- `new_accounts_found_last`가 전체의 5% 이하이고 `crawl_count >= 2`이면 `is_exhausted=TRUE`
- 고갈된 해시태그도 30일 후 자동 재시도 (그사이 새 게시물 쌓임)

**해시태그 자동 확장** (Enrichment와 연계, 추가 비용 없음)
Enrichment에서 수집한 게시물 캡션의 해시태그 중 도메인 관련성이 높은 것을 `seed_hashtag_pool`에 자동 추가 (`source='auto_extracted'`)

**시드 해시태그 세트**

| 도메인 | 대표 해시태그 |
|--------|-------------|
| 피부과 | `#피부과후기` `#레이저토닝` `#울쎄라후기` `#써마지후기` `#강남피부과` `#보톡스후기` `#필러후기` |
| 성형외과 | `#성형후기` `#코수술후기` `#쌍꺼풀후기` `#지방흡입후기` `#강남성형` `#압구정성형` |
| 비만클리닉 | `#비만클리닉` `#삭센다후기` `#위고비후기` `#지방분해주사` `#다이어트성공` `#강남다이어트` |

### B. Enrichment — 구조화 데이터 생성

Discovery 통과 계정에 대해 Apify Post Scraper로 최근 30개 게시물 수집 후 계산:

| 계산 항목 | 내용 |
|----------|------|
| `treatment_tags` / `region_tags` | 키워드 사전 매칭 |
| `content_consistency_score` | 시술 게시물 비율 + 연속 스트릭 |
| `recent_engagement_rate` | 최근 30일 실측 ER |
| `posts_last_30d` / `is_recently_active` | 활동성 |
| `has_medical_risk_flag` | 의료광고법 리스크 표현 감지 |
| `sponsorship_ratio` | #협찬/#ad 비율 |
| 협찬 시그널 저장 | `influencer_sponsorship_signals`에 감지된 클리닉/브랜드 |
| match_score 3종 | 피부과/성형/비만 도메인별 점수 |
| 해시태그 자동 확장 | 새 태그 → `seed_hashtag_pool` 등록 |

**우선순위:** 연락처 존재 또는 협찬 의향 감지 계정은 `priority=1`로 즉시 Enrichment

**90일 미활동:** `last_posted_at`이 90일 초과이면 `status='stale'`로 갱신 후 종료

### C. Refresh — 주기적 갱신

| 티어 | 조건 | 주기 |
|------|------|------|
| hot | 캠페인 진행 중, 조회 빈도 높음 | 매일 |
| warm | 최근 30일 내 저장/조회된 후보 | 주 1회 |
| cold | 장기 보관 후보 | 월 1회 |

매 Refresh마다 `influencer_metrics_snapshots`에 스냅샷 1행 저장 → 팔로워 증감 추적

---

## 스코어링 (match_score)

최대 1.00, 총점 = 가점 합 − 감점 합, `[0.0, 1.0]` clamp.
구현: `pipeline/keywords.py::calculate_match_score`

| 구성 요소 | 최대값 | 산출 방식 |
|-----------|--------|-----------|
| `treatment_tag_score` | +0.30 | 도메인 키워드 매칭 수 × 0.06 |
| `content_score` | +0.20 | `content_consistency_score` 또는 `treatment_content_ratio` |
| `tier_score` | +0.20 | 도메인별 티어 가중치 (아래 표) |
| `region_score` | +0.10 | `region_tags` 1개 이상 |
| `contact_score` | +0.08 | `has_contact_info = TRUE` |
| `intent_score` | +0.07 | `explicit_dm`=0.07 / `explicit_email`=0.05 / `has_experience`=0.03 |
| `active_score` | +0.05 | `is_recently_active = TRUE` |
| `sponsorship_penalty` | 감점 | `sponsorship_ratio > 0.5` 구간 |
| `risk_penalty` | −0.10 | `has_medical_risk_flag` |
| `flag_penalty` | 감점 | `quality_flags` 수 × 0.05 |
| `anomaly_penalty` | −0.10 | `anomaly_flag = TRUE` |
| `low_engagement_penalty` | −0.05 | `recent_engagement_rate < 0.01` |

**도메인별 tier_score 가중치**

| 티어 | skin_clinic | plastic_surgery | obesity_clinic |
|------|-------------|-----------------|----------------|
| nano (1k~1만) | 0.12 | 0.10 | **0.20** |
| micro (1만~10만) | **0.20** | **0.20** | 0.18 |
| mid (10만~50만) | 0.14 | 0.18 | 0.12 |
| macro (50만+) | 0.08 | 0.10 | 0.06 |

---

## Triage 필터 (`pipeline/keywords.py::passes_triage`)

```python
followers < 1_000                          → 탈락 (followers_too_low)
followers < 3_000 and ER < 0.02           → 탈락 (nano_low_engagement)
posts_count < 6                            → 탈락 (too_few_posts)
len(quality_flags) >= 2                    → 탈락 (low_quality)
is_business_account(bio, handle, ...)      → 탈락 (business)
```

탈락 계정은 `status='low_quality'` 또는 `'business'`로 **저장 유지** (조건 변경 시 재평가 가능)

**업체 계정 판단 기준 (`is_business_account`):**
bio 업체 키워드 2개 이상 / handle에 `clinic`·`피부과` 등 + bio 키워드 1개 이상 / `is_business=True` + bio 키워드 1개 이상

---

## Upsert 정책

**인플루언서:** `platform + instagram_user_id` 우선, 없으면 `platform + handle` fallback
handle 변경 감지 시 → 기존 handle을 `handle_history`에 추가 후 최신값으로 갱신

**게시물:** `platform + external_post_id` 우선, 없으면 `post_url`

---

## 배치 스케줄

| Job | 주기 | 스케줄러 |
|-----|------|---------|
| Discovery | 매주 월요일 09:00 + 5분마다(신규 해시태그 즉시) | scheduler.py + api.py |
| Enrichment | 매주 수요일 10:00 + 2분마다(priority=1 계정) | scheduler.py + api.py |
| Refresh(hot) | 매일 08:00 | 양쪽 동일 |
| Refresh(warm) | 매주 목요일 07:00 | scheduler.py |

모든 job은 `max_instances=1`로 중복 실행 방지. 실행마다 `seeding_run_logs`에 기록.

---

## 운영 원칙

1. **비용 분리** — Discovery(Instaloader 무료 + Hashtag Scraper)와 Enrichment(Post Scraper)를 별도 예산으로 관리. Enrichment는 Triage 통과 계정에만 적용 (전체의 20~30%).

2. **중복 방지** — `influencer_seed_queue`의 `UNIQUE(platform, handle, job_type) WHERE status='pending'`로 DB가 race condition 책임. `ON CONFLICT DO NOTHING` 조합.

3. **해시태그 고갈** — `new_accounts_found_last`가 5% 이하이면 `is_exhausted=TRUE`. 고갈 해시태그는 30일 후 자동 재시도. Enrichment에서 새 해시태그 자동 발굴로 풀 지속 확장.

4. **계정 상태 변화** — 비공개 전환: 30일 후 재시도. 계정 삭제: `status='deleted'` soft delete. 팔로워 급변: `anomaly_flag=TRUE` + 감점 + 운영자 검토 대상.

5. **한국 의료광고법** — `has_medical_risk_flag`(규칙 기반)로 1차 감지. 클리닉이 협찬 전 직접 확인할 수 있는 UI 필요.

6. **경쟁 클리닉 협찬 이력** — `influencer_sponsorship_signals` 테이블에 저장 중이나 match_score 감점에 미반영. 향후 "최근 3개월 내 경쟁 클리닉 협찬" 필터 구현 필요.

7. **DB 세션** — asyncpg 커넥션 풀(`pipeline/database.py`)을 FastAPI와 독립 관리. 각 cron job은 풀에서 acquire/release 자동 처리.

---

## 향후 개선 포인트

| 항목 | 현황 | 개선 방향 |
|------|------|-----------|
| AI 콘텐츠 분석 | 컬럼만 존재 | Gemini로 `comment_quality_score`, `ai_content_label` 생성 파이프라인 구현 |
| 경쟁 클리닉 감점 | 데이터 수집 중 | `influencer_sponsorship_signals` → match_score 반영 |
| 팔로워 증감 추적 | 부분 구현 | `follower_change_7d/30d` + `anomaly_flag` Refresh 배치 완전 구현 |
| match_score 최소값 필터 | 미구현 | Enrichment 완료 후 0.3 이하 계정 자동 cold tier 강등 |
