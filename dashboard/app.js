/* K-beauty Trend Radar — 정적 대시보드
 * 데이터: window.DASH (tools/report/dashboard_builder.py 가 생성한 data.js)
 * URL: ?w=2026-W47#trends  (주간)  |  ?m=2026-11#brands  (월간)
 */
(() => {
  const D = window.DASH;
  const META = D.meta;
  const ACCENT = '#d6336c';
  const GRAY = '#ced4da';
  Chart.defaults.font.family = 'Pretendard, -apple-system, sans-serif';
  Chart.defaults.color = '#868e96';

  const WEEKS = D.weeks;                                     // 오래된 순
  const WEEK = Object.fromEntries(WEEKS.map(w => [w.week, w]));
  const MONTHS = [...new Set(WEEKS.map(w => w.month))];      // 오래된 순
  const PAGES = {
    overview: '개요', trends: '트렌드', brands: '브랜드', products: '제품·VOC',
    inbound: '방한 추천', quality: '검증·품질', method: '방법론',
  };

  // ── 유틸 ──
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = n => (n === null || n === undefined) ? '—' : Number(n).toLocaleString('ko-KR');
  const pct = (v, d = 0) => (v === null || v === undefined) ? '—' : (v * 100).toFixed(d) + '%';
  const kwLabel = k => esc(String(k).replace(/_/g, ' '));
  const brandName = b => esc(D.brand_names[b] || b);
  const sum = a => a.reduce((x, y) => x + y, 0);
  const monthLabel = m => `${m.slice(0, 4)}년 ${+m.slice(5)}월`;
  let charts = [];
  const chart = (el, cfg) => { if (!el) return null; const c = new Chart(el, cfg); charts.push(c); return c; };

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

  // ── 기간 데이터 (월간은 단순 집계: 합계·합집합·월 마지막 주차 스냅샷) ──
  function periodWeeks(mode, key) {
    return mode === 'week' ? [WEEK[key]] : WEEKS.filter(w => w.month === key);
  }
  function periodKeys(mode) { return mode === 'week' ? WEEKS.map(w => w.week) : MONTHS; }
  function prevKey(mode, key) { const ks = periodKeys(mode); const i = ks.indexOf(key); return i > 0 ? ks[i - 1] : null; }

  function valid(ws) {
    const v = { rule: { pass: 0, warn: 0, fail: 0 }, llm: { pass: 0, warn: 0, fail: 0 }, model: null };
    ws.forEach(w => ['rule', 'llm'].forEach(k => {
      const x = w.validation[k]; if (!x) return;
      ['pass', 'warn', 'fail'].forEach(t => v[k][t] += x[t] || 0);
      if (k === 'llm') v.model = x.model;
    }));
    const tot = o => o.pass + o.warn + o.fail;
    const all = { pass: v.rule.pass + v.llm.pass, n: tot(v.rule) + tot(v.llm), fail: v.rule.fail + v.llm.fail };
    return { ...v, rate: all.n ? all.pass / all.n : null, fails: all.fail, ruleRate: tot(v.rule) ? v.rule.pass / tot(v.rule) : null,
             llmRate: tot(v.llm) ? v.llm.pass / tot(v.llm) : null };
  }
  function kpis(mode, key) {
    const ws = periodWeeks(mode, key);
    const last = ws[ws.length - 1];
    return {
      trends: new Set(ws.flatMap(w => w.trends.map(t => t.kw))).size,
      posts: sum(ws.map(w => w.kpi.posts)),
      ranked: last.kpi.ranked, ranked_brands: last.kpi.ranked_brands,
      valid: valid(ws).rate,
      fails: valid(ws).fails,
    };
  }
  function monthTrends(ws) {   // 키워드별: 등장 주차, 최고 z, 마지막 상태
    const m = {};
    ws.forEach(w => w.trends.forEach(t => {
      const x = m[t.kw] || (m[t.kw] = { kw: t.kw, weeks: [], zmax: -Infinity, posts: 0 });
      x.weeks.push(w.week); x.zmax = Math.max(x.zmax, t.z); x.posts += t.posts;
      x.shape = t.shape; x.opp = t.opp; x.need = t.need; x.mom = t.mom;
    }));
    return Object.values(m).sort((a, b) => b.weeks.length - a.weeks.length || b.zmax - a.zmax);
  }
  function mergedMatrix(ws) {
    const m = {};
    ws.forEach(w => Object.entries(w.matrix).forEach(([b, row]) => Object.entries(row).forEach(([k, n]) => {
      (m[b] = m[b] || {})[k] = (m[b][k] || 0) + n;
    })));
    return m;
  }

  // ── 공통 조각 ──
  const card = (title, hint, body, extra = '') => `<section class="card" ${extra}><h3>${title}</h3>${hint ? `<div class="hint">${hint}</div>` : ''}${body}</section>`;
  const empty = msg => `<div class="empty">${msg}</div>`;
  const oppMeta = o => META.opp[o] || { label: o || '—', color: '#868e96', bg: '#f1f3f5' };
  const shapeMeta = s => META.shape[s] || META.shape.steady;
  const quadMeta = q => META.quad[q] || META.quad.MONITOR;
  const pill = (m) => `<span class="pill" style="background:${m.bg};color:${m.color}">${esc(m.label)}</span>`;
  const snapNote = (mode, ws) => mode === 'month'
    ? `<div class="hint">월간 보기: 이 화면은 월의 마지막 데이터 주차(${ws[ws.length - 1].week}) 기준입니다.</div>` : '';

  function sparkline(el, values, highlightIdx) {
    chart(el, {
      type: 'line',
      data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: ACCENT, borderWidth: 2, tension: .35,
        pointRadius: values.map((_, i) => i === highlightIdx ? 3 : 0), pointBackgroundColor: ACCENT, fill: false }] },
      options: { responsive: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
                 scales: { x: { display: false }, y: { display: false } }, animation: false },
    });
  }

  // ── 페이지: 개요 ──
  function pageOverview(mode, key) {
    const ws = periodWeeks(mode, key);
    const k = kpis(mode, key);
    const pk = prevKey(mode, key);
    const p = pk ? kpis(mode, pk) : null;
    const keys = periodKeys(mode);
    const series = f => keys.map(x => f(kpis(mode, x)));
    const delta = (cur, prev, unit = '') => {
      if (prev === null || prev === undefined || cur === null) return `<div class="delta">비교 기간 없음</div>`;
      const d = cur - prev;
      const cls = d > 0 ? 'up' : d < 0 ? 'down' : '';
      const v = unit === '%' ? (d * 100).toFixed(0) + '%p' : fmt(Math.abs(d));
      return `<div class="delta ${cls}">${d > 0 ? '▲' : d < 0 ? '▼' : '–'} ${unit === '%' ? v : v} <span class="muted">vs ${pk}</span></div>`;
    };
    const kpiCard = (id, label, val, dHtml) => `<div class="kpi"><div class="label">${label}</div>
      <div class="row"><div><div class="val">${val}</div>${dHtml}</div><canvas id="${id}" width="90" height="36"></canvas></div></div>`;

    const last = ws[ws.length - 1];
    const summary = mode === 'week' ? last.summary
      : monthTrends(ws).slice(0, 3).map(t => ({ kw: t.kw, z: t.zmax, shape: t.shape, need: (t.need || '').split(/(?<=[.다])\s/)[0], weeks: t.weeks.length }));
    const rising = mode === 'week' ? last.rising_brands : Object.values(ws.flatMap(w => w.rising_brands).reduce((m, b) => {
      const x = m[b.kw] || (m[b.kw] = { ...b, n: 0 }); x.n++; x.z = Math.max(x.z, b.z); x.posts = Math.max(x.posts, b.posts); x.ok = x.ok || b.ok; return m; }, {}));
    const fails = ws.flatMap(w => w.fails.map(f => ({ ...f, week: w.week })));

    const html = `
      <div class="kpis">
        ${kpiCard('sp1', mode === 'week' ? '급상승 키워드' : '월간 등장 트렌드', fmt(k.trends), delta(k.trends, p?.trends))}
        ${kpiCard('sp2', '분석한 SNS 게시물', fmt(k.posts), delta(k.posts, p?.posts))}
        ${kpiCard('sp3', '랭킹 제품', fmt(k.ranked), `<div class="delta">브랜드 ${fmt(k.ranked_brands)}개 · OY Global</div>`)}
        ${kpiCard('sp4', '인사이트 검증 통과율', k.valid === null ? '—' : pct(k.valid), k.valid === null ? '<div class="delta">검증 기록 없음</div>' : `<div class="delta">검토 필요 ${k.fails}건</div>`)}
      </div>
      <div class="grid-main">
        ${card('주차별 SNS 수집량', '막대 = 게시물 수, 선 = engagement 합계. 선택한 기간은 진하게 표시합니다.',
               '<div style="position:relative;height:300px"><canvas id="tlChart"></canvas></div>')}
        <div>
          ${card(mode === 'week' ? '이번 주 요약' : '이번 달 요약', mode === 'week' ? 'z-score 상위 3개 트렌드' : '여러 주에 걸쳐 등장한 트렌드 순',
                 summary.length ? `<ul class="list">${summary.map(s => `<li><span class="kw">${kwLabel(s.kw)}</span>
                   <span class="pill">z ${Number(s.z).toFixed(1)} · ${esc(shapeMeta(s.shape).label)}</span>${s.weeks ? `<span class="pill gray">${s.weeks}주</span>` : ''}
                   <div class="muted" style="font-size:13px">${esc(s.need)}</div></li>`).join('')}</ul>` : empty('트렌드 없음'))}
          ${card('급상승 브랜드', '트렌드와 같은 기준: z ≥ 2, 게시물 ≥ 5 (회색 = 게시물 5건 미만, 참고)',
                 rising.length ? rising.map(b => `<span class="chip ${b.ok ? '' : 'out'}" style="${b.ok ? 'background:var(--primary-soft);color:var(--primary)' : ''}">
                   <b>${esc(b.kw)}</b> z ${b.z} · ${b.posts}건${b.n > 1 ? ` · ${b.n}주` : ''}</span>`).join('') : empty('해당 없음'))}
        </div>
      </div>
      ${card(`검토 필요 ${fails.length}건`, '근거와 맞지 않아 자동 검증에서 실패한 LLM 출력입니다. 리포트에는 그대로 두고 표시만 합니다.',
             fails.length ? `<div class="tbl-wrap"><table><thead><tr>${mode === 'month' ? '<th>주차</th>' : ''}<th>키워드</th><th>검사</th><th>대상</th><th>사유</th><th>판정</th></tr></thead><tbody>
               ${fails.map(f => `<tr>${mode === 'month' ? `<td>${f.week}</td>` : ''}<td>${kwLabel(f.keyword)}</td><td>${esc(f.check_type)}</td><td>${esc(f.target)}</td>
                 <td>${esc(f.detail)}</td><td>${f.checker === 'rule' ? '규칙' : 'LLM'}</td></tr>`).join('')}</tbody></table></div>` : empty('없음'))}
    `;
    return [html, () => {
      const i = keys.indexOf(key);
      sparkline(document.getElementById('sp1'), series(x => x.trends), i);
      sparkline(document.getElementById('sp2'), series(x => x.posts), i);
      sparkline(document.getElementById('sp3'), series(x => x.ranked), i);
      sparkline(document.getElementById('sp4'), series(x => x.valid ?? 0), i);
      const tl = D.timeline;
      const sel = new Set(ws.map(w => w.week));
      chart(document.getElementById('tlChart'), {
        data: { labels: tl.map(t => t.week.replace('2026-', '')), datasets: [
          { type: 'bar', label: '게시물', data: tl.map(t => t.posts), backgroundColor: tl.map(t => sel.has(t.week) ? ACCENT : GRAY), yAxisID: 'y', borderRadius: 4 },
          { type: 'line', label: 'engagement', data: tl.map(t => t.eng), borderColor: '#495057', borderWidth: 2, pointRadius: 2, tension: .3, yAxisID: 'y2' },
        ] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } },
                   scales: { y: { beginAtZero: true, title: { display: true, text: '게시물' } },
                             y2: { position: 'right', beginAtZero: true, grid: { display: false }, title: { display: true, text: 'engagement' } } } },
      });
    }];
  }

  // ── 페이지: 트렌드 ──
  let oppFilter = 'ALL';
  function pageTrends(mode, key) {
    const ws = periodWeeks(mode, key);
    if (mode === 'month') {
      const mt = monthTrends(ws);
      return [card('월간 트렌드', `${monthLabel(key)}의 데이터 주차(${ws.map(w => w.week).join(', ')})에 선정된 트렌드. 여러 주 연속 등장할수록 위에 둡니다.`,
        mt.length ? `<div class="tbl-wrap"><table><thead><tr><th>키워드</th>${ws.map(w => `<th class="c">${w.week.slice(5)}</th>`).join('')}
          <th class="num">최고 z</th><th class="num">게시물</th><th>마지막 모양</th><th>마지막 유형</th></tr></thead><tbody>
          ${mt.map(t => `<tr><td><b>${kwLabel(t.kw)}</b></td>${ws.map(w => `<td class="c">${t.weeks.includes(w.week) ? '<span style="color:var(--primary)">●</span>' : ''}</td>`).join('')}
            <td class="num">${t.zmax.toFixed(1)}</td><td class="num">${fmt(t.posts)}</td><td>${pill(shapeMeta(t.shape))}</td><td>${pill(oppMeta(t.opp))}</td></tr>`).join('')}
          </tbody></table></div>` : empty('트렌드 없음')), null];
    }
    const w = ws[0];
    const counts = {}; w.trends.forEach(t => counts[t.opp] = (counts[t.opp] || 0) + 1);
    const types = w.legacy ? Object.keys(counts) : ['leader', 'contested', 'white_space'];
    const desc = { leader: '한 브랜드가 언급 과반(3건↑) → 벤치마크·차별화', contested: '주인 없는 경쟁 → 먼저 각인시키는 쪽이 유리',
                   white_space: 'K-뷰티 브랜드 언급 없음 → 소통 공백 / 제품 공백' };
    const shown = w.trends.filter(t => oppFilter === 'ALL' || t.opp === oppFilter);
    const legend = Object.values(META.shape).map(m => `<span style="color:${m.color}">●</span> ${esc(m.label)}`).join(' · ');
    const html = `
      <div class="grid3" style="margin-bottom:16px">${types.map(o => `<div class="stat ${oppFilter === o ? 'on' : ''}" data-opp="${o}">
        <div class="n" style="color:${oppMeta(o).color}">${counts[o] || 0}</div><div class="t">${esc(oppMeta(o).label)}</div>
        <div class="d">${desc[o] || '이전 기준(AP 관점) 유형'}</div></div>`).join('')}</div>
      ${card('트렌드 레이더', `가로 = 모멘텀(최근 3개 데이터 주차 성장 배수, 로그) · 세로 = z-score(10 이상은 맨 위) · 크기 = 게시물 수 · 색 = ${legend}. 점을 누르면 카드로 이동합니다.`,
             w.trends.length ? '<div style="position:relative;height:340px"><canvas id="radar"></canvas></div>' : empty('트렌드 없음'))}
      ${shown.map((t, i) => {
        const om = oppMeta(t.opp), sm = shapeMeta(t.shape);
        return `<section class="card tcard" id="t_${w.trends.indexOf(t)}" style="border-left-color:${om.color}">
          <div class="head"><div><span class="name">${kwLabel(t.kw)}</span> ${pill(om)} ${pill(sm)} ${t.cross ? '<span class="pill gray">TikTok·YouTube 동시</span>' : ''}</div>
            <div class="meta">선행 ${esc(t.lead || '—')} · z ${t.z} · 모멘텀 ${t.mom}× · 게시물 ${fmt(t.posts)}</div></div>
          <div style="margin-top:8px">${t.brands.map(b => `<span class="chip" title="이 니즈에 맞는 제품 보유">${esc(String(b).toUpperCase())}</span>`).join('')}
            ${t.mentions.map(m => `<span class="chip out" title="캡션 언급">언급 ${esc(m)}</span>`).join('')}</div>
          <div class="body"><div>
            <p><b style="color:${om.color}">대화 점유</b> · ${esc(t.summary)}</p>
            ${t.need ? `<p><b>소비자 니즈</b> · ${esc(t.need)}</p>` : ''}
            ${t.evidence.length ? `<p style="margin-bottom:2px"><b>SNS 근거</b></p>${t.evidence.map(e => `<blockquote>“${esc(e)}”</blockquote>`).join('')}` : ''}
            <div class="act"><b>제안</b> · ${esc(t.action)}</div></div>
            <div style="position:relative;height:130px"><canvas id="mini_${i}"></canvas></div></div></section>`;
      }).join('')}`;
    return [html, () => {
      document.querySelectorAll('.stat[data-opp]').forEach(el => el.onclick = () => {
        oppFilter = oppFilter === el.dataset.opp ? 'ALL' : el.dataset.opp; render(); });
      shown.forEach((t, i) => chart(document.getElementById('mini_' + i), {
        type: 'line', data: { labels: t.hist.map(h => h[0].slice(5)), datasets: [{ data: t.hist.map(h => h[1]), borderColor: ACCENT,
          backgroundColor: ACCENT + '22', fill: true, tension: .3, pointRadius: 2 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
                   scales: { x: { ticks: { font: { size: 10 } } }, y: { beginAtZero: true, ticks: { font: { size: 10 }, maxTicksLimit: 4 } } } } }));
      const pts = w.trends.map((t, i) => ({ x: Math.max(t.mom, 0.5), y: Math.min(t.z, 10), r: 6 + Math.sqrt(Math.min(t.posts, 60)) * 2.2, t, i }));
      chart(document.getElementById('radar'), {
        type: 'bubble',
        data: { datasets: [{ data: pts, backgroundColor: pts.map(p => shapeMeta(p.t.shape).color + 'aa'), borderColor: pts.map(p => shapeMeta(p.t.shape).color) }] },
        options: { responsive: true, maintainAspectRatio: false, layout: { padding: { right: 20 } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => {
            const t = c.raw.t; return `${t.kw} · ${shapeMeta(t.shape).label} · ${oppMeta(t.opp).label} · z ${t.z} · 모멘텀 ${t.mom}× · 게시물 ${t.posts}`; } } } },
          onClick: (e, els) => { if (!els.length) return; const el = document.getElementById('t_' + pts[els[0].index].i);
            if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1500); } },
          scales: { x: { type: 'logarithmic', title: { display: true, text: '모멘텀 (배수, 로그)' } },
                    y: { suggestedMin: -1, max: 11, title: { display: true, text: 'z-score' } } } },
        plugins: [{ id: 'lbl', afterDatasetsDraw(c) {
          const ctx = c.ctx, right = c.chartArea.right; ctx.save(); ctx.font = '12px Pretendard, sans-serif'; ctx.fillStyle = '#343a40';
          c.getDatasetMeta(0).data.forEach((pt, i) => { const p = pts[i];
            const txt = p.t.kw.replace(/_/g, ' ') + (p.t.z > 10 ? ` (z ${p.t.z})` : ''); const w2 = ctx.measureText(txt).width;
            ctx.fillText(txt, pt.x + p.r + 3 + w2 > right ? pt.x - p.r - 3 - w2 : pt.x + p.r + 3, pt.y + 4); });
          ctx.restore(); } }],
      });
    }];
  }

  // ── 페이지: 브랜드 ──
  function pageBrands(mode, key) {
    const ws = periodWeeks(mode, key);
    const m = mergedMatrix(ws);
    const kwTotals = {}; Object.values(m).forEach(r => Object.entries(r).forEach(([k, n]) => kwTotals[k] = (kwTotals[k] || 0) + n));
    const kws = Object.keys(kwTotals).sort((a, b) => kwTotals[b] - kwTotals[a]).slice(0, 10);
    const totals = Object.entries(m).map(([b, r]) => [b, sum(Object.values(r))]).sort((a, b) => b[1] - a[1]);
    const all = sum(totals.map(t => t[1])) || 1;
    const top = totals.slice(0, 12);
    const vmax = Math.max(1, ...Object.values(m).flatMap(r => Object.values(r)));
    const last = ws[ws.length - 1];
    const rk = last.retail_brands;
    const byCo = {}; rk.forEach(([b, n]) => { const co = D.brand_company[b]; if (!co) return; (byCo[co] = byCo[co] || [0, []]); byCo[co][0] += n; byCo[co][1].push(D.brand_names[b] || b); });
    const co = Object.entries(byCo).sort((a, b) => b[1][0] - a[1][0]).slice(0, 8);
    const heat = top.length ? `<div class="tbl-wrap"><table><thead><tr><th>브랜드</th>${kws.map(k => `<th class="c">${kwLabel(k)}</th>`).join('')}<th class="num">합계</th><th class="num">점유</th></tr></thead><tbody>
      ${top.map(([b, t]) => `<tr><td><b>${brandName(b)}</b></td>${kws.map(k => { const c = m[b][k] || 0; const a = c ? .15 + .85 * c / vmax : 0;
        return `<td class="c" style="background:rgba(214,51,108,${a.toFixed(2)});color:${a > .55 ? '#fff' : '#333'}">${c || ''}</td>`; }).join('')}
        <td class="num">${t}</td><td class="num">${pct(t / all)}</td></tr>`).join('')}</tbody></table></div>` : empty('언급된 K-뷰티 브랜드 없음');
    const html = `
      ${card('트렌드별 브랜드 언급', `트렌드 캡션 샘플에서 언급된 K-뷰티 브랜드 상위 12개${mode === 'month' ? ' (월 합계, 언급 많은 트렌드 10개)' : ''}. 판매처·사람 이름·서구 브랜드는 제외. 진할수록 언급이 많습니다.`, heat)}
      <div class="grid2">
        ${card('트렌드 대화 점유', '전체 트렌드 언급 중 브랜드별 비중(%)', '<div style="position:relative;height:300px"><canvas id="share"></canvas></div>')}
        ${card('OY 랭킹 등장 제품 수', `브랜드 상위 12 · ${last.week} 기준`, '<div style="position:relative;height:300px"><canvas id="retail"></canvas></div>')}
      </div>
      ${card('회사별 OY 랭킹 등장 제품 수', `회사 정보가 등록된 브랜드 기준 · ${last.week}. SNS 대화 점유와 리테일 존재감을 나란히 봅니다.`,
             co.length ? `<div class="tbl-wrap"><table><thead><tr><th>회사</th><th class="num">랭킹 제품</th><th>브랜드</th></tr></thead><tbody>
               ${co.map(([c, [n, bs]]) => `<tr><td><b>${esc(c)}</b></td><td class="num">${n}</td><td class="muted">${esc(bs.slice(0, 6).join(', '))}${bs.length > 6 ? ' …' : ''}</td></tr>`).join('')}</tbody></table></div>` : empty('데이터 없음'))}`;
    return [html, () => {
      const hbar = (id, labels, data, unit) => chart(document.getElementById(id), {
        type: 'bar', data: { labels, datasets: [{ data, backgroundColor: ACCENT, borderRadius: 4 }] },
        options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
          tooltip: { callbacks: { label: c => c.raw + unit } } }, scales: { x: { beginAtZero: true, grid: { color: '#f1f3f5' } }, y: { grid: { display: false } } } } });
      hbar('share', top.slice(0, 10).map(t => D.brand_names[t[0]] || t[0]), top.slice(0, 10).map(t => +(t[1] / all * 100).toFixed(1)), '%');
      hbar('retail', rk.slice(0, 12).map(t => D.brand_names[t[0]] || t[0]), rk.slice(0, 12).map(t => t[1]), '개');
    }];
  }

  // ── 페이지: 제품·VOC ──
  let quadFilter = 'ALL', prodSearch = '';
  function pageProducts(mode, key) {
    const ws = periodWeeks(mode, key);
    const w = ws[ws.length - 1];
    const ps = w.products;
    if (!ps.length) return [empty('제품 인사이트 없음'), null];
    const counts = {}; ps.forEach(p => counts[p.quad] = (counts[p.quad] || 0) + 1);
    const qdesc = { PUSH_NOW: '긍정 + 별점 상승 → 즉시 집행', FIX_AND_PUSH: '불만 신호(별점↓·부정↑) → 개선 후 강화', HOLD: '긍정이나 정체 → 유지·관찰', MONITOR: '그 외 → 관망' };
    const shown = ps.filter(p => (quadFilter === 'ALL' || p.quad === quadFilter) &&
      (!prodSearch || `${p.name} ${p.brand}`.toLowerCase().includes(prodSearch)));
    const trend = v => v === null || v === undefined ? '<span class="muted">—</span>' : v > 0 ? `<span style="color:var(--primary)">▲ ${v.toFixed(2)}</span>` : v < 0 ? `▼ ${v.toFixed(2)}` : '±0';
    const kwRows = shown.filter(p => p.pos_kw.length || p.neg_kw.length || p.needs_kw.length);
    const html = `${snapNote(mode, ws)}
      <div class="grid4" style="margin-bottom:16px">${Object.keys(META.quad).map(q => `<div class="stat ${quadFilter === q ? 'on' : ''}" data-q="${q}">
        <div class="n" style="color:${quadMeta(q).color}">${counts[q] || 0}</div><div class="t">${esc(quadMeta(q).label)}</div><div class="d">${qdesc[q]}</div></div>`).join('')}</div>
      ${card('전략 분포 (별점 추세 × VOC 긍정도)', '가로 = 플랫폼 평균 별점의 주간 변화(0 기준) · 세로 = 리뷰 긍정 비율(0.65 기준) · 점 색 = 전략 분류. 별점 추세가 없는 제품은 가로 0에 놓입니다.',
             '<div style="position:relative;height:340px"><canvas id="quad"></canvas></div>')}
      <section class="card"><div class="toolbar"><h3 style="margin:0 8px 0 0">제품 ${shown.length}개</h3>
        <input id="psearch" placeholder="제품·브랜드 검색" value="${esc(prodSearch)}">${quadFilter !== 'ALL' ? `<span class="pill">${esc(quadMeta(quadFilter).label)} 필터 · 카드를 다시 누르면 해제</span>` : ''}</div>
        <div class="tbl-wrap"><table><thead><tr><th>제품</th><th>브랜드</th><th>분류</th><th class="num">VOC 긍정</th><th class="num">부정</th><th class="num">별점 추세</th>
          <th class="num">리뷰 유입</th><th class="num">OY 주문</th><th class="num">OY 한국</th><th>전략 요약</th></tr></thead><tbody>
          ${shown.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.brand)}</td><td>${pill(quadMeta(p.quad))}</td>
            <td class="num">${pct(p.pos)}</td><td class="num">${pct(p.neg)}</td><td class="num">${trend(p.rt)}</td><td class="num">${fmt(p.vel)}</td>
            <td class="num">${p.oy_orders ?? '—'}</td><td class="num">${p.oy_korea ?? '—'}</td><td class="muted" style="max-width:260px">${esc(p.action.slice(0, 90))}</td></tr>`).join('')}
        </tbody></table></div></section>
      ${card('제품별 VOC 키워드', '리뷰 감성 분석에서 뽑은 긍정·부정·개선 니즈 키워드',
             kwRows.length ? `<div class="tbl-wrap"><table><thead><tr><th>제품</th><th>긍정</th><th>부정</th><th>개선 니즈</th><th>리테일</th></tr></thead><tbody>
               ${kwRows.map(p => `<tr><td><b>${esc(p.name)}</b><div class="muted">${esc(p.brand)}</div></td>
                 <td>${p.pos_kw.map(k => `<span class="chip">${esc(k)}</span>`).join('')}</td>
                 <td>${p.neg_kw.map(k => `<span class="chip out">${esc(k)}</span>`).join('')}</td>
                 <td>${p.needs_kw.map(k => `<span class="chip out">${esc(k)}</span>`).join('')}</td>
                 <td>${p.oy_url ? `<a href="${esc(p.oy_url)}" target="_blank" rel="noopener">OY</a>` : ''} ${p.sephora_url ? `<a href="${esc(p.sephora_url)}" target="_blank" rel="noopener">Sephora</a>` : ''}</td></tr>`).join('')}
             </tbody></table></div>` : empty('키워드 없음'))}`;
    return [html, () => {
      document.querySelectorAll('.stat[data-q]').forEach(el => el.onclick = () => { quadFilter = quadFilter === el.dataset.q ? 'ALL' : el.dataset.q; render(); });
      const inp = document.getElementById('psearch');
      inp.oninput = () => { prodSearch = inp.value.toLowerCase().trim(); render(); const n = document.getElementById('psearch'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); };
      const lines = { id: 'lines', beforeDraw(c) { const { ctx, chartArea: a, scales: s } = c; if (!a) return;
        const x0 = s.x.getPixelForValue(0), y0 = s.y.getPixelForValue(0.65); ctx.save(); ctx.strokeStyle = '#ced4da'; ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(x0, a.top); ctx.lineTo(x0, a.bottom); ctx.moveTo(a.left, y0); ctx.lineTo(a.right, y0); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = '#adb5bd'; ctx.font = '11px Pretendard'; ctx.fillText('PUSH NOW (별점↑ · 긍정)', x0 + 6, a.top + 14);
        ctx.fillText('FIX & PUSH (별점↓)', a.left + 6, a.top + 14); ctx.restore(); } };
      const pts = ps.map(p => ({ x: p.rt ?? 0, y: p.pos ?? 0, p }));
      chart(document.getElementById('quad'), { type: 'scatter',
        data: { datasets: [{ data: pts, pointRadius: 5, pointBackgroundColor: pts.map(q => q.p.pos === null ? '#e9ecef' : quadMeta(q.p.quad).color) }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false },
          tooltip: { callbacks: { label: c => `${c.raw.p.brand} · ${c.raw.p.name} (별점 변화 ${c.raw.x}, VOC ${c.raw.p.pos ?? 'N/A'})` } } },
          scales: { x: { title: { display: true, text: '별점 추세 (주간 변화)' } }, y: { min: 0, max: 1, title: { display: true, text: 'VOC 긍정도' } } } },
        plugins: [lines] });
    }];
  }

  // ── 페이지: 방한 추천 ──
  function pageInbound(mode, key) {
    const ws = periodWeeks(mode, key);
    const rows = ws[ws.length - 1].inbound;
    if (!rows.length) return [snapNote(mode, ws) + empty('방한 추천 데이터 없음'), null];
    const bar = v => `<div style="height:4px;background:#f1f3f5;border-radius:2px;margin-top:4px"><div style="width:${Math.round(v * 100)}%;height:4px;background:${ACCENT};border-radius:2px"></div></div>`;
    return [`${snapNote(mode, ws)}
      <div class="grid3" style="margin-bottom:16px">${rows.slice(0, 3).map(r => `<div class="kpi"><div class="label">#${r.rank} · ${esc(r.brand)}</div>
        <div style="font-weight:700;margin:6px 0">${esc(r.name)}</div><div class="val" style="color:var(--primary)">${r.score.toFixed(3)}</div>${bar(r.score)}</div>`).join('')}</div>
      ${card('방한 추천 Top 15', '한국을 찾는 해외 소비자가 쇼핑리스트에 넣을 만한 제품. Pick Score = 0.35 × SNS 트렌드 강도 + 0.35 × VOC 긍정도 + 0.30 × 리테일 인기',
        `<div class="tbl-wrap"><table><thead><tr><th>순위</th><th>제품</th><th>브랜드</th><th class="num">OY 한국</th><th class="num">OY 주문</th><th class="num">VOC</th><th class="c">SNS</th><th class="num">Score</th><th>이유</th></tr></thead><tbody>
        ${rows.map(r => `<tr><td><b>#${r.rank}</b></td><td><b>${esc(r.name)}</b><div class="muted">${esc(r.kw.join(' · '))}</div></td><td>${esc(r.brand)}</td>
          <td class="num">${r.korea ?? '—'}</td><td class="num">${r.orders ?? '—'}</td><td class="num">${pct(r.voc)}</td><td class="c">${r.sns ? '●' : ''}</td>
          <td class="num">${r.score.toFixed(3)}${bar(r.score)}</td><td class="muted" style="max-width:280px">${esc(r.reason.slice(0, 110))}</td></tr>`).join('')}
        </tbody></table></div>`)}`, null];
  }

  // ── 페이지: 검증·품질 ──
  function pageQuality(mode, key) {
    const ws = periodWeeks(mode, key);
    const v = valid(ws);
    const plat = {}; ws.forEach(w => Object.entries(w.platforms).forEach(([k, n]) => plat[k] = (plat[k] || 0) + n));
    const ret = {}; ws.forEach(w => Object.entries(w.retail_platforms).forEach(([k, n]) => ret[k] = (ret[k] || 0) + n));
    const pname = { tiktok: 'TikTok', youtube: 'YouTube', oy_top_orders: 'OY Global 주문 랭킹', oy_top_korea: 'OY 한국 인기 랭킹', sephora_us: 'Sephora US' };
    const vw = WEEKS.filter(w => w.validation.rule || w.validation.llm);
    const fails = ws.flatMap(w => w.fails.map(f => ({ ...f, week: w.week })));
    return [`
      <div class="kpis">
        <div class="kpi"><div class="label">규칙 검사 통과율</div><div class="val">${pct(v.ruleRate)}</div><div class="delta">인용·브랜드 수·한국어 (비용 0)</div></div>
        <div class="kpi"><div class="label">LLM 판정 통과율</div><div class="val">${pct(v.llmRate)}</div><div class="delta">${esc(v.model || '판정 기록 없음')}</div></div>
        <div class="kpi"><div class="label">검토 필요</div><div class="val">${v.fails}</div><div class="delta">규칙 ${v.rule.fail} · LLM ${v.llm.fail}</div></div>
        <div class="kpi"><div class="label">경고</div><div class="val">${v.rule.warn + v.llm.warn}</div><div class="delta">근거 부족·미등록 브랜드</div></div>
      </div>
      <div class="grid2">
        ${card('주차별 검증 통과율', '검증 단계가 들어간 주차부터 표시합니다.', vw.length ? '<div style="position:relative;height:260px"><canvas id="vchart"></canvas></div>' : empty('검증 기록 없음'))}
        ${card('수집량', '선택한 기간에 수집한 건수', `<table><tbody>
          ${Object.entries(plat).map(([k, n]) => `<tr><td>${pname[k] || esc(k)} 게시물</td><td class="num">${fmt(n)}</td></tr>`).join('')}
          ${Object.entries(ret).map(([k, n]) => `<tr><td>${pname[k] || esc(k)}</td><td class="num">${fmt(n)}</td></tr>`).join('')}</tbody></table>`)}
      </div>
      ${card('검증 단계', '', `<ol class="method" style="padding-left:18px">
        <li><b>데이터 품질</b> — 플랫폼별 수집 건수와 빈 본문 비율. 기준에 못 미치면 LLM 단계를 돌리지 않습니다.</li>
        <li><b>규칙 검사</b> — LLM이 인용한 문장이 실제 캡션에 있는지, 언급 브랜드 수가 맞는지, 니즈가 한국어인지 문자열로 대조합니다.</li>
        <li><b>LLM 판정</b> — 생성 모델(Sonnet)과 다른 모델(Opus)이 같은 캡션을 보고 주장마다 지지 / 근거 부족 / 모순으로 판정합니다.</li></ol>`)}
      ${card(`실패 항목 ${fails.length}건`, '', fails.length ? `<div class="tbl-wrap"><table><thead><tr><th>주차</th><th>키워드</th><th>검사</th><th>대상</th><th>사유</th><th>판정</th></tr></thead><tbody>
        ${fails.map(f => `<tr><td>${f.week}</td><td>${kwLabel(f.keyword)}</td><td>${esc(f.check_type)}</td><td>${esc(f.target)}</td><td>${esc(f.detail)}</td><td>${f.checker === 'rule' ? '규칙' : 'LLM'}</td></tr>`).join('')}
        </tbody></table></div>` : empty('없음'))}`, () => {
      if (!vw.length) return;
      const r = w => { const x = valid([w]); return [x.ruleRate, x.llmRate].map(v => v === null ? null : +(v * 100).toFixed(1)); };
      chart(document.getElementById('vchart'), { type: 'line',
        data: { labels: vw.map(w => w.week.slice(5)), datasets: [
          { label: '규칙', data: vw.map(w => r(w)[0]), borderColor: '#495057', tension: .3 },
          { label: 'LLM 판정', data: vw.map(w => r(w)[1]), borderColor: ACCENT, tension: .3 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } }, scales: { y: { min: 0, max: 100, ticks: { callback: v => v + '%' } } } } });
    }];
  }

  // ── 페이지: 방법론 ──
  function pageMethod() {
    return [`<div class="method">
      ${card('데이터', '', `<p>TikTok 해시태그 영상(Apify, 미국 프록시 + 영어 캡션), YouTube 키워드 영상(Data API, regionCode=US), Olive Young Global 랭킹·리뷰(Playwright), Sephora US 리뷰(Bazaarvoice). 매주 목요일 Airflow가 수집합니다.</p>`)}
      ${card('트렌드 판정', '', `<p><code>engagement</code> TikTok 조회×0.3 + 좋아요×1.0 + 댓글×2.0 · YouTube 조회×0.2 + 좋아요×1.5 + 댓글×3.0</p>
        <p><code>z-score</code> (이번 주 − 과거 주차 평균) ÷ 표준편차. 과거 데이터 3개 주차 이상일 때 계산</p>
        <p><code>모멘텀</code> 이번 주 ÷ 데이터가 있는 3번째 전 주차</p>
        <p>후보 = 순간 급등(z ≥ 2.0) ∪ 지속 상승(3개 주차 연속 상승 + 모멘텀 ≥ 1.5) → 광의어·브랜드명 제외 → 게시물 ≥ 5, 캡션 ≥ 3 → 최대 10개</p>`)}
      ${card('대화 점유 (기회 유형)', '', `<p>캡션 속 K-뷰티 브랜드 언급 기준. 판매처·사람 이름·서구 브랜드는 제외합니다.</p>
        <p><b>선점</b> 1위 브랜드 ≥ 50% 그리고 ≥ 3건 · <b>각축</b> 과반 브랜드 없음 · <b>공백</b> K-브랜드 언급 없음 (맞는 제품이 있으면 소통 공백, 없으면 제품 공백)</p>`)}
      ${card('제품 전략 (VOC 4분면)', '', `<p>긍정 = VOC 긍정률 ≥ 0.65 또는 리테일 점수 ≥ 0.7 · 상승 = 별점 상승 · 불만 = 별점 하락 또는 부정 ≥ 0.35</p>
        <p><b>PUSH NOW</b> 긍정 + 상승 + 불만 없음 · <b>FIX & PUSH</b> 불만 · <b>HOLD</b> 긍정이나 정체 · <b>MONITOR</b> 그 외</p>
        <p>리뷰 유입(누적 리뷰 증가분)은 거의 항상 양수라 분류에 쓰지 않고 표시만 합니다.</p>`)}
      ${card('방한 추천', '', `<p>Pick Score = 0.35 × SNS 트렌드 강도 + 0.35 × VOC 긍정도 + 0.30 × 리테일 인기. 리테일 점수 = max(0, 1 − (순위 − 1) ÷ 100)</p>`)}
      ${card('월간 보기', '', `<p>새 판정 없이 주차 결과를 묶습니다. 게시물·언급은 합계, 트렌드는 합집합, 검증은 월 전체 통과율, 제품·방한 추천은 월의 마지막 데이터 주차 기준입니다.</p>`)}
    </div>`, null];
  }

  const RENDER = { overview: pageOverview, trends: pageTrends, brands: pageBrands, products: pageProducts, inbound: pageInbound, quality: pageQuality, method: pageMethod };

  // ── 렌더 ──
  function render() {
    charts.forEach(c => c.destroy()); charts = [];
    if (!state.key) { document.getElementById('content').innerHTML = empty('데이터 없음'); return; }
    document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('on', a.dataset.page === state.page));
    document.querySelectorAll('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.mode === state.mode));
    const sel = document.getElementById('periodSelect');
    sel.innerHTML = periodKeys(state.mode).slice().reverse().map(k => `<option value="${k}" ${k === state.key ? 'selected' : ''}>
      ${state.mode === 'week' ? `${k} · ${esc(WEEK[k].label)}` : monthLabel(k)}</option>`).join('');
    const ws = periodWeeks(state.mode, state.key);
    document.getElementById('pageTitle').textContent = PAGES[state.page];
    document.getElementById('periodLabel').textContent = state.mode === 'week'
      ? `${ws[0].label}` : `${monthLabel(state.key)} · 데이터 주차 ${ws.map(w => w.week.slice(5)).join(', ')}`;
    const v = valid(ws);
    document.getElementById('trustBadge').innerHTML = v.rate === null ? '검증 기록 없음'
      : `검증 통과 <b>${pct(v.rate)}</b> · 규칙 ${pct(v.ruleRate)} · LLM ${pct(v.llmRate)}`;
    document.getElementById('banner').innerHTML = ws.some(w => w.legacy)
      ? '<div class="banner">이 기간에는 이전 기준(아모레퍼시픽 관점)으로 분석된 주차가 포함되어 있습니다. 기회 유형 이름이 다를 수 있습니다.</div>' : '';
    document.getElementById('sideFoot').innerHTML = `데이터 갱신 ${esc(D.generated)}<br>주차 ${WEEKS.length}개 · TikTok · YouTube · OY Global · Sephora`;
    const [html, after] = RENDER[state.page](state.mode, state.key);
    document.getElementById('content').innerHTML = html;
    if (after) after();
  }

  // ── 이벤트 ──
  document.querySelectorAll('#nav a').forEach(a => a.onclick = () => go({ page: a.dataset.page }));
  document.querySelectorAll('#modeSeg button').forEach(b => b.onclick = () => {
    if (b.dataset.mode === state.mode) return;
    const ws = periodWeeks(state.mode, state.key);
    const last = ws[ws.length - 1];
    go(b.dataset.mode === 'month' ? { mode: 'month', key: last.month } : { mode: 'week', key: last.week });
  });
  document.getElementById('periodSelect').onchange = e => go({ key: e.target.value });

  readUrl();
  writeUrl(false);
  render();
})();
