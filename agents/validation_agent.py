"""
validation_agent.py
────────────────────
LLM 인사이트 검증 ③단계: LLM 판정(LLM-as-judge).

- 생성 모델(ANTHROPIC_MODEL, Sonnet)과 다른 모델(VALIDATOR_MODEL, 기본 Opus)로 판정해 자기 선호 편향을 줄인다.
- 인사이트 하나당 1회 호출. 판정 근거는 생성 모델이 봤던 것과 같은 캡션 샘플 + 연결 제품 정보.
- 주장 단위로 supported / insufficient / contradicted 판정 → pass / warn / fail 로 저장.
- 결과는 규칙 검사와 같은 fact_insight_validations 테이블에 checker=<판정 모델 ID> 로 쌓인다.

판정 항목 (check_type):
  llm_need         consumer_need 가 캡션에서 실제로 드러나는 니즈인가
  llm_opportunity  opportunity 문장이 캡션·제품 근거로 뒷받침되는가
  llm_product      연결 제품이 키워드·니즈와 실제로 맞는가 (direct 는 인용 근거, indirect 는 성분·효능 근거)
  llm_brand        언급 브랜드가 실제 뷰티 브랜드이고 캡션에서 언급되는가 (사람 이름·일반 명사 걸러냄)
"""

import json
import logging
import os
import sqlite3

import anthropic

from tools.analyze.llm_insight_tool import sample_captions  # .env 로드도 여기서 됨
from tools.quality.insight_checks import SCHEMA_SQL, CheckResult, WeekReport

logger = logging.getLogger(__name__)

DEFAULT_VALIDATOR_MODEL = "claude-opus-5-5"
CAPTION_CHARS = 400          # 캡션 1건당 프롬프트에 넣는 최대 글자 수
VERDICT_MAP = {"supported": "pass", "insufficient": "warn", "contradicted": "fail"}

# 구조화 출력(JSON schema) — 판정 결과를 항상 파싱 가능한 형태로 받는다
VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "check_type": {
                        "type": "string",
                        "enum": ["llm_need", "llm_opportunity", "llm_product", "llm_brand"],
                    },
                    "target": {
                        "type": "string",
                        "description": "llm_product 는 product_id, llm_brand 는 브랜드명, 나머지는 'consumer_need' / 'opportunity'",
                    },
                    "verdict": {"type": "string", "enum": ["supported", "insufficient", "contradicted"]},
                    "reason": {"type": "string", "description": "한국어 1~2문장. 근거 캡션 번호가 있으면 [#n] 으로 표기"},
                },
                "required": ["check_type", "target", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
}

SYSTEM_PROMPT = """너는 K-뷰티 트렌드 분석 결과를 검수하는 엄격한 검증자다.
다른 모델이 SNS 캡션 샘플을 읽고 만든 인사이트를, 같은 캡션 샘플과 제품 정보만 근거로 판정한다.
외부 지식으로 빈칸을 채워 주지 마라. 단, '이 이름이 실제 뷰티 브랜드인가'는 일반 지식으로 판단해도 된다.

판정 기준:
- supported    : 캡션(또는 제품 성분·효능)에 주장을 직접 뒷받침하는 근거가 있다.
- insufficient : 모순은 없지만 근거가 약하거나 과장·일반론이다.
- contradicted : 근거와 반대이거나, 근거가 전혀 없거나, 사실이 틀렸다
                 (예: 브랜드 목록에 사람 이름·인플루언서·일반 명사가 들어감, 키워드와 무관한 제품 연결).

주어진 모든 항목(니즈 1, 기회 1, 제품 전부, 브랜드 전부)에 판정을 하나씩 남겨라."""


def _json_list(value) -> list:
    try:
        v = json.loads(value or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _build_prompt(keyword: str, insight: sqlite3.Row, captions: list[str],
                  products: list[sqlite3.Row], mentions: dict) -> str:
    cap_lines = "\n".join(f"[#{i + 1}] {c[:CAPTION_CHARS].replace(chr(10), ' ')}"
                          for i, c in enumerate(captions)) or "(캡션 없음)"
    prod_lines = []
    for p in products:
        ings = ", ".join(_json_list(p["key_ingredients"])[:8])
        bens = ", ".join(_json_list(p["key_benefits"])[:6])
        prod_lines.append(
            f"- {p['product_id']} | {p['brand_id']} {p['product_name_en']} | "
            f"{p['category_main'] or '-'}/{p['category_sub'] or '-'} | 성분: {ings or '-'} | 효능: {bens or '-'}\n"
            f"  연결 유형: {p['match_type']} | 근거: {(p['evidence'] or '').strip() or '(없음)'}"
        )
    brand_lines = "\n".join(f"- {b}: {n}건 주장" for b, n in mentions.items()) or "(없음)"
    return f"""## 트렌드 키워드
{keyword}

## 캡션 샘플 ({len(captions)}건, 생성 모델이 본 것과 동일)
{cap_lines}

## 판정할 인사이트
- consumer_need: {insight['consumer_need'] or '(없음)'}
- opportunity: {insight['opportunity'] or '(없음)'}

## 연결 제품
{chr(10).join(prod_lines) or '(없음)'}

## 언급 브랜드 (생성 모델이 캡션에서 셌다고 주장한 횟수)
{brand_lines}
"""


class ValidationAgent:
    def __init__(self, db_path: str, model: str | None = None):
        self.db_path = db_path
        self.model = model or os.getenv("VALIDATOR_MODEL", DEFAULT_VALIDATOR_MODEL)
        self.client = anthropic.Anthropic()
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def _judge(self, prompt: str) -> list[dict]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        self.usage["input_tokens"] += resp.usage.input_tokens
        self.usage["output_tokens"] += resp.usage.output_tokens
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text:
            raise ValueError(f"판정 결과 없음 (stop_reason={resp.stop_reason})")
        return json.loads(text).get("verdicts", [])

    def validate_week(self, week: str) -> WeekReport:
        report = WeekReport(week=week)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            insights = conn.execute(
                "SELECT id, keyword, competitor_mentions, consumer_need, opportunity "
                "FROM fact_llm_insights WHERE week = ?", (week,),
            ).fetchall()
            for ins in insights:
                kw = ins["keyword"]
                captions = sample_captions(conn, kw, week)
                products = conn.execute(
                    """SELECT l.product_id, l.match_type, l.evidence,
                              p.brand_id, p.product_name_en, p.category_main, p.category_sub,
                              p.key_ingredients, p.key_benefits
                       FROM fact_llm_insight_products l
                       LEFT JOIN dim_product p ON p.product_id = l.product_id
                       WHERE l.insight_id = ?""", (ins["id"],),
                ).fetchall()
                try:
                    mentions = json.loads(ins["competitor_mentions"] or "{}")
                    mentions = mentions if isinstance(mentions, dict) else {}
                except ValueError:
                    mentions = {}

                try:
                    verdicts = self._judge(_build_prompt(kw, ins, captions, products, mentions))
                except Exception as e:  # 한 키워드 실패가 주 전체를 막지 않게
                    logger.error(f"[{kw}] LLM 판정 실패: {e}")
                    report.results.append(CheckResult(kw, "llm_error", "judge", "warn", str(e)[:200]))
                    continue

                for v in verdicts:
                    report.results.append(CheckResult(
                        kw, v.get("check_type", "llm_unknown"), str(v.get("target", "")),
                        VERDICT_MAP.get(v.get("verdict"), "warn"), v.get("reason", ""),
                    ))
        logger.info(f"{week} LLM 판정 완료 ({self.model}): {report.counts()} / 토큰 {self.usage}")
        return report

    def save(self, report: WeekReport) -> None:
        """같은 주차·같은 판정 모델의 기존 결과를 지우고 새로 저장 (재실행 안전)."""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(SCHEMA_SQL)
            conn.execute("DELETE FROM fact_insight_validations WHERE week = ? AND checker = ?",
                         (report.week, self.model))
            conn.executemany(
                """INSERT INTO fact_insight_validations
                       (week, keyword, check_type, target, verdict, detail, checker)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [(report.week, r.keyword, r.check_type, r.target, r.verdict, r.detail, self.model)
                 for r in report.results],
            )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db, wk = sys.argv[1], sys.argv[2]
    agent = ValidationAgent(db)
    rep = agent.validate_week(wk)
    agent.save(rep)
    for r in rep.results:
        print(f"{r.verdict:4} [{r.keyword}] {r.check_type} {r.target}: {r.detail}")
