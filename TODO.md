# TODO — 향후 작업

기존 분석·판정 로직은 유지하고, 운영·수집·대시보드·검증을 보강한다.

## 12/1 정기 카탈로그 전
- [ ] **카탈로그 속도 개선 — 이미지·폰트·미디어 요청 차단**
  - `tools/collect/oy_global_tool.py` Phase 2 페이지 풀 생성부(`browser.new_context` 직후)에
    `page.route("**/*", ...)` 로 image / font / media 요청 abort
  - 카테고리(`span.loc_cat`)가 그대로 수집되는지 소량(20개) 테스트 후 반영
  - **아코디언 클릭(`link.click()`)에 timeout 미지정 → 기본 30초 대기.** 2개 클릭이라 제품당 최대 1분 낭비
    → `click(timeout=3000)` 로 제한 (11/20 실행에서 50개 묶음당 약 40분의 주원인으로 추정)
  - 함께 확인: `_scrape_product_detail` 의 고정 대기 2초, 카테고리 미검출 시 재방문 비용
  - 배경: 2 vCPU / 4GB 서버에서 상세 50개당 약 30분 (2026-11-20)
  - **11/20 실측: 이미지·폰트·외부 도메인 차단해도 제품당 117~120초로 그대로** → 병목은 네트워크가 아니라
    서버 CPU (OY Vue 앱 렌더링 + 크롬 동시 3~4개). `goto(domcontentloaded)` 자체가 30초 초과.
    → 실제 대안: ① 카탈로그 실행 동안만 큰 인스턴스 사용 ② 상세 페이지 대신 OY 내부 JSON 요청 경로 조사
       (Cloudflare 차단 위험 있으니 소량으로) ③ 성분·효능 아코디언 수집을 빼고 카테고리만 수집
- [ ] 11/20 카탈로그에서 페이지 로드 실패한 브랜드(Centellian24, medicube) 재수집 확인

## 미국 실구매 채널: Amazon Best Sellers 연동 (지원 후, 설계 완료)
- 수집: Apify Amazon Best Sellers 액터 (공식 API PA-API 5는 2026 상반기 종료 → Creators API는 Associates 실적 필요라 제외)
  - Beauty › Skin Care 하위 8개 리스트 × 100위 = 주 800건 → **월 약 $17** (junglee $5/1K, 저가 액터면 $4~7)
  - Apify 클라우드에서 수집 → 서버 CPU 부담 없음
- [ ] 1단계 (4~6시간, 판정 로직 불변): `tools/collect/amazon_tool.py` (Apify 패턴은 `tiktok_tool.py` 재사용)
  → `_known_brand_map` 으로 K-브랜드만 → `find_matching_product` 매칭(같은 브랜드 내 퍼지 85)
  → `fact_retail_rankings` platform `amazon_us` → DAG `collect_amazon` 병렬 태스크 + `data_quality` MIN_ROWS
  → 리포트 제품·방한 탭에 Amazon 순위 열
  - 검증: K-브랜드 통과 건수, 기존 제품 매칭률, 매칭 20건 수작업 대조
- [ ] 2단계 (+2~3시간, **판정 로직 변경 → 승인 필요**): Pick Score 리테일 인기 = max(OY, Sephora, Amazon), 4분면 retail_score 반영
- [ ] 3단계 (+반나절): Amazon 리뷰 VOC (리뷰 액터 $0.3~1.5/1K)

## 12/3 이후
- [ ] W47 → W49 2주 뒤 유지율 계산 (기존 기준: Z ≥ 2.0, trend_shape)

## 운영·검증
- [ ] 과거 주차(W23·W27) backfill 로 Z-score 채운 뒤 유지율 백테스트
- [ ] 정답 세트 라벨링 약 50건 → LLM 판정 에이전트 일치도(κ, Gwet's AC1)
- [ ] LLM 토큰·비용 로그 (인사이트 생성 단계 `response.usage`)
- [ ] Sephora 성분 수집 403 대응

## 대시보드
- [ ] 3탭 전면 개편: 트렌드 레이더 / 브랜드 점유율(SoV) / 제품·VOC

## 1월 초 (연속 8주 데이터 축적 후)
- [ ] Z-score 기준선 기간 재검토 (현재는 수집 공백 때문에 과거 전체 주차 평균)
