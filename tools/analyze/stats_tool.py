"""
stats_tool.py
──────────────
통계 분석 툴.

수행 기능:
  A) Z-score 이상 탐지 — 이번 주 engagement가 평균 대비 얼마나 튀는가?
     → z ≥ Z_ALERT(2.0) : is_anomaly (LLM·Decision 후보는 post_count 하한 추가)
  B) Growth Rate 가속도 — 성장률의 성장률 (2차 도함수)
  C) Cross-platform 상관 계수 — TikTok ↔ YouTube 키워드 동조화
  D) Welch t-test — "이번 주 vs 이전 4주 평균" 유의미한 차이인가?

출력:
  DataFrame 반환 (에이전트가 판단에 사용)
  컬럼: keyword, platform_id(=cross_platform), z_score, post_count(합산), ...
"""

import sqlite3
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

# Analysis·Decision·LLM 선별용 — TikTok+YouTube 합산 grain
CROSS_PLATFORM_ID = "cross_platform"
SNS_PLATFORMS = ("tiktok", "youtube")


class StatsTool:
    """K-beauty SNS 신호 통계 분석."""

    Z_ALERT         = 2.0   # z ≥ this → is_anomaly
    MIN_POST_COUNT  = 5     # LLM·Decision actionable volume floor
    P_THRESH        = 0.05  # t-test 유의수준

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    @staticmethod
    def aggregate_signals_by_keyword(df: pd.DataFrame) -> pd.DataFrame:
        """
        fact_sns_signals 플랫폼 행 → (week, keyword) 단위 합산.
        engagement_score·post_count는 합산, growth_rate는 합산 점수 기준 재계산.
        """
        if df.empty:
            return df

        sns = df[df["platform_id"].isin(SNS_PLATFORMS)].copy()
        if sns.empty:
            sns = df.copy()

        agg = (
            sns.groupby(["week", "keyword"], as_index=False)
            .agg(
                engagement_score=("engagement_score", "sum"),
                post_count=("post_count", "sum"),
            )
        )
        agg = agg.sort_values(["keyword", "week"])
        agg["growth_rate"] = (
            agg.groupby("keyword")["engagement_score"]
            .pct_change()
            .mul(100)
            .round(1)
        )
        return agg

    # ──────────────────────────────────────────
    # A + B: Z-score + 성장 가속도 (키워드·크로스플랫폼)
    # ──────────────────────────────────────────
    def compute_sns_anomalies(self, current_week: str = None) -> pd.DataFrame:
        """
        SNS 신호 Z-score 이상 탐지 (TikTok + YouTube 합산 후 키워드별).
        반환 컬럼: keyword, platform_id=cross_platform, current_score,
                   mean_hist, std_hist, z_score, is_anomaly, post_count(합산), ...
        """
        week = current_week or self._current_week()

        with self._conn() as conn:
            df = pd.read_sql("""
                SELECT week, platform_id, keyword,
                       engagement_score, growth_rate, post_count
                FROM fact_sns_signals
                WHERE platform_id IN ('tiktok', 'youtube')
                ORDER BY keyword, platform_id, week
            """, conn)

        if df.empty:
            return pd.DataFrame()

        df = self.aggregate_signals_by_keyword(df)

        results = []
        for keyword, grp in df.groupby("keyword"):
            grp = grp.sort_values("week")
            current = grp[grp["week"] == week]
            history = grp[grp["week"] < week]

            if current.empty:
                continue

            current_score = float(current["engagement_score"].iloc[0])
            current_gr    = current["growth_rate"].iloc[0]
            current_post_count = int(current["post_count"].iloc[0])

            if len(history) >= 3:
                hist_scores = history["engagement_score"].values
                mean_h  = float(np.mean(hist_scores))
                std_h   = float(np.std(hist_scores, ddof=1))
                z_score = float((current_score - mean_h) / std_h) if std_h > 0 else 0.0

                last4 = history.tail(4)["engagement_score"].values
                if len(last4) >= 2:
                    _, p_val = stats.ttest_ind(
                        [current_score], last4, equal_var=False
                    )
                else:
                    p_val = 1.0

                gr_series = history["growth_rate"].dropna()
                growth_accel = float(
                    gr_series.iloc[-1] - gr_series.iloc[-2]
                ) if len(gr_series) >= 2 else None

            else:
                mean_h = std_h = z_score = 0.0
                p_val  = 1.0
                growth_accel = None

            results.append({
                "week":          week,
                "platform_id":   CROSS_PLATFORM_ID,
                "keyword":       keyword,
                "current_score": round(current_score, 1),
                "mean_hist":     round(mean_h, 1),
                "std_hist":      round(std_h, 1),
                "z_score":       round(z_score, 2),
                "is_anomaly":    1 if z_score >= self.Z_ALERT else 0,
                "growth_rate":   current_gr,
                "growth_accel":  round(growth_accel, 1) if growth_accel else None,
                "p_value":       round(p_val, 4),
                "significant":   1 if p_val < self.P_THRESH else 0,
                "post_count":    current_post_count,
            })

        df = pd.DataFrame(results)

        # z_score를 fact_sns_signals에 업데이트
        if not df.empty:
            with self._conn() as conn:
                # 컬럼이 없으면 추가
                existing = [r[1] for r in conn.execute("PRAGMA table_info(fact_sns_signals)").fetchall()]
                if "z_score" not in existing:
                    conn.execute("ALTER TABLE fact_sns_signals ADD COLUMN z_score REAL DEFAULT 0.0")
                for _, row in df.iterrows():
                    conn.execute(
                        "UPDATE fact_sns_signals SET z_score=? WHERE week=? AND keyword=?",
                        (row["z_score"], row["week"], row["keyword"]),
                    )

        return df

    # ──────────────────────────────────────────
    # C: TikTok ↔ YouTube 상관 계수
    # ──────────────────────────────────────────
    def compute_cross_platform_correlation(self) -> pd.DataFrame:
        """
        TikTok ↔ YouTube 키워드 동조화 분석.
        반환 컬럼: keyword, tiktok_youtube_corr
        """
        with self._conn() as conn:
            sns_df = pd.read_sql("""
                SELECT week, platform_id, keyword, engagement_score
                FROM fact_sns_signals
            """, conn)

        if sns_df.empty:
            return pd.DataFrame()

        tiktok  = sns_df[sns_df["platform_id"] == "tiktok"].pivot(
            index="week", columns="keyword", values="engagement_score"
        )
        youtube = sns_df[sns_df["platform_id"] == "youtube"].pivot(
            index="week", columns="keyword", values="engagement_score"
        )

        common_kws = set(tiktok.columns) & set(youtube.columns)
        results = []
        for kw in common_kws:
            try:
                weeks = sorted(set(tiktok.index) & set(youtube.index))
                if len(weeks) < 3:
                    continue
                tt = tiktok.loc[weeks, kw].values.astype(float)
                yt = youtube.loc[weeks, kw].values.astype(float)
                corr_ty, p_val = stats.pearsonr(tt, yt)
                if p_val >= self.P_THRESH:   # 통계적으로 유의하지 않으면 제외
                    continue
                results.append({
                    "keyword":             kw,
                    "tiktok_youtube_corr": round(corr_ty, 3),
                    "p_value":             round(p_val, 4),
                })
            except Exception:
                continue

        return pd.DataFrame(results)
