"""
html_report_tool.py
────────────────────
K-beauty Intelligence HTML 리포트 생성 툴.

3-Tab 구조:
  Tab 1. 트렌드 인사이트  — fact_trend_insights (키워드 × AP 제품 × 기회 유형)
  Tab 2. 제품 전략        — fact_product_insights (전략 사분면 × VOC × 리테일)
  Tab 3. 방한 추천        — fact_inbound_picks (방한 관광객 추천 Top 15)

생성 결과: exports/kbeauty_report_{week}.html
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 기회 유형 / 전략 사분면 한국어 + 색상 매핑 ──
OPP_META = {
    "amplify":   {"label": "즉시 강화",     "color": "#e91e8c", "bg": "#ffe0f0"},
    "position":  {"label": "차별화",        "color": "#1565c0", "bg": "#e3f2fd"},
    "counter":   {"label": "대응 필요",     "color": "#e65100", "bg": "#fff3e0"},
    "new_entry": {"label": "신규 진입",     "color": "#2e7d32", "bg": "#e8f5e9"},
}
# 기회 유형 짧은 설명 (요약 카드용)
OPP_DESC = {
    "amplify":   "AP 제품 보유 + 지속 상승",
    "position":  "AP 제품 보유 + 경쟁사 동반",
    "counter":   "AP 미보유 + 경쟁사 선점",
    "new_entry": "AP 미보유 + 화이트스페이스",
}
QUAD_META = {
    "PUSH_NOW":    {"label": "PUSH NOW",     "color": "#e91e8c", "bg": "#ffe0f0"},
    "FIX_AND_PUSH":{"label": "FIX & PUSH",  "color": "#e65100", "bg": "#fff3e0"},
    "HOLD":        {"label": "HOLD",         "color": "#1565c0", "bg": "#e3f2fd"},
    "MONITOR":     {"label": "MONITOR",      "color": "#666",    "bg": "#f5f5f5"},
}
# 전략 사분면 짧은 설명 (요약 카드용)
QUAD_DESC = {
    "PUSH_NOW":     "리테일 상위 + VOC 긍정 → 즉시 집행",
    "FIX_AND_PUSH": "리테일 상위 + VOC 약함 → 개선 후 강화",
    "HOLD":         "리테일 하위 + VOC 긍정 → 유통 확대 검토",
    "MONITOR":      "리테일 하위 + VOC 약함 → 관망",
}
# 브랜드 티어 배지 (영어 라벨)
TIER_META = {
    "international_luxury": {"label": "Intl Luxury",    "color": "#6a1b9a", "bg": "#f3e5f5"},
    "korean_luxury":       {"label": "Korean Luxury",  "color": "#8e24aa", "bg": "#f5e9fa"},
    "korean_premium":      {"label": "Korean Premium", "color": "#1565c0", "bg": "#e3f2fd"},
    "clinical_daily":      {"label": "Clinical Daily", "color": "#00838f", "bg": "#e0f7fa"},
    "korean_daily":        {"label": "Korean Daily",   "color": "#2e7d32", "bg": "#e8f5e9"},
    "korean_makeup":       {"label": "Korean Makeup",  "color": "#c2185b", "bg": "#fce4ec"},
}
# 트렌드 모양 (opportunity_type과 독립된 축)
SHAPE_META = {
    "sustained": {"label": "📈 지속 상승", "color": "#2e7d32", "bg": "#e8f5e9"},
    "spike":     {"label": "⚡ 순간 급등", "color": "#e65100", "bg": "#fff3e0"},
    "emerging":  {"label": "🌱 신규 등장", "color": "#6a1b9a", "bg": "#f3e5f5"},
    "steady":    {"label": "➖ 완만/정체", "color": "#666",    "bg": "#f5f5f5"},
}

# 차트에서 제외할 광의어 (브랜드·성분·제품유형은 유지)
EXCLUDE_KEYWORDS = {
    "kbeauty", "k_beauty", "koreanbeauty", "korean_beauty",
    "koreanskincare", "korean_skincare", "kbeautyskincare",
    "skincare", "skincare_routine", "skincareroutine",
    "morning_routine", "night_routine", "routine",
    "beauty", "oliveyoung", "olive_young",
}

# 라인 차트용 색상 팔레트
LINE_COLORS = [
    "#e91e8c", "#6c4ab6", "#1565c0", "#2e7d32", "#e65100",
    "#00838f", "#c2185b", "#5d4037", "#7b1fa2", "#f9a825",
    "#0097a7", "#d84315",
]


class HTMLReportTool:

    def __init__(self, db_path: str, export_dir: str):
        self.db_path    = db_path
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ──────────────────────────────────────────────────────
    # Tab 1: 트렌드 인사이트
    # ──────────────────────────────────────────────────────
    def _trend_tab(self, week: str) -> tuple[str, dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT t.keyword, t.opportunity_type, t.z_score, t.momentum_score,
                       t.trend_shape, t.lead_platform, t.is_cross_platform,
                       t.ap_brand_ids, t.consumer_need, t.competitor_mentions,
                       t.insight_summary, t.action_rec,
                       s.post_count, s.engagement_score
                FROM fact_trend_insights t
                LEFT JOIN (
                    SELECT keyword, SUM(post_count) as post_count,
                           SUM(engagement_score) as engagement_score
                    FROM fact_sns_signals
                    WHERE week=? AND platform_id IN ('tiktok','youtube')
                    GROUP BY keyword
                ) s ON t.keyword = s.keyword
                WHERE t.week=?
                ORDER BY t.z_score DESC, s.post_count DESC
            """, (week, week)).fetchall()

            sns_rows = conn.execute("""
                SELECT keyword, platform_id, engagement_score
                FROM fact_sns_signals
                WHERE week=? AND platform_id IN ('tiktok','youtube')
                ORDER BY engagement_score DESC
            """, (week,)).fetchall()

            # 키워드 × 주차 합산 engagement (현재 주 이하 전체 — 미니차트용)
            trend_rows = conn.execute("""
                SELECT week, keyword, SUM(engagement_score) AS eng
                FROM fact_sns_signals
                WHERE week <= ? AND platform_id IN ('tiktok','youtube')
                GROUP BY week, keyword
            """, (week,)).fetchall()

            # SNS 근거 인용 (direct 캡션 인용구 우선)
            ev_rows = conn.execute("""
                SELECT keyword, match_type, evidence
                FROM fact_llm_insight_products
                WHERE week=? AND evidence IS NOT NULL AND evidence != ''
                ORDER BY CASE match_type WHEN 'direct' THEN 0 ELSE 1 END
            """, (week,)).fetchall()

        # ── 막대 차트: 광의어 제외 후 top 12 ──
        filtered = [r for r in sns_rows if r["keyword"].lower() not in EXCLUDE_KEYWORDS]
        top_kws = list(dict.fromkeys(r["keyword"] for r in filtered))[:12]
        tiktok_scores  = [next((r["engagement_score"] for r in filtered if r["keyword"]==kw and r["platform_id"]=="tiktok"), 0) for kw in top_kws]
        youtube_scores = [next((r["engagement_score"] for r in filtered if r["keyword"]==kw and r["platform_id"]=="youtube"), 0) for kw in top_kws]
        bar_data = {"labels": top_kws, "tiktok": [round(v,0) for v in tiktok_scores], "youtube": [round(v,0) for v in youtube_scores]}

        # ── SNS 근거 인용 맵 (키워드별 direct 인용 우선 최대 2건) ──
        ev_map: dict = {}
        for e in ev_rows:
            kw = e["keyword"]
            ev_map.setdefault(kw, [])
            if len(ev_map[kw]) < 2 and e["evidence"] not in ev_map[kw]:
                ev_map[kw].append(e["evidence"])

        # ── 키워드별 미니 차트: 발굴 시점부터 최대 5주(데이터 있는 주차 기준) ──
        MAX_MINI_WEEKS = 5
        eng_map: dict = {}  # {keyword: {week: eng}}
        for tr in trend_rows:
            eng_map.setdefault(tr["keyword"], {})[tr["week"]] = tr["eng"]
        # {canvas_id: {weeks, data, color}}  (카드 렌더 시 동일 id 사용)
        mini_charts: dict = {}
        for i, r in enumerate(rows):
            kw = r["keyword"]
            cid = f"mini_{i}"
            kw_weeks = sorted(eng_map.get(kw, {}).keys())     # 발굴 시점부터 오름차순
            kw_weeks = kw_weeks[-MAX_MINI_WEEKS:]             # 최근 최대 5개 주차
            series = [round(eng_map[kw][w], 0) for w in kw_weeks]
            mini_charts[cid] = {"weeks": kw_weeks, "data": series,
                                "color": LINE_COLORS[i % len(LINE_COLORS)]}

        chart_data = {"bar": bar_data, "mini": mini_charts}

        # 기회 유형 / 트렌드 모양 카운트
        opp_counts = {}
        shape_counts = {}
        for r in rows:
            ot = r["opportunity_type"] or "unknown"
            opp_counts[ot] = opp_counts.get(ot, 0) + 1
            sh = r["trend_shape"] or "steady"
            shape_counts[sh] = shape_counts.get(sh, 0) + 1

        # 요약 카드 (클릭 = 해당 기회유형 필터, 같은 카드 재클릭 = 전체)
        summary_html = '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:1.2rem">'
        for ot, meta in OPP_META.items():
            cnt  = opp_counts.get(ot, 0)
            desc = OPP_DESC.get(ot, "")
            summary_html += f'''
            <div class="card oppcard" data-o="{ot}" onclick="filterOpp('{ot}')"
                 style="text-align:center;border-left:4px solid {meta["color"]};cursor:pointer;transition:box-shadow .15s">
              <div style="font-size:1.8rem;font-weight:700;color:{meta["color"]}">{cnt}</div>
              <div style="font-size:0.86rem;font-weight:600;color:#333;margin-top:2px">{meta["label"]}</div>
              <div style="font-size:0.72rem;color:#888;margin-top:5px;line-height:1.35">{desc}</div>
            </div>'''
        summary_html += '</div>'

        # 트렌드 모양 + 정렬: select 드롭다운
        shape_opts = '<option value="ALL">전체</option>'
        for sh, meta in SHAPE_META.items():
            cnt = shape_counts.get(sh, 0)
            if cnt == 0:
                continue
            shape_opts += f'<option value="{sh}">{meta["label"]} ({cnt})</option>'

        filter_html = f'''
        <div style="margin-bottom:1rem;display:flex;gap:1.2rem;flex-wrap:wrap;align-items:center">
          <label style="font-size:0.82rem;color:#555">트렌드 모양
            <select onchange="filterShape(this.value)" class="ctl-select">{shape_opts}</select>
          </label>
          <label style="font-size:0.82rem;color:#555">정렬
            <select onchange="sortCards(this.value)" class="ctl-select">
              <option value="z">Z-score 높은 순</option>
              <option value="mom">모멘텀 높은 순</option>
              <option value="eng">Engagement 높은 순</option>
            </select>
          </label>
          <span id="oppFilterTag" style="font-size:0.78rem;color:#e91e8c;display:none">
            · 필터: <b id="oppFilterName"></b>
            <a onclick="filterOpp('ALL')" style="cursor:pointer;color:#999;text-decoration:underline;margin-left:4px">해제</a>
          </span>
        </div>'''
        summary_html += filter_html

        # 상단 막대차트 복구 (브랜드 포함 키워드 비교)
        chart_html = '''
        <div class="card">
          <h3 style="margin-bottom:0.4rem">📱 SNS 키워드 Engagement (TikTok + YouTube)</h3>
          <div style="font-size:0.74rem;color:#aaa;margin-bottom:0.9rem">
            ※ kbeauty 등 광의어는 제외, 브랜드·성분·제품유형 키워드 포함 · 산식은 하단 참조
          </div>
          <div style="position:relative;height:300px"><canvas id="snsChart"></canvas></div>
        </div>'''

        # 키워드 카드 목록
        cards_html = ""
        for i, r in enumerate(rows):
            ot   = r["opportunity_type"] or "new_entry"
            meta = OPP_META.get(ot, OPP_META["new_entry"])
            try:
                brands = json.loads(r["ap_brand_ids"] or "[]")
            except Exception:
                brands = []
            try:
                comps = json.loads(r["competitor_mentions"] or "{}")
            except Exception:
                comps = {}

            brand_badges = "".join(
                f'<span style="background:#f3e5f5;color:#6a1b9a;border-radius:12px;padding:2px 10px;font-size:0.78rem;margin:2px;display:inline-block">{b.upper()}</span>'
                for b in brands
            )
            comp_badges = "".join(
                f'<span style="background:#fce4ec;color:#b71c1c;border-radius:12px;padding:2px 10px;font-size:0.78rem;margin:2px;display:inline-block">vs {c}</span>'
                for c in list(comps.keys())[:3]
            )
            lead_icon  = "🎵" if r["lead_platform"] == "tiktok" else "▶️" if r["lead_platform"] == "youtube" else "📡"
            cross_badge = '<span style="background:#e8f5e9;color:#1b5e20;border-radius:12px;padding:2px 8px;font-size:0.75rem;margin-left:6px">크로스플랫폼</span>' if r["is_cross_platform"] else ""
            shape = r["trend_shape"] or "steady"
            shape_meta = SHAPE_META.get(shape, SHAPE_META["steady"])
            shape_badge = f'<span style="background:{shape_meta["bg"]};color:{shape_meta["color"]};border-radius:12px;padding:3px 12px;font-size:0.8rem;font-weight:600;margin-left:8px">{shape_meta["label"]}</span>'
            z_val  = f"{r['z_score']:.2f}" if r["z_score"] else "—"
            mom    = f"{r['momentum_score']:.1f}×" if r["momentum_score"] else "—"
            posts  = f"{int(r['post_count']):,}" if r["post_count"] else "—"

            need_html = (
                f'<div style="font-size:0.85rem;color:#444;margin-bottom:0.5rem">'
                f'<strong style="color:#6a1b9a">🧬 소비자 니즈</strong> · {r["consumer_need"]}</div>'
            ) if r["consumer_need"] else ""

            # SNS 근거 인용 (direct 캡션 인용구)
            quotes = ev_map.get(r["keyword"], [])
            ev_html = ""
            if quotes:
                items = "".join(
                    f'<div style="font-size:0.78rem;color:#555;margin:2px 0;padding-left:0.5rem;border-left:2px solid #ddd">“{q}”</div>'
                    for q in quotes
                )
                ev_html = (
                    f'<div style="margin-bottom:0.5rem">'
                    f'<strong style="color:#00838f;font-size:0.8rem">💬 SNS 근거</strong>{items}</div>'
                )

            eng_cur = round(eng_map.get(r["keyword"], {}).get(week, 0), 0)
            z_sort   = r["z_score"] or 0
            mom_sort = r["momentum_score"] or 0

            cards_html += f'''
            <div class="card trend-card" data-opp="{ot}" data-shape="{shape}"
                 data-z="{z_sort}" data-mom="{mom_sort}" data-eng="{eng_cur}"
                 style="border-left:4px solid {meta["color"]}">
              <div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:0.5rem">
                <div>
                  <span style="font-size:1.1rem;font-weight:700">{r["keyword"]}</span>
                  <span style="background:{meta["bg"]};color:{meta["color"]};border-radius:12px;padding:3px 12px;font-size:0.8rem;font-weight:600;margin-left:8px">{meta["label"]}</span>
                  {shape_badge}
                  {cross_badge}
                </div>
                <div style="font-size:0.82rem;color:#666">
                  {lead_icon} {r["lead_platform"] or "—"} &nbsp;|&nbsp;
                  Z={z_val} &nbsp;|&nbsp; 모멘텀 {mom} &nbsp;|&nbsp; 포스트 {posts}건
                </div>
              </div>
              <div style="margin:0.6rem 0">{brand_badges}{comp_badges}</div>
              <div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:flex-start">
                <div style="flex:1;min-width:240px">
                  <div style="font-size:0.85rem;color:#333;margin-bottom:0.5rem">
                    <strong style="color:{meta["color"]}">🏷️ AP·경쟁사 관계</strong> · {r["insight_summary"] or ""}
                  </div>
                  {need_html}
                  {ev_html}
                  <div style="font-size:0.82rem;background:#f9f9f9;border-radius:8px;padding:0.6rem;color:#555">
                    💡 {r["action_rec"] or ""}
                  </div>
                </div>
                <div style="width:240px;height:130px;flex-shrink:0">
                  <canvas id="mini_{i}"></canvas>
                </div>
              </div>
            </div>'''

        # 하단: 산식 & 트렌드 키워드 선정 기준
        methodology_html = '''
        <div class="card" style="margin-top:1.5rem;background:#fafafe">
          <h3 style="font-size:0.95rem;margin-bottom:0.8rem">📐 산식 & 트렌드 키워드 선정 기준</h3>
          <div style="font-size:0.8rem;color:#555;line-height:1.9">
            <div><strong>Engagement</strong> :
              <code style="background:#f3f0fa;padding:1px 6px;border-radius:4px">TikTok</code>
              조회수×0.3 + 좋아요×1.0 + 댓글×2.0 &nbsp;|&nbsp;
              <code style="background:#fdeef5;padding:1px 6px;border-radius:4px">YouTube</code>
              조회수×0.2 + 좋아요×1.5 + 댓글×3.0
            </div>
            <div><strong>Z-score</strong> : (이번 주 engagement − 직전 4주 평균) ÷ 직전 4주 표준편차
              &nbsp;— 평소 대비 <b>순간 급등</b> 강도 (≥2.0이면 이상치)</div>
            <div><strong>모멘텀</strong> : 현재 주 engagement ÷ 3주 전 engagement
              &nbsp;— 최근 3주 <b>지속 상승</b> 배수 (weeks_rising = 연속 상승 단계)</div>
          </div>
          <div style="font-size:0.8rem;color:#555;line-height:1.9;margin-top:0.8rem;
                      border-top:1px solid #eee;padding-top:0.7rem">
            <strong>트렌드 키워드 선정 기준</strong> (아래 순서로 필터)
            <ol style="margin:0.3rem 0 0 1.2rem;padding:0;color:#666">
              <li>SNS 키워드별 Z-score 계산 (TikTok+YouTube 합산)</li>
              <li>이상치 키워드 선별: <b>Z-score ≥ 2.0</b> (is_anomaly)</li>
              <li>광의어·AP 브랜드·경쟁사 키워드 제외 (kbeauty, laneige 등)</li>
              <li>실행 가능 볼륨 확보: <b>포스트 수 ≥ 5</b></li>
              <li>분석 신뢰도 확보: 캡션 샘플 ≥ 3건</li>
              <li>상위 <b>최대 10개</b> 키워드 → LLM 인사이트 분석</li>
            </ol>
          </div>
        </div>'''

        html = (summary_html + chart_html
                + f'<div id="trendCards">{cards_html}</div>'
                + methodology_html)
        return html, chart_data

    # ──────────────────────────────────────────────────────
    # Tab 2: 제품 전략
    # ──────────────────────────────────────────────────────
    def _product_tab(self, week: str) -> tuple[str, dict]:
        with self._conn() as conn:
            _cols = [r[1] for r in conn.execute("PRAGMA table_info(fact_product_insights)").fetchall()]
            _vsw = "pi.voc_source_week" if "voc_source_week" in _cols else "NULL AS voc_source_week"
            _vel = "pi.voc_velocity"  if "voc_velocity"  in _cols else "NULL AS voc_velocity"
            _rt  = "pi.rating_trend"  if "rating_trend"  in _cols else "NULL AS rating_trend"
            _tot = "pi.total_reviews" if "total_reviews" in _cols else "NULL AS total_reviews"
            rows = conn.execute(f"""
                SELECT pi.product_id, p.product_name_en, pi.brand_id, b.brand_name_en,
                       pi.brand_tier, pi.strategy_quad,
                       pi.oy_rank_orders, pi.oy_rank_korea, pi.sephora_rank,
                       pi.retail_score, pi.sentiment_pos, pi.sentiment_neg, pi.avg_rating,
                       {_vsw}, {_vel}, {_rt}, {_tot},
                       pi.pos_keywords, pi.neg_keywords, pi.needs_keywords,
                       pi.insight_summary, pi.action_rec,
                       p.oy_url, p.sephora_url
                FROM fact_product_insights pi
                JOIN dim_product p ON pi.product_id = p.product_id
                JOIN dim_brand   b ON pi.brand_id   = b.brand_id
                WHERE pi.week=?
                ORDER BY pi.strategy_quad, pi.sentiment_pos DESC NULLS LAST
            """, (week,)).fetchall()

        if not rows:
            return "<p style='padding:2rem;color:#666'>제품 인사이트 데이터 없음</p>", {}

        # 사분면 카운트
        quad_counts = {q: 0 for q in QUAD_META}
        for r in rows:
            q = r["strategy_quad"] or "MONITOR"
            quad_counts[q] = quad_counts.get(q, 0) + 1

        # 요약 사분면 카드 (클릭 = 필터, 재클릭 = 전체) + 설명
        summary_html = '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:1.2rem">'
        for quad, meta in QUAD_META.items():
            cnt  = quad_counts.get(quad, 0)
            desc = QUAD_DESC.get(quad, "")
            summary_html += f'''
            <div class="card quadcard" data-q="{quad}" onclick="filterQuad('{quad}')"
                 style="text-align:center;border-left:4px solid {meta["color"]};cursor:pointer;transition:box-shadow .15s">
              <div style="font-size:1.8rem;font-weight:700;color:{meta["color"]}">{cnt}</div>
              <div style="font-size:0.86rem;font-weight:600;color:#333;margin-top:2px">{meta["label"]}</div>
              <div style="font-size:0.72rem;color:#888;margin-top:5px;line-height:1.35">{desc}</div>
            </div>'''
        summary_html += '</div>'

        # 검색 + 정렬 컨트롤
        control_html = '''
        <div style="margin-bottom:1rem;display:flex;gap:1rem;flex-wrap:wrap;align-items:center">
          <input id="prodSearch" type="text" placeholder="🔍 제품·브랜드 검색"
            oninput="searchProducts(this.value)"
            style="padding:7px 14px;border-radius:8px;border:1px solid #ddd;font-size:0.85rem;min-width:220px"/>
          <label style="font-size:0.82rem;color:#555">정렬
            <select onchange="sortProducts(this.value)" class="ctl-select">
              <option value="vel">리뷰 유입 많은 순</option>
              <option value="voc">VOC 긍정 높은 순</option>
              <option value="rating">평점 높은 순</option>
            </select>
          </label>
          <span id="quadFilterTag" style="font-size:0.78rem;color:#e91e8c;display:none">
            · 필터: <b id="quadFilterName"></b>
            <a onclick="filterQuad('ALL')" style="cursor:pointer;color:#999;text-decoration:underline;margin-left:4px">해제</a>
          </span>
        </div>'''

        # 전략 산점도 (리뷰 유입 velocity × VOC 긍정도)
        scatter_pts = []
        for r in rows:
            quad = r["strategy_quad"] or "MONITOR"
            scatter_pts.append({
                "x": int(r["voc_velocity"]) if r["voc_velocity"] is not None else 0,
                "y": round(r["sentiment_pos"], 3) if r["sentiment_pos"] is not None else 0,
                "label": (r["product_name_en"] or r["product_id"] or "")[:30],
                "brand": r["brand_name_en"] or r["brand_id"] or "",
                "color": QUAD_META.get(quad, QUAD_META["MONITOR"])["color"],
                "hasVoc": r["sentiment_pos"] is not None,
            })
        scatter_html = '''
        <div class="card">
          <h3 style="margin-bottom:0.4rem">🎯 전략 분포 (리뷰 유입 × VOC 긍정도)</h3>
          <div style="font-size:0.74rem;color:#aaa;margin-bottom:0.6rem">
            가로 = 리뷰 유입 증감(0 기준) · 세로 = VOC 긍정도(0.65 기준) · 점 색 = 전략 분류
            <br>※ 리뷰 유입은 직전 주차 대비 값이라, 연속 2주 수집이 쌓인 뒤(W26+)부터 분산됨
          </div>
          <div style="position:relative;height:360px"><canvas id="quadScatter"></canvas></div>
        </div>'''

        # 제품 테이블 (브랜드 티어 + 부정 VOC 추가)
        table_html = '''
        <div class="card" style="overflow-x:auto">
        <table id="productTable">
        <thead><tr>
          <th>제품</th><th>브랜드</th><th>Tier</th><th>사분면</th>
          <th>리뷰유입</th><th>별점추세</th>
          <th>VOC 긍정</th><th>VOC 부정</th><th>OY주문</th><th>OY한국</th><th>전략 요약</th>
        </tr></thead><tbody>'''

        for r in rows:
            quad = r["strategy_quad"] or "MONITOR"
            meta = QUAD_META.get(quad, QUAD_META["MONITOR"])
            voc_pos = r["sentiment_pos"]
            voc_neg = r["sentiment_neg"]
            voc_pct = f"{voc_pos:.0%}" if voc_pos is not None else "—"
            neg_pct = f"{voc_neg:.0%}" if voc_neg is not None else "—"
            # 폴백(과거 주차 VOC) 표시
            vsw = r["voc_source_week"]
            stale_mark = (f'<br><span style="font-size:0.66rem;color:#e65100" title="이번 주 수집 실패, 과거 주차 VOC 사용">📅 {vsw} 기준</span>'
                          if (vsw and vsw != week and voc_pos is not None) else "")
            voc_color = ("#2e7d32" if (voc_pos or 0) >= 0.65 else "#e65100" if (voc_pos or 0) < 0.5 else "#555") if voc_pos is not None else "#aaa"
            neg_color = ("#b71c1c" if (voc_neg or 0) >= 0.3 else "#999") if voc_neg is not None else "#aaa"
            rating = f"⭐ {r['avg_rating']:.1f}" if r["avg_rating"] else "—"

            tier = r["brand_tier"] or ""
            tmeta = TIER_META.get(tier)
            tier_badge = (f'<span style="background:{tmeta["bg"]};color:{tmeta["color"]};border-radius:10px;padding:2px 8px;font-size:0.72rem;white-space:nowrap">{tmeta["label"]}</span>'
                          if tmeta else "—")

            name = (r["product_name_en"] or "")
            brand = r["brand_name_en"] or r["brand_id"] or "—"
            srch = f"{name} {brand}".lower()
            voc_v = voc_pos or 0
            vel_v = r["voc_velocity"] if r["voc_velocity"] is not None else 0

            # 리뷰 유입(velocity) 표시
            vel = r["voc_velocity"]
            if vel is None:
                vel_html = '<span style="color:#aaa">—</span>'
            elif vel > 0:
                vel_html = f'<span style="color:#2e7d32;font-weight:600">▲ +{vel:,}</span>'
            elif vel < 0:
                vel_html = f'<span style="color:#b71c1c">▼ {vel:,}</span>'
            else:
                vel_html = '<span style="color:#999">0</span>'
            # 별점 추세
            rt = r["rating_trend"]
            if rt is None:
                rt_html = '<span style="color:#aaa">—</span>'
            elif rt > 0:
                rt_html = f'<span style="color:#2e7d32">▲ +{rt:.2f}</span>'
            elif rt < 0:
                rt_html = f'<span style="color:#b71c1c">▼ {rt:.2f}</span>'
            else:
                rt_html = '<span style="color:#999">±0</span>'

            table_html += f'''
            <tr data-quad="{quad}" data-search="{srch}"
                data-vel="{vel_v}" data-voc="{voc_v}" data-rating="{r["avg_rating"] or 0}">
              <td style="max-width:160px;font-size:0.85rem"><strong>{name[:35]}</strong></td>
              <td style="font-size:0.82rem">{brand}</td>
              <td>{tier_badge}</td>
              <td><span style="background:{meta["bg"]};color:{meta["color"]};border-radius:12px;padding:3px 10px;font-size:0.78rem;font-weight:600;white-space:nowrap">{meta["label"]}</span></td>
              <td style="text-align:center;font-size:0.82rem">{vel_html}</td>
              <td style="text-align:center;font-size:0.82rem">{rt_html}</td>
              <td style="text-align:center;color:{voc_color};font-weight:600">{voc_pct}{stale_mark}</td>
              <td style="text-align:center;color:{neg_color}">{neg_pct}</td>
              <td style="text-align:center">{r["oy_rank_orders"] or "—"}</td>
              <td style="text-align:center">{r["oy_rank_korea"] or "—"}</td>
              <td style="font-size:0.8rem;color:#555;max-width:200px">{(r["action_rec"] or "")[:80]}</td>
            </tr>'''

        table_html += '</tbody></table></div>'

        # VOC 키워드 하이라이트 (PUSH_NOW + FIX_AND_PUSH만) + 범례
        legend_html = '''
        <div style="display:flex;gap:1rem;flex-wrap:wrap;font-size:0.76rem;color:#777;margin-bottom:0.8rem">
          <span><span style="color:#1b5e20">✓</span> 긍정 키워드</span>
          <span><span style="color:#b71c1c">✗</span> 부정 키워드</span>
          <span><span style="color:#4527a0">→</span> 개선 니즈</span>
        </div>'''
        # VOC 키워드 표 (제품별 긍정/부정/니즈 분리 + 리테일 링크)
        voc_table = ('<div style="margin-top:1.5rem"><h3 style="margin-bottom:0.6rem;font-size:1rem">'
                     ' 제품별 VOC 키워드</h3>' + legend_html)
        # 사분면 필터 박스
        voc_table += '''
        <div style="margin-bottom:0.8rem;display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
          <span style="font-size:0.78rem;color:#999">사분면 필터</span>
          <button onclick="filterVocQuad('ALL')" class="vbtn active" data-vq="ALL"
            style="padding:5px 14px;border-radius:16px;border:1px solid #ddd;cursor:pointer;font-size:0.8rem;background:white">전체</button>'''
        for q, qm in QUAD_META.items():
            voc_table += f'''
          <button onclick="filterVocQuad('{q}')" class="vbtn" data-vq="{q}"
            style="padding:5px 14px;border-radius:16px;border:1px solid {qm["color"]};cursor:pointer;font-size:0.8rem;color:{qm["color"]};background:{qm["bg"]}">{qm["label"]}</button>'''
        voc_table += '</div>'
        voc_table += '''
        <div class="card" style="overflow-x:auto;padding:0">
        <table id="vocTable"><thead><tr>
          <th>제품</th><th>사분면</th>
          <th style="color:#1b5e20">✓ 긍정 키워드</th>
          <th style="color:#b71c1c">✗ 부정 키워드</th>
          <th style="color:#4527a0">→ 개선 니즈</th>
          <th>리테일</th>
        </tr></thead><tbody>'''
        for r in rows:
            quad = r["strategy_quad"] or "MONITOR"
            meta = QUAD_META.get(quad, QUAD_META["MONITOR"])
            try:
                pos_kws   = json.loads(r["pos_keywords"]   or "[]")
                neg_kws   = json.loads(r["neg_keywords"]   or "[]")
                needs_kws = json.loads(r["needs_keywords"] or "[]")
            except Exception:
                pos_kws = neg_kws = needs_kws = []
            neg_kws = [k for k in neg_kws if k and k.lower() != "none"]
            # 키워드 모두 없으면 표에서 제외
            if not (pos_kws or neg_kws or needs_kws):
                continue

            def _chips(kws, color, bg):
                return "".join(
                    f'<span style="background:{bg};color:{color};border-radius:10px;padding:2px 8px;font-size:0.74rem;margin:2px;display:inline-block">{k}</span>'
                    for k in kws[:6]
                ) or '<span style="color:#ccc">—</span>'

            # 리테일 링크 (OY / Sephora)
            links = []
            if r["oy_url"]:
                links.append(f'<a href="{r["oy_url"]}" target="_blank" style="color:#2e7d32;font-size:0.76rem;text-decoration:none">🛒 OY</a>')
            if r["sephora_url"]:
                links.append(f'<a href="{r["sephora_url"]}" target="_blank" style="color:#1565c0;font-size:0.76rem;text-decoration:none">🛒 Sephora</a>')
            link_html = " · ".join(links) if links else '<span style="color:#ccc">—</span>'

            voc_table += f'''
            <tr data-quad="{quad}">
              <td style="font-size:0.83rem;max-width:170px"><strong>{(r["product_name_en"] or "")[:35]}</strong>
                <div style="font-size:0.74rem;color:#888">{r["brand_name_en"] or r["brand_id"]}</div></td>
              <td><span style="background:{meta["bg"]};color:{meta["color"]};border-radius:12px;padding:2px 9px;font-size:0.74rem;font-weight:600;white-space:nowrap">{meta["label"]}</span></td>
              <td>{_chips(pos_kws, "#1b5e20", "#e8f5e9")}</td>
              <td>{_chips(neg_kws, "#b71c1c", "#fce4ec")}</td>
              <td>{_chips(needs_kws, "#4527a0", "#ede7f6")}</td>
              <td style="white-space:nowrap">{link_html}</td>
            </tr>'''
        voc_table += '</tbody></table></div></div>'

        # 하단: 산식 (4분면 분류 기준은 제외, 지표 산식만)
        methodology_html = '''
        <div class="card" style="margin-top:1.5rem;background:#fafafe">
          <h3 style="font-size:0.95rem;margin-bottom:0.8rem">📐 지표 산식 (VOC 중심)</h3>
          <div style="font-size:0.8rem;color:#555;line-height:1.9">
            <div><strong>VOC 긍정도</strong> : 리뷰 감성 분석 긍정 비율. <b>0.65 이상 = 긍정</b> (부정 0.35 이상 = 불만)</div>
            <div><strong>리뷰 유입(velocity)</strong> : 플랫폼 전체 누적 리뷰 수의 <b>주간 증감</b></div>
            <div><strong>별점 추세</strong> : 플랫폼 전체 평균 평점의 <b>주간 변화</b> (＋ = 만족도 상승)</div>
            <div><strong>리테일 점수</strong>(보조) : max(0, 1 − (최고순위 − 1) ÷ 100)
              &nbsp;— OliveYoung 랭킹 Top 100 기준. <b></div>
          </div>
        </div>'''

        html = summary_html + control_html + scatter_html + table_html + voc_table + methodology_html
        return html, {"scatter": scatter_pts}

    # ──────────────────────────────────────────────────────
    # Tab 3: 방한 추천
    # ──────────────────────────────────────────────────────
    def _inbound_tab(self, week: str) -> str:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT ip.rank, ip.pick_score, ip.brand_tier,
                       p.product_name_en, b.brand_name_en,
                       ip.korea_rank, ip.orders_rank,
                       ip.voc_pos, ip.sns_linked,
                       ip.pos_keywords, ip.pick_reason
                FROM fact_inbound_picks ip
                JOIN dim_product p ON ip.product_id = p.product_id
                JOIN dim_brand   b ON ip.brand_id   = b.brand_id
                WHERE ip.week=?
                ORDER BY ip.rank
            """, (week,)).fetchall()

        if not rows:
            return "<p style='padding:2rem;color:#666'>방한 추천 데이터 없음 (oy_top_korea 데이터 필요)</p>"

        # 상위 3개 포디움
        podium_html = '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1rem;margin-bottom:2rem">'
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rows[:3]):
            score_pct = int(r["pick_score"] * 100)
            podium_html += f'''
            <div class="card" style="text-align:center;border-top:4px solid #e91e8c;padding:1.5rem">
              <div style="font-size:2rem">{medals[i]}</div>
              <div style="font-weight:700;font-size:1rem;margin:0.5rem 0">{(r["product_name_en"] or "")[:25]}</div>
              <div style="font-size:0.85rem;color:#666;margin-bottom:0.8rem">{r["brand_name_en"] or "—"}</div>
              <div style="font-size:1.4rem;font-weight:700;color:#e91e8c">{r["pick_score"]:.3f}</div>
              <div style="font-size:0.75rem;color:#aaa">pick score</div>
              <div style="height:6px;background:#f0f0f0;border-radius:3px;margin-top:0.8rem">
                <div style="width:{score_pct}%;height:6px;background:linear-gradient(90deg,#e91e8c,#6c4ab6);border-radius:3px"></div>
              </div>
            </div>'''
        podium_html += '</div>'

        # 전체 리스트
        list_html = '<div class="card" style="overflow-x:auto"><table><thead><tr>'
        list_html += '<th>순위</th><th>제품</th><th>브랜드</th><th>Tier</th>'
        list_html += '<th>OY 한국</th><th>OY 주문</th><th>VOC 긍정</th><th>SNS</th>'
        list_html += '<th>Score</th><th>추천 이유</th></tr></thead><tbody>'

        for r in rows:
            voc_str = f"{r['voc_pos']:.0%}" if r["voc_pos"] else "—"
            voc_color = "#2e7d32" if (r["voc_pos"] or 0) >= 0.65 else "#555"
            sns_icon  = "✅" if r["sns_linked"] else "—"
            score_pct = int(r["pick_score"] * 100)

            try:
                pos_kws = json.loads(r["pos_keywords"] or "[]")[:3]
            except Exception:
                pos_kws = []
            kw_str = " · ".join(pos_kws) if pos_kws else ""

            list_html += f'''
            <tr>
              <td style="text-align:center;font-weight:700;font-size:1.1rem">#{r["rank"]}</td>
              <td style="font-size:0.85rem"><strong>{(r["product_name_en"] or "")[:30]}</strong>
                {f'<div style="font-size:0.75rem;color:#888">{kw_str}</div>' if kw_str else ""}
              </td>
              <td style="font-size:0.82rem">{r["brand_name_en"] or "—"}</td>
              <td style="font-size:0.75rem;color:#888">{r["brand_tier"] or "—"}</td>
              <td style="text-align:center">{r["korea_rank"] or "—"}</td>
              <td style="text-align:center">{r["orders_rank"] or "—"}</td>
              <td style="text-align:center;color:{voc_color};font-weight:600">{voc_str}</td>
              <td style="text-align:center">{sns_icon}</td>
              <td>
                <div style="font-weight:600;font-size:0.9rem;color:#e91e8c">{r["pick_score"]:.3f}</div>
                <div style="height:4px;background:#f0f0f0;border-radius:2px;margin-top:3px">
                  <div style="width:{score_pct}%;height:4px;background:linear-gradient(90deg,#e91e8c,#6c4ab6);border-radius:2px"></div>
                </div>
              </td>
              <td style="font-size:0.8rem;color:#555;max-width:220px">{(r["pick_reason"] or "")[:100]}</td>
            </tr>'''

        list_html += '</tbody></table></div>'

        # 선정 기준 설명
        criteria_html = '''
        <div class="card" style="margin-top:1.5rem;background:#f9f9f9">
          <h3 style="margin-bottom:0.8rem;font-size:0.95rem">📌 Pick Score 산출 기준</h3>
          <div style="font-size:0.85rem;color:#555;line-height:1.7">
            <strong>Pick Score</strong> = OY 한국 순위 × 0.45 + OY 글로벌 주문 순위 × 0.25
            + VOC 긍정도 × 0.20 + SNS 트렌드 연결 여부 × 0.10<br/>
            <span style="color:#888">• OY Top in Korea: 방한 관광객이 실제로 구매하는 상품 기준</span><br/>
            <span style="color:#888">• OY Top Orders: 글로벌 수요 보조 지표</span><br/>
            <span style="color:#888">• SNS 연결: fact_llm_insight_products에 포함된 제품 (트렌드 언급)</span>
          </div>
        </div>'''

        return podium_html + list_html + criteria_html

    @staticmethod
    def _week_label(week: str) -> str:
        """'2026-W24' → '2026년 6월 2주차 (6/8~6/14)' 형식의 사람이 읽을 라벨."""
        try:
            year, wnum = week.split("-W")
            year, wnum = int(year), int(wnum)
            from datetime import date, timedelta
            # ISO 주의 월요일
            monday = date.fromisocalendar(year, wnum, 1)
            sunday = monday + timedelta(days=6)
            # 해당 월의 '몇 주차' (주의 목요일 기준 월 판정 — ISO 관행)
            thursday = monday + timedelta(days=3)
            first_day = date(thursday.year, thursday.month, 1)
            week_of_month = (thursday.day + first_day.weekday()) // 7 + 1
            return (f"{thursday.year}년 {thursday.month}월 {week_of_month}주차 "
                    f"({monday.month}/{monday.day}~{sunday.month}/{sunday.day})")
        except Exception:
            return week

    # ──────────────────────────────────────────────────────
    # 최종 리포트 생성
    # ──────────────────────────────────────────────────────
    def generate(self, week: str = None) -> Path:
        week = week or datetime.now(tz=timezone.utc).strftime("%G-W%V")
        logger.info(f"HTML 리포트 생성 중 (week={week})")

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        week_label = self._week_label(week)

        trend_html,  chart_data   = self._trend_tab(week)
        product_html, product_data = self._product_tab(week)
        chart_data["scatter"] = product_data.get("scatter", [])
        inbound_html             = self._inbound_tab(week)

        # 요약 통계
        with self._conn() as conn:
            trend_cnt   = conn.execute("SELECT COUNT(*) FROM fact_trend_insights  WHERE week=?", (week,)).fetchone()[0]
            product_cnt = conn.execute("SELECT COUNT(*) FROM fact_product_insights WHERE week=?", (week,)).fetchone()[0]
            inbound_cnt = conn.execute("SELECT COUNT(*) FROM fact_inbound_picks    WHERE week=?", (week,)).fetchone()[0]
            push_cnt    = conn.execute("SELECT COUNT(*) FROM fact_product_insights WHERE week=? AND strategy_quad='PUSH_NOW'", (week,)).fetchone()[0]

        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>K-beauty Intelligence Report — {week}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{
    --primary: #e91e8c;
    --secondary: #6c4ab6;
    --bg: #f7f7fb;
    --card: #ffffff;
    --text: #1a1a2e;
    --muted: #666;
    --border: #e0e0e0;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: var(--bg); color: var(--text); }}
  header {{ background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white; padding: 2rem 2rem 1.5rem; }}
  header h1 {{ font-size: 1.7rem; font-weight: 700; }}
  header p  {{ opacity: 0.85; margin-top: 0.4rem; font-size: 0.92rem; }}
  .header-stats {{ display: flex; gap: 2rem; margin-top: 1.2rem; flex-wrap: wrap; }}
  .hstat {{ text-align: center; }}
  .hstat .val {{ font-size: 1.6rem; font-weight: 700; }}
  .hstat .lbl {{ font-size: 0.75rem; opacity: 0.8; margin-top: 2px; }}
  .tabs {{ display: flex; background: white; border-bottom: 2px solid var(--border);
           padding: 0 1.5rem; position: sticky; top: 0; z-index: 10;
           box-shadow: 0 2px 8px rgba(0,0,0,.06); }}
  .tab  {{ padding: 1rem 1.4rem; cursor: pointer; font-size: 0.92rem; font-weight: 500;
           white-space: nowrap; border-bottom: 3px solid transparent; color: var(--muted);
           transition: color 0.2s; }}
  .tab.active {{ color: var(--primary); border-bottom-color: var(--primary); }}
  .tab:hover  {{ color: var(--primary); }}
  .section {{ display: none; padding: 1.5rem; max-width: 1280px; margin: 0 auto; }}
  .section.active {{ display: block; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px;
           padding: 1.2rem; margin-bottom: 1rem; box-shadow: 0 2px 6px rgba(0,0,0,.04); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{ background: #f5f5f5; padding: 0.65rem 0.9rem; text-align: left;
        font-weight: 600; border-bottom: 2px solid var(--border); font-size: 0.82rem; }}
  td {{ padding: 0.6rem 0.9rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  tr:hover td {{ background: #fafafe; }}
  .qbtn.active {{ background: var(--primary) !important; color: white !important; border-color: var(--primary) !important; }}
  .vbtn.active {{ background: var(--primary) !important; color: white !important; border-color: var(--primary) !important; }}
  .oppcard:hover, .quadcard:hover {{ box-shadow: 0 3px 12px rgba(0,0,0,.12); }}
  .oppcard.active, .quadcard.active {{ box-shadow: 0 0 0 2px var(--primary); }}
  .ctl-select {{ margin-left: 6px; padding: 5px 10px; border-radius: 8px;
                 border: 1px solid var(--border); font-size: 0.82rem; background: white;
                 cursor: pointer; }}
  footer {{ text-align: center; padding: 2rem; color: var(--muted); font-size: 0.82rem;
            border-top: 1px solid var(--border); margin-top: 2rem; }}
  @media (max-width: 768px) {{
    .header-stats {{ gap: 1rem; }}
    .tab {{ padding: 0.8rem 0.8rem; font-size: 0.82rem; }}
  }}
</style>
</head>
<body>

<header>
  <h1> K-beauty Intelligence Report</h1>
  <p>Amorepacific US Market<br>
  <strong>{week}</strong> · {week_label} · Generated {ts}</p>
  <div style="margin-top:0.7rem;font-size:0.8rem;opacity:0.9;display:flex;gap:1.5rem;flex-wrap:wrap">
    <span><strong>SNS</strong> : TikTok / YouTube</span>
    <span><strong>Retail</strong> : OY Global / Sephora USA</span>
  </div>
  <div class="header-stats">
    <div class="hstat"><div class="val">{trend_cnt}</div><div class="lbl">트렌드 인사이트</div></div>
    <div class="hstat"><div class="val">{product_cnt}</div><div class="lbl">제품 분석</div></div>
    <div class="hstat"><div class="val">{push_cnt}</div><div class="lbl">PUSH NOW 제품</div></div>
    <div class="hstat"><div class="val">{inbound_cnt}</div><div class="lbl">방한 추천 제품</div></div>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="switchTab(0)"> 트렌드 인사이트</div>
  <div class="tab"        onclick="switchTab(1)"> 제품 전략</div>
  <div class="tab"        onclick="switchTab(2)"> 방한 추천</div>
</div>

<div class="section active" id="s0">{trend_html}</div>
<div class="section"        id="s1">{product_html}</div>
<div class="section"        id="s2">{inbound_html}</div>

<footer>K-beauty Intelligence Agent v3 &nbsp;|&nbsp; Amorepacific Portfolio Project</footer>

<script>
function switchTab(idx) {{
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', i===idx));
  document.querySelectorAll('.section').forEach((s,i) => s.classList.toggle('active', i===idx));
  if (idx === 1) renderScatter();  // 제품 탭 첫 진입 시 산점도 렌더 (숨김 캔버스 사이즈 이슈 회피)
}}

const QUAD_LABELS = {{ PUSH_NOW:'PUSH NOW', FIX_AND_PUSH:'FIX & PUSH', HOLD:'HOLD', MONITOR:'MONITOR' }};
let _quadFilter = 'ALL', _prodSearch = '';
function applyProductFilters() {{
  document.querySelectorAll('#productTable tbody tr').forEach(tr => {{
    const okQ = (_quadFilter==='ALL' || tr.dataset.quad===_quadFilter);
    const okS = (!_prodSearch || (tr.dataset.search||'').includes(_prodSearch));
    tr.style.display = (okQ && okS) ? '' : 'none';
  }});
}}
function filterQuad(q) {{
  _quadFilter = (q === _quadFilter) ? 'ALL' : q;
  document.querySelectorAll('.quadcard').forEach(c =>
    c.classList.toggle('active', c.dataset.q === _quadFilter));
  const tag = document.getElementById('quadFilterTag');
  if (tag) {{
    if (_quadFilter === 'ALL') {{ tag.style.display = 'none'; }}
    else {{
      tag.style.display = '';
      document.getElementById('quadFilterName').textContent = QUAD_LABELS[_quadFilter] || _quadFilter;
    }}
  }}
  applyProductFilters();
}}
function searchProducts(v) {{ _prodSearch = (v||'').toLowerCase().trim(); applyProductFilters(); }}
function filterVocQuad(q) {{
  document.querySelectorAll('.vbtn').forEach(b => b.classList.toggle('active', b.dataset.vq === q));
  document.querySelectorAll('#vocTable tbody tr').forEach(tr => {{
    tr.style.display = (q==='ALL' || tr.dataset.quad===q) ? '' : 'none';
  }});
}}
function sortProducts(key) {{
  const tbody = document.querySelector('#productTable tbody');
  if (!tbody) return;
  const attr = key==='vel' ? 'vel' : key==='voc' ? 'voc' : 'rating';
  Array.from(tbody.querySelectorAll('tr'))
    .sort((a,b) => parseFloat(b.dataset[attr]||0) - parseFloat(a.dataset[attr]||0))
    .forEach(tr => tbody.appendChild(tr));
}}

const OPP_LABELS = {{ amplify:'즉시 강화', position:'차별화', counter:'대응 필요', new_entry:'신규 진입' }};
let _oppFilter = 'ALL', _shapeFilter = 'ALL';
function applyTrendFilters() {{
  document.querySelectorAll('.trend-card').forEach(c => {{
    const okO = (_oppFilter==='ALL'   || c.dataset.opp===_oppFilter);
    const okS = (_shapeFilter==='ALL' || c.dataset.shape===_shapeFilter);
    c.style.display = (okO && okS) ? '' : 'none';
  }});
}}
function filterOpp(o) {{
  // 같은 카드 재클릭 시 전체 해제 (토글)
  _oppFilter = (o === _oppFilter) ? 'ALL' : o;
  document.querySelectorAll('.oppcard').forEach(c =>
    c.classList.toggle('active', c.dataset.o === _oppFilter));
  const tag = document.getElementById('oppFilterTag');
  if (_oppFilter === 'ALL') {{ tag.style.display = 'none'; }}
  else {{
    tag.style.display = '';
    document.getElementById('oppFilterName').textContent = OPP_LABELS[_oppFilter] || _oppFilter;
  }}
  applyTrendFilters();
}}
function filterShape(s) {{
  _shapeFilter = s;
  applyTrendFilters();
}}
function sortCards(key) {{
  const cont = document.getElementById('trendCards');
  if (!cont) return;
  const cards = Array.from(cont.querySelectorAll('.trend-card'));
  cards.sort((a,b) => parseFloat(b.dataset[key]||0) - parseFloat(a.dataset[key]||0));
  cards.forEach(c => cont.appendChild(c));
}}

// 차트 데이터
const chartData = {json.dumps(chart_data)};

// 상단 막대 차트 — 키워드별 TikTok/YouTube engagement (브랜드 포함)
const barData = chartData.bar || {{}};
if (barData.labels && barData.labels.length) {{
  new Chart(document.getElementById('snsChart'), {{
    type: 'bar',
    data: {{
      labels: barData.labels,
      datasets: [
        {{ label: 'TikTok',  data: barData.tiktok,  backgroundColor: 'rgba(105,201,208,0.8)', borderColor: '#69C9D0', borderWidth: 1 }},
        {{ label: 'YouTube', data: barData.youtube, backgroundColor: 'rgba(255,0,0,0.7)',     borderColor: '#FF0000', borderWidth: 1 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'top' }} }},
      scales: {{ x: {{ stacked: false }}, y: {{ beginAtZero: true }} }}
    }}
  }});
}}

// 키워드별 미니 차트 — 각 카드의 발굴 시점~최대 5주 engagement 추이
const miniCharts = chartData.mini || {{}};
Object.keys(miniCharts).forEach(cid => {{
  const m = miniCharts[cid];
  const el = document.getElementById(cid);
  if (!el || !m.weeks || !m.weeks.length) return;
  new Chart(el, {{
    type: 'line',
    data: {{
      labels: m.weeks,
      datasets: [{{
        data: m.data,
        borderColor: m.color,
        backgroundColor: m.color + '22',
        borderWidth: 2,
        tension: 0.3,
        pointRadius: 3,
        pointHoverRadius: 5,
        fill: true
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: true }} }},
      scales: {{
        x: {{ ticks: {{ font: {{ size: 9 }} }} }},
        y: {{ beginAtZero: true, ticks: {{ font: {{ size: 9 }}, maxTicksLimit: 4 }} }}
      }}
    }}
  }});
}});

// 전략 사분면 산점도 (제품 탭 첫 진입 시 1회 렌더)
let _scatterDone = false;
function renderScatter() {{
  if (_scatterDone) return;
  const pts = chartData.scatter || [];
  const el = document.getElementById('quadScatter');
  if (!el || !pts.length) {{ _scatterDone = true; return; }}
  _scatterDone = true;

  // 구분선 (x=0 리뷰유입 중립, y=0.65 VOC 긍정) 플러그인
  const quadrantLines = {{
    id: 'quadrantLines',
    beforeDraw(chart) {{
      const {{ ctx, chartArea, scales }} = chart;
      if (!chartArea) return;
      const xv = scales.x.getPixelForValue(0);
      const yv = scales.y.getPixelForValue(0.65);
      ctx.save();
      ctx.strokeStyle = '#ccc'; ctx.setLineDash([5,4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(xv, chartArea.top); ctx.lineTo(xv, chartArea.bottom); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(chartArea.left, yv); ctx.lineTo(chartArea.right, yv); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = '#bbb'; ctx.font = '11px sans-serif';
      ctx.fillText('PUSH NOW',  xv + 6, chartArea.top + 14);
      ctx.fillText('FIX & PUSH', xv + 6, chartArea.bottom - 6);
      ctx.fillText('HOLD',    chartArea.left + 6, chartArea.top + 14);
      ctx.fillText('MONITOR', chartArea.left + 6, chartArea.bottom - 6);
      ctx.restore();
    }}
  }};

  new Chart(el, {{
    type: 'scatter',
    data: {{ datasets: [{{
      data: pts.map(p => ({{ x: p.x, y: p.y }})),
      pointBackgroundColor: pts.map(p => p.hasVoc ? p.color : '#ccc'),
      pointBorderColor: pts.map(p => p.hasVoc ? p.color : '#aaa'),
      pointRadius: 6, pointHoverRadius: 9
    }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: (c) => {{
          const p = pts[c.dataIndex];
          return `${{p.brand}} · ${{p.label}} (리뷰유입 ${{p.x}}, VOC ${{p.hasVoc ? p.y : 'N/A'}})`;
        }} }} }}
      }},
      scales: {{
        x: {{ title: {{ display: true, text: '리뷰 유입 증감 →' }} }},
        y: {{ min: 0, max: 1, title: {{ display: true, text: 'VOC 긍정도 →' }} }}
      }}
    }},
    plugins: [quadrantLines]
  }});
}}
</script>
</body>
</html>"""

        out_path = self.export_dir / f"kbeauty_report_{week.replace(':', '-')}.html"
        out_path.write_text(html, encoding="utf-8")
        logger.info(f"HTML 리포트 저장: {out_path}")
        return out_path
