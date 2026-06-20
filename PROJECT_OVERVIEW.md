# K-Beauty Intelligence Agent v3 — 프로젝트 개요

> 최종 업데이트: 2026-06-19
> 버전: v3 (kbeauty_agent_v3)

---

## 1. 프로젝트 개요

K-Beauty 트렌드를 SNS·리테일·검색 데이터에서 자동 수집하고, 통계 분석을 통해 **아모레퍼시픽(AP) 브랜드 대응 인사이트**를 주 단위로 도출하는 멀티-에이전트 시스템입니다.

| 항목 | 내용 |
|------|------|
| 언어 | Python 3.11+ |
| DB | SQLite (`db/kbeauty.db`) |
| 실행 방식 | CLI (`python main.py agentic`) |
| 출력물 | HTML 리포트 3탭 (트렌드 / 제품 / 방한픽) |
| LLM | Claude Opus 4.8 (Anthropic) |
| 관측 | LangFuse (선택, 키 없으면 자동 스킵) |

---

## 2. 전체 아키텍처

```
main.py (CLI 진입점)
    └── Orchestrator
            │
            ├── [Rule-based] run_full()          ← 수집→분석→결정 하드코딩
            │
            └── [Agentic]    run_full_agentic()  ← Claude API Tool Use (ReAct 루프)
                    │
                    │  Claude가 DB 상태를 보고 스스로 판단
                    │
                    ├── Tool: check_db_status    ← DB 7개 테이블 현황 조회
                    ├── Tool: collect_data       → CollectionAgent.run()
                    ├── Tool: analyze_data       → AnalysisAgent.run()
                    └── Tool: decide_insights    → DecisionAgent.run()
                                │
                                ├── TrendInsightBuilder    → fact_trend_insights
                                ├── ProductInsightBuilder  → fact_product_insights
                                └── InboundPickBuilder     → fact_inbound_picks
```

### Rule-based vs Agentic 비교

| 항목 | Rule-based (`run_full`) | Agentic (`run_full_agentic`) |
|------|------------------------|------------------------------|
| 실행 순서 결정 | 코드 하드코딩 | Claude LLM이 판단 |
| 이미 완료된 주차 | 무조건 재실행 | DB 확인 후 스킵 |
| LLM 관여 | 없음 (순수 Python) | Orchestrator 레벨에서 Claude 호출 |
| 유연성 | 낮음 | 높음 |
| API 비용 | 0 | Claude API 호출 추가 (~2회) |

---

## 3. Agentic 실행 흐름 (ReAct 루프)

```
사용자: python main.py agentic --week 2026-W25

루프 #1
  Claude 판단: "DB 상태를 먼저 확인해야 한다"
  → Tool 호출: check_db_status()
  → 결과: fact_sns_raw=W25(513행), fact_llm_insights=W25(10행), fact_trend_insights=W25(10행)

루프 #2
  Claude 판단: "3개 테이블 모두 W25 데이터 존재 → 전 단계 스킵"
  → stop_reason: end_turn
  → 결과 JSON 반환: {pipeline_status: "all_stages_already_completed"}
```

**스킵 판단 기준:**
- `fact_sns_raw.latest_week == target_week` → collect_data 스킵
- `fact_llm_insights.latest_week == target_week` → analyze_data 스킵
- `fact_trend_insights.latest_week == target_week` → decide_insights 스킵

---

## 4. 디렉토리 구조

```
kbeauty_agent_v3/
├── main.py                      # CLI 진입점
├── scheduler.py                 # APScheduler 상주 스케줄러 (선택)
├── run_weekly.bat               # Windows 작업 스케줄러용 — 주간 파이프라인
├── run_catalog.bat              # Windows 작업 스케줄러용 — 월간 카탈로그
├── requirements.txt             # 의존 패키지
├── .env                         # API 키
│
├── logs/                        # 정기 실행 로그 (weekly_*.log, catalog_*.log)
│
├── agents/
│   ├── orchestrator.py          # Rule-based + Agentic 파이프라인
│   ├── collection_agent.py      # 수집 조율 (5개 플랫폼)
│   ├── analysis_agent.py        # 통계 분석 조율
│   └── decision_agent.py        # 인사이트 3종 생성 (3개 Builder)
│
├── tools/
│   ├── collect/
│   │   ├── tiktok_tool.py           # TikTok (Apify)
│   │   ├── youtube_tool.py          # YouTube (Google API)
│   │   ├── oy_global_tool.py        # OliveYoung Global (Playwright)
│   │   ├── sephora_tool.py          # Sephora US (Playwright + BazaarVoice)
│   │   ├── voc_collector.py         # VOC 리뷰 수집 (최신10 + 도움됨10)
│   │   ├── _ap_brands.py            # AP 브랜드 매핑 상수
│   │   └── _product_utils.py        # 제품명 정규화 유틸
│   │
│   ├── analyze/
│   │   ├── stats_tool.py            # Z-score / t-test / 상관 분석
│   │   ├── momentum_tool.py         # 크로스플랫폼 모멘텀 추적
│   │   ├── llm_insight_tool.py      # Claude 기반 LLM 인사이트
│   │   └── voc_tool.py              # VOC 감성 분석
│   │
│   └── report/
│       ├── html_report_tool.py      # 3탭 HTML 리포트 (Chart.js)
│       └── tableau_export_tool.py   # Tableau용 CSV 익스포트
│
├── memory/
│   └── agent_memory.py          # DB 연결 및 실행 이력 관리
│
└── db/
    ├── kbeauty.db               # SQLite DB (런타임 생성)
    └── schema.sql               # 테이블 정의
```

---

## 5. 에이전트별 기능

### 5-1. CollectionAgent — 데이터 수집

5개 플랫폼에서 K-beauty 데이터를 수집합니다.

| 툴 | 플랫폼 | 수집 방식 | 주요 데이터 |
|----|--------|-----------|------------|
| TikTokTool | TikTok | Apify API | 해시태그 21개 × 40건, 캡션 키워드·engagement |
| YouTubeTool | YouTube | Google Data API v3 | K-beauty 키워드 영상, 조회수·좋아요 |
| OYGlobalTool | OliveYoung Global | Playwright | 상품 랭킹, 카탈로그 |
| SephoraTool | Sephora US | Playwright + BazaarVoice | 상품 랭킹, 리뷰 API |
| VocCollector | Sephora / OY Global | BazaarVoice API / Playwright | 리뷰 최신10 + 도움됨10 (트렌드 제품 전용, on-demand) |

**저장 테이블:** `fact_sns_raw`, `fact_sns_signals`, `fact_retail_rankings`, `fact_raw_reviews`

> **VOC 수집 안정화 (2026-06):** 일시적 수집 실패(OY Playwright 타임아웃, BazaarVoice 빈 응답)로 인한 결측을 줄이기 위해 **제품별 재시도(최대 2회)**를 적용. 그래도 빈 제품은 DecisionAgent에서 **직전 주차 VOC로 폴백**(아래 5-3 참조). Sephora US는 랭킹 수집이 불가하여 `sephora_rank`는 항상 비어 있음(리테일 점수는 OliveYoung 랭킹만 사용).

#### 미국 타겟 수집 (2026-06 적용)

SNS 양 플랫폼 모두 **미국 시장 기준**으로 수집한다.

| 플랫폼 | 국가/언어 필터 |
|--------|----------------|
| YouTube | `regionCode="US"` + `relevanceLanguage="en"` |
| TikTok | `proxyCountryCode="US"`(미국 IP 접속) + `textLanguage=="en"` 후처리 필터(비영어 제거) |

> TikTok은 해시태그 검색이 글로벌 풀에서 나오므로, 미국 프록시(1차)로 미국 피드를 받고 영어 캡션만 남기는(2차) 2단계로 미국 타겟에 근접시킨다. W25까지는 전세계 기준 데이터가 일부 혼재하며, **W26부터 순수 미국 타겟으로 누적**된다.

---

### 5-2. AnalysisAgent — 통계 분석

| 툴 | 기능 |
|----|------|
| StatsTool | Z-score(≥2.0 주의/≥3.0 급등), Welch t-test(p<0.05), 성장 가속도. **계산한 z_score를 `fact_sns_signals.z_score`에 저장** |
| MomentumTool | **최근 3주 추세 기반 모멘텀** (아래 참조), 선행 플랫폼 탐지, 크로스플랫폼 판정 |
| LLMInsightTool | Claude 기반 소비자 니즈·경쟁사·기회 분석 → `fact_llm_insights` |
| VocTool | 리뷰 감성 분류, 긍정/부정/니즈 키워드 추출 → `fact_voc_signals` |

**저장 테이블:** `fact_sns_signals`(z_score 포함), `fact_voc_signals`, `fact_llm_insights`, `fact_llm_insight_products`

#### MomentumTool — 최근 3주 추세 기반 (2026-06 개편)

기존 "첫 등장 주차 대비 배수" 방식에서 **"데이터가 있는 최근 3개 주차" 기준**으로 변경.
주차 공백(예: W22·W24 누락)이 있어도 달력 주차가 아닌 "데이터 보유 주차"로 계산하여 견고함.

| 지표 | 정의 |
|------|------|
| `momentum_score` | 현재 주 engagement ÷ 3주 전 engagement (= **최근 3주 성장 배수**) |
| `weeks_rising` | 최근 3주 연속 상승 단계 수 (0~2, 2면 계속 상승 중. 상승 끊기면 리셋) |
| `lead_platform` | 현재 주 engagement 최상위 플랫폼 |
| `is_cross_platform` | 현재 주 2개 이상 플랫폼 동시 관측 여부 |

> z_score(직전 4주 평균 대비 **순간 급등**)와 momentum(최근 3주 **지속 상승**)은 서로 다른 시점을 측정하므로 상호 보완적. z는 높은데 momentum이 낮거나 그 반대도 정상.

> `fact_trend_first_seen` / `update_first_seen()`은 "키워드 최초 등장 주차" 기록용으로 보존되나, momentum 계산에는 더 이상 사용하지 않음.

---

### 5-3. DecisionAgent — 인사이트 3종 생성

DecisionAgent는 3개의 Builder로 구성됩니다.

#### TrendInsightBuilder → `fact_trend_insights`

SNS에서 통계적으로 급등 중인 키워드에 대한 AP 대응 인사이트.
**2개의 독립된 분류 축**으로 해석한다.

**축 1 — opportunity_type (누구의 기회인가)**

| opportunity_type | 조건 | 의미 |
|-----------------|------|------|
| `amplify` | AP 직접 연관 + 지속 상승 트렌드 | 기존 AP 제품 마케팅 즉시 강화 |
| `position` | AP 연관 + 경쟁사 동반 언급 | AP 제품 포지셔닝 조정 |
| `counter` | AP 미보유 + 경쟁사 점유 | 경쟁사 트렌드 대응 전략 |
| `new_entry` | AP 미보유 + 경쟁사 없음 | 신제품·라인 확장 검토 |

여기서 "지속 상승 트렌드" = `weeks_rising ≥ 2 AND momentum_score ≥ 1.5`.

**축 2 — trend_shape (어떤 모양의 트렌드인가, 2026-06 추가)**

| trend_shape | 조건 | 의미 / 액션 |
|-------------|------|------------|
| `emerging` | 이번 주 신규 등장 (is_new_keyword=1) | 🌱 초기 신호 — 관찰 |
| `sustained` | weeks_rising≥2 + momentum≥1.5 | 📈 지속 상승 — 중장기 예산 투입 |
| `spike` | z_score≥2, 지속성 없음 | ⚡ 순간 급등 — 빠르게 짧게 대응 |
| `steady` | 그 외 | ➖ 완만/정체 |

> 두 축은 직교한다. 예: "essence는 amplify(AP 기회) × sustained(지속 상승)", "vitamin_c는 position × spike(순간 급등)". HTML 리포트 트렌드 탭에 두 배지가 함께 표시된다.

#### ProductInsightBuilder → `fact_product_insights`

**VOC 중심** AP 제품별 전략 (2026-06 개편). 리테일 랭킹은 OliveYoung 단일 플랫폼 의존이라 결측이 많아 **보조 신호로 강등**하고, VOC 모멘텀(리뷰 유입 + 별점 추세)을 주 신호로 사용한다.

| strategy_quad | 조건 | 의미 |
|--------------|------|------|
| `PUSH_NOW` | VOC 긍정 + 상승 모멘텀(유입↑ 또는 별점↑), 하락 신호 없음 | 즉시 마케팅 집행 |
| `FIX_AND_PUSH` | 리뷰 유입↑ 인데 별점↓ 또는 부정↑ (또는 긍정 약함) | 제품 이슈 해결 후 push |
| `HOLD` | VOC 긍정이나 모멘텀 정체 | 유지/관찰 |
| `MONITOR` | 그 외 | 관망 |

**판정 변수**
- **VOC 긍정도 ≥ 0.65 = 긍정** / 부정도 ≥ 0.35 = 불만 (리뷰 감성 분석)
- **리뷰 유입(voc_velocity)** = 플랫폼 **전체 누적 리뷰 수**의 주간 증감 (＋ = 관심 상승). 수집 샘플(최대 20)이 아닌 **플랫폼 전체 리뷰 수** 기준
- **별점 추세(rating_trend)** = 플랫폼 **전체 평균 평점**의 주간 변화 (＋ = 만족도 상승)
- **리테일 점수**(보조) = `max(0, 1 − (최고순위 − 1) / 100)` — OliveYoung 랭킹 **Top 100** 기준, 가중치 낮춘 보조 신호

> **전체 통계 수집:** VocCollector가 Sephora(BazaarVoice statistics API → TotalReviewCount·AverageOverallRating) / OY Global(상품 페이지 `em.star-rating > span` width% → 평점, 리뷰 수 텍스트)에서 전체 리뷰 수·전체 평점을 캡처해 `fact_voc_signals.total_reviews`·`platform_avg_rating`에 저장. velocity·trend는 직전 주차 대비 값이라 **연속 2주 수집이 쌓인 뒤(W26+)부터 의미 있게 분류**된다.

VOC 트리거: ProductInsightBuilder 실행 중 트렌드 제품 감지 시 VocCollector 자동 호출.

**VOC 결측 폴백 (2026-06):** 당주 VOC 수집이 실패한 제품은 **가장 최근 과거 주차의 VOC로 대체**하고, `voc_source_week` 컬럼에 출처 주차를 기록한다(당주=신선, 과거주=폴백). HTML 리포트에 "📅 W## 기준" 배지로 폴백 여부를 표시. 과거 데이터가 없는 초기 주차에서는 여전히 결측이 남을 수 있으나, 운영 주차가 누적될수록 0에 수렴한다.

#### InboundPickBuilder → `fact_inbound_picks`

방한 외국인 관광객 구매 추천 제품 선정 (OliveYoung Korea 랭킹 등재 제품 풀, Top 15).

**Pick Score (2026-06 개편 — 순위 비중↓, SNS·VOC↑)**
```
pick_score = 0.30 × SNS 트렌드 강도 + 0.35 × VOC 긍정도
           + 0.25 × 한국 순위 + 0.10 × 글로벌 주문 순위
```
- **SNS 트렌드 강도**: 연결된 트렌드 키워드의 z_score·momentum 정규화 블렌드 `max(0.6·min(1,z/10) + 0.4·min(1,momentum/3))` — "지금 해외에서 뜨는 정도" (외국인이 사러 올 이유)
- **VOC 긍정도**: 리뷰 만족도 (사도 실패하지 않을 제품)
- **순위**(보조): Top 100 고정 기준 `max(0, 1 − (순위 − 1)/100)`. 기존 `total_korea`(행 수) 분모 버그 수정

> 기존 대비 **순위 70%→35%, SNS+VOC 22%→65%**로 재조정. 방한픽의 본질(해외 화제성 + 실제 만족도)을 우선시.

---

## 6. DB 테이블 구조

| 테이블 | 분류 | 설명 |
|--------|------|------|
| `dim_brand` | 마스터 | AP 브랜드 13개 |
| `dim_platform` | 마스터 | 플랫폼 7개 |
| `dim_product` | 마스터 | AP 전체 제품 카탈로그 |
| `fact_sns_raw` | 수집 | TikTok/YouTube 원문 포스트 |
| `fact_sns_signals` | 수집/분석 | 키워드별 주간 집계 신호 (+ `z_score` 컬럼, StatsTool이 채움) |
| `fact_retail_rankings` | 수집 | OY Global/Sephora 랭킹 |
| `fact_raw_reviews` | 수집 | 제품 리뷰 원문 (VOC) |
| `fact_voc_signals` | 분석 | 제품별 VOC 감성 집계 (+ `total_reviews`·`platform_avg_rating` 전체 통계) |
| `fact_llm_insights` | 분석 | Claude LLM 키워드 인사이트 |
| `fact_llm_insight_products` | 분석 | LLM 인사이트-제품 매핑 |
| `fact_trend_insights` | 결정 | 트렌드 인사이트 (`opportunity_type` + `trend_shape` 2축) |
| `fact_product_insights` | 결정 | 제품 인사이트 (strategy_quad + `voc_velocity`·`rating_trend`·`total_reviews`·`voc_source_week`) |
| `fact_inbound_picks` | 결정 | 방한픽 추천 |
| `fact_trend_first_seen` | 분석 | 키워드 최초 등장 주차 |
| `mem_agent_runs` | 메타 | 실행 이력 |

---

## 7. 데이터 흐름

```
[5개 플랫폼 수집]
  TikTok / YouTube / OY Global / Sephora / VOC(on-demand)
        ↓
  fact_sns_raw, fact_sns_signals
  fact_retail_rankings, fact_raw_reviews
        ↓
[통계 분석]
  Z-score / t-test / 모멘텀 / LLM 인사이트 / VOC 감성
        ↓
  fact_voc_signals, fact_llm_insights
        ↓
[인사이트 생성 — 3 Builders]
  TrendInsightBuilder  → fact_trend_insights  (opportunity_type)
  ProductInsightBuilder → fact_product_insights (strategy_quad)
  InboundPickBuilder   → fact_inbound_picks   (방한픽 랭킹)
        ↓
[HTML 리포트]
  exports/kbeauty_report_{week}.html
  (3탭: 트렌드 인사이트 / 제품 인사이트 / 방한픽)
```

---

## 8. CLI 실행 명령

```bash
# Agentic 실행 (권장) — Claude가 DB 상태 보고 필요한 단계만 실행
python main.py agentic
python main.py agentic --week 2026-W25

# Rule-based 전체 실행 — 무조건 수집→분석→결정 순 실행
python main.py full

# 단계별 개별 실행
python main.py collect                        # 수집만
python main.py collect --only tiktok youtube  # 특정 플랫폼만
python main.py analyze                        # 분석만
python main.py analyze --week 2026-W25        # 특정 주차 분석
python main.py decide                         # 인사이트 생성만
python main.py decide --week 2026-W25

# 카탈로그 초기 구축 (월 1회)
python main.py build-catalog
python main.py build-catalog --platforms oy_global sephora

# 옵션
python main.py agentic --log-level DEBUG
python main.py agentic --db /path/to/custom.db
```

---

## 8-1. 정기 실행 (스케줄링)

정해진 시각에 자동 실행하는 두 가지 방법.

| 작업 | 주기 | 시각(KST) | 실행 |
|------|------|-----------|------|
| 주간 전체 파이프라인 | 매주 목요일 | 09:00 | `main.py agentic` |
| 월간 카탈로그 재구축 | 매월 1일 | 03:00 | `main.py build-catalog` |

### 방법 A. APScheduler 상주 (`scheduler.py`)

```bash
python scheduler.py            # 포그라운드 상주 (Ctrl+C 종료)
python scheduler.py --now full # 스케줄 무시하고 즉시 1회 실행 (테스트)
python scheduler.py --now catalog
```

- 프로세스가 떠 있는 동안만 동작. 터미널 닫거나 재부팅하면 중단됨.
- 단기/테스트용. 타임존은 `scheduler.py`의 `TIMEZONE` 상수(기본 Asia/Seoul).

### 방법 B. Windows 작업 스케줄러 (운영 권장)

`run_weekly.bat` / `run_catalog.bat`을 OS 스케줄러가 호출. 로그는 `logs/`에 날짜별 기록.

```powershell
# 등록 (관리자 PowerShell)
schtasks /Create /TN "KBeauty\WeeklyPipeline"  /TR "<경로>\run_weekly.bat"  /SC WEEKLY  /D THU /ST 09:00 /F
schtasks /Create /TN "KBeauty\MonthlyCatalog" /TR "<경로>\run_catalog.bat" /SC MONTHLY /D 1   /ST 03:00 /F

# 상태 / 수동실행 / 삭제
schtasks /Query  /TN "KBeauty\WeeklyPipeline" /V /FO LIST
schtasks /Run    /TN "KBeauty\WeeklyPipeline"
schtasks /Delete /TN "KBeauty\WeeklyPipeline" /F
```

- 현재 등록: **로그인 사용자 기준 + PC 켜져 있을 때만** 실행. 자리 비움/화면잠금은 무관(실행됨).
- 예약 시각에 PC가 꺼져 있으면 건너뛰고, **다음에 PC를 켜면 밀린 작업을 따라잡아 1회 실행**.
- 배치 파일은 cmd가 cp949로 읽으므로 **ASCII(영문)로만** 작성해야 함(한글 주석 금지).

---

## 9. 환경변수 (.env)

| 변수 | 필수 | 용도 |
|------|------|------|
| `ANTHROPIC_API_KEY` | 필수 | LLM 인사이트 + Agentic Orchestrator |
| `APIFY_TOKEN` | 필수 | TikTok 수집 (Apify) |
| `YOUTUBE_API_KEY` | 필수 | YouTube Data API v3 |
| `OPENAI_API_KEY` | 선택 | VOC 감성 분석 (없으면 룰 기반 폴백) |
| `LANGFUSE_PUBLIC_KEY` | 선택 | LangFuse 트레이싱 활성화 |
| `LANGFUSE_SECRET_KEY` | 선택 | LangFuse 트레이싱 활성화 |
| `LANGFUSE_HOST` | 선택 | LangFuse 서버 URL (기본: cloud.langfuse.com) |

---

## 10. 주요 의존 패키지

| 패키지 | 용도 |
|--------|------|
| `anthropic` | Claude API (LLM 인사이트 + Agentic Orchestrator) |
| `openai` | VOC 감성 분석 |
| `playwright` | OliveYoung / Sephora 웹 스크래핑 |
| `google-api-python-client` | YouTube API |
| `requests` | BazaarVoice API, Apify API |
| `pandas` / `numpy` | 데이터 처리 |
| `scipy` | Z-score, t-test 통계 분석 |
| `rapidfuzz` | 제품명 퍼지 매칭 |
| `langfuse` | LLM 관측 (선택) |
| `apscheduler` | 스케줄 실행 |

---

## 11. v3 변경 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-05-04 | v3 초기 설계: Orchestrator + 3-Agent 분리, Z-score/모멘텀 분석 추가 |
| 2026-06 | DecisionAgent 리팩토링: 3 Builder 구조 (TrendInsight / ProductInsight / InboundPick) |
| 2026-06 | VOC 수집 개선: 최신10 + 도움됨10 혼합, OYKRTool 제거 |
| 2026-06-18 | **Agentic Orchestrator 추가**: Claude API Tool Use 기반 ReAct 루프 |
| 2026-06-18 | **LangFuse 트레이싱 통합**: generation/span 단위 추적, 키 없으면 자동 스킵 |
| 2026-06-19 | **z_score 영속화**: StatsTool이 계산한 z_score를 `fact_sns_signals.z_score`에 저장 |
| 2026-06-19 | **MomentumTool 개편**: 첫 등장 대비 → 최근 3주 추세 기반 (momentum_score + weeks_rising). DecisionAgent 중복 폴백 제거하고 MomentumTool로 통일 |
| 2026-06-19 | **trend_shape 분류 추가**: opportunity_type과 독립된 트렌드 모양 축 (emerging/sustained/spike/steady). HTML 리포트 트렌드 탭에 배지 노출 |
| 2026-06-19 | **TikTok 미국 타겟 수집**: `proxyCountryCode="US"` + `textLanguage=="en"` 필터 (YouTube와 동일 취지). W26부터 순수 미국 타겟 누적 |
| 2026-06-19 | **정기 실행 스케줄링**: `scheduler.py`(APScheduler 상주) + Windows 작업 스케줄러용 배치(run_weekly/run_catalog). 주간 목요일 09:00 / 월간 1일 03:00 |
| 2026-06-19 | **HTML 리포트 대폭 개선**: 트렌드 탭(미니차트 발굴~5주, 카드 클릭 필터+설명, trend_shape/정렬 드롭다운, SNS 근거 인용, 하단 산식·선정기준) / 제품 탭(전략 사분면 산점도, 검색·정렬, 브랜드 티어·부정 VOC, VOC 범례, 하단 분류기준) |
| 2026-06-19 | **VOC 결측 대응**: VocCollector 제품별 재시도(최대 2회) + DecisionAgent 직전 주차 VOC 폴백(`voc_source_week` 기록, 리포트 배지 표시). Sephora 순위 컬럼 제거(수집 불가) |
| 2026-06-19 | **제품 분류 VOC 중심 전환**: 리테일 비중 축소(Top 100 보조) + VOC 모멘텀(리뷰 유입 velocity·별점 추세) 주 신호화. Sephora statistics API + OY `em.star-rating` 스크랩으로 전체 리뷰 수·전체 평점 수집(`total_reviews`·`platform_avg_rating`). 산점도 축을 리테일→리뷰유입×VOC로 전환. velocity는 W26+부터 유효 |
| 2026-06-19 | **방한픽 Pick Score 개편**: 순위 비중 70%→35%, SNS·VOC 22%→65%. SNS를 연결여부(0/1)→트렌드 강도(z·momentum 정규화)로 전환. 순위 정규화 분모 버그 수정(total_korea→Top 100 고정) |
| 2026-06-19 | **제품 탭 VOC 표·헤더 개선**: 주요 VOC 키워드를 표 형식으로 전환(제품별 긍정/부정/니즈 분리 + 리테일 URL 링크 + 사분면 필터 버튼). 브랜드 티어 영어 라벨화. 산식 박스에서 4분면 분류 기준만 제거(지표 산식 유지). 헤더에 주차 라벨(예: "6월 3주차 6/15~6/21") + 데이터 소스(SNS: TikTok/YouTube, Retail: OY Global/Sephora USA) 표기 |
| 2026-06-21 | **프로젝트 정리**: 루트 debug/test/일회성 스크립트 23개 삭제, 레거시 `fact_opportunities` 테이블·export 제거, 미사용 의존성(pytrends·jinja2) 정리, `__pycache__`·routine_tool/google_trends 잔재 제거 |
