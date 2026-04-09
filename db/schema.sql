-- ============================================================
-- Influencer Seeding DB Schema (PostgreSQL)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 1. influencers (마스터 프로필)
-- ============================================================
CREATE TABLE influencers (
  id                           UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
  platform                     VARCHAR(20)  NOT NULL DEFAULT 'instagram',
  instagram_user_id            VARCHAR(50),
  handle                       VARCHAR(100) NOT NULL,
  handle_history               JSONB        NOT NULL DEFAULT '[]',
  profile_url                  TEXT,
  full_name                    TEXT,
  bio                          TEXT,
  category                     VARCHAR(50),

  -- 기본 지표
  followers                    INTEGER,
  following                    INTEGER,
  posts_count                  INTEGER,
  engagement_rate              NUMERIC(6,4),
  avg_likes                    NUMERIC(12,2),
  avg_comments                 NUMERIC(10,2),
  avg_reel_plays               NUMERIC(12,2),
  is_verified                  BOOLEAN      NOT NULL DEFAULT FALSE,
  is_business                  BOOLEAN      NOT NULL DEFAULT FALSE,
  profile_pic_url              TEXT,
  external_url                 TEXT,
  language_hint                VARCHAR(10),

  -- 도메인 특화
  treatment_tags               JSONB        NOT NULL DEFAULT '[]',
  region_tags                  JSONB        NOT NULL DEFAULT '[]',
  treatment_content_ratio      NUMERIC(4,3),
  sponsorship_ratio            NUMERIC(4,3),
  has_medical_risk_flag        BOOLEAN      NOT NULL DEFAULT FALSE,

  -- 티어 & 스코어링
  follower_tier                VARCHAR(10),   -- nano / micro / mid / macro
  seed_priority                VARCHAR(10)  NOT NULL DEFAULT 'warm',
  match_score_skin_clinic      NUMERIC(5,3),
  match_score_plastic_surgery  NUMERIC(5,3),
  match_score_obesity_clinic   NUMERIC(5,3),

  -- 품질 시그널
  quality_flags                JSONB        NOT NULL DEFAULT '[]',
  anomaly_flag                 BOOLEAN      NOT NULL DEFAULT FALSE,
  follower_change_7d           INTEGER,
  follower_change_30d          INTEGER,
  comment_quality_score        NUMERIC(4,3),
  audience_fit_score           NUMERIC(4,3),

  -- AI 분석
  ai_content_label             JSONB,

  -- 그래프 탐색
  last_graph_explored_at       TIMESTAMPTZ,

  -- 메타
  discovered_via               TEXT,
  last_scraped_at              TIMESTAMPTZ,
  last_posted_at               TIMESTAMPTZ,
  status                       VARCHAR(20)  NOT NULL DEFAULT 'active',
  -- active | stale | private | deleted | low_quality | blocked

  created_at                   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at                   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- upsert 키
CREATE UNIQUE INDEX idx_influencers_platform_user_id
  ON influencers (platform, instagram_user_id)
  WHERE instagram_user_id IS NOT NULL;

CREATE UNIQUE INDEX idx_influencers_platform_handle
  ON influencers (platform, handle);

-- 검색/필터용
CREATE INDEX idx_influencers_follower_tier        ON influencers (follower_tier, status);
CREATE INDEX idx_influencers_match_skin           ON influencers (match_score_skin_clinic DESC NULLS LAST);
CREATE INDEX idx_influencers_match_plastic        ON influencers (match_score_plastic_surgery DESC NULLS LAST);
CREATE INDEX idx_influencers_match_obesity        ON influencers (match_score_obesity_clinic DESC NULLS LAST);
CREATE INDEX idx_influencers_last_scraped         ON influencers (last_scraped_at);
CREATE INDEX idx_influencers_seed_priority        ON influencers (seed_priority, status);
CREATE INDEX idx_influencers_followers            ON influencers (followers DESC NULLS LAST);
CREATE INDEX idx_influencers_status               ON influencers (status);

-- JSONB 배열 GIN 인덱스 (치료 태그 / 지역 태그 필터링)
CREATE INDEX idx_influencers_treatment_tags       ON influencers USING GIN (treatment_tags);
CREATE INDEX idx_influencers_region_tags          ON influencers USING GIN (region_tags);
CREATE INDEX idx_influencers_quality_flags        ON influencers USING GIN (quality_flags);

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_influencers_updated_at
  BEFORE UPDATE ON influencers
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ============================================================
-- 2. influencer_metrics_snapshots (시점별 지표 스냅샷)
-- ============================================================
CREATE TABLE influencer_metrics_snapshots (
  id                        UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  influencer_id             UUID        NOT NULL REFERENCES influencers(id) ON DELETE CASCADE,
  captured_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  followers                 INTEGER,
  following                 INTEGER,
  posts_count               INTEGER,
  avg_likes                 NUMERIC(12,2),
  avg_comments              NUMERIC(10,2),
  avg_reel_plays            NUMERIC(12,2),
  engagement_rate           NUMERIC(6,4),
  estimated_reach           INTEGER,
  estimated_real_followers  INTEGER
);

CREATE INDEX idx_snapshots_influencer_id ON influencer_metrics_snapshots (influencer_id, captured_at DESC);
CREATE INDEX idx_snapshots_captured_at   ON influencer_metrics_snapshots (captured_at DESC);


-- ============================================================
-- 3. influencer_posts (게시물)
-- ============================================================
CREATE TABLE influencer_posts (
  id                UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  influencer_id     UUID        NOT NULL REFERENCES influencers(id) ON DELETE CASCADE,
  platform          VARCHAR(20) NOT NULL DEFAULT 'instagram',
  external_post_id  VARCHAR(100),
  post_url          TEXT        NOT NULL,
  post_type         VARCHAR(20),  -- photo / video / reel / carousel
  caption           TEXT,
  likes             INTEGER,
  comments          INTEGER,
  plays             INTEGER,
  hashtags          JSONB       NOT NULL DEFAULT '[]',
  mentions          JSONB       NOT NULL DEFAULT '[]',
  posted_at         TIMESTAMPTZ,
  scraped_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  is_sponsored      BOOLEAN     NOT NULL DEFAULT FALSE,
  treatment_mentions JSONB      NOT NULL DEFAULT '[]'
);

CREATE UNIQUE INDEX idx_posts_platform_ext_id
  ON influencer_posts (platform, external_post_id)
  WHERE external_post_id IS NOT NULL;

CREATE UNIQUE INDEX idx_posts_url ON influencer_posts (post_url);
CREATE INDEX idx_posts_influencer_id      ON influencer_posts (influencer_id, posted_at DESC);
CREATE INDEX idx_posts_is_sponsored       ON influencer_posts (is_sponsored);
CREATE INDEX idx_posts_posted_at          ON influencer_posts (posted_at DESC);
CREATE INDEX idx_posts_treatment_mentions ON influencer_posts USING GIN (treatment_mentions);
CREATE INDEX idx_posts_hashtags           ON influencer_posts USING GIN (hashtags);


-- ============================================================
-- 4. influencer_discovery_events (발견 경로 로그)
-- ============================================================
CREATE TABLE influencer_discovery_events (
  id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  influencer_id  UUID        NOT NULL REFERENCES influencers(id) ON DELETE CASCADE,
  source_type    VARCHAR(30) NOT NULL,  -- hashtag / keyword_search / manual / graph
  source_value   TEXT,
  discovered_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_discovery_influencer_id ON influencer_discovery_events (influencer_id);
CREATE INDEX idx_discovery_source_type   ON influencer_discovery_events (source_type, discovered_at DESC);


-- ============================================================
-- 5. influencer_seed_queue (배치 수집 큐)
-- ============================================================
CREATE TABLE influencer_seed_queue (
  id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
  influencer_id  UUID         REFERENCES influencers(id) ON DELETE CASCADE,
  platform       VARCHAR(20)  NOT NULL DEFAULT 'instagram',
  handle         VARCHAR(100) NOT NULL,
  priority       SMALLINT     NOT NULL DEFAULT 5,   -- 1(높음) ~ 10(낮음)
  job_type       VARCHAR(30)  NOT NULL,
  -- profile_refresh | posts_refresh | deep_enrich
  status         VARCHAR(20)  NOT NULL DEFAULT 'pending',
  -- pending | running | done | failed_api | failed_private | failed_deleted | skipped_fresh
  attempt_count  SMALLINT     NOT NULL DEFAULT 0,
  scheduled_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  started_at     TIMESTAMPTZ,
  finished_at    TIMESTAMPTZ,
  last_error     TEXT,
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- pending 상태 중복 방지 (PostgreSQL 조건부 UNIQUE)
CREATE UNIQUE INDEX idx_queue_pending_dedup
  ON influencer_seed_queue (platform, handle, job_type)
  WHERE status = 'pending';

CREATE INDEX idx_queue_status_priority ON influencer_seed_queue (status, priority, scheduled_at);
CREATE INDEX idx_queue_influencer_id   ON influencer_seed_queue (influencer_id);
CREATE INDEX idx_queue_scheduled_at    ON influencer_seed_queue (scheduled_at) WHERE status = 'pending';


-- ============================================================
-- 6. seed_hashtag_pool (해시태그 풀 관리)
-- ============================================================
CREATE TABLE seed_hashtag_pool (
  id                       UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
  hashtag                  VARCHAR(100) NOT NULL UNIQUE,
  domain                   VARCHAR(30)  NOT NULL,
  -- skin_clinic | plastic_surgery | obesity_clinic | general
  last_crawled_at          TIMESTAMPTZ,
  crawl_count              INTEGER      NOT NULL DEFAULT 0,
  new_accounts_found_last  INTEGER      NOT NULL DEFAULT 0,
  total_accounts_found     INTEGER      NOT NULL DEFAULT 0,
  is_exhausted             BOOLEAN      NOT NULL DEFAULT FALSE,
  source                   VARCHAR(30)  NOT NULL DEFAULT 'manual',
  -- manual | auto_extracted | competitor_tag
  created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_hashtag_pool_domain    ON seed_hashtag_pool (domain, is_exhausted, last_crawled_at);
CREATE INDEX idx_hashtag_pool_exhausted ON seed_hashtag_pool (is_exhausted);


-- ============================================================
-- 7. seeding_run_logs (배치 실행 이력)
-- ============================================================
CREATE TABLE seeding_run_logs (
  id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_type            VARCHAR(30) NOT NULL,
  -- discovery | profile_refresh | enrichment | post_refresh
  started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at         TIMESTAMPTZ,
  total_attempted     INTEGER     NOT NULL DEFAULT 0,
  success_count       INTEGER     NOT NULL DEFAULT 0,
  failed_count        INTEGER     NOT NULL DEFAULT 0,
  skipped_count       INTEGER     NOT NULL DEFAULT 0,
  new_accounts_found  INTEGER     NOT NULL DEFAULT 0,
  apify_calls_made    INTEGER     NOT NULL DEFAULT 0,
  error_summary       JSONB       NOT NULL DEFAULT '{}',
  db_total_after      INTEGER,
  metadata            JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_run_logs_job_type   ON seeding_run_logs (job_type, started_at DESC);
CREATE INDEX idx_run_logs_started_at ON seeding_run_logs (started_at DESC);


-- ============================================================
-- 8. influencer_sponsorship_signals (협찬 이력 감지)
-- ============================================================
CREATE TABLE influencer_sponsorship_signals (
  id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  influencer_id   UUID        NOT NULL REFERENCES influencers(id) ON DELETE CASCADE,
  detected_brand  TEXT        NOT NULL,
  signal_type     VARCHAR(30) NOT NULL,
  -- hashtag_mention | caption_mention | account_tag
  detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  post_url        TEXT
);

CREATE INDEX idx_sponsorship_influencer_id ON influencer_sponsorship_signals (influencer_id, detected_at DESC);
CREATE INDEX idx_sponsorship_brand         ON influencer_sponsorship_signals (detected_brand);


-- ============================================================
-- 초기 해시태그 시드 데이터
-- ============================================================
INSERT INTO seed_hashtag_pool (hashtag, domain, source) VALUES
  -- 피부과
  ('피부과후기',    'skin_clinic',      'manual'),
  ('피부과추천',    'skin_clinic',      'manual'),
  ('피부관리일기',  'skin_clinic',      'manual'),
  ('피부과가자',    'skin_clinic',      'manual'),
  ('레이저토닝',    'skin_clinic',      'manual'),
  ('피코레이저',    'skin_clinic',      'manual'),
  ('울쎄라후기',    'skin_clinic',      'manual'),
  ('써마지후기',    'skin_clinic',      'manual'),
  ('리프팅후기',    'skin_clinic',      'manual'),
  ('보톡스후기',    'skin_clinic',      'manual'),
  ('필러후기',      'skin_clinic',      'manual'),
  ('피부개선',      'skin_clinic',      'manual'),
  ('강남피부과',    'skin_clinic',      'manual'),
  ('홍대피부과',    'skin_clinic',      'manual'),
  ('압구정피부과',  'skin_clinic',      'manual'),
  ('신촌피부과',    'skin_clinic',      'manual'),
  ('분당피부과',    'skin_clinic',      'manual'),
  -- 성형외과
  ('성형후기',      'plastic_surgery',  'manual'),
  ('성형일기',      'plastic_surgery',  'manual'),
  ('성형변신',      'plastic_surgery',  'manual'),
  ('성형인증',      'plastic_surgery',  'manual'),
  ('코수술후기',    'plastic_surgery',  'manual'),
  ('쌍꺼풀후기',    'plastic_surgery',  'manual'),
  ('지방흡입후기',  'plastic_surgery',  'manual'),
  ('눈성형후기',    'plastic_surgery',  'manual'),
  ('강남성형',      'plastic_surgery',  'manual'),
  ('압구정성형',    'plastic_surgery',  'manual'),
  ('성형외과추천',  'plastic_surgery',  'manual'),
  -- 비만클리닉
  ('비만클리닉',    'obesity_clinic',   'manual'),
  ('다이어트주사',  'obesity_clinic',   'manual'),
  ('삭센다후기',    'obesity_clinic',   'manual'),
  ('위고비후기',    'obesity_clinic',   'manual'),
  ('지방분해주사',  'obesity_clinic',   'manual'),
  ('체중감량후기',  'obesity_clinic',   'manual'),
  ('다이어트일기',  'obesity_clinic',   'manual'),
  ('살빠졌어요',    'obesity_clinic',   'manual'),
  ('다이어트성공',  'obesity_clinic',   'manual'),
  ('비만치료',      'obesity_clinic',   'manual'),
  ('강남다이어트',  'obesity_clinic',   'manual'),
  ('강남비만클리닉','obesity_clinic',   'manual');
