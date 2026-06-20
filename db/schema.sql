-- ============================================================
-- K-Beauty Intelligence Agent v3 - SQLite Schema
-- 목적: 글로벌 소비자 반응 기반 아모레퍼시픽 브랜드 기회 탐지
-- 타겟: 미국 시장 (Sephora US + OY Global + SNS)
-- ============================================================

PRAGMA foreign_keys=ON;

-- ============================================================
-- DIMENSION TABLES
-- ============================================================

-- 아모레퍼시픽 브랜드 마스터 (13개)
CREATE TABLE IF NOT EXISTS dim_brand (
    brand_id      TEXT PRIMARY KEY,  -- 'laneige', 'cosrx'
    brand_name_en TEXT NOT NULL,     -- 'LANEIGE', 'COSRX'
    brand_name_kr TEXT,              -- '라네즈'
    tier          TEXT,              -- 'korean_luxury' | 'international_luxury' | 'korean_premium' | 'korean_daily' | 'clinical_daily' | 'korean_makeup'
    sns_keywords  TEXT               -- JSON: ["laneige","water sleeping mask","lip sleeping mask"]
);

INSERT OR IGNORE INTO dim_brand VALUES
-- korean_luxury
('sulwhasoo', 'Sulwhasoo',  '설화수',  'korean_luxury',       '["sulwhasoo","설화수","first care activating serum"]'),
('hera',      'HERA',       '헤라',    'korean_luxury',       '["hera","헤라","hera seoul","hera cushion"]'),
('primera',   'Primera',    '프리메라', 'korean_luxury',       '["primera","프리메라","primera seed"]'),
-- international_luxury
('tata_harper','Tata Harper', NULL,    'international_luxury','["tata harper","tataharper","tata harper resurfacing"]'),
-- korean_premium
('iope',      'IOPE',       '아이오페', 'korean_premium',      '["iope","아이오페","iope retinol"]'),
('aestura',   'AESTURA',    '에스트라', 'korean_premium',      '["aestura","에스트라","aestura cica"]'),
('mamonde',   'Mamonde',    '마몽드',  'korean_premium',      '["mamonde","마몽드","mamonde rose"]'),
('hanyul',    'HANYUL',     '한율',    'korean_premium',      '["hanyul","한율","hanyul rice"]'),
-- korean_daily
('laneige',   'LANEIGE',    '라네즈',  'korean_daily',        '["laneige","라네즈","water sleeping mask","lip sleeping mask","laneige cream skin"]'),
('innisfree', 'INNISFREE',  '이니스프리','korean_daily',       '["innisfree","이니스프리","green tea serum","innisfree sunscreen"]'),
-- clinical_daily
('cosrx',     'COSRX',      NULL,      'clinical_daily',      '["cosrx","snail mucin","ac collection","advanced snail","cosrx serum"]'),
-- korean_makeup
('espoir',    'espoir',     '에스쁘아', 'korean_makeup',       '["espoir","에스쁘아","espoir cushion"]'),
('etude',     'ETUDE',      '에뛰드',  'korean_makeup',       '["etude","에뛰드","etude house","etude fixing tint"]');


-- 플랫폼 마스터 (5개)
-- Google Trends 제거 (비공식 API rate limit으로 수집 불가)
-- Sephora US: VOC 전용 (랭킹 수집 제외)
CREATE TABLE IF NOT EXISTS dim_platform (
    platform_id   TEXT PRIMARY KEY,  -- 'tiktok', 'sephora_us'
    platform_name TEXT NOT NULL,     -- 'TikTok', 'Sephora US'
    platform_type TEXT NOT NULL,     -- 'sns' | 'retail'
    region        TEXT NOT NULL,     -- 'US' | 'KR' | 'Global'
    data_type     TEXT NOT NULL,     -- 'trend' | 'ranking' | 'voc' | 'ranking+voc'
    base_url      TEXT
);

INSERT OR IGNORE INTO dim_platform VALUES
('tiktok',        'TikTok',                    'sns',    'Global', 'trend',       'https://www.tiktok.com'),
('youtube',       'YouTube',                   'sns',    'Global', 'trend',       'https://www.youtube.com'),
('oy_kr',         'OliveYoung KR',             'retail', 'KR',     'ranking',     'https://www.oliveyoung.co.kr'),
('oy_global',     'OliveYoung Global',         'retail', 'Global', 'ranking+voc', 'https://global.oliveyoung.com'),
('oy_top_orders', 'OliveYoung Global - Top Orders',   'retail', 'Global', 'ranking', 'https://global.oliveyoung.com'),
('oy_top_korea',  'OliveYoung Global - Top in Korea', 'retail', 'KR',     'ranking', 'https://global.oliveyoung.com'),
('sephora_us',    'Sephora US',                'retail', 'US',     'voc',         'https://www.sephora.com');


-- 아모레퍼시픽 제품 마스터
-- product_id: 플랫폼 독립 서로게이트 키 (S00000001 형식)
-- 한 제품이 OY Global + Sephora 동시 입점해도 단일 row로 관리
CREATE TABLE IF NOT EXISTS dim_product (
    product_id       TEXT PRIMARY KEY,  -- 서로게이트 키: 'S00000001'
    brand_id         TEXT NOT NULL,     -- dim_brand.brand_id ('laneige', 'cosrx' 등)
    product_name_en  TEXT NOT NULL,     -- 'Water Sleeping Mask' (정제된 이름)
    product_name_kr  TEXT,              -- '워터 슬리핑 마스크' (OY KR 매칭용, 추후)
    category_main    TEXT,              -- 'skincare' | 'makeup' | 'suncare'
    category_sub     TEXT,              -- 'moisturizer' | 'serum' | 'toner' | 'sunscreen' | 'lip' | 'cushion' | 'tint'
    key_ingredients  TEXT,              -- JSON: ["niacinamide","hyaluronic acid"]
    key_benefits     TEXT,              -- JSON: ["hydration","brightening","barrier"]
    oy_prdtno        TEXT UNIQUE,       -- OY Global 제품번호 (A000000123456), 없으면 NULL
    sephora_pid      TEXT UNIQUE,       -- Sephora 제품번호 (P461833), 없으면 NULL
    oy_url           TEXT,              -- OY Global 제품 페이지 URL
    sephora_url      TEXT,              -- Sephora 제품 페이지 URL
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);


-- ============================================================
-- FACT TABLES - SNS
-- ============================================================

-- TikTok + YouTube 원문 Raw 저장
CREATE TABLE IF NOT EXISTS fact_sns_raw (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT    NOT NULL DEFAULT (datetime('now')),
    week         TEXT    NOT NULL,
    platform_id  TEXT    NOT NULL,   -- 'tiktok' | 'youtube'
    post_id      TEXT,               -- 플랫폼 고유 ID
    content_text TEXT,               -- 캡션 (TikTok) or 제목+설명 (YouTube)
    hashtags     TEXT,               -- JSON 배열 ["kbeauty","skincare",...]
    view_count   INTEGER DEFAULT 0,
    like_count   INTEGER DEFAULT 0,
    UNIQUE(platform_id, post_id)
);


-- TikTok + YouTube 주간 신호
CREATE TABLE IF NOT EXISTS fact_sns_signals (
    week             TEXT NOT NULL,   -- 'YYYY-WNN' (예: '2026-W17')
    platform_id      TEXT NOT NULL,   -- 'tiktok' | 'youtube'
    keyword          TEXT NOT NULL,   -- 추출된 정규화 키워드

    -- 볼륨
    post_count       INTEGER DEFAULT 0,  -- 해당 주 언급 포스트/영상 수
    total_views      INTEGER DEFAULT 0,  -- 누적 조회수
    total_likes      INTEGER DEFAULT 0,  -- 누적 좋아요
    total_comments   INTEGER DEFAULT 0,  -- 누적 댓글 수

    -- 에이전트 계산 지표
    -- TikTok:  views*0.3 + likes*1.0 + comments*2.0
    -- YouTube: views*0.2 + likes*1.5 + comments*3.0
    engagement_score REAL    DEFAULT 0,
    growth_rate      REAL,               -- 전주 대비 증가율 (%)
    is_new_keyword   INTEGER DEFAULT 0,  -- 이번 주 첫 등장 여부 (1/0)

    created_at       TEXT DEFAULT (datetime('now')),

    PRIMARY KEY (week, platform_id, keyword),
    FOREIGN KEY (platform_id) REFERENCES dim_platform(platform_id)
);


-- ============================================================
-- FACT TABLES - RETAIL
-- ============================================================

-- 플랫폼별 주간 제품 순위
CREATE TABLE IF NOT EXISTS fact_retail_rankings (
    week          TEXT NOT NULL,   -- 'YYYY-WNN'
    platform_id   TEXT NOT NULL,   -- 'oy_kr' | 'oy_global' | 'sephora_us'
    product_id    TEXT NOT NULL,   -- dim_product FK
    rank_position INTEGER,         -- 순위 (1위, 2위...)
    category      TEXT,            -- 'skincare' | 'makeup' | 'sunscreen'

    created_at    TEXT DEFAULT (datetime('now')),

    PRIMARY KEY (week, platform_id, product_id),
    FOREIGN KEY (platform_id) REFERENCES dim_platform(platform_id),
    FOREIGN KEY (product_id)  REFERENCES dim_product(product_id)
);


-- 리뷰 기반 VOC 키워드·감성
-- 수집 대상: OY Global + Sephora US
CREATE TABLE IF NOT EXISTS fact_voc_signals (
    week           TEXT NOT NULL,   -- 'YYYY-WNN'
    platform_id    TEXT NOT NULL,   -- 'oy_global' | 'sephora_us'
    product_id     TEXT NOT NULL,   -- dim_product FK

    -- 리뷰 볼륨
    review_count        INTEGER DEFAULT 0,  -- 해당 주 수집한 샘플 리뷰 수 (최대 20)
    avg_rating          REAL,               -- 수집 샘플 평균 평점 (1.0~5.0)
    total_reviews       INTEGER,            -- 플랫폼 전체 누적 리뷰 수 (velocity 계산용)
    platform_avg_rating REAL,               -- 플랫폼 전체 평균 평점 (별점 추세 계산용)

    -- 감성 분석
    sentiment_pos  REAL,   -- 긍정 비율 (0~1)
    sentiment_neg  REAL,   -- 부정 비율 (0~1)

    -- 키워드 추출 (GPT 활용)
    pos_keywords   TEXT,   -- JSON: ["lightweight","glass skin","no white cast"]
    neg_keywords   TEXT,   -- JSON: ["too heavy","strong scent","broke me out"]
    needs_keywords TEXT,   -- JSON: ["more SPF","fragrance free version","lighter texture"]

    created_at     TEXT DEFAULT (datetime('now')),

    PRIMARY KEY (week, platform_id, product_id),
    FOREIGN KEY (platform_id) REFERENCES dim_platform(platform_id),
    FOREIGN KEY (product_id)  REFERENCES dim_product(product_id)
);


-- 원본 리뷰 텍스트 저장
-- voc_tool.py가 이 테이블을 읽어 감성분석 → fact_voc_signals 에 집계
CREATE TABLE IF NOT EXISTS fact_raw_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week            TEXT NOT NULL,      -- 'YYYY-WNN'
    platform_id     TEXT NOT NULL,      -- 'oy_global' | 'sephora_us'
    product_id      TEXT NOT NULL,      -- dim_product FK
    rating          REAL,               -- 별점 (1.0~5.0, 없으면 NULL)
    review_text     TEXT NOT NULL,      -- 리뷰 본문
    helpful         INTEGER DEFAULT 0,  -- 도움됨 수
    submission_time TEXT,               -- 'YYYY/MM/DD'
    created_at      TEXT DEFAULT (datetime('now')),

    UNIQUE (platform_id, product_id, submission_time, review_text)  -- 중복 방지
);


-- ============================================================
-- FACT TABLES - 통합 분석
-- ============================================================



-- 플랫폼별 키워드 첫 등장 (크로스플랫폼 모멘텀 추적)
CREATE TABLE IF NOT EXISTS fact_trend_first_seen (
    keyword         TEXT NOT NULL,   -- 탐지된 키워드
    platform_id     TEXT NOT NULL,   -- 처음 등장한 플랫폼
    first_seen_week TEXT NOT NULL,   -- 처음 등장한 주차 'YYYY-WNN'
    initial_score   REAL,            -- 첫 등장 시 engagement/interest 점수

    created_at      TEXT DEFAULT (datetime('now')),

    PRIMARY KEY (keyword, platform_id),
    FOREIGN KEY (platform_id) REFERENCES dim_platform(platform_id)
);


-- LLM 인사이트 분석 결과 (키워드 요약)
CREATE TABLE IF NOT EXISTS fact_llm_insights (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    week                 TEXT NOT NULL,
    keyword              TEXT NOT NULL,
    platform_id          TEXT,
    competitor_mentions  TEXT,   -- JSON: {"Anua": 3, "SKIN1004": 2}
    consumer_need        TEXT,   -- "레티놀 자극 후 장벽 회복"
    consumer_language    TEXT,   -- "barrier repair, calming, soothing"
    opportunity          TEXT,   -- "손상 후 회복 루틴 포지셔닝"
    confidence           REAL,   -- 0~1
    raw_post_count       INTEGER DEFAULT 0,
    created_at           TEXT    DEFAULT (datetime('now')),
    UNIQUE (week, keyword)
);


-- LLM 인사이트 × AP 제품 연결 테이블
-- fact_llm_insights의 product 연결을 정규화
CREATE TABLE IF NOT EXISTS fact_llm_insight_products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id  INTEGER NOT NULL,   -- fact_llm_insights.id FK
    week        TEXT    NOT NULL,
    keyword     TEXT    NOT NULL,
    product_id  TEXT    NOT NULL,   -- dim_product FK
    match_type  TEXT    NOT NULL,   -- 'direct' | 'indirect'
    evidence    TEXT,               -- SNS 캡션 근거 문구 (direct인 경우)
    created_at  TEXT    DEFAULT (datetime('now')),
    UNIQUE (insight_id, product_id, match_type),
    FOREIGN KEY (product_id) REFERENCES dim_product(product_id)
);


-- ============================================================
-- FACT TABLES - DecisionAgent 출력
-- ============================================================

-- Step 1 출력: SNS 트렌드 키워드 → AP 제품 연결 + 기회 유형
CREATE TABLE IF NOT EXISTS fact_trend_insights (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    week                TEXT NOT NULL,
    keyword             TEXT NOT NULL,
    z_score             REAL,
    momentum_score      REAL,
    trend_shape         TEXT,               -- 'spike'|'sustained'|'emerging'|'steady'
    lead_platform       TEXT,               -- 'tiktok' | 'youtube'
    is_cross_platform   INTEGER DEFAULT 0,
    ap_brand_ids        TEXT,               -- JSON: ["laneige","cosrx"]
    ap_product_ids      TEXT,               -- JSON: ["S00000001","S00000002"]
    opportunity_type    TEXT NOT NULL,      -- 'amplify'|'position'|'counter'|'new_entry'
    consumer_need       TEXT,
    competitor_mentions TEXT,               -- JSON: {"Anua":3,"SKIN1004":2}
    insight_summary     TEXT,
    action_rec          TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(week, keyword)
);


-- Step 2 출력: AP 제품별 리테일 + VOC + 전략 사분면
CREATE TABLE IF NOT EXISTS fact_product_insights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week            TEXT NOT NULL,
    product_id      TEXT NOT NULL,          -- dim_product FK
    brand_id        TEXT,
    brand_tier      TEXT,                   -- dim_brand.tier
    oy_rank_orders  INTEGER,                -- oy_top_orders 순위
    oy_rank_korea   INTEGER,                -- oy_top_korea 순위
    sephora_rank    INTEGER,
    retail_score    REAL,                   -- 0~1 (낮은 순위 = 높은 점수)
    sentiment_pos   REAL,
    sentiment_neg   REAL,
    avg_rating      REAL,
    voc_source_week TEXT,                   -- VOC 출처 주차 (현재주=신선, 과거주=폴백)
    voc_velocity    INTEGER,                -- 전체 리뷰 수 주간 증감 (유입 속도)
    rating_trend    REAL,                   -- 전체 평균 평점 주간 변화
    total_reviews   INTEGER,                -- 플랫폼 전체 누적 리뷰 수
    pos_keywords    TEXT,                   -- JSON
    neg_keywords    TEXT,                   -- JSON
    needs_keywords  TEXT,                   -- JSON
    strategy_quad   TEXT NOT NULL,          -- 'PUSH_NOW'|'FIX_AND_PUSH'|'HOLD'|'MONITOR'
    insight_summary TEXT,
    action_rec      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(week, product_id),
    FOREIGN KEY (product_id) REFERENCES dim_product(product_id)
);


-- Step 3 출력: 방한 관광객 추천 제품
CREATE TABLE IF NOT EXISTS fact_inbound_picks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    week        TEXT NOT NULL,
    rank        INTEGER NOT NULL,           -- 추천 순위 (1~15)
    product_id  TEXT NOT NULL,
    brand_id    TEXT,
    brand_tier  TEXT,
    korea_rank  INTEGER,                    -- oy_top_korea 순위
    orders_rank INTEGER,                    -- oy_top_orders 순위
    voc_pos     REAL,                       -- 평균 감성 긍정도
    sns_linked  INTEGER DEFAULT 0,          -- SNS 트렌드 키워드 연결 여부
    pick_score  REAL,
    pos_keywords TEXT,                      -- JSON
    pick_reason TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(week, product_id),
    FOREIGN KEY (product_id) REFERENCES dim_product(product_id)
);


-- ============================================================
-- MEMORY TABLES
-- ============================================================

-- 에이전트 실행 이력
CREATE TABLE IF NOT EXISTS mem_agent_runs (
    run_id        TEXT PRIMARY KEY,     -- 'run_20260417_001'
    run_at        TEXT NOT NULL,        -- 실행 시각
    run_type      TEXT NOT NULL,        -- 'scheduled' | 'manual'
    status        TEXT NOT NULL,        -- 'success' | 'partial' | 'failed'

    -- 수집 결과
    sns_records      INTEGER DEFAULT 0,
    retail_records   INTEGER DEFAULT 0,
    voc_records      INTEGER DEFAULT 0,
    opportunities    INTEGER DEFAULT 0,

    -- 에러 추적
    error_message TEXT,
    duration_sec  REAL,

    created_at    TEXT DEFAULT (datetime('now'))
);
