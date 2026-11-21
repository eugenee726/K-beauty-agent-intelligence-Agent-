/* K-beauty Trend Radar — 정적 대시보드
 * 데이터: window.DASH (tools/report/dashboard_builder.py 가 생성한 data.js)
 * URL: ?w=2026-W47#trends  (주간)  |  ?m=2026-11#brands  (월간)
 *
 * 화면 표시 규칙(RULES)은 파이프라인 판정을 바꾸지 않는다. data.js 값으로 화면에서만 계산하고,
 * 기준값은 방법론 페이지에 그대로 노출한다.
 */
(() => {
  // ── 화면 표시 규칙 (방법론 페이지가 이 객체를 그대로 렌더링) ──
  const RULES = {
    LOW_SAMPLE_POSTS:   { v: 10,  label: '표본 부족', desc: '이번 주 게시물이 이 값보다 적으면 관찰 중' },
    MIN_CONSEC_WEEKS:   { v: 3,   label: '이력 짧음', desc: '이번 주까지 끊김 없이 이어진 ISO 주차 수가 이 값보다 적으면 관찰 중 (예: W45→W47은 W46이 없어 끊김)' },
    BIG_JUMP_RATIO:     { v: 10,  label: '급등 의심', desc: '직전 데이터 주차 대비 engagement가 이 배수 이상이면 관찰 중. 주차별 게시물 수가 데이터에 없어 게시물당 값 대신 총량으로 비교하고, 게시물 수를 함께 표시' },
    MIN_MENTIONS:       { v: 5,   label: '판단 보류', desc: '트렌드 캡션 전체에서 센 브랜드 언급 합계가 이 값보다 적으면 대화 점유(선점·각축·공백)를 판단하지 않음' },
    SURGE_Z:            { v: 2.0, label: '급등', desc: 'z-score가 이 값 이상이면 급등, 미만이면 완만한 상승으로 집계 (파이프라인 Z_ALERT와 같은 값)' },
    SUSTAIN_MOMENTUM:   { v: 1.5, label: '지속 기준선', desc: '레이더 가로 구분선. 파이프라인의 지속 상승 모멘텀 기준과 같은 값' },
    RADAR_X_MAX:        { v: 50,  label: '레이더 가로 상한', desc: '모멘텀이 이 배수를 넘으면 축 끝에 두고 실제 값을 라벨로 표시' },
    RADAR_Y_MAX:        { v: 10,  label: '레이더 세로 상한', desc: 'z-score가 이 값을 넘으면 축 위쪽 끝에 두고 실제 값을 라벨로 표시' },
    SHARE_TARGET_N:     { v: 40,  label: '점유 표본 하한', desc: '결정적 집계의 브랜드 언급이 이 값보다 적으면 점유를 "표본 부족(참고용)"으로 표시. n=40이면 점유 추정 오차가 약 ±15%p(95%)' },
    LEADER_CI_LOWER:    { v: 0.5, label: '유의한 선점', desc: '결정적 집계에서 1위 브랜드 점유의 Wilson 95% 하한이 이 값을 넘으면 "유의한 선점"' },
    VIRAL_SHARE:        { v: 0.8, label: '단일 게시물 의존', desc: '키워드 게시물 조회수 중 1위 게시물 비중이 이 값 이상이면 관찰 중 — 바이럴 영상 하나가 신호를 만든 경우' },
  };
  const SPONSOR_RE = /(#ad\b|#sponsored|partner|#광고|\[광고\]|쿠팡\s?파트너스|supported by|gifted|할인\s?코드|discount code|promo code|use code|code\s*[:：]\s*[a-z0-9]{3,})/i;

  const D = window.DASH;
  const META = D.meta;
  const ACCENT = '#d6336c', DARK = '#495057', LIGHT = '#ced4da';
  const TRUST = { ok: { label: '판단 가능', cls: 'ok' }, watch: { label: '관찰 중', cls: 'watch' }, hold: { label: '판단 보류', cls: 'hold' } };
  Chart.defaults.font.family = 'Pretendard, -apple-system, sans-serif';
  Chart.defaults.color = '#868e96';

  const WEEKS = D.weeks;
  const WEEK = Object.fromEntries(WEEKS.map(w => [w.week, w]));
  const MONTHS = [...new Set(WEEKS.map(w => w.month))];
  const PAGES = { overview: '개요', trends: '트렌드', brands: '브랜드', products: '제품 전략', inbound: '방한 추천', quality: '검증·품질', method: '방법론' };

  // ── 표기 ──
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString('ko-KR');
  const pct = (v, d = 0) => (v === null || v === undefined) ? '—' : (v * 100).toFixed(d) + '%';
  const zf = z => Number(z).toFixed(1);
  const momf = m => m >= 10 ? `${Math.round(m)}배` : `${Number(m).toFixed(1)}배`;
  const engf = n => n >= 1e8 ? `${(n / 1e8).toFixed(1)}억` : n >= 1e4 ? `${Math.round(n / 1e4).toLocaleString('ko-KR')}만` : fmt(n);
  const UPPER = new Set(['spf', 'bha', 'aha', 'pha', 'txa', 'pdrn', 'uv', 'led', 'diy', 'kbeauty']);
  const kwName = k => String(k).split('_').map(p => UPPER.has(p) ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1)).join(' ');
  const brandName = b => (D.brand_names[b] || String(b)).toUpperCase();
  const key = s => String(s || '').toLowerCase().replace(/[^a-z0-9가-힣]/g, '');
  const sum = a => a.reduce((x, y) => x + y, 0);
  const monthLabel = m => `${m.slice(0, 4)}년 ${+m.slice(5)}월`;
  const firstSentence = s => String(s || '').split(/(?<=[.다])\s/)[0];
  let charts = [];
  const chart = (el, cfg) => { if (!el) return null; const c = new Chart(el, cfg); charts.push(c); return c; };

  // ISO 주차 → 월요일 날짜(ms)
  const mondayOf = wk => { const [y, w] = wk.split('-W').map(Number); const jan4 = new Date(Date.UTC(y, 0, 4));
    const d = jan4.getUTCDay() || 7; return Date.UTC(y, 0, 4 - d + 1 + (w - 1) * 7); };
  const weekOrd = wk => Math.round(mondayOf(wk) / (7 * 864e5));
  const weekAdd = (wk, n) => {   // ISO 주차에 n주 더하기 (목요일 기준 연도)
    const thu = new Date(mondayOf(wk) + (n * 7 + 3) * 864e5);
    const y = thu.getUTCFullYear(), first = mondayOf(`${y}-W01`);
    return `${y}-W${String(Math.floor((thu - first) / (7 * 864e5)) + 1).padStart(2, '0')}`;
  };

  // ── 트렌드 표시 계산 (data.js 필드만 사용) ──
  function shapeView(t) {
    if (t.shape === 'sustained' && t.z >= RULES.SURGE_Z.v) return { icon: '⇈', label: '급등+지속' };
    return ({ spike: { icon: '↑', label: '급등' }, sustained: { icon: '↗', label: '지속' },
              emerging: { icon: '＋', label: '신규' } })[t.shape] || { icon: '→', label: '정체' };
  }
  const strength = t => t.z >= RULES.SURGE_Z.v ? '급등' : '완만한 상승';
  function mentionsOf(w, kw) {
    const rows = Object.entries(w.matrix).map(([b, r]) => [b, r[kw] || 0]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]);
    return { total: sum(rows.map(r => r[1])), rows };
  }
  function trustOf(w, t) {
    const reasons = [];
    if (t.posts < RULES.LOW_SAMPLE_POSTS.v) reasons.push(`표본 부족: 게시물 ${t.posts}건 (< ${RULES.LOW_SAMPLE_POSTS.v})`);
    let run = 0;
    for (let i = t.hist.length - 1; i >= 0; i--) {
      if (i === t.hist.length - 1) { run = t.hist[i][0] === w.week ? 1 : 0; if (!run) break; continue; }
      if (weekOrd(t.hist[i + 1][0]) - weekOrd(t.hist[i][0]) === 1) run++; else break;
    }
    if (run < RULES.MIN_CONSEC_WEEKS.v) reasons.push(`이력 짧음: 연속 ${run}주 (< ${RULES.MIN_CONSEC_WEEKS.v}) — ${t.hist.map(h => h[0].slice(5)).join('→')}`);
    const prev = t.hist.length >= 2 ? t.hist[t.hist.length - 2] : null;
    if (prev && prev[1] > 0 && t.eng / prev[1] >= RULES.BIG_JUMP_RATIO.v)
      reasons.push(`급등 의심: ${prev[0].slice(5)} 대비 engagement ${Math.round(t.eng / prev[1])}배, 게시물 ${t.posts}건`);
    if (t.top_post_share !== null && t.top_post_share !== undefined && t.top_post_share >= RULES.VIRAL_SHARE.v)
      reasons.push(`단일 게시물 의존: 조회수의 ${pct(t.top_post_share)}가 게시물 1개`);
    const m = mentionsOf(w, t.kw);
    // 판단 보류는 대화 점유 판정과 같은 집계(결정적, 없으면 LLM)의 언급 수로 본다
    const nMention = t.det && t.det.total !== undefined ? t.det.total : m.total;
    const state = nMention < RULES.MIN_MENTIONS.v ? 'hold' : reasons.length ? 'watch' : 'ok';
    if (state === 'hold') reasons.unshift(`판단 보류: K-브랜드 언급 ${nMention}건 (< ${RULES.MIN_MENTIONS.v})`);
    const base = t.hist.length >= 3 ? t.hist[t.hist.length - 3][0] : null;
    return { state, reasons, mentions: m, base };
  }
  const isSponsor = e => SPONSOR_RE.test(e);
  // Wilson 95% 구간
  const wilson = (k, n) => { if (!n) return [0, 0]; const z = 1.96, p = k / n, d = 1 + z * z / n;
    const c = p + z * z / (2 * n), m = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return [(c - m) / d, (c + m) / d]; };
  // 점유 표시용: 결정적 집계(det)가 있으면 그것을, 없으면 LLM 언급(matrix)을 쓴다
  function shareOf(w, t) {
    if (t.det && t.det.total) {
      const rows = t.det.brands, total = t.det.total, top = rows[0];
      const ci = top ? wilson(top[1], total) : [0, 0];
      return { src: '결정적', total, rows, captions: t.det.captions, deep: t.det.deep, ci,
               small: total < RULES.SHARE_TARGET_N.v, sig: !!top && ci[0] > RULES.LEADER_CI_LOWER.v };
    }
    const m = mentionsOf(w, t.kw);
    return { src: 'LLM', total: m.total, rows: m.rows, captions: null, ci: m.rows[0] ? wilson(m.rows[0][1], m.total) : [0, 0],
             small: m.total < RULES.SHARE_TARGET_N.v, sig: false };
  }
  const shareNote = sh => sh.total ? `언급 ${sh.total}건${sh.captions !== null ? ` · 캡션 ${sh.captions}건` : ''} · 1위 95% 범위 ${pct(sh.ci[0])}~${pct(sh.ci[1])}` : '언급 없음';
  const FAIL_TYPE = { direct_quote: '인용 불일치', brand_count: '언급 수 불일치', indirect_reason: '근거 부족', korean_need: '형식 오류',
                      llm_product: '제품 연결 오류', llm_brand: '브랜드 아님', llm_need: '니즈 해석 문제', llm_opportunity: '기회 해석 문제',
                      absent_brand: '캡션에 없는 브랜드' };
  // 브랜드 관련 실패를 두 종류로 나눈다: 캡션에 아예 없는 브랜드를 셈(absent) / 판매처·매체·가수 등 브랜드가 아닌 것을 셈(llm_brand)
  const bcount = f => { const m = /(\d+)건 중 '.*?' 등장 (\d+)건 \(LLM 주장 (.+?)\)/.exec(f.detail || ''); return m ? { n: +m[1], hit: +m[2], claim: m[3] } : null; };
  const NOT_BRAND_RE = /아닙니다|아니다|아님|아니며|not a (beauty )?brand|판매처|소매|플랫폼|매체|그룹|서비스|retailer|media/i;
  function failKey(f) {
    if (f.check_type === 'llm_brand') return NOT_BRAND_RE.test(f.detail || '') ? 'llm_brand' : 'absent_brand';
    if (f.check_type === 'brand_count') { const c = bcount(f); return c && c.hit === 0 ? 'absent_brand' : 'brand_count'; }
    return f.check_type;
  }
  const bname = b => (D.brand_names && (D.brand_names[b] || D.brand_names[String(b).toLowerCase().replace(/\s+/g, '_')])) || b;
  function failPlain(f) {
    const who = f.target_name || f.target, k = failKey(f);
    if (k === 'absent_brand') { const c = bcount(f);
      return `LLM이 '${bname(f.target)}'을(를) 언급 브랜드로 셌지만${c ? `(${c.claim}건)` : ''}, LLM이 읽은 캡션 ${c ? c.n : 30}건 어디에도 없음. 근거 없는 언급`; }
    if (f.check_type === 'brand_count') { const c = bcount(f);
      return c ? `LLM은 '${bname(f.target)}'이(가) ${c.claim}번 나온다고 했지만, 실제 캡션 ${c.n}건에는 ${c.hit}번` : `언급 수가 실제 캡션과 다름 — ${f.detail}`; }
    if (f.check_type === 'direct_quote') return `근거로 든 인용문이 실제 캡션에 없음 (${who} 연결 근거). 여러 캡션을 이어 붙였거나 바꿔 쓴 문장`;
    if (f.check_type === 'indirect_reason') return `${who} 간접 연결의 근거 문장이 비어 있거나 너무 짧음`;
    if (f.check_type === 'korean_need') return '소비자 니즈가 한국어로 작성되지 않음';
    if (f.check_type === 'llm_product') return `'${who}' 연결이 캡션 근거와 맞지 않음 — ${f.detail}`;
    if (f.check_type === 'llm_brand') return `'${f.target}'은(는) 판매처·매체·사람 등으로 화장품 브랜드가 아님 — ${f.detail}`;
    return f.detail || '';
  }
  const failsOf = (w, kw) => w.fails.filter(f => f.keyword === kw);
  const failKind = f => /^S\d{6,}$/.test(f.target) ? 'evidence' : ['consumer_need', 'opportunity'].includes(f.target) ? 'need' : 'brand';
  const failTip = fs => fs.map(f => `[${f.checker === 'rule' ? '규칙' : 'LLM'}] ${f.target}: ${f.detail}`).join('\n');
  const oppView = (t, tr) => tr.state === 'hold' ? { label: '판단 보류', o: 'hold' } : { label: (META.opp[t.opp] || {}).label || t.opp || '—', o: t.opp };

  // ── 상태 / URL ──
  const state = { mode: 'week', key: WEEKS.length ? WEEKS[WEEKS.length - 1].week : null, page: 'overview' };
  function readUrl() {
    const q = new URLSearchParams(location.search);
    if (q.get('m') && MONTHS.includes(q.get('m'))) { state.mode = 'month'; state.key = q.get('m'); }
    else if (q.get('w') && WEEK[q.get('w')]) { state.mode = 'week'; state.key = q.get('w'); }
    const h = location.hash.replace('#', '');
    if (PAGES[h]) state.page = h;
  }
  function writeUrl(push) {
    const url = `${location.pathname}?${state.mode === 'week' ? 'w' : 'm'}=${state.key}#${state.page}`;
    (push ? history.pushState : history.replaceState).call(history, null, '', url);
  }
  function go(change) { Object.assign(state, change); writeUrl(true); render(); window.scrollTo(0, 0); }
  window.addEventListener('popstate', () => { readUrl(); render(); });

  // ── 기간 ──
  const periodWeeks = (mode, k) => mode === 'week' ? [WEEK[k]] : WEEKS.filter(w => w.month === k);
  const periodKeys = mode => mode === 'week' ? WEEKS.map(w => w.week) : MONTHS;
  function valid(ws) {
    const v = { rule: { pass: 0, warn: 0, fail: 0 }, llm: { pass: 0, warn: 0, fail: 0 }, model: null };
    ws.forEach(w => ['rule', 'llm'].forEach(k => { const x = w.validation[k]; if (!x) return;
      ['pass', 'warn', 'fail'].forEach(t => v[k][t] += x[t] || 0); if (k === 'llm') v.model = x.model; }));
    const tot = o => o.pass + o.warn + o.fail;
    const n = tot(v.rule) + tot(v.llm);
    return { ...v, rate: n ? (v.rule.pass + v.llm.pass) / n : null, fails: v.rule.fail + v.llm.fail,
             ruleRate: tot(v.rule) ? v.rule.pass / tot(v.rule) : null, llmRate: tot(v.llm) ? v.llm.pass / tot(v.llm) : null };
  }
  // 기간 안 트렌드(월간은 키워드별 마지막 등장 주차 값)
  function periodTrends(ws) {
    const m = {};
    ws.forEach(w => w.trends.forEach(t => { const prev = m[t.kw];
      m[t.kw] = { t, w, tr: trustOf(w, t), weeks: [...(prev ? prev.weeks : []), w.week], zmax: Math.max(prev ? prev.zmax : -Infinity, t.z) }; }));
    return Object.values(m).sort((a, b) => b.t.z - a.t.z);
  }

  // ── 공통 조각 ──
  const card = (title, hint, body, extra = '') => `<section class="card" ${extra}><h3>${title}</h3>${hint ? `<div class="hint">${hint}</div>` : ''}${body}</section>`;
  const empty = msg => `<div class="empty">${msg}</div>`;
  const tag = (label, cls = '', tip = '', attrs = '') => `<span class="tag ${cls}" ${tip ? `title="${esc(tip)}"` : ''} ${attrs}>${label}</span>`;
  const trustTag = tr => tag(TRUST[tr.state].label, 'trust ' + TRUST[tr.state].cls, tr.reasons.join('\n') || '경고 없음');
  const shapeTag = t => { const s = shapeView(t); return tag(`${s.icon} ${s.label}`, 'shape'); };
  const reviewTag = (fs, label) => fs.length ? tag(`${label || '검토 필요'} ${fs.length}`, 'review', failTip(fs), 'data-track="review_badge_hover"') : '';
  const snapNote = (mode, ws) => mode === 'month' ? `<div class="hint">월간 보기: 이 화면은 월의 마지막 데이터 주차(${ws[ws.length - 1].week}) 기준입니다.</div>` : '';

  function shareBar(m) {
    if (!m.total) return '<div class="sharebar empty-bar">K-브랜드 언급 없음</div>';
    const top = m.rows.slice(0, 3), rest = m.total - sum(top.map(r => r[1]));
    const seg = (label, n, i) => `<span class="seg-${i}" style="flex:${n}" title="${esc(label)} ${n}건 (${pct(n / m.total)})">${esc(label)} ${n}</span>`;
    return `<div class="sharebar">${top.map((r, i) => seg(brandName(r[0]), r[1], i)).join('')}${rest > 0 ? seg('기타', rest, 3) : ''}</div>`;
  }

  // ── 페이지: 개요 ──
  function pageOverview(mode, k) {
    const ws = periodWeeks(mode, k);
    const pt = periodTrends(ws);
    const ok = pt.filter(x => x.tr.state === 'ok').slice(0, 3);
    const watch = pt.filter(x => x.tr.state !== 'ok');
    const surge = pt.filter(x => strength(x.t) === '급등').length;
    const v = valid(ws);
    const last = ws[ws.length - 1];
    const rising = mode === 'week' ? last.rising_brands : Object.values(ws.flatMap(w => w.rising_brands).reduce((m, b) => {
      const x = m[b.kw] || (m[b.kw] = { ...b, n: 0 }); x.n++; x.z = Math.max(x.z, b.z); x.posts = Math.max(x.posts, b.posts); x.ok = x.ok || b.ok; return m; }, {}));
    const nextOk = weekAdd(last.week, RULES.MIN_CONSEC_WEEKS.v - 1);
    const kwChip = x => `<a class="kwchip" href="?${mode === 'week' ? 'w' : 'm'}=${k}#trends" data-kw="${esc(x.t.kw)}" title="z ${zf(x.t.z)} · ${TRUST[x.tr.state].label}">${esc(kwName(x.t.kw))}</a>`;
    const line = x => { const s = shapeView(x.t), o = oppView(x.t, x.tr);
      return `<li><span class="kw">${esc(kwName(x.t.kw))}</span> <span class="muted">${s.icon} ${s.label} · ${esc(o.label)} · z ${zf(x.t.z)}</span>
        <div class="need">${esc(firstSentence(x.t.need))}</div></li>`; };
    const html = `
      <div class="grid-main tight">
        ${card(mode === 'week' ? '이번 주 판단' : '이번 달 판단', `'판단 가능' 트렌드 중 z-score 상위 3개. 판단 가능 = 표본·이력·급등 경고 없음 + K-브랜드 언급 ${RULES.MIN_MENTIONS.v}건 이상 (방법론 › 화면 표시 규칙)`,
          ok.length ? `<ol class="judge">${ok.map(line).join('')}</ol>`
            : `<div class="empty left">판단 가능한 트렌드가 없습니다. 모든 트렌드에 경고가 있어 아래 '관찰 중'에 모았습니다.<br>
               <span class="muted">${last.week === WEEKS[WEEKS.length - 1].week
                 ? `수집 주차가 띄엄띄엄이라(연속 ${RULES.MIN_CONSEC_WEEKS.v}주 미만) 이력 경고가 붙습니다. 매주 자동 수집이 이어지면 ${nextOk}부터 판단 가능한 트렌드가 나올 수 있습니다.`
                 : `이 기간은 앞뒤 수집이 비어 있어(연속 ${RULES.MIN_CONSEC_WEEKS.v}주 미만) 이력 경고가 붙었습니다.`}</span></div>`)}
        ${card(`관찰 중 ${watch.length}`, '경고가 붙은 트렌드. 칩에 마우스를 올리면 사유가 보입니다.',
          watch.length ? watch.map(x => `<a class="chip watch-chip ${x.tr.state}" href="?${mode === 'week' ? 'w' : 'm'}=${k}#trends" data-kw="${esc(x.t.kw)}" title="${esc(x.tr.reasons.join('\n'))}">
            ${esc(kwName(x.t.kw))} <small>${TRUST[x.tr.state].label}</small></a>`).join('') : empty('없음'))}
      </div>
      <div class="kpis">
        <div class="kpi"><div class="label">급등 <span class="muted small">z ≥ ${RULES.SURGE_Z.v}</span></div><div class="val">${surge}</div>
          <div class="kwchips">${pt.filter(x => strength(x.t) === '급등').map(kwChip).join('') || '<span class="muted small">없음</span>'}</div></div>
        <div class="kpi"><div class="label">완만한 상승 <span class="muted small">z < ${RULES.SURGE_Z.v}</span></div><div class="val">${pt.length - surge}</div>
          <div class="kwchips">${pt.filter(x => strength(x.t) !== '급등').map(kwChip).join('') || '<span class="muted small">없음</span>'}</div></div>
        <div class="kpi"><div class="label">관찰 중 / 판단 보류</div><div class="val">${watch.filter(x => x.tr.state === 'watch').length} / ${watch.filter(x => x.tr.state === 'hold').length}</div><div class="delta">경고 있음 / 언급 ${RULES.MIN_MENTIONS.v}건 미만</div></div>
        <div class="kpi"><div class="label">검토 필요</div><div class="val review-num">${v.fails}</div><div class="delta">LLM 출력 자동 검증 실패</div></div>
      </div>
      <div class="grid-main tight">
        ${card('급상승 브랜드', `트렌드와 같은 기준: z ≥ ${RULES.SURGE_Z.v}, 게시물 ≥ 5 (테두리 칩 = 게시물 5건 미만, 참고)`,
          rising.length ? rising.map(b => `<span class="chip ${b.ok ? 'solid' : 'out'}"><b>${esc(String(b.kw).toUpperCase())}</b> z ${zf(b.z)} · ${b.posts}건${b.n > 1 ? ` · ${b.n}주` : ''}</span>`).join('') : empty('해당 없음'))}
        <section class="card review-link"><h3>검토 필요 ${v.fails}건</h3>
          <div class="hint">근거와 맞지 않아 자동 검증에서 실패한 LLM 출력. 각 트렌드 카드에 배지로 표시됩니다.</div>
          <a href="?${mode === 'week' ? 'w' : 'm'}=${k}#quality" data-page-link="quality">검증·품질에서 보기 →</a></section>
      </div>
      ${card('주차별 SNS 수집량', '막대 = 게시물 수, 선 = engagement 합계. 선택한 기간의 막대만 강조합니다.', '<div style="position:relative;height:220px"><canvas id="tlChart"></canvas></div>')}`;
    return [html, () => {
      document.querySelectorAll('.watch-chip, .kwchip').forEach(a => a.onclick = e => { e.preventDefault(); openKw = a.dataset.kw; go({ page: 'trends' }); });
      document.querySelectorAll('[data-page-link]').forEach(a => a.onclick = e => { e.preventDefault(); go({ page: a.dataset.pageLink }); });
      const tl = D.timeline, sel = new Set(ws.map(w => w.week));
      chart(document.getElementById('tlChart'), {
        data: { labels: tl.map(t => t.week.slice(5)), datasets: [
          { type: 'bar', label: '게시물', data: tl.map(t => t.posts), backgroundColor: tl.map(t => sel.has(t.week) ? ACCENT : LIGHT), yAxisID: 'y', borderRadius: 4 },
          { type: 'line', label: 'engagement', data: tl.map(t => t.eng), borderColor: DARK, borderWidth: 2, pointRadius: 2, tension: .3, yAxisID: 'y2' }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } },
          tooltip: { callbacks: { label: c => c.dataset.label === 'engagement' ? `engagement ${engf(c.raw)}` : `게시물 ${fmt(c.raw)}` } } },
          scales: { y: { beginAtZero: true }, y2: { position: 'right', beginAtZero: true, grid: { display: false }, ticks: { callback: v => engf(v) } } } },
      });
    }];
  }

  // ── 페이지: 트렌드 ──
  let oppFilter = 'ALL', openKw = null;
  function trendCard(w, t, i, dupActions) {
    const tr = trustOf(w, t), o = oppView(t, tr), fs = failsOf(w, t.kw);
    const byKind = k => fs.filter(f => failKind(f) === k);
    const spons = t.evidence.filter(isSponsor).length;
    const sh = shareOf(w, t), top = sh.rows[0];
    const reason = `선행 ${t.lead || '—'} · ${t.cross ? 'TikTok·YouTube 동시' : '단일 플랫폼'} · 1위 브랜드 점유 ${top ? pct(top[1] / sh.total) : '—'} · 협찬 근거 ${spons}/${t.evidence.length}`;
    const dup = dupActions.has(t.kw);
    const brandFails = byKind('brand');
    const chipFail = name => brandFails.filter(f => key(f.target) === key(name));
    const open = openKw === t.kw;
    return `<section class="card tcard ${open ? 'open' : ''}" id="t_${i}" data-kw="${esc(t.kw)}">
      <div class="tc-main">
        <div class="tc-left">
          <div class="tc-row1"><span class="name">${esc(kwName(t.kw))}</span> ${shapeTag(t)} ${tr.state === 'hold' ? '' : tag(esc(o.label), 'opp ' + o.o)} ${trustTag(tr)} ${reviewTag(fs)}</div>
          <div class="tc-need"><b>${esc(firstSentence(t.need))}</b>${spons && spons === t.evidence.length ? ' ' + tag('오가닉 근거 없음', 'warnline', '인용한 근거가 모두 협찬·광고 게시물입니다') : ''}${reviewTag(byKind('need'), '검토')}</div>
          <div class="tc-share">${shareBar(sh)} <span class="muted small">${shareNote(sh)}</span>
            ${sh.total ? (sh.small ? tag('표본 부족(참고용)', 'trust hold', `언급 ${sh.total}건 < ${RULES.SHARE_TARGET_N.v}`) : sh.sig ? tag('유의한 선점', 'trust ok', `1위 95% 하한 ${pct(sh.ci[0])} > ${pct(RULES.LEADER_CI_LOWER.v)}`) : '') : ''}
            ${brandFails.length ? reviewTag(brandFails, '브랜드 검토') : ''}</div>
          <div class="tc-reason muted small">${dup ? esc(reason) : '제안 · ' + esc(t.action)}</div>
        </div>
        <div class="tc-spark"><canvas id="sp_${i}"></canvas></div>
      </div>
      <button class="tc-toggle" data-track="trend_card_expand" data-kw="${esc(t.kw)}">${open ? '접기 ▲' : '자세히 ▼'}</button>
      <div class="tc-more">
        <div class="kv"><span>z ${zf(t.z)} (게시물 비중)</span>${t.eng_z !== null && t.eng_z !== undefined ? `<span>engagement 비중 z ${zf(t.eng_z)}</span>` : ''}${t.top_post_share !== null && t.top_post_share !== undefined ? `<span>1위 게시물 조회수 비중 ${pct(t.top_post_share)}</span>` : ''}<span>모멘텀 ${momf(t.mom)}${tr.base ? ` (비교 기준 ${tr.base.slice(5)})` : ''}</span><span>게시물 ${fmt(t.posts)}</span><span>engagement ${engf(t.eng)}</span><span>선행 ${esc(t.lead || '—')}</span></div>
        <p><b>대화 점유</b> · ${esc(t.summary)} <span class="muted small">(파이프라인 판정: LLM 샘플 언급 ${tr.mentions.total}건 기준)</span></p>
        <p><b>점유 집계 비교</b> · 결정적 집계(캡션 ${t.det ? t.det.captions : '—'}건${t.det && t.det.deep ? `, 트렌드 심층 수집 ${t.det.deep}건 포함` : ''}): ${t.det && t.det.total ? t.det.brands.slice(0, 4).map(b => `${esc(brandName(b[0]))} ${b[1]}`).join(', ') : '없음'}
          · LLM 샘플: ${tr.mentions.rows.slice(0, 4).map(b => `${esc(brandName(b[0]))} ${b[1]}`).join(', ') || '없음'}</p>
        <p><b>이 니즈에 맞는 제품 보유</b> ${t.brands.map(b => `<span class="chip">${esc(String(b).toUpperCase())}</span>`).join('') || '<span class="muted">없음</span>'}</p>
        <p><b>캡션 언급</b> ${t.mentions.map(m => { const f = chipFail(m); return `<span class="chip out ${f.length ? 'chip-fail' : ''}" ${f.length ? `title="${esc(failTip(f))}" data-track="review_badge_hover"` : ''}>${esc(String(m).toUpperCase())}</span>`; }).join('')}</p>
        <details data-track="evidence_open" data-kw="${esc(t.kw)}" open><summary><b>SNS 근거</b> ${spons ? `<span class="muted small">협찬 ${spons}/${t.evidence.length}</span>` : ''} ${reviewTag(byKind('evidence'), '근거 검토')}</summary>
          ${t.evidence.map(e => `<blockquote class="${isSponsor(e) ? 'sponsor' : ''}">${isSponsor(e) ? tag('협찬', 'spons') + ' ' : ''}“${esc(e)}”</blockquote>`).join('') || '<span class="muted">없음</span>'}</details>
        <div class="act"><b>제안</b> · ${esc(t.action)}</div>
      </div></section>`;
  }
  function pageTrends(mode, k) {
    const ws = periodWeeks(mode, k);
    if (mode === 'month') {
      const pt = periodTrends(ws).sort((a, b) => b.weeks.length - a.weeks.length || b.zmax - a.zmax);
      return [card('월간 트렌드', `${monthLabel(k)}의 데이터 주차(${ws.map(w => w.week.slice(5)).join(', ')})에 선정된 트렌드. 여러 주에 등장할수록 위에 둡니다. 배지는 마지막 등장 주차 기준.`,
        pt.length ? `<div class="tbl-wrap"><table><thead><tr><th>키워드</th>${ws.map(w => `<th class="c">${w.week.slice(5)}</th>`).join('')}
          <th class="num">최고 z</th><th>모양</th><th>대화 점유</th><th>신뢰</th></tr></thead><tbody>
          ${pt.map(x => `<tr><td><b>${esc(kwName(x.t.kw))}</b></td>${ws.map(w => `<td class="c">${x.weeks.includes(w.week) ? '●' : ''}</td>`).join('')}
            <td class="num">${zf(x.zmax)}</td><td>${shapeTag(x.t)}</td><td>${tag(esc(oppView(x.t, x.tr).label), 'opp')}</td><td>${trustTag(x.tr)}</td></tr>`).join('')}
          </tbody></table></div>` : empty('트렌드 없음')), null];
    }
    const w = ws[0];
    const items = w.trends.map((t, i) => ({ t, i, tr: trustOf(w, t) }));
    const oppKey = x => x.tr.state === 'hold' ? 'hold' : x.t.opp;
    const counts = {}; items.forEach(x => counts[oppKey(x)] = (counts[oppKey(x)] || 0) + 1);
    const types = w.legacy ? Object.keys(counts) : ['leader', 'contested', 'white_space', 'hold'];
    const desc = { leader: '한 브랜드의 대화 과반 점유. 1위 브랜드 메시지·제품 기준의 차별 포인트 탐색',
                   contested: '대표 브랜드 없이 여러 브랜드가 분점. 이 니즈의 대표 제품 선점 기회',
                   white_space: '관심은 있으나 K-브랜드 언급 없음. 맞는 제품은 홍보, 없으면 제품 기획 검토',
                   hold: `브랜드 언급 ${RULES.MIN_MENTIONS.v}건 미만으로 점유 판단 불가. 다음 주 수집 후 재확인` };
    const label = o => o === 'hold' ? '판단 보류' : (META.opp[o] || {}).label || o;
    const norm = t => String(t.action).split(t.kw).join('{kw}');
    const freq = {}; w.trends.forEach(t => freq[norm(t)] = (freq[norm(t)] || 0) + 1);
    const dup = new Set(w.trends.filter(t => freq[norm(t)] >= 2).map(t => t.kw));
    const shown = items.filter(x => oppFilter === 'ALL' || oppKey(x) === oppFilter);
    const html = `
      <div class="grid4" style="margin-bottom:16px">${types.map(o => `<div class="stat ${oppFilter === o ? 'on' : ''}" data-opp="${o}" data-track="opp_filter">
        <div class="n">${counts[o] || 0}</div><div class="t">${esc(label(o))}</div><div class="d">${desc[o] || '이전 기준(AP 관점) 유형'}</div></div>`).join('')}</div>
      ${card('트렌드 레이더', `가로 = 모멘텀(최근 3개 데이터 주차 성장 배수, 로그, ${RULES.RADAR_X_MAX.v}배 상한) · 세로 = z-score(${RULES.RADAR_Y_MAX.v} 상한) · 크기 = 게시물 수 · 속 빈 원 = 관찰 중/판단 보류. 구분선: 모멘텀 ${RULES.SUSTAIN_MOMENTUM.v}배, z ${RULES.SURGE_Z.v}`,
        (oppFilter !== 'ALL' ? `<div class="legend-line">${esc(label(oppFilter))} 트렌드만 표시 중 · 위 카드를 다시 누르면 전체</div>` : '') +
        (shown.length ? `<div style="position:relative;height:340px"><canvas id="radar"></canvas></div><ol class="radar-legend">${shown.map(x =>
          `<li data-i="${x.i}"><b>${x.i + 1}</b> ${esc(kwName(x.t.kw))} <span class="muted">z ${zf(x.t.z)} · ${momf(x.t.mom)}</span></li>`).join('')}</ol>` : empty(items.length ? '이 유형에 해당하는 트렌드 없음' : '트렌드 없음')))}
      ${shown.map(x => trendCard(w, x.t, x.i, dup)).join('')}`;
    return [html, () => {
      document.querySelectorAll('.stat[data-opp]').forEach(el => el.onclick = () => { oppFilter = oppFilter === el.dataset.opp ? 'ALL' : el.dataset.opp; render(); });
      document.querySelectorAll('.tc-toggle').forEach(b => b.onclick = () => { const c = b.closest('.tcard'); c.classList.toggle('open'); b.textContent = c.classList.contains('open') ? '접기 ▲' : '자세히 ▼'; });
      const focus = i => { const el = document.getElementById('t_' + i); if (!el) return; el.classList.add('open', 'flash');
        el.querySelector('.tc-toggle').textContent = '접기 ▲'; el.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTimeout(() => el.classList.remove('flash'), 1500); };
      document.querySelectorAll('.radar-legend li').forEach(li => li.onclick = () => focus(li.dataset.i));
      if (openKw) { const x = items.find(y => y.t.kw === openKw); if (x) setTimeout(() => focus(x.i), 50); openKw = null; }
      // 스파크라인: x = 실제 주차 간격, 빈 주차 구간은 점선
      shown.forEach(x => {
        const pts = x.t.hist.map(h => ({ x: weekOrd(h[0]), y: h[1], wk: h[0] }));
        chart(document.getElementById('sp_' + x.i), { type: 'line',
          data: { datasets: [{ data: pts, borderColor: DARK, borderWidth: 1.5, pointRadius: 2, pointBackgroundColor: DARK, tension: 0,
            segment: { borderDash: c => (c.p1.parsed.x - c.p0.parsed.x > 1) ? [3, 3] : undefined, borderColor: c => (c.p1.parsed.x - c.p0.parsed.x > 1) ? LIGHT : DARK } }] },
          options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false },
            tooltip: { callbacks: { title: c => c[0].raw.wk, label: c => `engagement ${engf(c.raw.y)}` } } },
            scales: { x: { type: 'linear', ticks: { font: { size: 9 }, callback: v => { const p = pts.find(q => q.x === v); return p ? p.wk.slice(5) : ''; }, autoSkip: false },
                           afterBuildTicks: s => { s.ticks = pts.length ? [{ value: pts[0].x }, { value: pts[pts.length - 1].x }] : []; }, grid: { display: false } },
                      y: { display: false, beginAtZero: true } } } });
      });
      // 레이더
      // 세로축은 이번 주 z 범위에 맞추되, 급등 기준선(z 2)은 항상 보이게
      if (!shown.length) return;
      const zs = shown.map(x => Math.min(x.t.z, RULES.RADAR_Y_MAX.v));
      const yMin = Math.min(-1, Math.floor(Math.min(...zs)) - 0.5), yMax = Math.max(RULES.SURGE_Z.v + 1, Math.min(RULES.RADAR_Y_MAX.v + 1, Math.ceil(Math.max(...zs)) + 1));
      const pts = shown.map(x => ({ x: Math.min(Math.max(x.t.mom, 0.5), RULES.RADAR_X_MAX.v), y: Math.max(Math.min(x.t.z, RULES.RADAR_Y_MAX.v), yMin),
                                    r: 6 + Math.sqrt(Math.min(x.t.posts, 60)) * 1.1, x0: x }));
      chart(document.getElementById('radar'), { type: 'bubble',
        data: { datasets: [{ data: pts, backgroundColor: pts.map(p => p.x0.tr.state === 'ok' ? DARK + 'cc' : 'rgba(0,0,0,0)'),
          borderColor: DARK, borderWidth: 1.5, clip: false }] },
        options: { responsive: true, maintainAspectRatio: false, layout: { padding: { right: 28, top: 14 } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => { const x = c.raw.x0;
            return `${x.i + 1}. ${kwName(x.t.kw)} · ${shapeView(x.t).label} · z ${zf(x.t.z)} · 모멘텀 ${momf(x.t.mom)} · 게시물 ${x.t.posts} · ${TRUST[x.tr.state].label}`; } } } },
          onClick: (e, els) => { if (els.length) focus(pts[els[0].index].x0.i); },
          scales: { x: { type: 'logarithmic', min: 0.5, max: RULES.RADAR_X_MAX.v, title: { display: true, text: '모멘텀 (배, 로그)' } },
                    y: { min: yMin, max: yMax, title: { display: true, text: 'z-score (게시물 비중)' } } } },
        plugins: [{ id: 'quad', beforeDatasetsDraw(c) { const { ctx, chartArea: a, scales: s } = c;
            const xv = s.x.getPixelForValue(RULES.SUSTAIN_MOMENTUM.v), yv = s.y.getPixelForValue(RULES.SURGE_Z.v);
            ctx.save(); ctx.strokeStyle = '#dee2e6'; ctx.setLineDash([4, 4]); ctx.beginPath();
            ctx.moveTo(xv, a.top); ctx.lineTo(xv, a.bottom); ctx.moveTo(a.left, yv); ctx.lineTo(a.right, yv); ctx.stroke();
            ctx.setLineDash([]); ctx.fillStyle = '#adb5bd'; ctx.font = '12px Pretendard'; ctx.textAlign = 'right';
            ctx.fillText('강하게 뜨는 중', a.right - 40, yv - 8); ctx.fillText('꾸준히 오르는 중', a.right - 40, a.bottom - 8);
            ctx.textAlign = 'left'; ctx.fillText('한 주 반짝', a.left + 6, yv - 8); ctx.restore(); },
          afterDatasetsDraw(c) { const ctx = c.ctx; ctx.save(); ctx.font = '600 11px Pretendard'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            c.getDatasetMeta(0).data.forEach((el, i) => { const p = pts[i];
              ctx.fillStyle = p.x0.tr.state === 'ok' ? '#fff' : DARK; ctx.fillText(String(p.x0.i + 1), el.x, el.y);
              const clip = []; if (p.x0.t.mom > RULES.RADAR_X_MAX.v) clip.push(`▶ ${momf(p.x0.t.mom)}`); if (p.x0.t.z > RULES.RADAR_Y_MAX.v) clip.push(`▲ z ${zf(p.x0.t.z)}`);
              if (clip.length) { ctx.fillStyle = DARK; ctx.textAlign = 'right'; ctx.fillText(clip.join(' · '), el.x - p.r - 4, el.y); ctx.textAlign = 'center'; } });
            ctx.restore(); } }] });
    }];
  }

  // ── 페이지: 브랜드 ──
  let brandSrc = 'det';
  function pageBrands(mode, k) {
    const ws = periodWeeks(mode, k);
    const m = {};
    ws.forEach(w => {
      if (brandSrc === 'det') w.trends.forEach(t => (t.det ? t.det.brands : []).forEach(([b, n]) => { (m[b] = m[b] || {})[t.kw] = (m[b][t.kw] || 0) + n; }));
      else Object.entries(w.matrix).forEach(([b, r]) => Object.entries(r).forEach(([kw, n]) => { (m[b] = m[b] || {})[kw] = (m[b][kw] || 0) + n; }));
    });
    const kwTot = {}; Object.values(m).forEach(r => Object.entries(r).forEach(([kw, n]) => kwTot[kw] = (kwTot[kw] || 0) + n));
    const kws = Object.keys(kwTot).sort((a, b) => kwTot[b] - kwTot[a]).slice(0, 10);
    const totals = Object.entries(m).map(([b, r]) => [b, sum(Object.values(r))]).sort((a, b) => b[1] - a[1]);
    const all = sum(totals.map(t => t[1])) || 1, top = totals.slice(0, 12);
    const vmax = Math.max(1, ...Object.values(m).flatMap(r => Object.values(r)));
    const last = ws[ws.length - 1], rk = last.retail_brands;
    const byCo = {}; rk.forEach(([b, n]) => { const co = D.brand_company[b]; if (!co) return; (byCo[co] = byCo[co] || [0, []]); byCo[co][0] += n; byCo[co][1].push(brandName(b)); });
    const co = Object.entries(byCo).sort((a, b) => b[1][0] - a[1][0]).slice(0, 8);
    const heat = top.length ? `<div class="tbl-wrap"><table><thead><tr><th>브랜드</th>${kws.map(x => `<th class="c">${esc(kwName(x))}</th>`).join('')}<th class="num">합계</th><th class="num">점유</th></tr></thead><tbody>
      ${top.map(([b, t]) => `<tr><td><b>${esc(brandName(b))}</b></td>${kws.map(x => { const c = m[b][x] || 0; const a = c ? .12 + .7 * c / vmax : 0;
        return `<td class="c" style="background:rgba(73,80,87,${a.toFixed(2)});color:${a > .5 ? '#fff' : '#333'}">${c || ''}</td>`; }).join('')}
        <td class="num">${t}</td><td class="num">${pct(t / all)}</td></tr>`).join('')}</tbody></table></div>` : empty('언급된 K-뷰티 브랜드 없음');
    const srcSeg = `<div class="seg small-seg" style="margin-bottom:12px">${[['det', '결정적 집계 (캡션 전체)'], ['llm', 'LLM 샘플']].map(([v, l]) =>
      `<button data-src="${v}" class="${brandSrc === v ? 'on' : ''}" data-track="brand_source">${l}</button>`).join('')}</div>`;
    const srcHint = brandSrc === 'det'
      ? `키워드가 들어간 캡션 전체에서 등록 브랜드 이름을 직접 셈(캡션당 1회, 오탐 단어 제외). 총 언급 ${sum(totals.map(t => t[1]))}건. 트렌드별 ${RULES.SHARE_TARGET_N.v}건 미만이면 점유는 참고용입니다.`
      : `LLM이 캡션 샘플(최대 30건)에서 센 브랜드. 비교·검증용으로만 보여 주며, 기회 유형 판정에는 결정적 집계를 씁니다(LLM이 캡션에 없는 브랜드를 세는 경우가 있어서).`;
    const html = `
      ${srcSeg}
      ${card('트렌드별 브랜드 언급', `${srcHint}${mode === 'month' ? ' 월 합계, 언급 많은 트렌드 10개.' : ''}`, heat)}
      <div class="grid2">
        ${card('트렌드 대화 점유', '전체 트렌드 언급 중 브랜드별 비중(%)', '<div style="position:relative;height:300px"><canvas id="share"></canvas></div>')}
        ${card('OY 랭킹 등장 제품 수', `브랜드 상위 12 · ${last.week} 기준`, '<div style="position:relative;height:300px"><canvas id="retail"></canvas></div>')}
      </div>
      ${card('회사별 OY 랭킹 등장 제품 수', `회사 정보가 등록된 브랜드 기준 · ${last.week}`,
        co.length ? `<div class="tbl-wrap"><table><thead><tr><th>회사</th><th class="num">랭킹 제품</th><th>브랜드</th></tr></thead><tbody>
          ${co.map(([c, [n, bs]]) => `<tr><td><b>${esc(c)}</b></td><td class="num">${n}</td><td class="muted">${esc(bs.slice(0, 6).join(', '))}${bs.length > 6 ? ' …' : ''}</td></tr>`).join('')}</tbody></table></div>` : empty('데이터 없음'))}`;
    return [html, () => {
      document.querySelectorAll('[data-src]').forEach(b => b.onclick = () => { brandSrc = b.dataset.src; render(); });
      const hbar = (id, labels, data, unit) => chart(document.getElementById(id), { type: 'bar',
        data: { labels, datasets: [{ data, backgroundColor: DARK, borderRadius: 4 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => c.raw + unit } } },
                   scales: { x: { beginAtZero: true, grid: { color: '#f1f3f5' } }, y: { grid: { display: false } } } } });
      hbar('share', top.slice(0, 10).map(t => brandName(t[0])), top.slice(0, 10).map(t => +(t[1] / all * 100).toFixed(1)), '%');
      hbar('retail', rk.slice(0, 12).map(t => brandName(t[0])), rk.slice(0, 12).map(t => t[1]), '개');
    }];
  }

  // ── 페이지: 제품 전략 ──
  const QUAD_ORDER = ['PUSH_NOW', 'FIX_AND_PUSH', 'MONITOR', 'HOLD'];
  let quadFilter = 'ALL', prodSearch = '', openGroups = new Set(['PUSH_NOW', 'FIX_AND_PUSH', 'MONITOR']);
  function pageProducts(mode, k) {
    const ws = periodWeeks(mode, k), w = ws[ws.length - 1], ps = w.products;
    if (!ps.length) return [snapNote(mode, ws) + empty('제품 인사이트 없음'), null];
    const counts = {}; ps.forEach(p => counts[p.quad] = (counts[p.quad] || 0) + 1);
    const qdesc = { PUSH_NOW: '리뷰 반응(또는 랭킹) 양호 + 별점 상승. 광고·프로모션 확대 적기',
                    FIX_AND_PUSH: '별점 하락 또는 불만 리뷰 증가. 불만 포인트 개선 후 확대',
                    HOLD: '반응은 좋으나 변화 없음. 현 수준 유지·관찰',
                    MONITOR: '긍정 비율 65% 미만, 하락 신호 없음. 관찰 대상' };
    const match = p => (quadFilter === 'ALL' || p.quad === quadFilter) && (!prodSearch || `${p.name} ${p.brand}`.toLowerCase().includes(prodSearch));
    const trend = v => v === null || v === undefined ? '<span class="muted">—</span>' : v > 0 ? `▲ ${v.toFixed(2)}` : v < 0 ? `▼ ${v.toFixed(2)}` : '±0';
    const groups = QUAD_ORDER.map(q => { const rows = ps.filter(p => p.quad === q && match(p));
      const acts = new Set(rows.map(p => p.action)); return { q, rows, same: rows.length > 1 && acts.size === 1 ? rows[0].action : null }; }).filter(g => g.rows.length);
    const body = groups.map(g => {
      const open = openGroups.has(g.q) || !!prodSearch;
      const head = `<tr class="grp"><td colspan="9"><button class="grp-btn" data-track="product_group_expand" data-group="${g.q}">${open ? '▼' : '▶'}</button>
        <span class="tag quad" ${g.same ? `title="${esc('전략 요약: ' + g.same)}"` : ''}>${esc((META.quad[g.q] || {}).label || g.q)}</span> <span class="muted">${g.rows.length}개${g.same ? ' · 전략 요약 동일 (라벨에 마우스)' : ''}</span></td></tr>`;
      return head + (open ? g.rows.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(String(p.brand).toUpperCase())}</td>
        <td class="num">${pct(p.pos)}</td><td class="num">${pct(p.neg)}</td><td class="num">${trend(p.rt)}</td><td class="num">${fmt(p.vel)}</td>
        <td class="num">${p.oy_orders ?? '—'}</td><td class="num">${p.oy_korea ?? '—'}</td><td class="muted small">${g.same ? '' : esc(p.action.slice(0, 90))}</td></tr>`).join('') : '');
    }).join('');
    const kwRows = ps.filter(p => match(p) && (p.pos_kw.length || p.neg_kw.length || p.needs_kw.length));
    const html = `${snapNote(mode, ws)}
      <div class="grid4" style="margin-bottom:16px">${QUAD_ORDER.map(q => `<div class="stat ${quadFilter === q ? 'on' : ''}" data-q="${q}">
        <div class="n">${counts[q] || 0}</div><div class="t">${esc((META.quad[q] || {}).label)}</div><div class="d">${qdesc[q]}</div></div>`).join('')}</div>
      ${card('전략 분포 (별점 추세 × VOC 긍정도)', '가로 = 플랫폼 평균 별점의 주간 변화 · 세로 = 리뷰 긍정 비율(0.65 기준선). 별점 추세가 없는 제품은 왼쪽 회색 띠에 따로 모았습니다.',
        '<div style="position:relative;height:320px"><canvas id="quad"></canvas></div>')}
      <section class="card"><div class="toolbar"><h3 style="margin:0 8px 0 0">제품</h3>
        <input id="psearch" placeholder="제품·브랜드 검색" value="${esc(prodSearch)}">${quadFilter !== 'ALL' ? `<span class="tag">${esc(META.quad[quadFilter].label)} 필터 · 카드를 다시 누르면 해제</span>` : ''}</div>
        <div class="tbl-wrap"><table><thead><tr><th>제품</th><th>브랜드</th><th class="num">VOC 긍정</th><th class="num">부정</th><th class="num">별점 추세</th>
          <th class="num">리뷰 유입</th><th class="num">OY 주문</th><th class="num">OY 한국</th><th>전략 요약</th></tr></thead><tbody>${body}</tbody></table></div></section>
      ${card('제품별 VOC 키워드', '리뷰 감성 분석의 긍정·부정·개선 니즈 키워드',
        kwRows.length ? `<div class="tbl-wrap"><table><thead><tr><th>제품</th><th>긍정</th><th>부정</th><th>개선 니즈</th><th>리테일</th></tr></thead><tbody>
          ${kwRows.map(p => `<tr><td><b>${esc(p.name)}</b><div class="muted small">${esc(String(p.brand).toUpperCase())}</div></td>
            <td>${p.pos_kw.map(x => `<span class="chip">${esc(x)}</span>`).join('')}</td><td>${p.neg_kw.map(x => `<span class="chip out">${esc(x)}</span>`).join('')}</td>
            <td>${p.needs_kw.map(x => `<span class="chip out">${esc(x)}</span>`).join('')}</td>
            <td>${p.oy_url ? `<a href="${esc(p.oy_url)}" target="_blank" rel="noopener">OY</a>` : ''} ${p.sephora_url ? `<a href="${esc(p.sephora_url)}" target="_blank" rel="noopener">Sephora</a>` : ''}</td></tr>`).join('')}
          </tbody></table></div>` : empty('키워드 없음'))}`;
    return [html, () => {
      document.querySelectorAll('.stat[data-q]').forEach(el => el.onclick = () => { quadFilter = quadFilter === el.dataset.q ? 'ALL' : el.dataset.q; render(); });
      document.querySelectorAll('.grp-btn').forEach(b => b.onclick = () => { const g = b.dataset.group; openGroups.has(g) ? openGroups.delete(g) : openGroups.add(g); render(); });
      const inp = document.getElementById('psearch');
      inp.oninput = () => { prodSearch = inp.value.toLowerCase().trim(); render(); const n = document.getElementById('psearch'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); };
      const xs = ps.filter(match).filter(p => p.rt !== null && p.rt !== undefined).map(p => p.rt);
      const lo = Math.min(0, ...xs), hi = Math.max(0, ...xs), span = Math.max(hi - lo, 0.02);
      const bandX = lo - span * 0.22, edge = lo - span * 0.1, xmin = lo - span * 0.34;
      const pts = ps.filter(match).map((p, i) => ({ x: p.rt === null || p.rt === undefined ? bandX + ((i % 7) - 3) * span * 0.012 : p.rt, y: p.pos ?? 0, p }));
      const deco = { id: 'deco', beforeDatasetsDraw(c) { const { ctx, chartArea: a, scales: s } = c;
        const xe = s.x.getPixelForValue(edge), x0 = s.x.getPixelForValue(0), y0 = s.y.getPixelForValue(0.65);
        ctx.save(); ctx.fillStyle = '#f1f3f5'; ctx.fillRect(a.left, a.top, xe - a.left, a.bottom - a.top);
        ctx.fillStyle = '#868e96'; ctx.font = '11px Pretendard'; ctx.fillText('추세 없음', a.left + 6, a.bottom - 8);
        ctx.strokeStyle = '#ced4da'; ctx.setLineDash([5, 4]); ctx.beginPath(); ctx.moveTo(x0, a.top); ctx.lineTo(x0, a.bottom);
        ctx.moveTo(xe, y0); ctx.lineTo(a.right, y0); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = '#adb5bd';
        ctx.fillText('PUSH NOW (별점↑ · 긍정)', x0 + 6, a.top + 14); ctx.fillText('FIX & PUSH (별점↓)', xe + 6, a.top + 14); ctx.restore(); } };
      const qc = { PUSH_NOW: ACCENT, FIX_AND_PUSH: DARK, HOLD: '#adb5bd', MONITOR: '#dee2e6' };
      chart(document.getElementById('quad'), { type: 'scatter',
        data: { datasets: [{ data: pts, pointRadius: 4.5, pointBackgroundColor: pts.map(q => q.p.pos === null ? '#e9ecef' : qc[q.p.quad]), pointBorderColor: '#fff' }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
          tooltip: { callbacks: { label: c => `${String(c.raw.p.brand).toUpperCase()} · ${c.raw.p.name} (별점 변화 ${c.raw.p.rt ?? '없음'}, VOC ${pct(c.raw.p.pos)})` } } },
          scales: { x: { min: xmin, title: { display: true, text: '별점 추세 (주간 변화)' }, ticks: { callback: v => v < edge ? '' : Number(v).toFixed(2) } },
                    y: { min: 0, max: 1, title: { display: true, text: 'VOC 긍정도' } } } }, plugins: [deco] });
    }];
  }

  // ── 페이지: 방한 추천 ──
  function pageInbound(mode, k) {
    const ws = periodWeeks(mode, k), rows = ws[ws.length - 1].inbound;
    if (!rows.length) return [snapNote(mode, ws) + empty('방한 추천 데이터 없음'), null];
    const bar = v => `<div class="minibar"><i style="width:${Math.round(v * 100)}%"></i></div>`;
    return [`${snapNote(mode, ws)}
      <div class="grid3" style="margin-bottom:16px">${rows.slice(0, 3).map(r => `<div class="kpi"><div class="label">#${r.rank} · ${esc(String(r.brand).toUpperCase())}</div>
        <div style="font-weight:700;margin:6px 0">${esc(r.name)}</div><div class="val">${r.score.toFixed(3)}</div>${bar(r.score)}</div>`).join('')}</div>
      ${card('방한 추천 Top 15', 'Pick Score = 0.35 × SNS 트렌드 강도 + 0.35 × VOC 긍정도 + 0.30 × 리테일 인기',
        `<div class="legend-line">OY 한국 · OY 주문: 숫자 = 랭킹 순위 · <b>0</b> = 랭킹 목록에는 있으나 순위가 표기되지 않음 · <b>—</b> = 해당 랭킹에 없음</div>
        <div class="tbl-wrap"><table><thead><tr><th>순위</th><th>제품</th><th>브랜드</th><th class="num">OY 한국</th><th class="num">OY 주문</th><th class="num">VOC</th><th class="c">SNS 연결</th><th class="num">Score</th><th>이유</th></tr></thead><tbody>
        ${rows.map(r => `<tr><td><b>#${r.rank}</b></td><td><b>${esc(r.name)}</b><div class="muted small">${esc(r.kw.join(' · '))}</div></td><td>${esc(String(r.brand).toUpperCase())}</td>
          <td class="num">${r.korea ?? '—'}</td><td class="num">${r.orders ?? '—'}</td><td class="num">${pct(r.voc)}</td><td class="c">${r.sns ? '●' : ''}</td>
          <td class="num">${r.score.toFixed(3)}${bar(r.score)}</td><td class="muted small" style="max-width:280px">${esc(r.reason.slice(0, 110))}</td></tr>`).join('')}
        </tbody></table></div>`)}`, null];
  }

  // ── 페이지: 검증·품질 ──
  function pageQuality(mode, k) {
    const ws = periodWeeks(mode, k), v = valid(ws), last = ws[ws.length - 1];
    const plat = {}; ws.forEach(w => Object.entries(w.platforms).forEach(([x, n]) => plat[x] = (plat[x] || 0) + n));
    const ret = {}; ws.forEach(w => Object.entries(w.retail_platforms).forEach(([x, n]) => ret[x] = (ret[x] || 0) + n));
    const pname = { tiktok: 'TikTok 게시물', youtube: 'YouTube 영상', oy_top_orders: 'OY Global 주문 랭킹', oy_top_korea: 'OY 한국 인기 랭킹', sephora_us: 'Sephora US' };
    const vw = WEEKS.filter(w => w.validation.rule || w.validation.llm);
    const fails = ws.flatMap(w => w.fails.map(f => ({ ...f, week: w.week })));
    // 규칙과 LLM이 같은 오류(같은 주·키워드·유형·대상)를 둘 다 잡으면 한 줄로 묶는다
    const grouped = []; const gidx = {};
    fails.forEach(f => { const k = failKey(f), key = [f.week, f.keyword, k, String(f.target).toLowerCase().replace(/[\s_]+/g, '')].join('|');
      if (gidx[key] !== undefined) { const g = grouped[gidx[key]]; g.by.add(f.checker === 'rule' ? '규칙' : 'LLM'); g.raw.push(f); if (f.checker === 'rule') g.f = f; return; }
      gidx[key] = grouped.length; grouped.push({ f, k, by: new Set([f.checker === 'rule' ? '규칙' : 'LLM']), raw: [f] }); });
    const byKind = {}; grouped.forEach(g => byKind[g.k] = (byKind[g.k] || 0) + 1);
    const KIND_HELP = { absent_brand: 'LLM이 캡션에 없는 브랜드를 언급 브랜드로 셈(카탈로그 등 입력의 다른 정보를 끌어옴)',
                        llm_brand: '판매처(Sephora·Amazon)·매체(Vogue)·가수(ATEEZ)를 브랜드로 셈', brand_count: '브랜드는 맞지만 언급 횟수를 부풀림',
                        direct_quote: '근거 인용문이 캡션 원문과 다름', llm_product: '다른 브랜드 제품 캡션을 근거로 연결' };
    return [`
      <div class="kpis">
        <div class="kpi"><div class="label">규칙 검사 통과율</div><div class="val">${pct(v.ruleRate)}</div><div class="delta">인용·브랜드 수·한국어 (비용 0)</div></div>
        <div class="kpi"><div class="label">LLM 판정 통과율</div><div class="val">${pct(v.llmRate)}</div><div class="delta">${esc(v.model || '판정 기록 없음')}</div></div>
        <div class="kpi"><div class="label">검토 필요</div><div class="val review-num">${v.fails}</div><div class="delta">규칙 ${v.rule.fail} · LLM ${v.llm.fail}</div></div>
        <div class="kpi"><div class="label">경고</div><div class="val">${v.rule.warn + v.llm.warn}</div><div class="delta">근거 부족·미등록 브랜드</div></div>
      </div>
      <div class="kpis">
        <div class="kpi"><div class="label">분석한 SNS 게시물</div><div class="val">${fmt(sum(Object.values(plat)))}</div><div class="delta">TikTok ${fmt(plat.tiktok || 0)} · YouTube ${fmt(plat.youtube || 0)}</div></div>
        <div class="kpi"><div class="label">랭킹 제품</div><div class="val">${fmt(last.kpi.ranked)}</div><div class="delta">브랜드 ${fmt(last.kpi.ranked_brands)}개 · ${last.week}</div></div>
        <div class="kpi"><div class="label">트렌드 후보 → 선정</div><div class="val">${sum(ws.map(w => w.trends.length))}</div><div class="delta">z 급등 ∪ 지속 상승, 최대 10개/주</div></div>
        <div class="kpi"><div class="label">제품 인사이트</div><div class="val">${fmt(last.products.length)}</div><div class="delta">${last.week} 기준</div></div>
      </div>
      <div class="grid2">
        ${card('주차별 검증 통과율', '검증 단계가 들어간 주차부터 표시', vw.length ? '<div style="position:relative;height:240px"><canvas id="vchart"></canvas></div>' : empty('검증 기록 없음'))}
        ${card('수집량', '선택한 기간에 수집한 건수', `<table><tbody>${Object.entries(plat).concat(Object.entries(ret)).map(([x, n]) => `<tr><td>${pname[x] || esc(x)}</td><td class="num">${fmt(n)}</td></tr>`).join('')}</tbody></table>`)}
      </div>
      ${card('검증 단계', '', `<ol class="method" style="padding-left:18px">
        <li><b>데이터 품질</b> — 플랫폼별 수집 건수와 빈 본문 비율. 기준에 못 미치면 LLM 단계를 돌리지 않습니다.</li>
        <li><b>규칙 검사</b> — LLM이 인용한 문장이 실제 캡션에 있는지, 언급 브랜드 수가 맞는지, 니즈가 한국어인지 대조합니다.</li>
        <li><b>LLM 판정</b> — 생성 모델(Sonnet)과 다른 모델(Opus)이 같은 캡션을 보고 주장마다 지지 / 근거 부족 / 모순으로 판정합니다.</li></ol>`)}
      ${card(`검토 필요 ${fails.length}건 · 서로 다른 오류 ${grouped.length}개`, '인사이트를 쓴 모델(Sonnet)의 출력을 자동 검증해 걸린 항목입니다. <b>규칙</b> = 코드가 캡션 원문과 문자열로 대조 · <b>LLM</b> = 다른 모델(Opus)이 같은 캡션을 읽고 판정. 둘 다 잡은 오류는 한 줄로 묶었습니다. 원문 사유는 설명에 마우스를 올리면 보입니다.',
        fails.length ? `<div class="fail-sum">${Object.entries(byKind).sort((a, b) => b[1] - a[1]).map(([k, n]) =>
          `<div><span class="tag review">${esc(FAIL_TYPE[k] || k)}</span> <b>${n}</b> <span class="muted small">${esc(KIND_HELP[k] || '')}</span></div>`).join('')}</div>
        <div class="tbl-wrap"><table><thead><tr><th>주차</th><th>키워드</th><th>잡은 쪽</th><th>유형</th><th>무엇이 틀렸나</th></tr></thead><tbody>
        ${grouped.map(({ f, k, by, raw }) => `<tr><td>${f.week.slice(5)}</td><td><b>${esc(kwName(f.keyword))}</b></td><td>${[...by].join(' + ')}</td>
          <td>${tag(esc(FAIL_TYPE[k] || k), 'review')}</td><td title="${esc(raw.map(x => `[${x.checker === 'rule' ? '규칙' : 'LLM'}] ${x.target}: ${x.detail}`).join('\n'))}">${esc(failPlain(f))}</td></tr>`).join('')}
        </tbody></table></div>` : empty('없음'))}`, () => {
      if (!vw.length) return;
      const r = w => { const x = valid([w]); return [x.ruleRate, x.llmRate].map(y => y === null ? null : +(y * 100).toFixed(1)); };
      chart(document.getElementById('vchart'), { type: 'line',
        data: { labels: vw.map(w => w.week.slice(5)), datasets: [
          { label: '규칙', data: vw.map(w => r(w)[0]), borderColor: DARK, tension: .3 },
          { label: 'LLM 판정', data: vw.map(w => r(w)[1]), borderColor: '#adb5bd', borderDash: [4, 3], tension: .3 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { min: 0, max: 100, ticks: { callback: y => y + '%' } } } } });
    }];
  }

  // ── 페이지: 방법론 ──
  // 텍스트 나열 대신 흐름도·도식·실제 데이터 차트로 기준을 보여 준다. 수치 기준은 RULES 와 파이프라인 상수 그대로.
  function pageMethod(mode, k) {
    const ws = periodWeeks(mode, k), w = ws[ws.length - 1];
    const rules = Object.entries(RULES).map(([key, r]) => `<tr><td><code>${key}</code></td><td>${esc(r.label)}</td><td class="num"><b>${r.v}</b></td><td>${esc(r.desc)}</td></tr>`).join('');
    const flow = [['수집', 'TikTok · YouTube · OY · Sephora'], ['품질 검사', '건수·빈 본문 미달 시 중단'], ['탐지', '게시물 비중 z · 모멘텀'],
                  ['심층 수집', '트렌드 키워드만 추가 검색'], ['LLM 해석', '니즈 · 제품 연결 (Sonnet)'], ['의사결정', '대화 점유 · 제품 전략 · 방한 추천'],
                  ['검증', '규칙 + 다른 모델 (Opus)'], ['대시보드', '주·월 단위 조회']];
    const detN = sum(Object.values(w.platforms)), deepN = sum(w.trends.map(t => (t.det && t.det.deep) || 0));
    const shares = w.trends.map(t => { const s = shareOf(w, t);
      return { kw: kwName(t.kw), top: s.rows[0] ? brandName(s.rows[0][0]) : '—', v: s.total && s.rows[0] ? s.rows[0][1] / s.total : 0, n: s.total }; })
      .sort((a, b) => b.v - a.v);
    const v = valid([w]);
    const mini = (label, cls) => `<span class="tag ${cls}">${label}</span>`;
    const quad = cells => `<div class="q2">${cells.map(([t, d, cls]) => `<div class="q2c ${cls || ''}"><b>${t}</b><span>${d}</span></div>`).join('')}</div>`;
    const html = `<div class="method">
      ${card('전체 흐름', `매주 목요일 Airflow 실행 · YouTube는 매일 수집 · 기준 주차 ${esc(w.week)}`,
        `<div class="flow">${flow.map(([t, d], i) => `<div class="fstep"><div class="fn">${i + 1}</div><b>${t}</b><span>${d}</span></div>`).join('')}</div>`)}

      <div class="grid2">
        ${card('표본: 탐지용과 해석용 분리', '키워드로 검색한 결과를 탐지에 섞으면 스스로 뜨는 것처럼 보이는 순환 발생, 그래서 분리',
          `<div class="split">
             <div class="sbox"><div class="sh">탐지용</div><div class="sn">${fmt(detN)}<small>건</small></div>
               <ul><li>매주 같은 검색어·해시태그</li><li>YouTube 매일 · TikTok 주 500건</li><li>트렌드 선정 · z · 게시물 수에 사용</li></ul></div>
             <div class="sbox alt"><div class="sh">해석용 (심층 수집)</div><div class="sn">${fmt(deepN)}<small>건</small></div>
               <ul><li>트렌드 키워드로 추가 검색</li><li>키워드당 YouTube ~100 · TikTok 28</li><li>LLM 캡션 샘플 · 브랜드 집계에만 사용</li></ul></div>
           </div>`)}
        ${card('탐지 지표: 총량에서 비중으로', '총량 기준은 수집량이 늘어난 주에 키워드가 한꺼번에 급등으로 잡힘. 비중 기준으로 바꾸고 전 주차 재분석',
          `<div style="position:relative;height:190px"><canvas id="m_norm"></canvas></div>
           <div class="formula"><span><b>비중</b> = 키워드 게시물 ÷ 그 주 전체 게시물</span><span><b>z</b> = (이번 주 비중 − 과거 평균) ÷ 표준편차</span></div>`)}
      </div>

      ${card('트렌드 판정', '두 조건 중 하나만 충족해도 후보 · 광의어·브랜드명 제외 · 게시물 5건 이상 · 최대 10개',
        `<div class="grid2 inner">
           <div>${quad([['한 주 반짝', `z ≥ ${RULES.SURGE_Z.v} · 모멘텀 낮음`], ['강하게 뜨는 중', '급등 + 지속', 'hi'],
                        ['후보 아님', '두 조건 모두 미달', 'dim'], ['꾸준히 오르는 중', `3주 연속 상승 · 모멘텀 ≥ ${RULES.SUSTAIN_MOMENTUM.v}`, 'hi2']])}
             <div class="axis"><span>↑ z-score (게시물 비중)</span><span>모멘텀 (3개 데이터 주차 전 대비) →</span></div></div>
           <div class="rulelist">
             <div><span class="rk">순간 급등</span><span>게시물 비중 z ≥ <b>${RULES.SURGE_Z.v}</b> (과거 3개 데이터 주차 이상일 때)</span></div>
             <div><span class="rk">지속 상승</span><span>3개 데이터 주차 연속 상승 + 모멘텀 ≥ <b>${RULES.SUSTAIN_MOMENTUM.v}배</b></span></div>
             <div><span class="rk">제외</span><span>광의어(skincare 등) · 브랜드명 · 게시물 5건 미만</span></div>
             <div><span class="rk">engagement</span><span>TikTok 조회×0.3 + 좋아요×1 + 댓글×2 · YouTube 조회×0.2 + 좋아요×1.5 + 댓글×3</span></div>
           </div></div>`)}

      ${card('대화 점유 (기회 유형)', `트렌드 캡션 전체에서 등록 브랜드 이름을 코드로 집계(캡션당 1회), LLM 집계 미사용 · 오른쪽은 ${esc(w.week)} 실제 값`,
        `<div class="grid2 inner">
           <div class="opp-ex">
             <div><div class="oh">${mini('선점', 'opp')} 1위 50% 이상 · 3건 이상</div><div class="sharebar"><span class="seg-0" style="width:62%">A 62%</span><span class="seg-2" style="width:23%">B</span><span class="seg-3" style="width:15%">기타</span></div></div>
             <div><div class="oh">${mini('각축', 'opp')} 과반 브랜드 없음</div><div class="sharebar"><span class="seg-0" style="width:30%">A 30%</span><span class="seg-1" style="width:25%">B 25%</span><span class="seg-2" style="width:20%">C</span><span class="seg-3" style="width:25%">기타</span></div></div>
             <div><div class="oh">${mini('공백', 'opp')} K-브랜드 언급 없음</div><div class="sharebar empty-bar">언급 0건 · 맞는 제품 있으면 소통 공백, 없으면 제품 공백</div></div>
             <div><div class="oh">${mini('판단 보류', 'trust hold')} 언급 ${RULES.MIN_MENTIONS.v}건 미만</div><div class="sharebar empty-bar">표본 부족으로 판단 안 함</div></div>
           </div>
           <div>${shares.length ? `<div style="position:relative;height:${Math.max(170, shares.length * 34 + 50)}px"><canvas id="m_share"></canvas></div>` : empty('트렌드 없음')}</div>
         </div>`)}

      <div class="grid2">
        ${card('신뢰 배지', '트렌드 카드마다 하나 · 위에서부터 먼저 걸리는 것',
          `<div class="badges">
             <div>${mini('판단 보류', 'trust hold')}<span>브랜드 언급 ${RULES.MIN_MENTIONS.v}건 미만</span></div>
             <div>${mini('관찰 중', 'trust watch')}<span>게시물 ${RULES.LOW_SAMPLE_POSTS.v}건 미만 · 연속 수집 ${RULES.MIN_CONSEC_WEEKS.v}주 미만 · engagement ${RULES.BIG_JUMP_RATIO.v}배 급등 · 조회수 ${pct(RULES.VIRAL_SHARE.v)} 이상이 게시물 1개</span></div>
             <div>${mini('판단 가능', 'trust ok')}<span>경고 없음</span></div>
             <div>${mini('검토 필요', 'review')}<span>자동 검증 실패 항목 있음 (카드 안 표시)</span></div>
           </div>`)}
        ${card('검증', `LLM 출력의 주장마다 검사 · ${esc(w.week)} 결과`,
          `<div class="vflow"><span>LLM 출력<small>Sonnet</small></span><i>›</i><span>규칙 검사<small>인용·언급 수·한국어 대조</small></span><i>›</i><span>다른 모델 판정<small>Opus · 지지 / 근거 부족 / 모순</small></span></div>
           ${v.rate === null ? empty('검증 기록 없음') : '<div style="position:relative;height:130px"><canvas id="m_valid"></canvas></div>'}`)}
      </div>

      <div class="grid2">
        ${card('제품 전략', '가로 = 별점 추세 · 세로 = 리뷰 긍정 비율(65% 기준)',
          `<div class="q3">
             <div class="q2c hi2 fix"><b>FIX & PUSH</b><span>별점 하락 또는 부정 35% 이상 · 긍정 여부와 무관하게 우선 · 개선 후 확대</span></div>
             <div class="q2c"><b>HOLD</b><span>긍정 · 별점 정체 · 유지·관찰</span></div>
             <div class="q2c hi"><b>PUSH NOW</b><span>긍정 · 별점 상승 · 확대</span></div>
             <div class="q2c dim mon"><b>MONITOR</b><span>긍정 기준 미달 · 하락 신호 없음 · 관찰</span></div>
           </div>
           <div class="axis"><span>↑ 리뷰 긍정 비율 (65%·랭킹 점수 0.7 기준)</span><span>별점 추세: 하락 │ 정체 │ 상승 →</span></div>`)}
        ${card('방한 추천 점수', '제품별 0~1 · 상위 15개 표시',
          `<div class="wbar"><span style="width:35%">SNS 트렌드 35%</span><span style="width:35%">VOC 긍정 35%</span><span style="width:30%">리테일 순위 30%</span></div>
           <div class="formula"><span><b>리테일 점수</b> = max(0, 1 − (순위 − 1) ÷ 100)</span><span><b>월간 보기</b> = 주차 결과 합산, 새 판정 없음</span></div>`)}
      </div>

      <details class="card"><summary><b>화면 표시 기준값 전체</b> <span class="muted small">dashboard/app.js RULES 상수 · 협찬 태그 · 제외 브랜드명</span></summary>
        <div class="tbl-wrap" style="margin-top:12px"><table><thead><tr><th>상수</th><th>표시</th><th class="num">기준값</th><th>설명</th></tr></thead><tbody>${rules}</tbody></table></div>
        <p class="small muted" style="margin-top:8px">협찬 태그: #ad, #sponsored, partner, #광고, 쿠팡 파트너스, gifted, 할인·promo code 패턴 (삭제하지 않고 표시만) · 일반 단어와 겹쳐 집계에서 뺀 브랜드명: ${esc((D.brand_stopwords || []).join(', '))}</p>
      </details>
    </div>`;
    return [html, () => {
      chart(document.getElementById('m_norm'), { type: 'bar',
        data: { labels: ['2026-W21', '2026-W47'], datasets: [
          { label: '총량 기준 (이전)', data: [38, 5], backgroundColor: LIGHT, borderRadius: 4 },
          { label: '비중 기준 (현재)', data: [0, 0], backgroundColor: DARK, borderRadius: 4, minBarLength: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 10 } },
          tooltip: { callbacks: { label: c => `${c.dataset.label}: z ≥ 2 키워드 ${c.raw}개` } } },
          scales: { y: { beginAtZero: true, title: { display: true, text: 'z ≥ 2 키워드 수' }, grid: { color: '#f1f3f5' } }, x: { grid: { display: false } } } } });
      if (shares.length) chart(document.getElementById('m_share'), { type: 'bar',
        data: { labels: shares.map(s => s.kw), datasets: [{ data: shares.map(s => +(s.v * 100).toFixed(1)),
          backgroundColor: shares.map(s => s.v >= 0.5 ? DARK : LIGHT), borderRadius: 4 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, layout: { padding: { right: 70, top: 16 } },
          plugins: { legend: { display: false }, title: { display: true, text: '트렌드별 1위 브랜드 점유 (%)', align: 'start', font: { size: 12 } },
            tooltip: { callbacks: { label: c => `${shares[c.dataIndex].top} ${c.raw}% · 언급 ${shares[c.dataIndex].n}건` } } },
          scales: { x: { min: 0, max: 100, grid: { color: '#f1f3f5' } }, y: { grid: { display: false } } } },
        plugins: [{ id: 'th', afterDatasetsDraw(c) { const { ctx, chartArea: a, scales: s } = c; const x = s.x.getPixelForValue(50);
          ctx.save(); ctx.strokeStyle = ACCENT; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke();
          ctx.setLineDash([]); ctx.fillStyle = ACCENT; ctx.font = '11px Pretendard'; ctx.fillText('선점 기준 50%', x + 4, a.top - 4);
          ctx.fillStyle = '#495057'; ctx.textBaseline = 'middle';
          c.getDatasetMeta(0).data.forEach((b, i) => ctx.fillText(`${shares[i].top} ${shares[i].v ? pct(shares[i].v) : ''}`, b.x + 6, b.y)); ctx.restore(); } }] });
      if (v.rate !== null) {
        const rows = [['규칙', v.rule], ['Opus', v.llm]];
        chart(document.getElementById('m_valid'), { type: 'bar',
          data: { labels: rows.map(r => r[0]), datasets: [['pass', '통과', '#2b8a3e'], ['warn', '경고', '#f59f00'], ['fail', '실패', '#e03131']].map(([key, l, col]) =>
            ({ label: l, data: rows.map(r => r[1][key]), backgroundColor: col })) },
          options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom', labels: { boxWidth: 10 } } },
            scales: { x: { stacked: true, grid: { color: '#f1f3f5' } }, y: { stacked: true, grid: { display: false } } } } });
      }
    }];
  }

  const RENDER = { overview: pageOverview, trends: pageTrends, brands: pageBrands, products: pageProducts, inbound: pageInbound, quality: pageQuality, method: pageMethod };

  function render() {
    charts.forEach(c => c.destroy()); charts = [];
    if (!state.key) { document.getElementById('content').innerHTML = empty('데이터 없음'); return; }
    document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.page === state.page));
    document.querySelectorAll('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.mode === state.mode));
    const sel = document.getElementById('periodSelect');
    sel.dataset.mode = state.mode; sel.dataset.period = state.key;
    sel.innerHTML = periodKeys(state.mode).slice().reverse().map(x => `<option value="${x}" ${x === state.key ? 'selected' : ''}>
      ${state.mode === 'week' ? `${x} · ${esc(WEEK[x].label)}` : monthLabel(x)}</option>`).join('');
    const ws = periodWeeks(state.mode, state.key), last = ws[ws.length - 1];
    const partial = D.generated < `${last.end} 23:59`;
    document.getElementById('pageTitle').textContent = PAGES[state.page];
    document.getElementById('periodLabel').textContent = (state.mode === 'week' ? last.label
      : `${monthLabel(state.key)} · 데이터 주차 ${ws.map(w => w.week.slice(5)).join(', ')}`) + (partial ? ' (집계 중)' : '');
    const v = valid(ws);
    document.getElementById('trustBadge').innerHTML = v.rate === null ? '검증 기록 없음' : `검증 통과 <b>${pct(v.rate)}</b> · 검토 필요 <b class="review-num">${v.fails}</b>`;
    document.getElementById('banner').innerHTML = ws.some(w => w.legacy)
      ? '<div class="banner">이 기간에는 이전 기준(아모레퍼시픽 관점)으로 분석된 주차가 포함되어 있습니다. 기회 유형 이름이 다를 수 있습니다.</div>' : '';
    document.getElementById('sideFoot').innerHTML = `데이터 갱신 ${esc(D.generated)}<br>주차 ${WEEKS.length}개`;
    const [html, after] = RENDER[state.page](state.mode, state.key);
    document.getElementById('content').innerHTML = html;
    if (after) after();
  }

  document.querySelectorAll('#nav a').forEach(a => a.onclick = () => go({ page: a.dataset.page }));
  document.querySelectorAll('#modeSeg button').forEach(b => b.onclick = () => {
    if (b.dataset.mode === state.mode) return;
    const ws = periodWeeks(state.mode, state.key), last = ws[ws.length - 1];
    go(b.dataset.mode === 'month' ? { mode: 'month', key: last.month } : { mode: 'week', key: last.week });
  });
  document.getElementById('periodSelect').onchange = e => go({ key: e.target.value });

  readUrl(); writeUrl(false); render();
})();
