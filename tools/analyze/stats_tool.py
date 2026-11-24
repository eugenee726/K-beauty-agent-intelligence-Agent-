"""
stats_tool.py
──────────────
통계 분석 툴.

수행 기능:
  A) Z-score 이상 탐지 — 이번 주 키워드 게시물 비중(그 주 전체 게시물 대비)이 과거 평균 대비 얼마나 튀는가?
     (총량 대신 비중을 써서 수집량 변화에 흔들리지 않게 함. engagement 비중 z 는 eng_z 로 별도 계산)
     → z ≥ Z_ALERT(2.0) : is_anomaly (LLM·Decision 후보는 post_count 하한 추가)
  B) Growth Rate 가속도 — 성장률의 성장률 (2차 도함수)
  C) Cross-platform 상관 계수 — TikTok ↔ YouTube 키워드 동조화
  (이번 주 값 1개 vs 과거 4주로 하던 Welch t-test 는 표본 1개라 p 가 항상 NaN 이어서 제거)

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
    P_THRESH        = 0.05  # 플랫폼 간 상관 유의수준

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

        # 탐지 지표 정규화: 수집량이 주마다 달라도 비교되도록 '그 주 전체 대비 비중'으로 본다.
        #   z_score = 키워드 게시물 비중의 z  (게시물 수 기반 → 바이럴 1건에 흔들리지 않음)
        #   eng_z   = 키워드 engagement 비중의 z (표시용)
        with self._conn() as conn:
            post_total = dict(conn.execute(
                "SELECT week, COUNT(*) FROM fact_sns_raw WHERE platform_id IN ('tiktok','youtube') GROUP BY week"
            ).fetchall())
        eng_total = df.groupby("week")["engagement_score"].sum().to_dict()
        df["post_share"] = df.apply(lambda r: (r["post_count"] or 0) / post_total[r["week"]]
                                    if post_total.get(r["week"]) else 0.0, axis=1)
        df["eng_share"] = df.apply(lambda r: (r["engagement_score"] or 0) / eng_total[r["week"]]
                                   if eng_total.get(r["week"]) else 0.0, axis=1)

        def _z(cur, hist):
            sd = float(np.std(hist, ddof=1)) if len(hist) >= 2 else 0.0
            return float((cur - float(np.mean(hist))) / sd) if sd > 0 else 0.0

        results = []
        for keyword, grp in df.groupby("keyword"):
            grp = grp.sort_values("week")
            current = grp[grp["week"] == week]
            history = grp[grp["week"] < week]

            if current.empty:
                continue

            current_score = float(current["post_share"].iloc[0])
            current_gr    = current["growth_rate"].iloc[0]
            current_post_count = int(current["post_count"].iloc[0])
            eng_z = 0.0

            if len(history) >= 3:
                hist_scores = history["post_share"].values
                mean_h  = float(np.mean(hist_scores))
                std_h   = float(np.std(hist_scores, ddof=1))
                z_score = _z(current_score, hist_scores)
                eng_z   = _z(float(current["eng_share"].iloc[0]), history["eng_share"].values)

                gr_series = history["growth_rate"].dropna()
                growth_accel = float(
                    gr_series.iloc[-1] - gr_series.iloc[-2]
                ) if len(gr_series) >= 2 else None

            else:
                mean_h = std_h = z_score = 0.0
                growth_accel = None

            results.append({
                "week":          week,
                "platform_id":   CROSS_PLATFORM_ID,
                "keyword":       keyword,
                "current_score": round(current_score, 5),
                "mean_hist":     round(mean_h, 5),
                "std_hist":      round(std_h, 5),
                "z_score":       round(z_score, 2),
                "eng_z":         round(eng_z, 2),
                "is_anomaly":    1 if z_score >= self.Z_ALERT else 0,
                "growth_rate":   current_gr,
                "growth_accel":  round(growth_accel, 1) if growth_accel else None,
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
                if "eng_z" not in existing:
                    conn.execute("ALTER TABLE fact_sns_signals ADD COLUMN eng_z REAL DEFAULT 0.0")
                for _, row in df.iterrows():
                    conn.execute(
                        "UPDATE fact_sns_signals SET z_score=?, eng_z=? WHERE week=? AND keyword=?",
                        (row["z_score"], row["eng_z"], row["week"], row["keyword"]),
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
