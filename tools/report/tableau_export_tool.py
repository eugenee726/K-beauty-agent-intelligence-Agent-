"""
tableau_export_tool.py
───────────────────────
Tableau / Excel 분석용 CSV 익스포트 툴.

생성 파일 (exports/ 폴더):
  1. kbeauty_sns_signals_{week}.csv     — TikTok + YouTube 신호
  2. kbeauty_retail_rankings_{week}.csv — 플랫폼별 랭킹
  3. kbeauty_voc_{week}.csv             — VOC 감성/키워드

Tableau 활용 가이드:
  - sns_signals: 키워드별 engagement_score 트렌드 라인 차트
  - retail_rankings: 플랫폼 × 브랜드 히트맵
"""

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class TableauExportTool:

    def __init__(self, db_path: str, export_dir: str):
        self.db_path    = db_path
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    def _save(self, df: pd.DataFrame, filename: str) -> Path:
        path = self.export_dir / filename
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"  CSV 저장: {path} ({len(df)}행)")
        return path

    # ──────────────────────────────────────────
    # 개별 내보내기 메서드
    # ──────────────────────────────────────────
    def export_sns_signals(self, week: str = None) -> Path:
        week = week or self._current_week()
        with self._conn() as conn:
            df = pd.read_sql("""
                SELECT s.week, s.platform_id, s.keyword,
                       s.post_count, s.total_views, s.total_likes,
                       s.total_comments, s.engagement_score,
                       s.growth_rate, s.is_new_keyword,
                       p.platform_name, p.platform_type
                FROM fact_sns_signals s
                LEFT JOIN dim_platform p ON s.platform_id = p.platform_id
                WHERE s.week = ?
                ORDER BY s.engagement_score DESC
            """, conn, params=(week,))
        return self._save(df, f"kbeauty_sns_signals_{week.replace(':', '-')}.csv")

    def export_retail_rankings(self, week: str = None) -> Path:
        week = week or self._current_week()
        with self._conn() as conn:
            df = pd.read_sql("""
                SELECT r.week, r.platform_id, pl.platform_name,
                       r.product_id, p.product_name_en,
                       p.brand_id, b.brand_name_en, b.tier,
                       r.rank_position, r.category
                FROM fact_retail_rankings r
                LEFT JOIN dim_platform pl ON r.platform_id = pl.platform_id
                LEFT JOIN dim_product  p  ON r.product_id  = p.product_id
                LEFT JOIN dim_brand    b  ON p.brand_id    = b.brand_id
                WHERE r.week = ?
                ORDER BY r.platform_id, r.rank_position
            """, conn, params=(week,))
        return self._save(df, f"kbeauty_retail_rankings_{week.replace(':', '-')}.csv")

    def export_voc(self, week: str = None) -> Path:
        week = week or self._current_week()
        with self._conn() as conn:
            df = pd.read_sql("""
                SELECT v.week, v.platform_id, v.product_id,
                       p.product_name_en, p.brand_id,
                       b.brand_name_en, b.tier,
                       v.review_count, v.avg_rating,
                       v.sentiment_pos, v.sentiment_neg,
                       v.pos_keywords, v.neg_keywords, v.needs_keywords
                FROM fact_voc_signals v
                LEFT JOIN dim_product p ON v.product_id = p.product_id
                LEFT JOIN dim_brand   b ON p.brand_id   = b.brand_id
                WHERE v.week = ?
                ORDER BY v.sentiment_pos DESC
            """, conn, params=(week,))
        return self._save(df, f"kbeauty_voc_{week.replace(':', '-')}.csv")

    # ──────────────────────────────────────────
    # 전체 내보내기 (Reporter가 호출)
    # ──────────────────────────────────────────
    def export_all(self, week: str = None) -> dict[str, Path]:
        week = week or self._current_week()
        logger.info(f"CSV 전체 내보내기 시작 (week={week})")

        paths = {}
        try: paths["sns"]       = self.export_sns_signals(week)
        except Exception as e:  logger.warning(f"SNS 내보내기 실패: {e}")
        try: paths["retail"]    = self.export_retail_rankings(week)
        except Exception as e:  logger.warning(f"Retail 내보내기 실패: {e}")
        try: paths["voc"]       = self.export_voc(week)
        except Exception as e:  logger.warning(f"VOC 내보내기 실패: {e}")

        logger.info(f"CSV 내보내기 완료: {len(paths)}개 파일")
        return paths
