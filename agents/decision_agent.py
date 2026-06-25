"""
decision_agent.py
──────────────────
의사결정 에이전트.

3단계 인사이트 생성:
  Step 1. TrendInsightBuilder  — SNS 트렌드 키워드 해석 + AP 제품 연결 (VOC 없음)
  Step 2. ProductInsightBuilder — AP 제품별 VOC + 리테일 분석 + 전략 사분면
           └ VOC 수집 트리거: fact_llm_insight_products + fact_retail_rankings 대상
  Step 3. InboundPickBuilder   — 방한 관광객 추천 제품 선정 (VOC 보조, Step 2 재사용)
  Step 4. ReportBuilder        — HTML 리포트 + CSV 익스포트

기회 유형 (opportunity_type):
  amplify   : AP 제품 보유 + 높은 모멘텀 → 즉시 강화
  position  : AP 제품 보유 + 경쟁사 동반 언급 → 차별화 포지셔닝
  counter   : AP 제품 없음 + 경쟁사 점유 → 대응 필요
  new_entry : AP 제품 없음 + 경쟁사 없음 → 신규 진입 기회

전략 사분면 (strategy_quad):
  PUSH_NOW    : 리테일 상위 + VOC 긍정 ≥ 0.65
  FIX_AND_PUSH: 리테일 상위 + VOC 긍정 < 0.65
  HOLD        : 리테일 하위 + VOC 긍정 ≥ 0.65
  MONITOR     : 리테일 하위 + VOC 긍정 < 0.65
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

import pandas as pd

from memory.agent_memory import AgentMemory
from tools.analyze.momentum_tool      import MomentumTool
from tools.report.html_report_tool    import HTMLReportTool
from tools.report.tableau_export_tool import TableauExportTool

logger = logging.getLogger(__name__)


class DecisionAgent:
    """분석 결과를 받아 3단계 인사이트 생성 + 리포트 출력."""

    def __init__(self, memory: AgentMemory):
        self.memory  = memory
        self.db_path = memory.db_path
        self.week    = memory.get_current_week()

        export_dir = str(Path(self.db_path).parent.parent / "exports")
        self.html_report = HTMLReportTool(db_path=self.db_path, export_dir=export_dir)
        self.csv_export  = TableauExportTool(db_path=self.db_path, export_dir=export_dir)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ─────────────────────────────────────────────────────────
    # Step 1. TrendInsightBuilder
    # ─────────────────────────────────────────────────────────
    def _build_trend_insights(self, analysis: dict) -> int:
        """
        SNS 트렌드 키워드 해석 + AP 제품 연결.
        입력: sns_anomalies, momentum, llm_insights (analysis dict)
        출력: fact_trend_insights
        """
        sns_df      = analysis.get("sns_anomalies", pd.DataFrame())
        momentum_df = analysis.get("momentum",      pd.DataFrame())
        llm_list    = analysis.get("llm_insights",  [])

        # analysis에 없으면 DB에서 직접 조회
        if not llm_list:
            logger.info("TrendInsight: analysis에 llm_insights 없음, DB 조회")
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM fact_llm_insights WHERE week=?", (self.week,)
                ).fetchall()
                llm_list = [dict(r) for r in rows]

        # sns_anomalies가 빈 DataFrame이면 DB에서 직접 조회
        if sns_df.empty:
            logger.info("TrendInsight: sns_anomalies 없음, DB 조회")
            with self._conn() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(fact_sns_signals)").fetchall()]
                z_col = "z_score" if "z_score" in cols else "0.0 AS z_score"
                rows = conn.execute(
                    f"SELECT *, {z_col} FROM fact_sns_signals WHERE week=?", (self.week,)
                ).fetchall()
                if rows:
                    sns_df = pd.DataFrame([dict(r) for r in rows])

        # momentum이 빈 DataFrame이면 MomentumTool로 직접 계산 (최근 3주 추세)
        if momentum_df.empty:
            logger.info("TrendInsight: momentum 없음, MomentumTool 3주 추세 계산")
            momentum_df = MomentumTool(self.db_path).compute_momentum(self.week)

        if not llm_list:
            logger.warning("TrendInsight: 인사이트 데이터 없음 — 스킵")
            return 0

        # keyword → 연결 AP 제품 매핑
        with self._conn() as conn:
            ip_rows = conn.execute("""
                SELECT lip.keyword, lip.product_id, lip.match_type,
                       p.brand_id, p.product_name_en, b.tier
                FROM fact_llm_insight_products lip
                JOIN dim_product p ON lip.product_id = p.product_id
                JOIN dim_brand   b ON p.brand_id = b.brand_id
                WHERE lip.week = ?
            """, (self.week,)).fetchall()

        kw_to_products: dict[str, list[dict]] = {}
        for row in ip_rows:
            kw = row["keyword"]
            kw_to_products.setdefault(kw, []).append(dict(row))

        saved = 0
        for insight in llm_list:
            kw = insight.get("keyword", "")
            if not kw:
                continue

            # SNS 신호
            sns_row: dict = {}
            if not sns_df.empty and "keyword" in sns_df.columns:
                match = sns_df[sns_df["keyword"].str.lower() == kw.lower()]
                if not match.empty:
                    sns_row = match.iloc[0].to_dict()

            # 모멘텀 신호
            mom_row: dict = {}
            if not momentum_df.empty and "keyword" in momentum_df.columns:
                match = momentum_df[momentum_df["keyword"].str.lower() == kw.lower()]
                if not match.empty:
                    mom_row = match.iloc[0].to_dict()

            # 연결 AP 제품
            linked   = kw_to_products.get(kw, [])
            prod_ids = [p["product_id"] for p in linked]
            brand_ids = list({p["brand_id"] for p in linked})

            # 기회 유형 분류
            has_ap        = bool(linked)
            momentum_score = float(mom_row.get("momentum_score", 0.0))
            weeks_rising   = int(mom_row.get("weeks_rising", 0))
            z_score        = float(sns_row.get("z_score", 0.0) or 0.0)
            is_new_kw      = int(sns_row.get("is_new_keyword", 0) or 0)
            # 최근 3주 연속 상승(weeks_rising==2)하며 누적 성장 시 상승 트렌드로 판단
            is_rising_trend = weeks_rising >= 2 and momentum_score >= 1.5

            # 트렌드 모양 분류 (opportunity_type과 독립된 축)
            #   emerging : 이번 주 신규 등장 키워드
            #   sustained: 최근 3주 지속 상승
            #   spike    : 베이스라인 대비 순간 급등(z≥2)이나 지속성 없음
            #   steady   : 그 외 (완만/정체)
            if is_new_kw:
                trend_shape = "emerging"
            elif is_rising_trend:
                trend_shape = "sustained"
            elif z_score >= 2.0:
                trend_shape = "spike"
            else:
                trend_shape = "steady"

            try:
                competitors = json.loads(insight.get("competitor_mentions") or "{}")
                if not isinstance(competitors, dict):
                    competitors = {}
            except Exception:
                competitors = {}
            has_competitor = bool(competitors)

            if has_ap and is_rising_trend:
                opp_type = "amplify"
            elif has_ap and has_competitor:
                opp_type = "position"
            elif not has_ap and has_competitor:
                opp_type = "counter"
            else:
                opp_type = "new_entry"

            insight_summary = self._trend_insight_text(
                kw, opp_type, brand_ids,
                insight.get("consumer_need", ""),
                insight.get("opportunity", ""),
                competitors, sns_row,
            )
            action_rec = self._trend_action_text(kw, opp_type, brand_ids, insight, mom_row)

            try:
                with self._conn() as conn:
                    # 기존 DB에 trend_shape 컬럼 없으면 추가
                    cols = [r[1] for r in conn.execute("PRAGMA table_info(fact_trend_insights)").fetchall()]
                    if "trend_shape" not in cols:
                        conn.execute("ALTER TABLE fact_trend_insights ADD COLUMN trend_shape TEXT")
                    conn.execute("""
                        INSERT OR REPLACE INTO fact_trend_insights
                            (week, keyword, z_score, momentum_score, trend_shape,
                             lead_platform, is_cross_platform,
                             ap_brand_ids, ap_product_ids, opportunity_type,
                             consumer_need, competitor_mentions,
                             insight_summary, action_rec)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        self.week, kw,
                        round(z_score, 2),
                        round(momentum_score, 2),
                        trend_shape,
                        mom_row.get("lead_platform"),
                        int(mom_row.get("is_cross_platform", 0)),
                        json.dumps(brand_ids),
                        json.dumps(prod_ids),
                        opp_type,
                        insight.get("consumer_need", ""),
                        json.dumps(competitors),
                        insight_summary,
                        action_rec,
                    ))
                saved += 1
            except Exception as e:
                logger.warning(f"TrendInsight 저장 실패 ({kw}): {e}")

        logger.info(f"TrendInsight: {saved}건 저장")
        return saved

    def _trend_insight_text(
        self, kw, opp_type, brand_ids, consumer_need, opportunity, competitors, sns_row
    ) -> str:
        # AP/경쟁사 관계만 서술 (Z·포스트 등 통계 수치, 소비자 니즈는 제외)
        brands   = ", ".join(b.upper() for b in brand_ids) if brand_ids else "없음"
        comp_str = ", ".join(competitors.keys()) if competitors else ""

        if opp_type == "amplify":
            return f"AP 브랜드({brands})가 해당 포지션을 점유 중 — 즉시 확장 가능."
        elif opp_type == "position":
            return f"AP({brands}) 제품이 있으나 경쟁사({comp_str})와 동반 언급 중."
        elif opp_type == "counter":
            return f"경쟁사({comp_str})가 선점 중이며 AP 대응 제품 부재."
        else:
            return f"AP 미진입 화이트스페이스 — {opportunity or '신규 진입 검토 필요'}."

    def _trend_action_text(self, kw, opp_type, brand_ids, insight, mom_row) -> str:
        brands = ", ".join(b.upper() for b in brand_ids) if brand_ids else ""
        lead   = mom_row.get("lead_platform", "TikTok")
        opp    = insight.get("opportunity", "")

        if opp_type == "amplify":
            return (
                f"{brands} 브랜드의 '{kw}' 중심 {lead} 콘텐츠 강화. "
                "인플루언서 협업 및 검색광고 입찰 즉시 검토."
            )
        elif opp_type == "position":
            return (
                f"{brands} 제품 차별화 메시지 강화. "
                f"경쟁사 대비 고유 편익을 {lead} 콘텐츠에 부각."
            )
        elif opp_type == "counter":
            return (
                f"'{kw}' 포지션 대응을 위한 기존 라인 확장 검토. "
                f"경쟁사 점유 전 {lead} 노출 선점."
            )
        else:
            return (
                f"'{kw}' 트렌드 대응 신제품 기획 검토. "
                + (f"기회: {opp}." if opp else "카테고리 진입 가능성 평가 권장.")
            )

    # ─────────────────────────────────────────────────────────
    # Step 2. ProductInsightBuilder
    # ─────────────────────────────────────────────────────────
    def _get_voc_targets(self) -> list[dict]:
        """VOC 수집 대상: fact_llm_insight_products + fact_retail_rankings 합집합."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT p.product_id, p.sephora_pid, p.oy_prdtno
                FROM dim_product p
                WHERE p.product_id IN (
                    SELECT product_id FROM fact_llm_insight_products WHERE week=?
                    UNION
                    SELECT product_id FROM fact_retail_rankings WHERE week=?
                )
            """, (self.week, self.week)).fetchall()

        targets = []
        for r in rows:
            if r["sephora_pid"]:
                targets.append({
                    "product_id":   r["product_id"],
                    "platform":     "sephora_us",
                    "platform_pid": r["sephora_pid"],
                })
            if r["oy_prdtno"]:
                targets.append({
                    "product_id":   r["product_id"],
                    "platform":     "oy_global",
                    "platform_pid": r["oy_prdtno"],
                })
        return targets

    def _trigger_voc(self, targets: list[dict]) -> int:
        """이미 수집된 (platform, product) 제외 후 VOC 수집 + GPT 감성 분석."""
        from tools.collect.voc_collector import VocCollector
        from tools.analyze.voc_tool      import VOCTool

        with self._conn() as conn:
            existing = {
                (r[0], r[1]) for r in conn.execute(
                    "SELECT platform_id, product_id FROM fact_voc_signals WHERE week=?",
                    (self.week,)
                ).fetchall()
            }

        new_targets = [
            t for t in targets
            if (t["platform"], t["product_id"]) not in existing
        ]
        if not new_targets:
            logger.info("VOC 트리거: 모두 기수집 — 스킵")
            return 0

        logger.info(f"VOC 트리거: {len(new_targets)}개 제품 수집 시작")

        collector = VocCollector(self.db_path)
        try:
            result = collector.collect(new_targets, self.week)
            logger.info(f"VOC 수집: {result}")
        except Exception as e:
            logger.warning(f"VOC 수집 실패: {e}")
            return 0
        platform_stats = collector.platform_stats

        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not openai_key:
            logger.warning("OPENAI_API_KEY 없음 — VOC 감성 분석 스킵")
            return result.get("total", 0)

        voc_tool = VOCTool(self.db_path, openai_key)
        saved = 0
        for t in new_targets:
            with self._conn() as conn:
                raw_rows = conn.execute("""
                    SELECT review_text, rating FROM fact_raw_reviews
                    WHERE week=? AND platform_id=? AND product_id=?
                """, (self.week, t["platform"], t["product_id"])).fetchall()
            if not raw_rows:
                continue
            reviews    = [r["review_text"] for r in raw_rows]
            avg_rating = round(
                sum(float(r["rating"] or 3.0) for r in raw_rows) / len(raw_rows), 2
            )
            pstat = platform_stats.get((t["platform"], t["product_id"]), {})
            ok = voc_tool.analyze_and_store(
                platform_id=t["platform"],
                product_id=t["product_id"],
                reviews=reviews,
                avg_rating=avg_rating,
                review_count=len(reviews),
                week=self.week,
                total_reviews=pstat.get("total_reviews"),
                platform_avg_rating=pstat.get("platform_avg_rating"),
            )
            if ok:
                saved += 1

        logger.info(f"VOC 감성 분석 저장: {saved}건")
        return saved

    def _build_product_insights(self) -> int:
        """
        AP 제품별 리테일 + VOC 분석 → 전략 사분면.
        VOC 수집 트리거 포함.
        출력: fact_product_insights
        """
        voc_targets = self._get_voc_targets()
        self._trigger_voc(voc_targets)

        with self._conn() as conn:
            products = conn.execute("""
                SELECT DISTINCT p.product_id, p.brand_id, p.product_name_en,
                                p.category_main, b.tier
                FROM dim_product p
                JOIN dim_brand b ON p.brand_id = b.brand_id
                WHERE p.product_id IN (
                    SELECT product_id FROM fact_llm_insight_products WHERE week=?
                    UNION
                    SELECT product_id FROM fact_retail_rankings WHERE week=?
                )
            """, (self.week, self.week)).fetchall()

            retail_rows = conn.execute("""
                SELECT product_id, platform_id, rank_position
                FROM fact_retail_rankings WHERE week=?
            """, (self.week,)).fetchall()

            _vcols = [r[1] for r in conn.execute("PRAGMA table_info(fact_voc_signals)").fetchall()]
            _tr  = "total_reviews"       if "total_reviews"       in _vcols else "NULL AS total_reviews"
            _par = "platform_avg_rating" if "platform_avg_rating" in _vcols else "NULL AS platform_avg_rating"
            voc_rows = conn.execute(f"""
                SELECT product_id, sentiment_pos, sentiment_neg,
                       pos_keywords, neg_keywords, needs_keywords,
                       avg_rating, review_count, {_tr}, {_par}
                FROM fact_voc_signals WHERE week=?
            """, (self.week,)).fetchall()

        # 리테일: product_id → {platform: rank}
        retail_map: dict[str, dict[str, int]] = {}
        for r in retail_rows:
            retail_map.setdefault(r["product_id"], {})[r["platform_id"]] = r["rank_position"]

        # VOC: product_id → 집계
        voc_map: dict[str, dict] = {}
        for r in voc_rows:
            pid = r["product_id"]
            if pid not in voc_map:
                voc_map[pid] = {
                    "pos": [], "neg": [], "rating": [],
                    "pos_kws": [], "neg_kws": [], "needs_kws": [],
                    "review_count": 0, "total_reviews": 0, "platform_rating": [],
                }
            v = voc_map[pid]
            if r["sentiment_pos"] is not None:
                v["pos"].append(float(r["sentiment_pos"]))
            if r["sentiment_neg"] is not None:
                v["neg"].append(float(r["sentiment_neg"]))
            if r["avg_rating"] is not None:
                v["rating"].append(float(r["avg_rating"]))
            v["pos_kws"].extend(json.loads(r["pos_keywords"]  or "[]"))
            v["neg_kws"].extend(json.loads(r["neg_keywords"]  or "[]"))
            v["needs_kws"].extend(json.loads(r["needs_keywords"] or "[]"))
            v["review_count"] += r["review_count"] or 0
            if r["total_reviews"] is not None:
                v["total_reviews"] += int(r["total_reviews"])
            if r["platform_avg_rating"] is not None:
                v["platform_rating"].append(float(r["platform_avg_rating"]))

        # 직전 주차 전체 통계 (velocity·별점추세 계산용)
        prev_stats = self._get_prev_voc_stats([p["product_id"] for p in products])

        # 이번 주 VOC 없는 제품 → 직전 주차 VOC 폴백
        missing_pids = [p["product_id"] for p in products if p["product_id"] not in voc_map]
        fallback_voc = self._get_voc_fallback(missing_pids) if missing_pids else {}
        if fallback_voc:
            logger.info(f"VOC 폴백: {len(fallback_voc)}개 제품 직전 주차 값 사용")

        # 기존 DB에 voc_source_week 컬럼 없으면 추가
        with self._conn() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(fact_product_insights)").fetchall()]
            if "voc_source_week" not in cols:
                conn.execute("ALTER TABLE fact_product_insights ADD COLUMN voc_source_week TEXT")
            for newcol, coltype in [("voc_velocity", "INTEGER"), ("rating_trend", "REAL"),
                                    ("total_reviews", "INTEGER")]:
                if newcol not in cols:
                    conn.execute(f"ALTER TABLE fact_product_insights ADD COLUMN {newcol} {coltype}")

        saved = 0
        for p in products:
            pid  = p["product_id"]
            tier = p["tier"] or "korean_daily"

            ranks      = retail_map.get(pid, {})
            best_rank  = min(ranks.values()) if ranks else None
            # 리테일은 보조 신호: Top 100 기준 (100위까지 우수)
            retail_score = max(0.0, 1.0 - (best_rank - 1) / 100.0) if best_rank else 0.0

            voc = voc_map.get(pid, {})
            voc_source = self.week  # 기본: 현재 주 (신선)
            if not voc and pid in fallback_voc:
                voc = fallback_voc[pid]
                voc_source = voc.get("source_week")

            sentiment_pos = round(sum(voc["pos"]) / len(voc["pos"]), 3) if voc.get("pos") else None
            sentiment_neg = round(sum(voc["neg"]) / len(voc["neg"]), 3) if voc.get("neg") else None
            avg_rating    = round(sum(voc["rating"]) / len(voc["rating"]), 2) if voc.get("rating") else None
            pos_kws   = list(dict.fromkeys(voc.get("pos_kws",   [])))[:8]
            neg_kws   = list(dict.fromkeys(voc.get("neg_kws",   [])))[:8]
            needs_kws = list(dict.fromkeys(voc.get("needs_kws", [])))[:6]
            # VOC가 전혀 없으면 출처도 없음
            if sentiment_pos is None and avg_rating is None:
                voc_source = None

            # VOC velocity (리뷰 유입 증감) + 별점 추세
            cur_total  = voc.get("total_reviews") or 0
            cur_prat   = (round(sum(voc["platform_rating"]) / len(voc["platform_rating"]), 3)
                          if voc.get("platform_rating") else None)
            prev = prev_stats.get(pid, {})
            voc_velocity = (cur_total - prev["total_reviews"]
                            if cur_total and prev.get("total_reviews") else None)
            rating_trend = (round(cur_prat - prev["platform_rating"], 3)
                            if cur_prat is not None and prev.get("platform_rating") is not None else None)
            total_reviews_out = cur_total or None

            quad = self._strategy_quad(
                retail_score, sentiment_pos, sentiment_neg, voc_velocity, rating_trend
            )

            insight_summary = self._product_insight_text(
                p["product_name_en"], p["brand_id"], tier,
                quad, ranks, pos_kws, neg_kws
            )
            action_rec = self._product_action_text(tier, quad, needs_kws)

            try:
                with self._conn() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO fact_product_insights
                            (week, product_id, brand_id, brand_tier,
                             oy_rank_orders, oy_rank_korea, sephora_rank,
                             retail_score,
                             sentiment_pos, sentiment_neg, avg_rating, voc_source_week,
                             voc_velocity, rating_trend, total_reviews,
                             pos_keywords, neg_keywords, needs_keywords,
                             strategy_quad, insight_summary, action_rec)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        self.week, pid, p["brand_id"], tier,
                        ranks.get("oy_top_orders"),
                        ranks.get("oy_top_korea"),
                        ranks.get("sephora_us"),
                        round(retail_score, 3),
                        sentiment_pos, sentiment_neg, avg_rating, voc_source,
                        voc_velocity, rating_trend, total_reviews_out,
                        json.dumps(pos_kws),
                        json.dumps(neg_kws),
                        json.dumps(needs_kws),
                        quad, insight_summary, action_rec,
                    ))
                saved += 1
            except Exception as e:
                logger.warning(f"ProductInsight 저장 실패 ({pid}): {e}")

        logger.info(f"ProductInsight: {saved}건 저장")
        return saved

    def _get_voc_fallback(self, product_ids: list[str]) -> dict:
        """
        이번 주 VOC가 없는 제품에 대해, 가장 최근 과거 주차의 VOC를 가져온다.
        반환: {product_id: {pos, neg, rating, pos_kws, neg_kws, needs_kws, source_week}}
        """
        result: dict = {}
        if not product_ids:
            return result

        with self._conn() as conn:
            for pid in product_ids:
                # 현재 주 이전에서 VOC가 있는 가장 최근 주차
                src = conn.execute("""
                    SELECT week FROM fact_voc_signals
                    WHERE product_id=? AND week < ?
                    ORDER BY week DESC LIMIT 1
                """, (pid, self.week)).fetchone()
                if not src:
                    continue
                src_week = src["week"]

                rows = conn.execute("""
                    SELECT sentiment_pos, sentiment_neg, avg_rating,
                           pos_keywords, neg_keywords, needs_keywords
                    FROM fact_voc_signals
                    WHERE product_id=? AND week=?
                """, (pid, src_week)).fetchall()

                agg = {"pos": [], "neg": [], "rating": [],
                       "pos_kws": [], "neg_kws": [], "needs_kws": [],
                       "source_week": src_week}
                for r in rows:
                    if r["sentiment_pos"] is not None:
                        agg["pos"].append(float(r["sentiment_pos"]))
                    if r["sentiment_neg"] is not None:
                        agg["neg"].append(float(r["sentiment_neg"]))
                    if r["avg_rating"] is not None:
                        agg["rating"].append(float(r["avg_rating"]))
                    agg["pos_kws"].extend(json.loads(r["pos_keywords"]   or "[]"))
                    agg["neg_kws"].extend(json.loads(r["neg_keywords"]   or "[]"))
                    agg["needs_kws"].extend(json.loads(r["needs_keywords"] or "[]"))
                if agg["pos"] or agg["rating"]:
                    result[pid] = agg
        return result

    def _strategy_quad(
        self,
        retail_score: float,
        sentiment_pos: float | None,
        sentiment_neg: float | None = None,
        voc_velocity: int | None = None,
        rating_trend: float | None = None,
    ) -> str:
        """
        VOC 중심 분류 (리테일은 보조).
          주 신호: VOC 긍정도 + 모멘텀(리뷰 유입 velocity, 별점 추세)
          보조:    retail_score (Top 100 기준, 동점 가산)

        - PUSH_NOW    : 긍정 + 상승 모멘텀 (유입↑ 또는 별점↑)
        - FIX_AND_PUSH: 관심↑(유입↑)인데 불만↑(별점↓ 또는 부정↑)
        - HOLD        : 긍정이나 모멘텀 약함 (정체)
        - MONITOR     : 그 외
        """
        voc_pos = sentiment_pos if sentiment_pos is not None else 0.0
        voc_neg = sentiment_neg if sentiment_neg is not None else 0.0
        has_voc = sentiment_pos is not None

        rising   = (voc_velocity is not None and voc_velocity > 0) or \
                   (rating_trend is not None and rating_trend > 0)
        declining = (rating_trend is not None and rating_trend < 0) or voc_neg >= 0.35
        inflow_up = voc_velocity is not None and voc_velocity > 0

        is_positive = (has_voc and voc_pos >= 0.65) or retail_score >= 0.7

        if is_positive and rising and not declining:
            return "PUSH_NOW"
        elif inflow_up and (declining or not is_positive):
            return "FIX_AND_PUSH"
        elif is_positive:
            return "HOLD"
        else:
            return "MONITOR"

    def _get_prev_voc_stats(self, product_ids: list[str]) -> dict:
        """
        각 제품의 직전(현재 주 이전) 주차 전체 통계: {pid: {total_reviews, platform_rating}}.
        velocity·별점 추세 계산용.
        """
        result: dict = {}
        if not product_ids:
            return result
        with self._conn() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(fact_voc_signals)").fetchall()]
            if "total_reviews" not in cols:
                return result
            for pid in product_ids:
                src = conn.execute("""
                    SELECT week FROM fact_voc_signals
                    WHERE product_id=? AND week < ? AND total_reviews IS NOT NULL
                    ORDER BY week DESC LIMIT 1
                """, (pid, self.week)).fetchone()
                if not src:
                    continue
                rows = conn.execute("""
                    SELECT total_reviews, platform_avg_rating
                    FROM fact_voc_signals WHERE product_id=? AND week=?
                """, (pid, src["week"])).fetchall()
                tot = sum(int(r["total_reviews"]) for r in rows if r["total_reviews"] is not None)
                prats = [float(r["platform_avg_rating"]) for r in rows if r["platform_avg_rating"] is not None]
                result[pid] = {
                    "total_reviews":   tot or None,
                    "platform_rating": round(sum(prats) / len(prats), 3) if prats else None,
                }
        return result

    def _product_insight_text(self, name, brand_id, tier, quad, ranks, pos_kws, neg_kws) -> str:
        rank_str = ", ".join(f"{k}:{v}위" for k, v in ranks.items()) if ranks else "랭킹 없음"
        pos_str  = ", ".join(pos_kws[:3]) if pos_kws else ""
        neg_str  = ", ".join(neg_kws[:2]) if neg_kws else ""
        labels   = {"PUSH_NOW": "즉시 강화", "FIX_AND_PUSH": "개선 후 강화",
                    "HOLD": "유지/관찰", "MONITOR": "모니터링"}
        txt = f"{brand_id.upper()} '{name}' — {labels.get(quad, quad)} [{rank_str}]."
        if pos_str:
            txt += f" 긍정 반응: {pos_str}."
        if neg_str:
            txt += f" 개선 필요: {neg_str}."
        return txt

    def _product_action_text(self, tier, quad, needs_kws) -> str:
        needs = ", ".join(needs_kws[:3]) if needs_kws else ""
        if quad == "PUSH_NOW":
            return f"리뷰 기반 긍정 메시지 강화 + 유통 확대 권장.{f' 소비자 니즈: {needs}.' if needs else ''}"
        elif quad == "FIX_AND_PUSH":
            return f"부정 피드백 해소 후 마케팅 강화. 제품 개선 포인트: {needs or '성분/텍스처 점검'}."
        elif quad == "HOLD":
            return "충성 고객 리텐션 집중. 신규 유통 채널 진출 검토."
        else:
            return (
                f"성과 모니터링 지속."
                + (f" 소비자 니즈({needs}) 반영한 리포지셔닝 검토." if needs else " 전략 재수립 필요.")
            )

    # ─────────────────────────────────────────────────────────
    # Step 3. InboundPickBuilder
    # ─────────────────────────────────────────────────────────
    def _build_inbound_picks(self) -> int:
        """
        방한 관광객 추천 제품 선정 (Top 15).
        기준(2026-06 개편 — Sephora 포함 + SNS 강화):
          pick_score = 0.35 × SNS 트렌드 강도 + 0.35 × VOC 긍정도 + 0.30 × 리테일 인기

        · 후보 풀 = OY(한국 인기 ∪ 글로벌 주문) ∪ Sephora(리뷰 활성 제품)
        · 리테일 인기 = OY 한국 랭킹 · OY 글로벌 랭킹 · Sephora 인기 중 최고값
        · Sephora 인기 = 리뷰 유입 속도(velocity) 우선, 없으면 누적 리뷰 수 log 보조(디스카운트)
        · SNS 트렌드 강도 = 연결 트렌드 키워드의 z_score/momentum 정규화 블렌드
        출력: fact_inbound_picks
        """
        import math
        with self._conn() as conn:
            # 후보 풀 = OY(한국 인기 ∪ 글로벌 주문) ∪ Sephora(리뷰 활성 제품)
            #   OY 랭킹 + 미국 현지(Sephora) 인기까지 포함해 방한 동선을 폭넓게 반영
            korea_map: dict[str, int] = {
                r["product_id"]: r["rank_position"]
                for r in conn.execute("""
                    SELECT product_id, rank_position FROM fact_retail_rankings
                    WHERE week=? AND platform_id='oy_top_korea'
                """, (self.week,)).fetchall()
            }
            orders_map: dict[str, int] = {
                r["product_id"]: r["rank_position"]
                for r in conn.execute("""
                    SELECT product_id, rank_position FROM fact_retail_rankings
                    WHERE week=? AND platform_id='oy_top_orders'
                """, (self.week,)).fetchall()
            }
            # Sephora 인기 신호: 전체 리뷰 수 + 평점 (랭킹 접근 불가 → 리뷰로 인기 대리)
            sephora_stats: dict[str, dict] = {}
            for r in conn.execute("""
                SELECT product_id, total_reviews, platform_avg_rating
                FROM fact_voc_signals
                WHERE week=? AND platform_id='sephora_us' AND total_reviews IS NOT NULL
            """, (self.week,)).fetchall():
                sephora_stats[r["product_id"]] = {
                    "total_reviews": r["total_reviews"],
                    "rating": r["platform_avg_rating"],
                }

            # 합집합 후보 (제품 메타 포함)
            cand_ids = set(korea_map) | set(orders_map) | set(sephora_stats)
            candidates = []
            if cand_ids:
                ph = ",".join("?" * len(cand_ids))
                for r in conn.execute(f"""
                    SELECT p.product_id, p.brand_id, p.product_name_en, b.tier
                    FROM dim_product p JOIN dim_brand b ON p.brand_id = b.brand_id
                    WHERE p.product_id IN ({ph})
                """, tuple(cand_ids)).fetchall():
                    candidates.append(r)

            # 제품 → 연결 트렌드 키워드
            prod_kws: dict[str, list[str]] = {}
            for r in conn.execute(
                "SELECT DISTINCT product_id, keyword FROM fact_llm_insight_products WHERE week=?",
                (self.week,)
            ).fetchall():
                prod_kws.setdefault(r["product_id"], []).append(r["keyword"])
            sns_linked: set[str] = set(prod_kws.keys())

            # 키워드 → (z_score, momentum_score) — SNS 트렌드 강도 계산용
            kw_signal: dict[str, tuple] = {
                r["keyword"]: (r["z_score"] or 0.0, r["momentum_score"] or 0.0)
                for r in conn.execute(
                    "SELECT keyword, z_score, momentum_score FROM fact_trend_insights WHERE week=?",
                    (self.week,)
                ).fetchall()
            }

            voc_pos_map: dict[str, float] = {}
            voc_kw_map:  dict[str, list]  = {}
            for r in conn.execute("""
                SELECT product_id,
                       AVG(sentiment_pos) as avg_pos,
                       GROUP_CONCAT(pos_keywords, '||') as kws_raw
                FROM fact_voc_signals
                WHERE week=?
                GROUP BY product_id
            """, (self.week,)).fetchall():
                if r["avg_pos"] is not None:
                    voc_pos_map[r["product_id"]] = round(float(r["avg_pos"]), 3)
                if r["kws_raw"]:
                    all_kws = []
                    for chunk in r["kws_raw"].split("||"):
                        try:
                            all_kws.extend(json.loads(chunk))
                        except Exception:
                            pass
                    voc_kw_map[r["product_id"]] = all_kws

        if not candidates:
            logger.warning("InboundPick: 리테일 랭킹 후보 없음 — 스킵")
            return 0

        # Sephora velocity 계산용 직전 주차 전체 리뷰 수
        sephora_prev = self._get_prev_voc_stats(list(sephora_stats.keys())) if sephora_stats else {}

        picks = []
        for row in candidates:
            pid         = row["product_id"]
            korea_rank  = korea_map.get(pid)
            orders_rank = orders_map.get(pid)
            voc_pos     = voc_pos_map.get(pid, 0.0)
            is_sns      = pid in sns_linked

            # SNS 트렌드 강도: 연결 키워드 중 최고 신호 (z/momentum 정규화 블렌드)
            sns_strength = 0.0
            for kw in prod_kws.get(pid, []):
                z, mom = kw_signal.get(kw, (0.0, 0.0))
                z_norm   = min(1.0, z / 10.0)      # z≥10 → 1.0
                mom_norm = min(1.0, mom / 3.0)     # 3배 성장 → 1.0
                sns_strength = max(sns_strength, 0.6 * z_norm + 0.4 * mom_norm)

            # ── 리테일 인기 점수 = OY 랭킹 · Sephora 인기 중 최고값 ──
            oy_korea_score  = max(0.0, 1.0 - (korea_rank - 1) / 100.0)  if korea_rank  else 0.0
            oy_orders_score = max(0.0, 1.0 - (orders_rank - 1) / 100.0) if orders_rank else 0.0
            # Sephora 인기: 리뷰 유입 속도(velocity) 우선, 없으면 누적 리뷰 수 log 보조(디스카운트)
            sephora_score = 0.0
            if pid in sephora_stats:
                cur_tot = sephora_stats[pid]["total_reviews"] or 0
                prev_tot = sephora_prev.get(pid, {}).get("total_reviews")
                if prev_tot and cur_tot > prev_tot:
                    # velocity: 주간 리뷰 증가율 정규화 (50% 증가 → 1.0)
                    sephora_score = min(1.0, (cur_tot - prev_tot) / prev_tot / 0.5)
                elif cur_tot:
                    # 보조: 누적 리뷰 수 log 정규화 후 0.6 디스카운트 (스테디셀러 편향 완화)
                    sephora_score = 0.6 * min(1.0, math.log10(cur_tot + 1) / 3.0)
            retail_pop = max(oy_korea_score, oy_orders_score, sephora_score)

            pick_score = round(
                0.35 * sns_strength + 0.35 * voc_pos + 0.30 * retail_pop,
                3,
            )

            pos_kws = list(dict.fromkeys(voc_kw_map.get(pid, [])))[:5]
            picks.append({
                "product_id":  pid,
                "brand_id":    row["brand_id"],
                "brand_tier":  row["tier"],
                "korea_rank":  korea_rank,
                "orders_rank": orders_rank,
                "voc_pos":     voc_pos,
                "sns_linked":  int(is_sns),
                "pick_score":  pick_score,
                "pos_keywords": json.dumps(pos_kws),
                "pick_reason": self._inbound_reason(
                    row["product_name_en"], row["brand_id"],
                    korea_rank, orders_rank, voc_pos, is_sns, pos_kws,
                    sephora_pop=(not korea_rank and not orders_rank and pid in sephora_stats),
                ),
            })

        picks.sort(key=lambda x: x["pick_score"], reverse=True)

        # 이전 실행 결과 제거 (후보·순위가 바뀌어도 잔존 방지)
        with self._conn() as conn:
            conn.execute("DELETE FROM fact_inbound_picks WHERE week=?", (self.week,))

        saved = 0
        for i, pick in enumerate(picks[:15], 1):
            try:
                with self._conn() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO fact_inbound_picks
                            (week, rank, product_id, brand_id, brand_tier,
                             korea_rank, orders_rank, voc_pos, sns_linked,
                             pick_score, pos_keywords, pick_reason)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        self.week, i,
                        pick["product_id"], pick["brand_id"], pick["brand_tier"],
                        pick["korea_rank"],  pick["orders_rank"],
                        pick["voc_pos"],     pick["sns_linked"],
                        pick["pick_score"],  pick["pos_keywords"],
                        pick["pick_reason"],
                    ))
                saved += 1
            except Exception as e:
                logger.warning(f"InboundPick 저장 실패: {e}")

        logger.info(f"InboundPick: {saved}건 저장")
        return saved

    def _inbound_reason(self, name, brand_id, korea_rank, orders_rank, voc_pos, is_sns,
                        pos_kws, sephora_pop=False) -> str:
        parts = [f"{brand_id.upper()}"]
        if korea_rank:
            parts.append(f"한국 현지 인기 {korea_rank}위")
        if orders_rank:
            parts.append(f"글로벌 주문 {orders_rank}위")
        if sephora_pop:
            parts.append("Sephora 리뷰 활발")
        if voc_pos >= 0.65:
            parts.append(f"VOC 긍정도 {voc_pos:.0%}")
        if is_sns:
            parts.append("SNS 트렌드 키워드 연결")
        if pos_kws:
            parts.append(f"소비자 반응: {', '.join(pos_kws[:3])}")
        return ". ".join(parts) + "."

    # ─────────────────────────────────────────────────────────
    # 진입점
    # ─────────────────────────────────────────────────────────
    def run(self, analysis: dict) -> dict:
        """
        Orchestrator 호출 진입점.

        실행 순서:
          Step 1. TrendInsightBuilder  (VOC 없음)
          Step 2. ProductInsightBuilder (VOC 수집 트리거 포함)
          Step 3. InboundPickBuilder    (Step 2 VOC 재사용)
          Step 4. ReportBuilder

        반환: { week, trend_insights, product_insights, inbound_picks,
                report_path, csv_count }
        """
        logger.info(f"=== Decision Agent 시작 (week={self.week}) ===")

        # Step 1
        logger.info("--- Step 1: TrendInsightBuilder ---")
        trend_count = 0
        try:
            trend_count = self._build_trend_insights(analysis)
        except Exception as e:
            logger.warning(f"TrendInsight 실패: {e}", exc_info=True)

        # Step 2
        logger.info("--- Step 2: ProductInsightBuilder ---")
        product_count = 0
        try:
            product_count = self._build_product_insights()
        except Exception as e:
            logger.warning(f"ProductInsight 실패: {e}", exc_info=True)

        # Step 3
        logger.info("--- Step 3: InboundPickBuilder ---")
        inbound_count = 0
        try:
            inbound_count = self._build_inbound_picks()
        except Exception as e:
            logger.warning(f"InboundPick 실패: {e}", exc_info=True)

        # Step 4
        logger.info("--- Step 4: ReportBuilder ---")
        report_path = None
        csv_paths: list[str] = []
        try:
            report_path = self.html_report.generate(self.week)
            logger.info(f"HTML 리포트: {report_path}")
        except Exception as e:
            logger.warning(f"HTML 리포트 실패: {e}")
        try:
            csv_paths = self.csv_export.export_all(self.week)
            logger.info(f"CSV 익스포트: {len(csv_paths)}개")
        except Exception as e:
            logger.warning(f"CSV 익스포트 실패: {e}")

        logger.info(
            f"=== Decision 완료: "
            f"TrendInsight {trend_count} / "
            f"ProductInsight {product_count} / "
            f"InboundPick {inbound_count} ==="
        )
        return {
            "week":             self.week,
            "trend_insights":   trend_count,
            "product_insights": product_count,
            "inbound_picks":    inbound_count,
            "report_path":      report_path,
            "csv_count":        len(csv_paths),
        }
