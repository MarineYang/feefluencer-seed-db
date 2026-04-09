# Apify 기반 인플루언서 시딩 DB 구현 전략

## 문서 목적

이 문서는 Apify를 활용해 피부과/성형외과/비만클리닉 협찬 인플루언서 데이터를 지속적으로 수집하고 DB에 누적하는 **시딩 DB 전용 전략**을 정리한다.
실시간 검색이 아닌, 반복 수집 파이프라인을 통해 5만~10만 계정 규모의 내부 인플루언서 자산을 구축하는 것이 목표다.

이 문서가 다루는 내용:

1. 왜 시딩 DB가 필요한가
2. 어떤 데이터 구조로 저장해야 하는가
3. 어떤 수집 전략으로 쌓아야 하는가
4. 어떤 배치 파이프라인으로 운영해야 하는가
5. 어떤 순서로 구현하는 것이 현실적인가

대상 도메인: **피부과, 성형외과, 비만클리닉** 협찬 인플루언서

---

## 왜 시딩 DB가 필요한가

실시간 검색만으로 운영하면 아래 문제가 생긴다.

1. 검색 응답 속도가 외부 API 상태에 전적으로 의존한다.
2. 같은 인플루언서를 매번 다시 긁게 되어 비용이 커진다.
3. 과거 대비 성장률, 최근 활동성, 협찬 성과 비교가 어렵다.
4. 클리닉이 저장한 후보군을 장기적으로 관리하기 어렵다.
5. 추천, 랭킹, 유사 계정 탐색 같은 기능을 만들기 어렵다.
6. 시술 카테고리별 필터링, 지역 매칭, 협찬 이력 확인이 불가능하다.

따라서 시딩 DB는 선택이 아니라 핵심 자산이다.

---

## 목표 상태

목표는 아래 흐름이다.

1. 외부에서 인플루언서 후보를 계속 발견한다.
2. 발견한 계정을 내부 DB에 upsert 한다.
3. 프로필/게시물/지표를 시점별로 저장한다.
4. 활동성이 높은 계정은 더 자주 갱신한다.
5. 검색 API는 실시간 외부 호출보다 내부 DB 조회를 우선한다.
6. 클리닉 도메인 특화 데이터(시술 태그, 지역, 협찬 이력)를 기반으로 정밀 필터링한다.

즉, 장기적으로는 "Apify 호출 -> 즉시 응답"이 아니라 아래로 바뀌어야 한다.

`Apify 수집 -> 내부 DB 저장 -> 검색/추천/CRM/캠페인 추적에 재사용`

---

## 추천 데이터 모델

시딩 DB는 아래 8개 테이블로 구성하는 것을 권장한다.

### 1. influencers

마스터 프로필 테이블이다.

필수 컬럼:

1. `platform`
2. `handle`
3. `profile_url`
4. `full_name`
5. `bio`
6. `category`
   뷰티, 성형, 피부, 다이어트, 라이프스타일 등 최상위 분류
7. `followers`
8. `following`
9. `posts_count`
10. `engagement_rate`
11. `avg_likes`
12. `avg_comments`
13. `avg_reel_plays`
14. `is_verified`
15. `is_business`
16. `profile_pic_url`
17. `external_url`
18. `discovered_via`
19. `last_scraped_at`
20. `status`
    active, stale, blocked, hidden 정도로 운영 가능

추가 권장 컬럼:

1. `instagram_user_id`
   Instagram 내부 고정 숫자 ID. handle과 달리 계정명 변경에도 유지된다.
   실제 upsert 키는 `platform + instagram_user_id` 조합을 사용해야 한다.
   handle이 바뀌어도 동일 계정으로 인식하여 중복 저장을 방지한다.

2. `handle_history`
   JSON 배열. 이전 handle 이력을 보관한다.
   예: `["old_handle_1", "old_handle_2"]`
   handle 변경 감지 시 현재 handle을 여기 추가하고 handle을 최신값으로 갱신한다.

3. `seed_priority`
   hot, warm, cold

4. `last_posted_at`
   최근 활동일

5. `follower_tier`
   팔로워 구간 분류. nano / micro / mid / macro
   의료 협찬 기준:
   - nano: 1천~1만 (신뢰도 높음, 비만클리닉 적합)
   - micro: 1만~10만 (가장 효율 좋음, 피부과/성형 핵심 타겟)
   - mid: 10만~50만 (인지도 캠페인 적합)
   - macro: 50만+ (단가 높음, ROI 불확실)

6. `treatment_tags`
   JSON 배열. 게시물 캡션/해시태그에서 추출한 시술 키워드.
   도메인별 분류:
   - 피부과: 레이저토닝, 피코레이저, 울쎄라, 써마지, 보톡스, 필러, 리프팅, 피부관리
   - 성형외과: 쌍꺼풀, 코성형, 지방흡입, 눈성형, 가슴성형, 윤곽
   - 비만클리닉: 다이어트주사, 삭센다, 위고비, 지방분해주사, 체중감량

7. `region_tags`
   JSON 배열. 지역 사전 기반 매칭으로 추출.
   예: `["서울", "강남", "강남구"]`
   피부과/성형외과는 오프라인 방문 비즈니스라 지역 매칭이 핵심이다.
   감지 소스: 바이오 텍스트, 해시태그(#강남피부과), 위치 태그 게시물

8. `treatment_content_ratio`
   최근 30개 게시물 중 시술/뷰티 관련 게시물 비율 (0.0~1.0)
   이 수치가 높을수록 클리닉 협찬 적합성이 높다.

9. `sponsorship_ratio`
   최근 30개 게시물 중 협찬 표시(#ad, #협찬, #유료광고) 게시물 비율
   과다하면 스코어 감점 요소가 된다.

10. `has_medical_risk_flag`
    Boolean. 전후 비교 사진, 효능 주장성 표현 등 한국 의료광고법 위반 소지 감지 여부.
    클리닉이 협찬 전 리스크 확인용으로 사용한다.

11. `match_score_skin_clinic`
    피부과 협찬 매칭 점수

12. `match_score_plastic_surgery`
    성형외과 협찬 매칭 점수

13. `match_score_obesity_clinic`
    비만클리닉 협찬 매칭 점수

14. `language_hint`

15. `quality_flags`
    JSON 배열. 봇/가짜 팔로워 의심 신호를 기록한다.
    감지 기준:
    - `high_following_ratio`: following/followers > 1.5 → 맞팔 계정 의심
    - `low_engagement`: engagement_rate < 0.3% → 팔로워 구매 의심
    - `low_post_count`: posts_count < 9 → 콘텐츠 부족
    - `low_reel_reach`: avg_reel_plays / followers < 0.01 → 도달률 비정상
    - `comment_quality_low`: 댓글이 이모지/단어 나열 위주, 의미 있는 대화 없음
    quality_flags가 2개 이상이면 status를 `low_quality`로 분류한다.

16. `follower_change_7d`
    7일 전 대비 팔로워 변화량.
    초기 적재 시점에는 비교할 스냅샷이 없으므로 NULL로 시작한다.
    `influencer_metrics_snapshots`에 7일 이전 row가 생긴 이후부터 계산 가능하다.

17. `follower_change_30d`
    30일 전 대비 팔로워 변화량.
    마찬가지로 초기값 NULL, 30일 후 첫 계산 가능.

18. `anomaly_flag`
    Boolean. 팔로워 이상 변화 감지 여부.
    - 7일 내 -20% 이상 감소 → 팔로워 구매 후 삭제 의심
    - 30일 내 +200% 이상 급증 → 팔로워 구매 의심
    anomaly_flag = true인 경우 스코어 감점 및 운영자 검토 대상으로 분류한다.

19. `comment_quality_score`
    댓글 신뢰도 점수 (0.0~1.0).
    단순 이모지/단어 나열 댓글 비율이 낮고, 시술/제품에 대한 실질적 질문과 대화가 많을수록 높다.
    AWS Bedrock 같은 LLM을 활용한 댓글 샘플 분석으로 산출할 수 있다.

20. `audience_fit_score`
    오디언스 적합도 점수 (0.0~1.0).
    클리닉 타겟 고객층(20~50대 여성)과 팔로워 구성의 매칭도.
    Instagram Business API 또는 게시물 댓글 분석으로 추정한다.

21. `last_graph_explored_at`
    팔로잉 그래프 탐색을 마지막으로 실행한 시점.
    30일 이상 지났고 match_score가 일정 이상인 계정을 대상으로 팔로잉 탐색 배치에서 사용한다.

22. `ai_content_label`
    LLM 기반 콘텐츠 자동 분류 결과. JSON.
    예: `{"specialty": "피부미용", "tone": "positive", "expertise_level": "high", "ad_density": "low"}`
    AWS Bedrock Claude 또는 동급 LLM으로 바이오 + 최근 게시물 샘플을 분석하여 산출한다.

### 2. influencer_metrics_snapshots

시점별 메트릭 기록용 테이블이 필요하다.

필수 컬럼:

1. `id`
2. `influencer_id`
3. `captured_at`
4. `followers`
5. `following`
6. `posts_count`
7. `avg_likes`
8. `avg_comments`
9. `avg_reel_plays`
10. `engagement_rate`
11. `estimated_reach`
12. `estimated_real_followers`

이 테이블이 있어야 성장률, 휴면 여부, 최근 변화량을 계산할 수 있다.

### 3. influencer_posts

현재 테이블을 유지하되 중복 방지 키를 강화하는 것이 좋다.

권장 컬럼:

1. `influencer_id`
2. `post_url`
3. `external_post_id`
4. `post_type`
5. `caption`
6. `likes`
7. `comments`
8. `plays`
9. `hashtags`
10. `posted_at`
11. `scraped_at`
12. `is_sponsored`
    Boolean. #ad, #협찬, #유료광고, #광고 등 감지 여부
13. `treatment_mentions`
    JSON 배열. 해당 게시물에서 감지된 시술 키워드

권장 제약:

1. `platform + external_post_id` unique
2. 외부 ID가 불안정하면 `post_url` unique

### 4. influencer_discovery_events

어떤 경로로 계정을 발견했는지 로그를 남기는 테이블이다.

필수 컬럼:

1. `influencer_id`
2. `source_type`
   hashtag, keyword_search, manual, competitor, campaign 등

3. `source_value`
   예: `성형후기`, `강남피부과추천`

4. `discovered_at`

이 로그는 나중에 어떤 키워드가 좋은 인플루언서를 많이 발굴하는지 분석하는 데 유용하다.

### 5. influencer_seed_queue

배치 수집용 큐 테이블을 따로 두는 것이 좋다.

필수 컬럼:

1. `platform`
2. `handle`
3. `priority`
4. `job_type`
   profile_refresh, posts_refresh, deep_enrich

5. `status`
   - `pending`: 실행 대기
   - `running`: 실행 중 (중복 실행 방지용)
   - `done`: 성공
   - `failed_api`: Apify 오류 → 재시도 대상
   - `failed_private`: 계정 비공개 전환 → 30일 후 재시도
   - `failed_deleted`: 계정 삭제 → 수집 중단, influencer.status='deleted'로 갱신
   - `skipped_fresh`: 최근 수집 완료 → 갱신 불필요

6. `attempt_count`
7. `scheduled_at`
8. `started_at`
9. `finished_at`
10. `last_error`

중복 방지 제약:
- `UNIQUE(platform, handle, job_type)` WHERE `status = 'pending'`
- queue 등록 전 반드시 pending 상태 중복 여부 체크
- 동일 계정이 여러 해시태그 배치에서 동시 발견되어도 queue에는 1개만 등록되어야 한다.

### 6. seed_hashtag_pool (신규)

Discovery 배치에서 사용할 해시태그 풀을 관리하는 테이블이다.
같은 해시태그를 반복 수집하면 신규 계정 발굴 효율이 급감한다.
이 테이블이 있어야 해시태그 고갈을 감지하고 순환/확장 전략을 운영할 수 있다.

필수 컬럼:

1. `hashtag`
2. `domain`
   skin_clinic, plastic_surgery, obesity_clinic, general
3. `last_crawled_at`
4. `crawl_count`
   누적 수집 횟수
5. `new_accounts_found_last`
   마지막 크롤에서 신규 발견된 계정 수
6. `total_accounts_found`
   누적 발견 계정 수
7. `is_exhausted`
   Boolean. `new_accounts_found_last`가 전체 대비 5% 이하면 true로 전환.
   exhausted 해시태그는 우선순위 최하로 밀리고, 신규 해시태그로 교체한다.
8. `source`
   manual, auto_extracted (게시물 캡션에서 자동 발굴), competitor_tag

신규 해시태그 자동 확장:
- Enrichment 배치에서 수집한 게시물의 해시태그를 분석하여 풀에 자동 추가한다.
- 기존 해시태그와 함께 등장 빈도가 높고 도메인 관련성이 높은 태그를 우선 추가한다.

### 7. seeding_run_logs (신규)

배치 실행 이력과 진행률을 추적하는 운영 로그 테이블이다.
5만~10만 규모 운영에서 파이프라인이 정상 동작하는지 확인하기 위해 반드시 필요하다.

필수 컬럼:

1. `run_id`
2. `job_type`
   discovery, profile_refresh, enrichment, post_refresh
3. `started_at` / `finished_at`
4. `total_attempted`
5. `success_count`
6. `failed_count`
7. `skipped_count`
   이미 최신 상태라 수집 생략된 수
8. `new_accounts_found`
   이번 배치에서 신규 등록된 인플루언서 수
9. `apify_calls_made`
   Apify API 실제 호출 횟수
10. `error_summary`
    JSON. 오류 유형별 집계. 예: `{"failed_private": 12, "failed_api": 3}`
11. `db_total_after`
    배치 완료 후 전체 DB 인플루언서 수 (누적 현황 파악용)

### 8. influencer_sponsorship_signals (신규)

어떤 클리닉/브랜드와 협찬 이력이 있는지 감지 로그를 남기는 테이블이다.
클리닉 입장에서 "이 인플루언서가 최근 경쟁 클리닉과 협찬했는가"는 매우 중요한 정보다.

필수 컬럼:

1. `influencer_id`
2. `detected_brand`
   게시물에서 감지된 클리닉/브랜드명. 예: `강남 OO피부과`, `OO성형외과`
3. `signal_type`
   hashtag_mention, caption_mention, account_tag
4. `detected_at`
5. `post_url`

이 데이터가 있어야 "최근 3개월 내 경쟁 클리닉 협찬 없음" 필터가 가능하다.

---

## 도메인 특화 시드 해시태그 세트

Discovery 배치가 실제로 협찬 가능한 인플루언서를 발굴하려면,
일반 뷰티 해시태그가 아닌 도메인 특화 해시태그로 수집해야 한다.

### 피부과 협찬 발굴용

```
#피부과후기 #피부과추천 #피부관리일기 #피부과가자
#레이저토닝 #피코레이저 #울쎄라후기 #써마지후기
#리프팅후기 #보톡스후기 #필러후기 #피부개선
#강남피부과 #홍대피부과 #신촌피부과 #분당피부과
```

### 성형외과 협찬 발굴용

```
#성형후기 #성형일기 #성형변신 #성형인증
#코수술후기 #쌍꺼풀후기 #지방흡입후기 #눈성형후기
#강남성형 #압구정성형 #성형외과추천
```

### 비만클리닉 협찬 발굴용

```
#비만클리닉 #다이어트주사 #삭센다후기 #위고비후기
#지방분해주사 #체중감량후기 #다이어트일기
#살빠졌어요 #다이어트성공 #비만치료
#강남다이어트 #강남비만클리닉
```

---

## 팔로워 티어 전략

의료/뷰티 협찬에서의 실제 전환율 특성에 맞게 티어를 정의한다.

| 티어 | 팔로워 구간 | 특성 | 주요 적합 도메인 |
|------|------------|------|----------------|
| nano | 1천~1만 | 신뢰도 높음, 팔로워와 친밀감 강함 | 비만클리닉, 동네 피부과 |
| micro | 1만~10만 | 효율 최상, 비용 대비 전환 우수 | 피부과, 성형외과 핵심 |
| mid | 10만~50만 | 브랜딩/인지도 캠페인 | 대형 성형외과, 체인 클리닉 |
| macro | 50만+ | 인지도는 높으나 ROI 불확실 | 선택적 활용 |

---

## 추천 수집 전략

시딩 DB는 한 번 대량 수집하고 끝나는 구조가 아니라, "발견", "보강", "갱신" 3개 흐름으로 나눠야 한다.

### A. Discovery 수집

새로운 계정을 계속 발견하는 단계다.
이 단계의 목적은 "후보군을 넓게 확보"하는 것이다.

#### A-1. Apify 비용 최적화 원칙 (핵심)

같은 해시태그로 반복 수집하면 이미 DB에 있는 계정이 대부분이라 Profile Scraper 비용이 낭비된다.
아래 순서를 반드시 지켜야 한다.

```
Hashtag Scraper 실행 ($2.30/1K - 저렴, 자유롭게 사용 가능)
    ↓
핸들 목록 추출
    ↓
DB에 이미 있는 핸들 제거 (SELECT 조회)
    ↓
Profile Scraper는 신규 핸들만 호출 ($2.30/1K - 신규만)
```

예시: Hashtag로 1,000개 핸들 수집 → DB에 950개 이미 존재 → Profile Scraper는 50개만 호출
→ 비용이 1/20로 줄어든다.

#### A-2. 해시태그 소스 (초기)

1. 도메인 특화 시드 해시태그 세트 (위 섹션 참고)
2. 클리닉 카테고리별 핵심 키워드 기반 수집
3. 경쟁 클리닉 계정 태그 기반 수집
4. 운영자가 수동으로 넣은 계정

#### A-3. 해시태그 시간 윈도우 재사용

Instagram Hashtag Scraper는 **최신 게시물** 순으로 가져온다.
같은 해시태그라도 7~14일 후에 다시 돌리면 올린 사람이 달라지므로 신규 계정이 나온다.

운영 정책:
- `seed_hashtag_pool.last_crawled_at` 기준으로 **7일 이내 크롤된 해시태그는 건너뜀**
- 동일 해시태그의 재수집 간격은 최소 7일 유지
- `new_accounts_found_last`가 전체의 5% 이하로 떨어지면 `is_exhausted = true`로 전환

#### A-4. 게시물 해시태그/멘션 자동 확장 (추가 비용 없음)

Enrichment 단계에서 이미 게시물을 수집하므로, 추가 Apify 비용 없이 해시태그 풀을 자동으로 넓힐 수 있다.

처리 방식:
```
게시물 캡션에서 해시태그 전체 추출
    ↓
도메인 관련성 판단 (treatment_keywords 사전과 교차 확인)
    ↓
seed_hashtag_pool에 없는 태그면 자동 추가 (source = 'auto_extracted')
    ↓
다음 Discovery 배치에서 자연스럽게 활용
```

예시: "#울쎄라후기" 게시물 수집 중 "#써마클" "#피코토닝" 같은 새 태그 발굴 → 자동으로 풀에 추가

#### A-5. 팔로잉 그래프 탐색 (향후 검토)

> **현재 단계에서는 미구현. Apify Following Scraper Actor의 안정성 검증 후 도입 여부 결정.**

같은 도메인의 인플루언서들은 서로를 팔로우하는 경향이 강하다.
이미 DB에 있는 검증된 인플루언서의 팔로잉 목록을 수집하면 비슷한 계정을 대량으로 발굴할 수 있다.

처리 방식 (도입 시):
```
DB에서 match_score 상위 N명 선정 (검증된 계정)
    ↓
Instagram Following Scraper 호출 (팔로잉 목록 수집)
    ↓
DB에 없는 신규 핸들 추출
    ↓
Profile Scraper → upsert
```

운영 정책 (도입 시):
- 팔로잉 탐색 대상은 match_score 상위 계정 중 `last_graph_explored_at`이 30일 이상 지난 계정
- 한 번에 팔로잉 전체를 수집하면 비용 과다 → 팔로잉 상위 200명만 수집
- `influencers` 테이블에 `last_graph_explored_at` 컬럼 추가 필요

보류 이유:
- 현재 사용 가능한 Following Scraper Actor(`louisdeconinck/instagram-following-scraper`)가 평점 2.8/5, 이슈 4개로 안정성이 낮음
- 초기 단계에서는 해시태그 기반 Discovery(A-1~A-4)만으로 충분히 운영 가능
- DB가 어느 정도 쌓인 이후 안정적인 Actor 확보 시 추가 도입

### B. Enrichment 수집

발견한 계정을 실제 운영 가능한 데이터로 만드는 단계다.

Apify로 아래를 수집한다.

1. 프로필 정보
2. 최근 게시물 (최소 30개, 협찬 비율 계산을 위해)
3. 최근 릴스
4. 평균 반응 수치
5. 최근 업로드일
6. 카테고리 분류용 텍스트

수집 후 아래를 계산해 저장한다.

1. `treatment_tags` 추출 (시술 키워드 사전 매칭)
2. `region_tags` 추출 (지역명 사전 매칭)
3. `treatment_content_ratio` 계산
4. `sponsorship_ratio` 계산
5. `has_medical_risk_flag` 감지
6. `follower_tier` 분류
7. `influencer_sponsorship_signals` 감지 및 저장
8. 도메인별 match_score 계산

이 단계의 목적은 "검색/필터링/추천이 가능한 구조화 데이터"를 만드는 것이다.

### C. Refresh 수집

이미 저장된 계정을 주기적으로 갱신하는 단계다.

모든 계정을 같은 주기로 갱신하면 비효율적이므로 티어를 나눈다.

1. Hot
   최근 검색량 높음, 캠페인 진행 중, 즐겨찾기 많이 됨
   1일 1회 또는 3일 1회

2. Warm
   최근 30일 내 조회 또는 저장된 후보
   1주 1회

3. Cold
   장기 보관 후보
   1개월 1회

---

## 추천 구현 구조

실행 구조는 API와 배치를 분리하는 것이 맞다.

### 1. API 레이어

사용자 검색 요청을 받는다.

단기적으로는 아래 방식이 현실적이다.

1. 내부 DB 우선 조회
2. 결과가 부족하면 Apify 실시간 호출
3. 실시간 호출 결과도 DB에 저장

즉, 검색도 결국 시딩을 돕는 경로가 되어야 한다.

### 2. Seeder Service 레이어

별도 서비스 모듈이 필요하다.

핵심 역할:

1. discovery job 실행
2. profile refresh 실행
3. post refresh 실행
4. DB upsert
5. snapshot 저장
6. queue 상태 관리
7. treatment_tags / region_tags / sponsorship_signals 추출 및 저장

### 3. Job Runner 레이어

주기 수집을 담당한다.

초기에는 복잡한 메시지 큐 없이도 가능하다.

예시:

1. 관리용 내부 endpoint
2. cron 기반 실행
3. 이후 Celery, RQ, Dramatiq 같은 워커로 분리

초기 MVP에서는 cron 또는 단순 스케줄러로도 충분하다.

---

## Upsert 정책

시딩 DB는 중복 저장보다 일관된 upsert가 중요하다.

### 인플루언서 upsert 키

기본은 `platform + instagram_user_id`를 사용한다.
Instagram 계정명(handle)은 언제든 변경될 수 있어 upsert 키로 부적합하다.
`instagram_user_id`를 수집하지 못한 초기에는 `platform + handle`로 fallback한다.

handle 변경 감지 정책:
1. 수집된 `instagram_user_id`가 기존 DB row의 것과 일치하지만 handle이 다를 경우
2. 기존 handle을 `handle_history` JSON 배열에 추가
3. `handle` 컬럼을 최신값으로 갱신

일반 upsert 정책:
1. 이미 있으면 프로필 최신값 반영
2. 없으면 신규 생성
3. `last_scraped_at` 갱신
4. 수집 출처는 discovery event로 별도 저장
5. `treatment_tags`, `region_tags`, `follower_tier` 재계산 및 갱신
6. `follower_change_7d`, `follower_change_30d` 재계산 및 `anomaly_flag` 갱신
7. `quality_flags` 재평가

### 게시물 upsert 키

가능하면 `external_post_id`를 사용한다.

없으면 `post_url` 기준으로 처리한다.

정책:

1. 이미 있으면 수치만 업데이트
2. 없으면 신규 생성
3. 삭제 여부는 즉시 hard delete 하지 말고 soft 상태를 고려
4. `is_sponsored`, `treatment_mentions` 매 갱신 시 재계산

### 스냅샷 저장 정책

프로필 refresh가 성공할 때마다 무조건 1행 저장하는 것이 좋다.

이유:

1. 팔로워 추이 분석 가능
2. 최근 성장률 계산 가능
3. 캠페인 전후 비교 가능

---

## 추천 배치 흐름

### 배치 1. Seed Discovery Job (해시태그 기반)

입력:

1. `seed_hashtag_pool`에서 `is_exhausted=False` AND `last_crawled_at < 7일 전` 해시태그

처리:

1. Hashtag Scraper 호출 → 핸들 목록 추출
2. **DB에 이미 있는 핸들 제거** (비용 최적화 핵심)
3. 신규 핸들만 Profile Scraper 배치 호출
4. influencer upsert
5. discovery event 저장
6. enrichment queue 등록
7. `seed_hashtag_pool` 갱신 (`last_crawled_at`, `new_accounts_found_last`)
8. `new_accounts_found_last`가 5% 이하면 `is_exhausted = true`

### 배치 1-B. Graph Discovery Job (팔로잉 그래프 기반) — 향후 검토

> **현재 미구현. Following Scraper Actor 안정성 검증 후 도입.**

입력:

1. DB에서 match_score 상위 계정 중 `last_graph_explored_at < 30일 전`인 계정 (최대 20명/배치)

처리 (도입 시):

1. Instagram Following Scraper 호출 (계정당 팔로잉 상위 200명)
2. DB에 없는 신규 핸들 추출
3. Profile Scraper 배치 호출
4. influencer upsert
5. discovery event 저장 (`source_type = 'graph_traversal'`)
6. enrichment queue 등록
7. `last_graph_explored_at` 갱신

### 배치 1-C. Hashtag Auto-Expand Job (게시물 해시태그 자동 확장)

이 배치는 Enrichment 배치와 함께 실행되며 추가 Apify 비용이 없다.

처리:

1. 수집된 게시물 캡션에서 해시태그 전체 추출
2. treatment_keywords 사전과 교차 확인 → 도메인 관련성 판단
3. `seed_hashtag_pool`에 없는 태그면 자동 추가 (`source = 'auto_extracted'`)
4. 동시에 게시물에서 @멘션된 계정도 추출 → 신규 핸들이면 Profile 수집 queue 등록

### 배치 2. Profile Refresh Job

입력:

1. `influencer_seed_queue`의 `profile_refresh`

처리:

1. batch profile scrape
2. `influencers` 업데이트
3. `influencer_metrics_snapshots` 저장
4. stale 여부 계산

### 배치 3. Post Refresh + Enrichment Job

입력:

1. hot/warm tier 계정
2. 캠페인 연결 계정

처리:

1. posts scrape (최소 30개)
2. reels scrape
3. 게시물 upsert
4. 최근 업로드일 갱신
5. 게시물 기반 평균 지표 재계산
6. `treatment_tags` 추출 및 갱신
7. `region_tags` 추출 및 갱신
8. `treatment_content_ratio` 재계산
9. `sponsorship_ratio` 재계산
10. `has_medical_risk_flag` 감지
11. `influencer_sponsorship_signals` 감지 및 저장
12. 도메인별 match_score 재계산

---

## 대규모 수집 설계 (5만~10만 규모)

단순히 배치를 돌리는 것으로는 이 규모에 도달하기 어렵다.
속도, 비용, 품질을 함께 설계해야 한다.

### 수집 속도 추산

현재 Apify 프로필 배치가 50명/70초 기준:
- 10만명 프로필 수집 = 2,000 배치 = 약 39시간
- 하루 8시간 운영 시 약 5일 소요 (단일 워커 기준)
- 병렬 워커 3개 운영 시 약 1.5~2일 단축 가능

게시물 Enrichment는 별도 단계로 분리해야 비용을 통제할 수 있다.

### 수집 단계 분리 전략

전체 계정에 게시물 수집까지 다 하면 비용이 수배 증가한다.
아래처럼 단계를 나누어 Enrichment 대상을 제한한다.

```
1단계 (Discovery):   프로필만 수집 → 전체 후보
2단계 (Triage):      quality_flags + follower_tier + follower_count 기준으로 필터
3단계 (Enrichment):  Triage 통과 계정만 게시물 수집 + AI 분석 + 태깅
```

Enrichment는 전체의 20~30%에만 적용해도 충분히 운영 가능하다.

### Apify 비용 예산 설계

월별 배치 계획을 세우고 `seeding_run_logs`의 `apify_calls_made`로 실사용량을 추적한다.
Discovery 배치 vs Enrichment 배치를 별도 예산으로 관리한다.

### DB 인덱스 전략

10만 행에서 복합 필터링 성능을 보장하려면 인덱스를 사전에 설계해야 한다.
**MySQL**을 사용하는 경우 PostgreSQL 전용 문법(GIN, 조건부 UNIQUE 등)은 사용 불가하다.

```sql
-- 기본 upsert 키
UNIQUE (platform, instagram_user_id)
UNIQUE (platform, handle)

-- 갱신 대상 조회용
INDEX (last_scraped_at)
INDEX (seed_priority, status)

-- 검색/필터링용
INDEX (follower_tier, status)
INDEX (match_score_skin_clinic)
INDEX (match_score_plastic_surgery)
INDEX (match_score_obesity_clinic)
```

**JSON 컬럼 인덱스 (MySQL 방식)**

MySQL은 GIN 인덱스를 지원하지 않는다.
`treatment_tags`, `region_tags` 같은 JSON 배열 필터링이 필요하면 아래 중 하나를 선택한다.

방법 1 (권장): Generated Column + INDEX
```sql
ALTER TABLE influencers
  ADD COLUMN treatment_tags_str TEXT GENERATED ALWAYS AS (JSON_UNQUOTE(treatment_tags)) STORED,
  ADD FULLTEXT INDEX (treatment_tags_str);
```

방법 2: 별도 정규화 테이블 분리
```sql
-- influencer_treatment_tags 테이블
-- influencer_id, tag → INDEX(tag)
-- JOIN으로 필터링
```

초기에는 방법 2가 더 간단하고 유연하다. DB 규모가 커지면 재검토한다.

**Queue 중복 방지 (MySQL 방식)**

MySQL은 `WHERE status = 'pending'` 조건부 UNIQUE 인덱스를 지원하지 않는다.
대신 application level에서 아래 로직으로 처리한다.

```python
# queue 등록 전 항상 이 체크를 먼저 수행
existing = db.query(SeedQueue).filter_by(
    platform=platform,
    handle=handle,
    job_type=job_type,
    status="pending"
).first()
if not existing:
    db.add(SeedQueue(...))
    db.commit()
```

---

## AI 기반 콘텐츠 분석 (AWS Bedrock 참고)

피처링 사례(AWS Bedrock 활용)에서 확인된 핵심 인사이트를 반영한다.
단순 지표 계산을 넘어 LLM으로 콘텐츠 품질과 적합성을 평가하는 레이어가 필요하다.

### 적용 포인트

1. **콘텐츠 전문성 평가**
   바이오 + 최근 게시물 샘플 5~10개를 LLM에 입력하여 아래를 자동 판단한다.
   - 시술/뷰티 관련 전문성 수준 (high / mid / low)
   - 콘텐츠 톤 (정보 제공형 / 광고형 / 일상공유형)
   - 광고 밀도 평가 (과도한 협찬 계정 감지)
   결과는 `ai_content_label` JSON으로 저장한다.

2. **댓글 신뢰도 분석**
   이모지/단어 나열 댓글이 아닌, 시술 결과에 대한 실질적 질문과 대화가 많은 계정이 진짜 영향력이 있다.
   게시물 댓글 샘플을 LLM으로 분석하여 `comment_quality_score`를 산출한다.

3. **before-after 콘텐츠 자동 감지**
   의료광고법 리스크인 전후 비교 사진 포함 게시물을 LLM + 이미지 캡션 분석으로 감지한다.
   `has_medical_risk_flag` 감지 정확도를 높이는 데 활용한다.

4. **부작용 투명성 지표**
   시술 결과를 과장 없이 솔직하게 공유하는 계정이 의료 협찬에서 신뢰도가 높다.
   부작용/단점 언급 여부를 LLM으로 감지하여 `ai_content_label`에 포함한다.

### AI 분석 적용 우선순위

LLM 분석은 비용이 발생하므로, 아래 순서로 적용한다.

1. Triage 통과 계정 전체에 콘텐츠 전문성 평가 (가벼운 분석)
2. match_score 상위 30%에 댓글 신뢰도 분석 (무거운 분석)
3. has_medical_risk_flag 후보에만 before-after 감지 (정밀 분석)

---

## 추천 필터/스코어링

시딩 DB는 단순 저장보다 "좋은 후보를 위로 올리는 것"이 중요하다.
도메인 특화 규칙 기반 점수로 시작하고, ML은 나중에 적용한다.

### 공통 기본 점수 요소

1. 팔로워 규모 (log 스케일 적용)
2. 최근 30일 활동 여부
3. 평균 좋아요/댓글
4. ER
5. 광고성 과다 여부 (`sponsorship_ratio` 기반 감점)
6. 지역성 단서 존재 여부
7. `comment_quality_score` 가중치 (진정성 있는 팔로워 신호)
8. `audience_fit_score` 가중치 (클리닉 타겟 고객층 매칭도)
9. `quality_flags` 2개 이상이면 즉시 감점 및 low_quality 처리
10. `anomaly_flag` = true이면 감점

### 피부과 협찬 매칭 점수 (`match_score_skin_clinic`)

1. `treatment_tags`에 레이저/리프팅/보톡스/필러 포함 여부 가중치
2. `treatment_content_ratio` 가중치
3. micro 티어 가중치
4. `region_tags` 매칭 (클리닉 지역 기준)
5. `has_medical_risk_flag` 감점

### 성형외과 협찬 매칭 점수 (`match_score_plastic_surgery`)

1. `treatment_tags`에 쌍꺼풀/코성형/지방흡입 포함 여부 가중치
2. 성형 후기 해시태그 사용 빈도
3. micro / mid 티어 가중치
4. `region_tags` 매칭
5. 경쟁 클리닉 최근 협찬 이력 감점 (`influencer_sponsorship_signals` 기반)

### 비만클리닉 협찬 매칭 점수 (`match_score_obesity_clinic`)

1. `treatment_tags`에 다이어트주사/삭센다/위고비/지방분해 포함 여부 가중치
2. 체중감량 관련 콘텐츠 비율
3. nano / micro 티어 가중치
4. 팔로워와의 친밀감 (댓글 수 / 좋아요 비율)
5. `has_medical_risk_flag` 감점

---

## 구현 순서 제안

시딩 DB 파이프라인을 처음 구축하는 경우 아래 순서가 현실적이다.

### 1단계. upsert 함수 작성

필요 작업:

1. influencer upsert 함수 작성 (platform + instagram_user_id 기준)
2. posts upsert 함수 작성
3. metrics snapshot 저장 함수 작성

### 2단계. 스냅샷 테이블 + 클리닉 특화 컬럼 추가

추이 분석과 도메인 필터링을 위해 필요하다.

필요 작업:

1. `influencer_metrics_snapshots` 모델 추가
2. `influencers` 테이블에 `treatment_tags`, `region_tags`, `follower_tier`, `treatment_content_ratio`, `sponsorship_ratio`, `has_medical_risk_flag`, 도메인별 match_score 컬럼 추가
3. DB 마이그레이션 적용

### 3단계. seed queue + sponsorship_signals 추가

배치 수집과 협찬 이력 추적을 위해 필요하다.

필요 작업:

1. `influencer_seed_queue` 모델 추가
2. `influencer_sponsorship_signals` 모델 추가
3. 우선순위, 재시도, 실패 기록 추가

### 4단계. Enrichment 로직 추가

수집 후 도메인 특화 데이터를 계산하는 로직이 필요하다.

필요 작업:

1. 시술 키워드 사전 정의 (피부과 / 성형외과 / 비만클리닉)
2. 지역명 사전 정의
3. `treatment_tags` 추출 함수
4. `region_tags` 추출 함수
5. `sponsorship_ratio`, `treatment_content_ratio` 계산 함수
6. `has_medical_risk_flag` 감지 함수
7. 도메인별 match_score 계산 함수

### 5단계. 배치 runner 추가

초기에는 단순 cron으로 충분하다.

필요 작업:

1. 관리용 내부 endpoint 작성
2. 도메인별 시드 해시태그로 discovery batch 실행
3. enrichment batch 실행
4. refresh batch 실행

### 6단계. 검색 API를 DB 우선 구조로 전환

장기적으로는 실시간 Apify 호출 비중을 낮춰야 한다.

구조:

1. 내부 DB 검색 (treatment_tags, region_tags, follower_tier, match_score 기반)
2. 결과 부족 시 외부 수집
3. 외부 수집 결과 저장
4. 저장된 결과 재노출

---

## 권장 모듈 구성

시딩 DB 파이프라인은 아래 모듈 단위로 분리하는 것을 권장한다.

**서비스 레이어**

1. `influencer_seed` — 시딩 배치 orchestration (discovery / queue 처리 / refresh)
2. `influencer_store` — influencer, post, snapshot, sponsorship_signals upsert
3. `influencer_enricher` — treatment_tags / region_tags / match_score 계산
4. `influencer_quality` — 봇/가짜 팔로워 감지, anomaly_flag, quality_flags 계산
5. `influencer_ai_analyzer` — LLM 기반 콘텐츠 분석 (comment_quality_score, ai_content_label, has_medical_risk_flag 정밀 감지)

**데이터 모델**

6. `influencer_snapshot` — metrics snapshot 테이블
7. `influencer_seed_queue` — 배치 큐 테이블
8. `influencer_sponsorship_signal` — 협찬 신호 감지 로그 테이블
9. `seed_hashtag_pool` — 해시태그 풀 및 고갈 관리 테이블
10. `seeding_run_log` — 배치 실행 이력 및 진행률 로그 테이블

**데이터 사전**

11. `treatment_keywords` — 도메인별 시술 키워드 사전 (피부과 / 성형외과 / 비만클리닉)
12. `region_keywords` — 지역명 사전

**관리 API**

13. `admin_seed` — 수동 실행 및 현황 조회 endpoint

---

## 운영 관점의 주의점

### 1. 비용 관리

Apify 호출은 누적 비용 구조다. Discovery(프로필만)와 Enrichment(게시물+분석)를 명확히 분리하고, Enrichment는 Triage 통과 계정에만 적용한다. `seeding_run_logs`의 `apify_calls_made`로 실사용량을 모니터링한다.

### 2. 중복 처리

같은 handle이 여러 해시태그에서 반복 발견된다. discovery event는 여러 개여도 되지만 influencer master는 하나여야 한다. upsert 키는 `platform + instagram_user_id`를 우선 사용한다. queue에는 UNIQUE 제약으로 중복 등록을 방지한다.

### 3. Handle 변경 처리

Instagram 계정명은 언제든 바뀔 수 있다. `instagram_user_id`를 기본 키로 사용하고, handle 변경이 감지되면 `handle_history`에 기록한 뒤 최신값으로 갱신한다.

### 4. 계정 상태 변화 처리

비공개 전환(`failed_private`): 30일 후 재시도. 계속 비공개면 status='private'.
계정 삭제(`failed_deleted`): 수집 중단, influencer.status='deleted'. soft delete로 보관.
팔로워 이상 감지(`anomaly_flag`): 스코어 감점 + 운영자 검토 대상 분류.

### 5. 신선도 관리

모든 계정을 같은 주기로 갱신하면 비효율적이다. Hot/Warm/Cold 티어로 나누고, 캠페인 연결 여부와 조회 빈도가 티어 결정 기준이 된다.

### 6. 해시태그 고갈 관리

같은 해시태그를 반복 수집하면 신규 발굴 효율이 급감한다. `seed_hashtag_pool`의 `new_accounts_found_last`를 모니터링하고, `is_exhausted` 상태가 된 해시태그는 교체한다. 게시물 캡션에서 새 해시태그를 자동 발굴하여 풀을 지속적으로 확장한다.

### 7. 추적 가능성

왜 이 인플루언서가 추천됐는지 설명 가능한 데이터가 필요하다. source keyword, match type, score breakdown, ai_content_label을 함께 남긴다.

### 8. 한국 의료광고법 준수

시술 전후 비교 사진, 효능 주장 등의 콘텐츠는 의료법상 광고로 사용할 수 없다. `has_medical_risk_flag`로 1차 규칙 기반 감지 후, LLM 분석으로 정밀 검토한다. 클리닉이 협찬 전 직접 확인할 수 있는 UI가 필요하다.

### 9. 경쟁 클리닉 협찬 이력

같은 지역의 경쟁 클리닉과 최근 협찬한 인플루언서를 걸러내는 것은 클리닉 플랫폼의 핵심 가치다. `influencer_sponsorship_signals` 테이블이 이를 가능하게 한다.

### 10. 봇/저품질 계정 관리

`quality_flags` 기준으로 저품질 계정을 status='low_quality'로 분류한다. 완전 삭제하지 말고 보관하되 검색 결과에서는 제외한다. 기준이 바뀌면 재평가가 가능하기 때문이다.

### 11. instagram_user_id 수집 가능 여부 사전 확인 필요

`instagram_user_id`를 upsert 실제 키로 사용하는 전략은 Apify Instagram Profile Scraper가 이 값을 반환해야 작동한다.
구현 전에 실제 Scraper 응답에 `id` 또는 `userId` 필드가 포함되어 있는지 반드시 확인한다.
반환하지 않을 경우 `platform + handle`이 주 upsert 키가 되고, handle 변경 감지는 불가능하다.
이 경우 `handle_history`, `last_graph_explored_at` 등 관련 컬럼의 필요성도 재검토한다.

### 12. APScheduler cron job 내 DB 세션 관리

APScheduler cron job은 FastAPI의 request lifecycle 밖에서 실행된다.
따라서 `Depends(get_db)` 방식을 사용할 수 없다.
cron job 함수 내부에서 반드시 아래 패턴으로 세션을 직접 관리해야 한다.

```python
from app.core.database import SessionLocal

async def discovery_cron_job():
    db = SessionLocal()
    try:
        await run_discovery_job(db)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Discovery cron failed: {e}")
    finally:
        db.close()
```

이 패턴을 누락하면 세션이 닫히지 않아 DB 커넥션 풀이 고갈된다.

### 13. Triage 처리 위치 명시

"대규모 수집 설계"에서 Discovery → Triage → Enrichment 3단계를 정의했지만,
Triage는 별도 배치가 아니라 **배치 1(Discovery) 완료 직후 enrichment queue 등록 시점**에 인라인으로 처리한다.

```python
# upsert 후 enrichment queue 등록 시 Triage 조건 체크
if (
    influencer.follower_tier in ["micro", "mid", "macro"]  # nano 제외 또는 포함 결정
    and len(influencer.quality_flags or []) < 2             # 저품질 제외
    and influencer.followers >= 1000                         # 최소 팔로워
):
    enqueue_enrichment(db, influencer)
```

Triage 조건은 운영 중 조정 가능하며, 조건 변경 시 기존 cold 계정을 재평가하는 배치도 고려한다.

---

## 최종 의견

시딩 DB의 핵심은 "검색 서비스"를 "데이터 파이프라인"으로 확장하는 것이다.

피부과/성형외과/비만클리닉 협찬 플랫폼으로 가려면 일반 인플루언서 DB가 아니라, **클리닉 도메인에 특화된 구조화 데이터**가 핵심 자산이 된다.

5만~10만 규모 수집을 목표로 한다면, 가장 먼저 해야 할 일은 아래 6가지다.

1. 검색 결과 DB 저장 (upsert 키: platform + instagram_user_id)
2. 메트릭 스냅샷 저장
3. 시술 키워드 / 지역 키워드 사전 정의 및 태깅
4. 협찬 신호 감지 구조 추가
5. Queue 중복 방지 제약 + 계정 상태별 실패 처리 세분화
6. seed_hashtag_pool + seeding_run_logs 로 수집 진행률 모니터링

이 여섯 가지가 되면 그다음부터는 봇 필터링, AI 콘텐츠 분석, 검색 품질 개선, 추천, CRM, 캠페인 추적, 경쟁 클리닉 필터까지 모두 같은 데이터 자산 위에서 확장할 수 있다.

대규모 수집에서 핵심 원칙 2가지:
- **Discovery(프로필만)와 Enrichment(게시물+AI분석)는 반드시 분리** → 비용 통제
- **upsert 키는 instagram_user_id** → 중복/handle 변경 문제를 근본적으로 해결
