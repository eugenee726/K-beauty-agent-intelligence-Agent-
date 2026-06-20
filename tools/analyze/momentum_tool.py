"""
momentum_tool.py
─────────────────
크로스플랫폼 모멘텀 추적 툴.

목적:
  "어떤 플랫폼에서 트렌드가 먼저 시작됐고, 어디서 퍼지고 있나?"
  → 선행 플랫폼 탐지 → 후속 플랫폼 예측 → 마케팅 타이밍 최적화

분석 로직:
  A) 첫 등장 플랫폼 기록 (fact_trend_first_seen, lead 판단용)
  B) 모멘텀 스코어 = 현재 주 / 3주 전 engagement (최근 3주 성장 배수)
  C) weeks_rising = 최근 3주 연속 상승 단계 수 (추세 지속성)
  D) 교차 플랫폼 동시 상승 = 강한 기회 신호

출력:
  DataFrame: keyword, lead_platform, momentum_score, weeks_rising,
             is_cross_platform, opportunity_hint
"""

import sqlite3
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MomentumTool:

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    # ──────────────────────────────────────────
    # A. 첫 등장 플랫폼 기록 업데이트
    # ──────────────────────────────────────────
    def update_first_seen(self, current_week: str = None):
        """
        fact_sns_signals에서 is_new_keyword=1인 키워드를 fact_trend_first_seen에 기록.
        """
        week = current_week or self._current_week()

        with self._conn() as conn:
            new_sns = conn.execute("""
                SELECT platform_id, keyword, engagement_score as score
                FROM fact_sns_signals
                WHERE week=? AND is_new_keyword=1
            """, (week,)).fetchall()

            for row in new_sns:
                conn.execute("""
                    INSERT OR IGNORE INTO fact_trend_first_seen
                        (keyword, platform_id, first_seen_week, initial_score)
                    VALUES (?, ?, ?, ?)
                """, (row["keyword"], row["platform_id"], week, row["score"]))

    # ──────────────────────────────────────────
    # B. 최근 3주 추세 기반 모멘텀 스코어
    # ──────────────────────────────────────────
    def compute_momentum(self, current_week: str = None) -> pd.DataFrame:
        """
        현재 주(current_week)를 포함한, 데이터가 존재하는 최근 3개 주차를 기준으로
        키워드별 상승 추세를 계산한다.

        momentum_score = 현재 주 engagement / 3주 전 engagement (최근 3주 성장 배수)
        weeks_rising   = 최근 3주 연속 상승 단계 수 (0~2, 2면 계속 상승)

        반환 컬럼: week, keyword, lead_platform, momentum_score, weeks_rising,
                  current_score, window_start_score, is_cross_platform,
                  platforms_active, opportunity_hint
        """
        week = current_week or self._current_week()

        with self._conn() as conn:
            # 현재 주 이하 모든 주차의 키워드별 플랫폼 합산 engagement
            sns_all = pd.read_sql("""
                SELECT week, keyword,
                       SUM(engagement_score) AS engagement,
                       COUNT(DISTINCT platform_id) AS n_platforms
                FROM fact_sns_signals
                WHERE week <= ?
                GROUP BY week, keyword
            """, conn, params=(week,))

            # 현재 주 키워드별 최상위 플랫폼
            lead_rows = pd.read_sql("""
                SELECT keyword, platform_id, engagement_score
                FROM fact_sns_signals
                WHERE week=?
                ORDER BY keyword, engagement_score DESC
            """, conn, params=(week,))

        if sns_all.empty:
            return pd.DataFrame()

        # 키워드별 최상위 플랫폼 / 활성 플랫폼
        lead_map: dict[str, str] = {}
        active_map: dict[str, set] = {}
        for _, r in lead_rows.iterrows():
            lead_map.setdefault(r["keyword"], r["platform_id"])
            active_map.setdefault(r["keyword"], set()).add(r["platform_id"])

        results = []
        for keyword, grp in sns_all.groupby("keyword"):
            grp = grp.sort_values("week")
            # 현재 주 데이터 없으면 추세 판단 불가
            if week not in set(grp["week"]):
                continue

            # 데이터가 있는 최근 3개 주차 (현재 주 포함)
            recent = grp.tail(3)
            eng = recent["engagement"].tolist()

            window_start = float(eng[0])
            current_eng  = float(eng[-1])
            momentum = round(current_eng / window_start, 2) if window_start > 0 else 0.0

            # 연속 상승 단계 수 (상승 끊기면 리셋)
            weeks_rising = 0
            for i in range(1, len(eng)):
                if eng[i] > eng[i - 1]:
                    weeks_rising += 1
                else:
                    weeks_rising = 0

            platforms_active = active_map.get(keyword, set())
            is_cross = 1 if len(platforms_active) >= 2 else 0
            lead_platform = lead_map.get(keyword)

            # 기회 힌트 (최근 3주 추세 기반)
            if is_cross and weeks_rising >= 2 and momentum >= 1.5:
                hint = "cross_platform_breakout"
            elif lead_platform == "tiktok" and weeks_rising >= 2:
                hint = "tiktok_leading"
            elif weeks_rising >= 1:
                hint = "rising"
            else:
                hint = "monitoring"

            results.append({
                "week":               week,
                "keyword":            keyword,
                "lead_platform":      lead_platform,
                "momentum_score":     momentum,
                "weeks_rising":       weeks_rising,
                "current_score":      round(current_eng, 1),
                "window_start_score": round(window_start, 1),
                "is_cross_platform":  is_cross,
                "platforms_active":   list(platforms_active),
                "opportunity_hint":   hint,
            })

        out = pd.DataFrame(results)
        if out.empty or "momentum_score" not in out.columns:
            return out
        return out.sort_values("momentum_score", ascending=False)

    # ──────────────────────────────────────────
    # C. 아모레퍼시픽 브랜드 키워드 집중 분석
    # ──────────────────────────────────────────
    def compute_brand_momentum(self, current_week: str = None) -> pd.DataFrame:
        """
        아모레퍼시픽 브랜드 SNS 키워드 모멘텀.
        dim_brand의 sns_keywords 기반으로 필터링.
        """
        week = current_week or self._current_week()

        with self._conn() as conn:
            brands = conn.execute(
                "SELECT brand_id, brand_name_en, sns_keywords FROM dim_brand"
            ).fetchall()

            sns_all = pd.read_sql("""
                SELECT week, platform_id, keyword, engagement_score, growth_rate
                FROM fact_sns_signals
                ORDER BY keyword, week
            """, conn)

        if sns_all.empty:
            return pd.DataFrame()

        import json
        results = []
        for brand in brands:
            try:
                brand_kws = json.loads(brand["sns_keywords"] or "[]")
            except Exception:
                brand_kws = []

            for kw in brand_kws:
                matches = sns_all[sns_all["keyword"].str.contains(
                    kw.lower().replace(" ", "_"), case=False, na=False
                )]
                if matches.empty:
                    continue

                cur = matches[matches["week"] == week]
                if cur.empty:
                    continue

                for _, row in cur.iterrows():
                    results.append({
                        "week":            week,
                        "brand_id":        brand["brand_id"],
                        "brand_name":      brand["brand_name_en"],
                        "keyword":         row["keyword"],
                        "platform_id":     row["platform_id"],
                        "engagement_score": row["engagement_score"],
                        "growth_rate":     row["growth_rate"],
                    })

        return pd.DataFrame(results)
