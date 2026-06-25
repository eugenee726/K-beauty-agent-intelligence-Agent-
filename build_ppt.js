const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";            // 13.33 x 7.5
pres.author = "박유진";
pres.title = "K-Beauty Intelligence Agent";

// ── 팔레트 (K-beauty premium) ──
const C = {
  plum:    "2B1B33",   // deep plum (dark bg)
  plum2:   "3D2647",
  magenta: "E91E8C",   // accent
  purple:  "6C4AB6",
  ink:     "1A1A2E",   // dark text
  muted:   "6B6B7B",
  line:    "E3DCE8",
  tintP:   "FCE9F2",   // magenta tint
  tintV:   "EEE8F7",   // purple tint
  white:   "FFFFFF",
  offwhite:"FAF7FB",
};
const F = "Malgun Gothic";              // 한글 안전 폰트 (한국 Windows 기본)
const W = 13.33, H = 7.5;

const shadow = () => ({ type: "outer", color: "000000", blur: 7, offset: 2, angle: 90, opacity: 0.10 });

// 스텝 라벨 (01 · PROBLEM)
function stepBadge(slide, num, label, dark) {
  const col = dark ? C.white : C.ink;
  slide.addText([
    { text: num + "  ", options: { color: C.magenta, bold: true } },
    { text: label, options: { color: col, bold: true } },
  ], { x: 0.6, y: 0.45, w: 8, h: 0.45, fontFace: F, fontSize: 15, charSpacing: 2, margin: 0 });
}

// ════════════════════════════════════════════════
// SLIDE 1 — 문제 정의
// ════════════════════════════════════════════════
let s1 = pres.addSlide();
s1.background = { color: C.plum };
stepBadge(s1, "01", "PROBLEM  ·  문제 정의", true);

s1.addText("K-Beauty Intelligence Agent", {
  x: 0.6, y: 1.05, w: 12, h: 0.5, fontFace: F, fontSize: 16, color: "C9A8D6", margin: 0 });

s1.addText("수백 개 제품의 트렌드를,\n사람이 매주 따라잡을 수 있을까?", {
  x: 0.6, y: 1.5, w: 8.1, h: 1.7, fontFace: F, fontSize: 33, bold: true, color: C.white, lineSpacing: 42, margin: 0 });

s1.addText("해외 K-뷰티의 SNS·리테일·리뷰 반응을 AI가 분석해, \"어떤 제품을 왜 지금 밀어야 하는가\"를\n근거와 함께 매주 자동으로 제안하는 멀티 에이전트", {
  x: 0.62, y: 3.25, w: 8.0, h: 0.9, fontFace: F, fontSize: 13.5, color: "D9C7E2", lineSpacing: 20, margin: 0 });

// Signal Gap 다이어그램 (우측)
const gx = 9.15, gw = 3.55;
s1.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: gx, y: 1.55, w: gw, h: 1.0, fill: { color: C.plum2 }, line: { color: C.magenta, width: 1 }, rectRadius: 0.08 });
s1.addText([{ text: "SNS 트렌드 신호\n", options: { bold: true, color: C.white, fontSize: 14 } }, { text: "TikTok · YouTube", options: { color: "B89BC7", fontSize: 11 } }],
  { x: gx, y: 1.6, w: gw, h: 0.9, fontFace: F, align: "center", valign: "middle", lineSpacing: 18 });

s1.addText("⇅  불일치 (Gap)", { x: gx, y: 2.62, w: gw, h: 0.5, fontFace: F, fontSize: 14, bold: true, color: C.magenta, align: "center", margin: 0 });

s1.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: gx, y: 3.15, w: gw, h: 1.0, fill: { color: C.plum2 }, line: { color: C.purple, width: 1 }, rectRadius: 0.08 });
s1.addText([{ text: "리테일 구매 신호\n", options: { bold: true, color: C.white, fontSize: 14 } }, { text: "OY Global · Sephora", options: { color: "B89BC7", fontSize: 11 } }],
  { x: gx, y: 3.2, w: gw, h: 0.9, fontFace: F, align: "center", valign: "middle", lineSpacing: 18 });

// 3 stat callouts (하단)
const stats = [
  ["+37%", "美 K-뷰티 시장 성장 (2025)"],
  ["美 > 中", "AP 미국 매출, 중국 첫 추월"],
  ["80%", "방한 외국인의 올리브영 구매"],
];
let sx = 0.6;
const cardW = 4.0, gap = 0.13;
stats.forEach(([big, lbl]) => {
  s1.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: sx, y: 4.55, w: cardW, h: 1.25, fill: { color: C.plum2 }, rectRadius: 0.08 });
  s1.addText(big, { x: sx + 0.05, y: 4.68, w: cardW - 0.1, h: 0.62, fontFace: F, fontSize: 28, bold: true, color: C.magenta, align: "center", margin: 0 });
  s1.addText(lbl, { x: sx + 0.05, y: 5.32, w: cardW - 0.1, h: 0.4, fontFace: F, fontSize: 12, color: "D9C7E2", align: "center", margin: 0 });
  sx += cardW + gap;
});

// 하단 서사 리본
s1.addText([
  { text: "AP AI Innovation Challenge 2026", options: { color: "B89BC7" } },
  { text: "  ·  6인 팀장으로 기획 → ", options: { color: "B89BC7" } },
  { text: "활용 한계를 진단해 개인 프로젝트로 전면 재설계", options: { color: C.white, bold: true } },
], { x: 0.6, y: 6.15, w: 12.1, h: 0.45, fontFace: F, fontSize: 13, align: "center", margin: 0 });

s1.addText("13개 브랜드 × 700여 개 제품 × 4개 플랫폼을 수동 교차검증하는 것은 구조적으로 불가능하다", {
  x: 0.6, y: 6.62, w: 12.1, h: 0.4, fontFace: F, fontSize: 12, italic: true, color: "8E7A9B", align: "center", margin: 0 });

// ════════════════════════════════════════════════
// SLIDE 2 — 가설 설정
// ════════════════════════════════════════════════
let s2 = pres.addSlide();
s2.background = { color: C.white };
stepBadge(s2, "02", "HYPOTHESIS  ·  가설 설정", false);

s2.addText("정답 라벨이 없는 문제 → 검증 가능한 \"설계 가설\"로 전환", {
  x: 0.6, y: 1.0, w: 12.1, h: 0.6, fontFace: F, fontSize: 25, bold: true, color: C.ink, margin: 0 });
s2.addText("\"이 트렌드가 진짜 기회였는가\"의 정답 데이터는 없다. 그래서 정확도가 아니라, 기존 방식의 한계를 진단하고 더 나은 신호를 가설로 세워 검증했다.", {
  x: 0.6, y: 1.62, w: 12.1, h: 0.5, fontFace: F, fontSize: 13.5, color: C.muted, margin: 0 });

const hyps = [
  ["H1 · 트렌드 탐지", "7일 단순 성장률은 베이스가 작으면 비율이 폭발하고, 통계적 유의성 판단이 없다.",
   "직전 4주 평균 대비 Z-score + 3주 모멘텀이 \"노이즈 vs 진짜 급등\"을 더 정확히 구분할 것이다.", C.magenta, C.tintP],
  ["H2 · 제품 반응", "리테일 순위는 단일 플랫폼 의존·결측이 많은 후행 지표다. VOC 100개 고정 샘플은 반응 규모를 지운다.",
   "전체 리뷰 수의 주간 증감(유입 속도)이 실제 소비자 활동을 더 빠르게 포착하는 선행 신호일 것이다.", C.purple, C.tintV],
  ["H3 · 실행 연결", "키워드·브랜드 수준 연결과 단순 연결 여부(0/1)만으로는 담당자가 바로 실행하기 어렵다.",
   "SKU 단위 매핑 + SNS 트렌드 강도(정규화)가 \"무엇을 왜\"까지 짚어 실행 가능성을 높일 것이다.", C.magenta, C.tintP],
];
let hy = 2.35;
const hH = 1.45, hgap = 0.18;
hyps.forEach(([tag, obs, hyp, col, tint]) => {
  s2.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y: hy, w: 12.13, h: hH, fill: { color: C.offwhite }, line: { color: C.line, width: 1 }, rectRadius: 0.06, shadow: shadow() });
  // 태그
  s2.addText(tag, { x: 0.85, y: hy + 0.18, w: 2.7, h: 1.1, fontFace: F, fontSize: 15, bold: true, color: col, valign: "top", margin: 0 });
  // 관찰 → 가설
  s2.addText([
    { text: "관찰   ", options: { bold: true, color: C.muted, fontSize: 11 } },
    { text: obs + "\n", options: { color: C.ink, fontSize: 12.5, breakLine: true } },
    { text: "가설   ", options: { bold: true, color: col, fontSize: 11 } },
    { text: hyp, options: { color: C.ink, fontSize: 12.5, bold: true } },
  ], { x: 3.5, y: hy + 0.16, w: 9.05, h: hH - 0.3, fontFace: F, valign: "middle", lineSpacing: 17, margin: 0, paraSpaceAfter: 4 });
  hy += hH + hgap;
});

// ════════════════════════════════════════════════
// SLIDE 3 — 실험 설계
// ════════════════════════════════════════════════
let s3 = pres.addSlide();
s3.background = { color: C.white };
stepBadge(s3, "03", "METHOD  ·  실험 설계", false);

s3.addText("가설을 검증하기 위한 시스템 — 무엇을, 어떻게 바꿨나", {
  x: 0.6, y: 1.0, w: 12.1, h: 0.6, fontFace: F, fontSize: 25, bold: true, color: C.ink, margin: 0 });

// 파이프라인 흐름
const steps = ["데이터 수집\n4개 플랫폼", "통계·LLM 분석\nz-score·모멘텀", "3대 의사결정\nTrend·Product·Inbound", "자동 리포트\n3-탭 HTML"];
let px = 0.6; const pW = 2.78, pgap = 0.36, pY = 1.72;
steps.forEach((t, i) => {
  s3.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: px, y: pY, w: pW, h: 0.92, fill: { color: i % 2 ? C.tintV : C.tintP }, rectRadius: 0.06 });
  s3.addText(t, { x: px, y: pY, w: pW, h: 0.92, fontFace: F, fontSize: 12, bold: true, color: C.ink, align: "center", valign: "middle", lineSpacing: 15, margin: 0 });
  if (i < steps.length - 1) s3.addText("→", { x: px + pW, y: pY, w: pgap, h: 0.92, fontFace: F, fontSize: 18, bold: true, color: C.magenta, align: "center", valign: "middle", margin: 0 });
  px += pW + pgap;
});
s3.addText("Agentic Orchestrator — Claude가 DB 상태를 보고 필요한 단계만 판단·실행 (불필요한 재수집 방지)", {
  x: 0.6, y: 2.66, w: 12.1, h: 0.35, fontFace: F, fontSize: 11.5, italic: true, color: C.muted, align: "center", margin: 0 });

// Before → After 표
const rows = [
  ["트렌드 탐지", "7일 단순 성장률", "Z-score + 3주 모멘텀", "통계적 이상치 + 지속성 분리"],
  ["제품 반응", "VOC 100개 고정 샘플", "전체 리뷰 유입 속도(velocity)", "실제 소비자 활동 측정"],
  ["제품 매칭", "difflib 문자열 유사도", "rapidfuzz + SKU 매핑", "표기·순서 차이에 견고"],
  ["의사결정", "규칙 기반 분류", "Agentic + 우선순위 점수", "자원 집중 자동 정렬"],
];
const tX = 0.6, tY = 3.25, colW = [2.2, 3.35, 3.6, 2.98];
const head = ["구분", "기존 (팀 버전)", "재설계 (개인)", "왜"];
function rowCells(cells, isHead) {
  return cells.map((c, i) => ({
    text: c,
    options: {
      fontFace: F, fontSize: isHead ? 13 : 12.5, bold: isHead || i === 0 || i === 2,
      color: isHead ? C.white : (i === 2 ? C.magenta : C.ink),
      fill: { color: isHead ? C.plum : (i === 1 ? "F4F0F6" : i === 2 ? C.tintP : C.white) },
      align: i === 0 ? "center" : "left", valign: "middle",
      margin: [3, 6, 3, 6],
    },
  }));
}
const tableData = [rowCells(head, true), ...rows.map(r => rowCells(r, false))];
s3.addTable(tableData, { x: tX, y: tY, w: 12.13, colW, rowH: [0.45, 0.62, 0.62, 0.62, 0.62],
  border: { type: "solid", pt: 1, color: C.line } });

s3.addText([
  { text: "화이트박스 원칙   ", options: { bold: true, color: C.purple } },
  { text: "모든 점수를 공개 수식으로 직접 설계 — \"왜 이 제품이 PUSH NOW인가\"를 누구나 추적 가능", options: { color: C.ink } },
], { x: 0.6, y: 6.55, w: 12.1, h: 0.5, fontFace: F, fontSize: 13, align: "center", valign: "middle", margin: 0 });

// ════════════════════════════════════════════════
// SLIDE 4 — 결과
// ════════════════════════════════════════════════
let s4 = pres.addSlide();
s4.background = { color: C.plum };
stepBadge(s4, "04", "RESULTS  ·  결과 & 검증", true);

s4.addText("근거와 함께, 매주 자동으로 — 3가지 실행형 인사이트", {
  x: 0.6, y: 1.0, w: 12.1, h: 0.6, fontFace: F, fontSize: 25, bold: true, color: C.white, margin: 0 });

// 3 output cards
const outs = [
  ["📈", "트렌드 인사이트", "급등 키워드를 기회 유형 × 트렌드 모양 2축으로 해석 + SKU 연결"],
  ["🛍️", "제품 전략", "AP 제품을 VOC 모멘텀(리뷰 유입·별점 추세)으로 4분면 분류"],
  ["✈️", "방한 추천", "\"해외 화제 + 실제 만족 + 현지 인기\"로 방한 쇼핑 Top 15"],
];
let ox = 0.6; const oW = 4.0, ogap = 0.13, oY = 1.75;
outs.forEach(([ic, ti, de]) => {
  s4.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: ox, y: oY, w: oW, h: 1.85, fill: { color: C.white }, rectRadius: 0.08, shadow: shadow() });
  s4.addText(ic, { x: ox + 0.25, y: oY + 0.22, w: 0.9, h: 0.7, fontFace: F, fontSize: 30, margin: 0 });
  s4.addText(ti, { x: ox + 1.05, y: oY + 0.3, w: oW - 1.2, h: 0.55, fontFace: F, fontSize: 16, bold: true, color: C.ink, valign: "middle", margin: 0 });
  s4.addText(de, { x: ox + 0.28, y: oY + 1.0, w: oW - 0.55, h: 0.75, fontFace: F, fontSize: 12, color: C.muted, lineSpacing: 16, margin: 0 });
  ox += oW + ogap;
});

// 성과 지표 줄
const metrics = [["5", "수집 플랫폼"], ["13", "AP 브랜드"], ["721", "제품 카탈로그"], ["주간", "무인 자동 실행"]];
let mx = 0.6; const mW = 3.0, mgap = 0.105, mY = 3.95;
metrics.forEach(([big, lbl]) => {
  s4.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: mx, y: mY, w: mW, h: 1.05, fill: { color: C.plum2 }, rectRadius: 0.07 });
  s4.addText(big, { x: mx, y: mY + 0.12, w: mW, h: 0.55, fontFace: F, fontSize: 26, bold: true, color: C.magenta, align: "center", margin: 0 });
  s4.addText(lbl, { x: mx, y: mY + 0.68, w: mW, h: 0.32, fontFace: F, fontSize: 12, color: "D9C7E2", align: "center", margin: 0 });
  mx += mW + mgap;
});

// 검증 + 차별점
s4.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.6, y: 5.28, w: 12.13, h: 1.55, fill: { color: C.plum2 }, rectRadius: 0.08 });
s4.addText([
  { text: "검증   ", options: { bold: true, color: C.magenta, fontSize: 12 } },
  { text: "SNS 트렌드 강도가 높은 제품이 판매 1위(SNS 무연결)를 앞서며, \"지금 해외에서 화제인 제품\"이 상위로 정렬됨을 확인.\n", options: { color: C.white, fontSize: 12.5, breakLine: true } },
  { text: "차별점   ", options: { bold: true, color: C.magenta, fontSize: 12 } },
  { text: "정답이 없을수록 \"판단 근거의 투명성\"이 신뢰를 만든다 — 블랙박스가 아닌, 객관적 사실에 근거해 AP의 기획·전략·개발을 독려하는 화이트박스 에이전트.", options: { color: C.white, fontSize: 12.5 } },
], { x: 0.9, y: 5.45, w: 11.5, h: 1.25, fontFace: F, valign: "middle", lineSpacing: 19, margin: 0, paraSpaceAfter: 5 });

pres.writeFile({ fileName: "K-Beauty_Agent_Portfolio.pptx" }).then(() => console.log("DONE"));
