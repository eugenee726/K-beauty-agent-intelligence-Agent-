"""
data_quality.py
────────────────
수집 직후 실행하는 결정적(LLM 없는) 데이터 품질 검사.

Airflow DAG 의 data_quality_check task 가 호출한다. 실패하면 이후 분석·LLM 단계를
돌리지 않아, 비어 있거나 망가진 데이터에 API 비용을 쓰지 않게 한다.

하한값은 최근 정상 주차(W41, W47) 실측치의 대략 1/3 수준으로 잡았다.
  TikTok 저장 93~253 / YouTube 275~346 / OY 랭킹 24~41 (AP 한정 시절 기준)
"""

import sqlite3
from dataclasses import dataclass, field

# 주차별 최소 행 수
MIN_ROWS = {
    ("fact_sns_raw", "tiktok"):         30,
    ("fact_sns_raw", "youtube"):        100,
    ("fact_retail_rankings", None):     10,
}


@dataclass
class QualityReport:
    week: str
    counts: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def check_week(db_path: str, week: str) -> QualityReport:
    report = QualityReport(week=week)
    with sqlite3.connect(db_path, timeout=10) as conn:
        for (table, platform), minimum in MIN_ROWS.items():
            if platform:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE week = ? AND platform_id = ?",
                    (week, platform),
                ).fetchone()[0]
                label = f"{table}[{platform}]"
            else:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE week = ?", (week,)
                ).fetchone()[0]
                label = table
            report.counts[label] = n
            if n < minimum:
                report.failures.append(f"{label}: {n}건 < 최소 {minimum}건")

        # 같은 주차·플랫폼에서 본문이 비어 있는 SNS 게시물 비율
        empty, total = conn.execute(
            """SELECT SUM(CASE WHEN content_text IS NULL OR TRIM(content_text) = ''
                               THEN 1 ELSE 0 END), COUNT(*)
               FROM fact_sns_raw WHERE week = ?""",
            (week,),
        ).fetchone()
        if total and (empty or 0) / total > 0.2:
            report.failures.append(f"fact_sns_raw 본문 공백 비율 {empty}/{total} > 20%")
    return report
