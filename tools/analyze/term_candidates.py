"""
term_candidates.py
───────────────────
사전 밖 용어 후보: 매주 저장된 원문에서 사전에 없는 단어·두 단어 조합을 세어 사람이 검토할 목록을 만든다.

왜: 키워드 사전이 고정돼 있으면 PDRN 처럼 새로 뜨는 성분·제형을 탐지할 수 없다.
    원문은 사전과 무관하게 전부 저장하므로(2026-W49~) 사전 밖 말도 셀 수 있다.

판정은 하지 않는다. 후보를 '이번 주 게시물 수' 순으로 보여 주고 승인·거절은 사람이 한다.
  - 하한: 이번 주 게시물 StatsTool.MIN_POST_COUNT(5)건 이상 (트렌드 후보와 같은 하한)
  - 참고 열: 직전 4개 데이터 주차 평균 게시물 수, 이전 등장 주 수 (새로 나온 말인지 보는 용도)
  - 거절한 용어(dim_term.status='rejected')는 다시 보이지 않는다 → 주마다 목록이 깨끗해진다.

승인: python main.py term approve pdrn collagen --category 성분 --note "..."
거절: python main.py term reject glow makeup
"""

import json
import logging
import re
import sqlite3
from collections import Counter

from tools.analyze.stats_tool import StatsTool
from tools.collect.brand_registry import brand_terms, load_brands
from tools.collect.sns_terms import EXTRA_BRAND_TERMS, TERM_SCHEMA, kbeauty_terms

logger = logging.getLogger(__name__)

TOP_N = 30          # 화면에 보여 줄 후보 수
PREV_WEEKS = 4      # 참고용 직전 데이터 주차 수

SCHEMA = """CREATE TABLE IF NOT EXISTS fact_term_candidates (
    week        TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    term        TEXT NOT NULL,      -- 원형 (공백 구분)
    posts       INTEGER NOT NULL,   -- 이번 주 이 말이 들어간 게시물 수 (TikTok+YouTube)
    prev_avg    REAL,               -- 직전 데이터 주차 평균 게시물 수
    weeks_seen  INTEGER,            -- 이전 데이터 주차 중 1건 이상 나온 주 수
    PRIMARY KEY (week, term)
)"""

# 영어 기능어 + 영상·SNS 상투어 + 너무 넓은 뷰티 일반어 (후보 잡음 제거용, 판정과 무관)
STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here hers herself
him himself his how i if in into is it its itself just let me more most my myself no nor not now of off on once only or
other our ours ourselves out over own same she should so some such than that the their theirs them themselves then there
these they this those through to too under until up very was we were what when where which while who whom why will with
would you your yours yourself yourselves get got go going make made use using used one two three new best like love
really much many even still way thing things day days week today daily time first last every want need try tried trying
see watch video videos shorts short channel subscribe comment comments link links follow click check out full review
reviews haul unboxing vlog tutorial tips tip how-to vs part episode live free sale shop shopping buy bought order code
discount off amazon store available www http https com bit ly instagram tiktok youtube fyp foryou foryoupage viral trending
skin skincare beauty product products routine face facial care korean korea asian makeup cosmetics cosmetic glowing
good great amazing favorite favourite must have results result before after help helps helped work works worked feel
feels look looks looking dry oily combination normal type types step steps morning night am pm everyday
little worth honest actually don doesn didn isn know official lol omg guys everything something anything without finally
real right better small big perfect cute pretty ever never always sure okay yes thank thanks please here's let's that's
it's i'm you're what's own life people girl girls thing year years month months minute minutes hour hours
""".split())
# 판매처 (후보가 아니라 유통 채널)
STOPWORDS |= {"olive young", "oliveyoung", "yesstyle", "sephora", "ulta", "stylekorean", "amazon", "tiktokshop", "target"}

_URL = re.compile(r"https?://\S+|www\.\S+")
_TOKEN = re.compile(r"[a-z][a-z0-9\-]{2,}")


def _texts(conn, week: str):
    for content, tags in conn.execute(
            "SELECT content_text, hashtags FROM fact_sns_raw WHERE week=? AND platform_id IN ('tiktok','youtube')",
            (week,)):
        try:
            t = json.loads(tags or "[]")
            t = " ".join(t) if isinstance(t, list) else str(t)
        except (TypeError, ValueError):
            t = tags or ""
        yield _URL.sub(" ", f"{content or ''} {t}".lower())


def _grams(text: str, blocked: set[str]) -> set[str]:
    """게시물 하나의 후보 n-gram (단어 1개, 연속 2개). 게시물당 1회."""
    toks = [t.strip("-") for t in _TOKEN.findall(text)]
    def keep(t: str) -> bool:
        base = t[:-1] if t.endswith("s") else t          # 복수형도 같은 말로 본다 (pores → pore)
        return not (t in STOPWORDS or t in blocked or base in STOPWORDS or base in blocked or t.isdigit())
    ok = [t if keep(t) else None for t in toks]
    out = {t for t in ok if t}
    out |= {f"{a} {b}" for a, b in zip(ok, ok[1:]) if a and b}
    return out


class TermCandidates:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _blocked(self, conn) -> tuple[set[str], re.Pattern]:
        """이미 사전에 있거나 브랜드이거나 검토가 끝난 말."""
        conn.execute(TERM_SCHEMA)
        reviewed = {r[0] for r in conn.execute("SELECT term FROM dim_term")}
        dictionary = kbeauty_terms(self.db_path)
        brands = brand_terms(load_brands(conn, tracked_only=False)) | EXTRA_BRAND_TERMS
        phrases = dictionary | brands | reviewed
        words = {w for p in phrases for w in p.split() if len(w) >= 3}   # 'beauty of joseon' → joseon
        words |= {re.sub(r"[\s\-&]+", "", p) for p in phrases}          # 해시태그형 붙여쓰기: koreanskincare, glassskin
        # 사전·브랜드 구(phrase)를 포함하는 조합도 뺀다 (예: 'snail mucin' 이 사전에 있으면 'mucin essence' 는 남고 'snail mucin' 은 빠짐)
        return words, re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, phrases), key=len, reverse=True)) + r")\b")

    def run(self, week: str) -> list[dict]:
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute(SCHEMA)
            words, phrase_re = self._blocked(conn)
            weeks = [r[0] for r in conn.execute(
                "SELECT DISTINCT week FROM fact_sns_raw WHERE week<? ORDER BY week DESC", (week,))]
            prev_weeks = weeks[:PREV_WEEKS]

            def count(wk: str) -> Counter:
                c = Counter()
                for text in _texts(conn, wk):
                    c.update(_grams(text, words))
                return c

            cur = count(week)
            prev = [count(w) for w in weeks]          # 등장 주 수는 전체 이력, 평균은 직전 PREV_WEEKS
            rows = []
            for term, n in cur.items():
                if n < StatsTool.MIN_POST_COUNT or term in STOPWORDS or phrase_re.search(term):
                    continue
                rows.append({
                    "term": term, "posts": n,
                    "prev_avg": round(sum(p[term] for p in prev[:len(prev_weeks)]) / len(prev_weeks), 1)
                                if prev_weeks else None,
                    "weeks_seen": sum(1 for p in prev if p[term]),
                })
            # 단어와 그 단어를 포함한 조합이 같은 게시물 수면 조합만 남긴다 (예: 'pimple' 20 = 'pimple patch' 20)
            bigram = {r["term"]: r["posts"] for r in rows if " " in r["term"]}
            rows = [r for r in rows if " " in r["term"]
                    or not any(r["term"] in b.split() and n == r["posts"] for b, n in bigram.items())]
            rows.sort(key=lambda r: (-r["posts"], r["term"]))
            rows = rows[:TOP_N]
            conn.execute("DELETE FROM fact_term_candidates WHERE week=?", (week,))
            for i, r in enumerate(rows, 1):
                conn.execute("INSERT INTO fact_term_candidates (week, rank, term, posts, prev_avg, weeks_seen) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (week, i, r["term"], r["posts"], r["prev_avg"], r["weeks_seen"]))
        logger.info(f"사전 밖 용어 후보 {week}: {len(rows)}개")
        return rows
