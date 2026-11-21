"""
dashboard_builder.py
─────────────────────
주차별 분석 결과를 모아 정적 대시보드(dashboard/)용 data.js 를 만든다.

- 대시보드는 서버 없이 동작하는 정적 웹앱이다 (GitHub Pages, 로컬 파일 모두 가능).
- 주간·월간 전환과 URL 선택(?w=2026-W47, ?m=2026-11, #trends)은 프런트엔드(dashboard/app.js)가 처리한다.
- 판정 로직은 건드리지 않고, 이미 DB에 저장된 결과를 읽어서 묶기만 한다.

사용:
    python -m tools.report.dashboard_builder db/kbeauty.db exports/dashboard
"""

import json
import logging
import re
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from tools.analyze.brand_count import BRAND_STOPWORDS, brand_patterns, count_brands
from tools.report.html_report_tool import OPP_META, QUAD_META, SHAPE_META, HTMLReportTool

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "dashboard"
STATIC_FILES = ("index.html", "app.js", "style.css")


def _json(v, default):
    try:
        out = json.loads(v) if v else default
        return out if isinstance(out, type(default)) else default
    except (TypeError, ValueError):
        return default


def _week_dates(week: str) -> tuple[date, date, str]:
    y, w = week.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    thursday = monday + timedelta(days=3)
    return monday, monday + timedelta(days=6), f"{thursday.year}-{thursday.month:02d}"


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[.다])\s", (text or "").strip(), maxsplit=1)[0]


class DashboardBuilder:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 전체 주차 공통 ──
    def _weeks(self, conn) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT week FROM fact_trend_insights ORDER BY week")]

    def _timeline(self, conn) -> list[dict]:
        """수집이 있었던 모든 주차의 게시물·engagement 합계 (개요 추이 차트용)."""
        posts = dict(conn.execute("SELECT week, COUNT(*) FROM fact_sns_raw GROUP BY week").fetchall())
        eng = dict(conn.execute("""SELECT week, SUM(engagement_score) FROM fact_sns_signals
                                   WHERE platform_id IN ('tiktok','youtube') GROUP BY week""").fetchall())
        return [{"week": w, "posts": posts.get(w, 0), "eng": round(eng.get(w) or 0)}
                for w in sorted(set(posts) | set(eng))]

    # ── 주차 하나 ──
    def _week(self, conn, week: str, known: dict, names: dict, company: dict) -> dict:
        from tools.quality.insight_checks import _brand_key
        start, end, month = _week_dates(week)
        q = lambda sql, *a: conn.execute(sql, (week, *a)).fetchall()

        # KPI
        posts = q("SELECT platform_id, COUNT(*) n FROM fact_sns_raw WHERE week=? GROUP BY platform_id")
        retail = q("SELECT platform_id, COUNT(*) n FROM fact_retail_rankings WHERE week=? GROUP BY platform_id")
        ranked = q("SELECT COUNT(DISTINCT product_id) FROM fact_retail_rankings WHERE week=?")[0][0]
        try:
            vrows = q("""SELECT checker, verdict, COUNT(*) n FROM fact_insight_validations
                         WHERE week=? GROUP BY checker, verdict""")
            fails = q("""SELECT keyword, check_type, target, detail, checker FROM fact_insight_validations
                         WHERE week=? AND verdict='fail' ORDER BY checker DESC, keyword""")
        except sqlite3.OperationalError:
            vrows, fails = [], []
        validation: dict = {}
        for r in vrows:
            key = "rule" if r["checker"] == "rule" else "llm"
            validation.setdefault(key, {"pass": 0, "warn": 0, "fail": 0, "model": r["checker"]})[r["verdict"]] = r["n"]

        # 트렌드
        trend_rows = q("""
            SELECT t.*, s.post_count, s.eng FROM fact_trend_insights t
            LEFT JOIN (SELECT keyword, SUM(post_count) post_count, SUM(engagement_score) eng
                       FROM fact_sns_signals WHERE week=? AND platform_id IN ('tiktok','youtube')
                       GROUP BY keyword) s ON s.keyword = t.keyword
            WHERE t.week=? ORDER BY t.z_score DESC""", week)
        ev = {}
        for r in q("""SELECT keyword, evidence FROM fact_llm_insight_products
                      WHERE week=? AND evidence IS NOT NULL AND evidence != ''
                      ORDER BY CASE match_type WHEN 'direct' THEN 0 ELSE 1 END"""):
            lst = ev.setdefault(r["keyword"], [])
            if len(lst) < 2 and r["evidence"] not in lst:
                lst.append(r["evidence"])
        hist = {}
        for r in conn.execute("""SELECT keyword, week, SUM(engagement_score) e FROM fact_sns_signals
                                 WHERE week <= ? AND platform_id IN ('tiktok','youtube')
                                 GROUP BY keyword, week ORDER BY week""", (week,)):
            hist.setdefault(r["keyword"], []).append([r["week"], round(r["e"] or 0)])
        # 결정적 집계: 키워드가 들어간 이번 주 캡션 전체(1차 raw + 심층 deep)에서 브랜드를 직접 센다 (캡션당 1회).
        # 대화 점유(기회 유형) 판정도 같은 함수를 쓴다 (tools/analyze/brand_count.py)
        def det_count(kw: str) -> dict:
            d = count_brands(conn, week, kw, self._patterns)
            d["brands"] = d["brands"][:12]
            return d

        def top_share(kw: str):
            """1차 수집 원문에서 키워드가 들어간 게시물 조회수 중 1위 게시물 비중 (단일 게시물 의존 판단용)."""
            plain = kw.replace("_", " ")
            views = [r[0] or 0 for r in conn.execute(
                """SELECT view_count FROM fact_sns_raw WHERE week=? AND (LOWER(content_text) LIKE ?
                   OR LOWER(content_text) LIKE ? OR LOWER(hashtags) LIKE ?)""",
                (week, f"%{kw.lower()}%", f"%{plain.lower()}%", f"%{kw.lower()}%"))]
            tot = sum(views)
            return round(max(views) / tot, 3) if tot else None

        cols_sig = {r[1] for r in conn.execute("PRAGMA table_info(fact_sns_signals)")}
        eng_z = {r[0]: round(r[1], 2) for r in conn.execute(
            "SELECT keyword, MAX(eng_z) FROM fact_sns_signals WHERE week=? GROUP BY keyword", (week,))} \
            if "eng_z" in cols_sig else {}

        trends = []
        for r in trend_rows:
            mentions = _json(r["competitor_mentions"], {})
            trends.append({
                "kw": r["keyword"], "opp": r["opportunity_type"], "shape": r["trend_shape"] or "steady",
                "z": round(r["z_score"] or 0, 2), "mom": round(r["momentum_score"] or 0, 2),
                "posts": int(r["post_count"] or 0), "eng": round(r["eng"] or 0),
                "lead": r["lead_platform"], "cross": bool(r["is_cross_platform"]),
                "brands": _json(r["ap_brand_ids"], []), "mentions": list(mentions.keys())[:4],
                "summary": r["insight_summary"] or "", "need": r["consumer_need"] or "",
                "action": r["action_rec"] or "", "evidence": ev.get(r["keyword"], []),
                "hist": hist.get(r["keyword"], [])[-5:],
                "det": det_count(r["keyword"]),
                "top_post_share": top_share(r["keyword"]),
                "eng_z": eng_z.get(r["keyword"]),
            })

        # 브랜드 언급 (알려진 K-브랜드만) — 트렌드 행렬
        matrix: dict = {}
        for t, r in zip(trends, trend_rows):
            for name, n in _json(r["competitor_mentions"], {}).items():
                bid = known.get(_brand_key(name))
                if not bid:
                    continue
                try:
                    n = max(int(n), 1)
                except (TypeError, ValueError):
                    n = 1
                row = matrix.setdefault(bid, {})
                row[t["kw"]] = row.get(t["kw"], 0) + n

        # 급상승 브랜드 (트렌드와 같은 기준: z ≥ 2, 게시물 ≥ 5)
        from agents.analysis_agent import GENERIC_EXCLUDE, _exclude_keywords
        from tools.analyze.stats_tool import StatsTool
        brand_kws = _exclude_keywords(self.db_path) - GENERIC_EXCLUDE
        rising = [{"kw": r["keyword"], "z": round(r["z"], 1), "posts": r["posts"]}
                  for r in q("""SELECT keyword, SUM(post_count) posts, MAX(z_score) z FROM fact_sns_signals
                                WHERE week=? GROUP BY keyword HAVING MAX(z_score) >= ? ORDER BY z DESC""",
                             StatsTool.Z_ALERT)
                  if r["keyword"].lower() in brand_kws]
        for b in rising:
            b["ok"] = b["posts"] >= StatsTool.MIN_POST_COUNT

        # 리테일 존재감
        rk = q("""SELECT p.brand_id, COUNT(DISTINCT r.product_id) n FROM fact_retail_rankings r
                  JOIN dim_product p ON p.product_id = r.product_id
                  WHERE r.week=? AND r.platform_id LIKE 'oy_%' GROUP BY p.brand_id ORDER BY n DESC""")
        retail_brands = [[b["brand_id"], b["n"]] for b in rk]

        # 제품·VOC
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fact_product_insights)")}
        opt = lambda c: f"pi.{c}" if c in cols else f"NULL AS {c}"
        products = [{
            "name": r["product_name_en"], "brand": r["brand_name_en"] or r["brand_id"], "tier": r["brand_tier"],
            "quad": r["strategy_quad"] or "MONITOR",
            "pos": r["sentiment_pos"], "neg": r["sentiment_neg"], "rating": r["avg_rating"],
            "vel": r["voc_velocity"], "rt": r["rating_trend"],
            "oy_orders": r["oy_rank_orders"], "oy_korea": r["oy_rank_korea"],
            "action": r["action_rec"] or "",
            "pos_kw": _json(r["pos_keywords"], [])[:5],
            "neg_kw": [k for k in _json(r["neg_keywords"], []) if k and str(k).lower() != "none"][:5],
            "needs_kw": _json(r["needs_keywords"], [])[:5],
            "oy_url": r["oy_url"], "sephora_url": r["sephora_url"],
        } for r in q(f"""
            SELECT pi.*, p.product_name_en, p.oy_url, p.sephora_url, b.brand_name_en,
                   {opt('voc_velocity')}, {opt('rating_trend')}
            FROM fact_product_insights pi
            JOIN dim_product p ON p.product_id = pi.product_id
            JOIN dim_brand b ON b.brand_id = pi.brand_id
            WHERE pi.week=? ORDER BY pi.sentiment_pos DESC""")]

        inbound = [{
            "rank": r["rank"], "name": r["product_name_en"], "brand": r["brand_name_en"],
            "tier": r["brand_tier"], "korea": r["korea_rank"], "orders": r["orders_rank"],
            "voc": r["voc_pos"], "sns": bool(r["sns_linked"]), "score": round(r["pick_score"] or 0, 3),
            "reason": r["pick_reason"] or "", "kw": _json(r["pos_keywords"], [])[:3],
        } for r in q("""SELECT ip.*, p.product_name_en, b.brand_name_en FROM fact_inbound_picks ip
                        JOIN dim_product p ON p.product_id = ip.product_id
                        JOIN dim_brand b ON b.brand_id = ip.brand_id
                        WHERE ip.week=? ORDER BY ip.rank""")]

        return {
            "week": week, "month": month, "label": HTMLReportTool._week_label(week),
            "start": start.isoformat(), "end": end.isoformat(),
            "kpi": {"trends": len(trends), "posts": sum(r["n"] for r in posts),
                    "ranked": ranked, "ranked_brands": len(rk)},
            "platforms": {r["platform_id"]: r["n"] for r in posts},
            "retail_platforms": {r["platform_id"]: r["n"] for r in retail},
            "validation": validation,
            "fails": [{**dict(r), "target_name": self._product_name(conn, r["target"])} for r in fails],
            "trends": trends,
            "summary": [{"kw": t["kw"], "z": t["z"], "shape": t["shape"], "need": _first_sentence(t["need"])}
                        for t in trends[:3]],
            "rising_brands": rising,
            "matrix": matrix,
            "retail_brands": retail_brands,
            "products": products,
            "inbound": inbound,
            "legacy": any(t["opp"] in ("amplify", "position", "counter", "new_entry") for t in trends),
        }

    @staticmethod
    def _product_name(conn, target: str) -> str | None:
        if not re.fullmatch(r"S\d{6,}", target or ""):
            return None
        r = conn.execute("SELECT b.brand_name_en, p.product_name_en FROM dim_product p "
                         "JOIN dim_brand b ON b.brand_id = p.brand_id WHERE p.product_id=?", (target,)).fetchone()
        return f"{r[0]} {r[1]}" if r else None

    def build(self) -> dict:
        with self._conn() as conn:
            self._patterns = brand_patterns(conn)
            from agents.decision_agent import DecisionAgent
            known = DecisionAgent._known_brand_map(conn)
            names, company = {}, {}
            for r in conn.execute("SELECT brand_id, brand_name_en, company FROM dim_brand"):
                names[r["brand_id"]] = r["brand_name_en"] or r["brand_id"]
                company[r["brand_id"]] = r["company"]
            weeks = self._weeks(conn)
            data = {
                "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "weeks": [self._week(conn, w, known, names, company) for w in weeks],
                "timeline": self._timeline(conn),
                "brand_names": names,
                "brand_company": {k: v for k, v in company.items() if v},
                "meta": {"opp": OPP_META, "quad": QUAD_META, "shape": SHAPE_META},
                "brand_stopwords": sorted(BRAND_STOPWORDS),
            }
        return data

    def write(self, out_dir: str) -> Path:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        data = self.build()
        (out / "data.js").write_text(
            "window.DASH = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
            encoding="utf-8")
        for f in STATIC_FILES:
            shutil.copy2(STATIC_DIR / f, out / f)
        logger.info(f"대시보드 생성: {out} (주차 {len(data['weeks'])}개)")
        return out / "index.html"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(DashboardBuilder(sys.argv[1]).write(sys.argv[2] if len(sys.argv) > 2 else "exports/dashboard"))
