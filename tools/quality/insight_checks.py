"""
insight_checks.py
──────────────────
LLM 인사이트 출력의 결정적(규칙 기반) 검증. LLM 호출 없음, 비용 0.

검증 3단 구조 중 ②단계 (①=data_quality.py 수집 품질, ③=LLM 판정 검증 에이전트):
  - direct_quote     : 직접 연결 제품의 인용(evidence)이 LLM 이 본 캡션에 실제로 있는가
  - indirect_reason  : 간접 연결 제품에 근거 문장이 있는가
  - brand_count      : brand_mentions 의 브랜드가 캡션에 실제로 등장하고, 개수가 크게 틀리지 않는가
  - known_brand      : 언급 브랜드가 dim_brand 에 있는가 (없으면 사람 이름 등일 수 있어 경고)
  - korean_need      : consumer_need 가 한국어로 작성됐는가

결과는 fact_insight_validations 에 저장한다 (LLM 판정 결과도 같은 테이블에 쌓인다).
verdict: pass | warn | fail
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field

from tools.analyze.llm_insight_tool import sample_captions
from tools.collect.brand_registry import load_brands

RULE_CHECKER = "rule"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fact_insight_validations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    week        TEXT NOT NULL,
    keyword     TEXT NOT NULL,
    check_type  TEXT NOT NULL,     -- direct_quote | brand_count | ... | llm_claim
    target      TEXT,              -- 검사 대상 (product_id, 브랜드명, 주장 문장 등)
    verdict     TEXT NOT NULL,     -- pass | warn | fail
    detail      TEXT,              -- 판정 이유
    checker     TEXT NOT NULL,     -- 'rule' 또는 판정 모델 ID
    created_at  TEXT DEFAULT (datetime('now'))
)
"""


@dataclass
class CheckResult:
    keyword: str
    check_type: str
    target: str
    verdict: str
    detail: str = ""


@dataclass
class WeekReport:
    week: str
    results: list = field(default_factory=list)

    def counts(self) -> dict:
        out = {"pass": 0, "warn": 0, "fail": 0}
        for r in self.results:
            out[r.verdict] += 1
        return out

    @property
    def fail_rate(self) -> float:
        return self.counts()["fail"] / len(self.results) if self.results else 0.0


def _norm(text: str) -> str:
    """대소문자·공백·따옴표·말줄임 차이를 흡수한 비교용 문자열."""
    t = (text or "").lower()
    t = re.sub(r"[“”‘’\"'`]", "", t)
    t = t.replace("…", "").replace("...", "")
    return re.sub(r"\s+", " ", t).strip()


def _quote_found(quote: str, captions_norm: list[str]) -> bool:
    q = _norm(quote)
    if not q:
        return False
    if any(q in c for c in captions_norm):
        return True
    # LLM 이 인용을 살짝 줄이거나 이어 붙이는 경우: 6단어 이상 연속 구간이 하나라도 있으면 인정
    words = q.split()
    if len(words) >= 6:
        for i in range(len(words) - 5):
            chunk = " ".join(words[i:i + 6])
            if any(chunk in c for c in captions_norm):
                return True
    return False


def _brand_key(name: str) -> str:
    """브랜드명 비교 키: 소문자 + 영숫자만 ('Make prem' == 'makeprem', 'rom&nd' == 'romnd')."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _hangul_ratio(text: str) -> float:
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "가" <= ch <= "힣") / len(letters)


def check_week(db_path: str, week: str) -> WeekReport:
    report = WeekReport(week=week)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        brands = load_brands(conn)
        known = {_brand_key(b["brand_name_en"]) for b in brands} | {_brand_key(b["brand_id"]) for b in brands}
        for b in brands:
            try:
                known.update(_brand_key(a) for a in json.loads(b.get("aliases") or "[]"))
            except (TypeError, ValueError):
                pass

        insights = conn.execute(
            "SELECT id, keyword, competitor_mentions, consumer_need FROM fact_llm_insights WHERE week = ?",
            (week,),
        ).fetchall()

        for ins in insights:
            kw = ins["keyword"]
            captions = sample_captions(conn, kw, week)
            caps_norm = [_norm(c) for c in captions]

            # ── 제품 연결 근거 ──
            links = conn.execute(
                "SELECT product_id, match_type, evidence FROM fact_llm_insight_products WHERE insight_id = ?",
                (ins["id"],),
            ).fetchall()
            for ln in links:
                ev = (ln["evidence"] or "").strip()
                if ln["match_type"] == "direct":
                    ok = _quote_found(ev, caps_norm)
                    report.results.append(CheckResult(
                        kw, "direct_quote", ln["product_id"], "pass" if ok else "fail",
                        "" if ok else f"인용이 캡션 샘플에 없음: {ev[:80]!r}",
                    ))
                else:
                    ok = len(ev) >= 15
                    report.results.append(CheckResult(
                        kw, "indirect_reason", ln["product_id"], "pass" if ok else "fail",
                        "" if ok else "간접 연결 근거 문장이 비어 있거나 너무 짧음",
                    ))

            # ── 브랜드 언급 ──
            try:
                mentions = json.loads(ins["competitor_mentions"] or "{}")
                if not isinstance(mentions, dict):
                    mentions = {}
            except ValueError:
                mentions = {}
            for brand, claimed in mentions.items():
                name = _norm(brand)
                actual = sum(1 for c in caps_norm if name and name in c)
                try:
                    claimed_n = int(claimed)
                except (TypeError, ValueError):
                    claimed_n = -1
                if actual == 0:
                    verdict, detail = "fail", f"캡션 샘플 {len(captions)}건 중 '{brand}' 등장 0건 (LLM 주장 {claimed})"
                elif claimed_n < 0 or abs(claimed_n - actual) > max(1, actual // 2):
                    verdict, detail = "warn", f"LLM 주장 {claimed}건 vs 실제 {actual}건"
                else:
                    verdict, detail = "pass", ""
                report.results.append(CheckResult(kw, "brand_count", brand, verdict, detail))

                if _brand_key(brand) not in known:
                    report.results.append(CheckResult(
                        kw, "known_brand", brand, "warn",
                        "dim_brand 에 없는 이름 — 신규 브랜드이거나 사람·제품명일 수 있음 (LLM 판정에서 확인)",
                    ))

            # ── 소비자 니즈 한국어 여부 ──
            ratio = _hangul_ratio(ins["consumer_need"])
            report.results.append(CheckResult(
                kw, "korean_need", "consumer_need", "pass" if ratio >= 0.5 else "fail",
                "" if ratio >= 0.5 else f"한국어 비율 {ratio:.0%}",
            ))
    return report


def save_report(db_path: str, report: WeekReport) -> None:
    """같은 주차의 기존 규칙 검사 결과를 지우고 새로 저장 (재실행 안전)."""
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(SCHEMA_SQL)
        conn.execute(
            "DELETE FROM fact_insight_validations WHERE week = ? AND checker = ?",
            (report.week, RULE_CHECKER),
        )
        conn.executemany(
            """INSERT INTO fact_insight_validations
                   (week, keyword, check_type, target, verdict, detail, checker)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(report.week, r.keyword, r.check_type, r.target, r.verdict, r.detail, RULE_CHECKER)
             for r in report.results],
        )
