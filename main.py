"""
main.py
────────
K-Beauty Intelligence Agent v3 — CLI 진입점.

사용법:
  python main.py full              # 수집 → 분석 → 의사결정 전체 파이프라인
  python main.py collect           # 데이터 수집만
  python main.py analyze                    # 분석만 — 달력 기준 현재 ISO 주차
  python main.py analyze --week 2026-W20  # 분석만 — 지정 주차
  python main.py report            # 리포트만 생성
  python main.py reshape                    # 저장된 전 주차의 트렌드 모양만 현재 규칙으로 재계산
  python main.py reshape --week 2026-W47    # 지정 주차만 (LLM·VOC·리포트는 건드리지 않음)
  python main.py backfill-terms [--dry-run] # 사전에 새로 넣은 용어의 과거 주차 신호를 저장된 원문에서 채움
  python main.py term-coverage --week 2026-W49  # 새 용어 게시물 중 기존 사전 용어가 없는 비율 (과거 하한 영향 확인)
  python main.py term candidates [--week W]  # 사전 밖 용어 후보 (기본: 원문이 있는 마지막 주차)
  python main.py term approve pdrn collagen --category 성분 --note "근거"  # 사전에 추가 + 과거 주차 재집계
  python main.py term reject glow makeup     # 후보에서 다시 보이지 않게
  python main.py term list                   # 승인·거절된 용어
  python main.py build-catalog     # 카탈로그 구축 (월 1회)
  python main.py build-catalog --platforms oy_global sephora

환경변수 (.env):
  APIFY_TOKEN       — TikTok 수집 (Apify)
  YOUTUBE_API_KEY   — YouTube Data API v3
  ANTHROPIC_API_KEY — 인사이트 생성·검증, VOC 감성 분석
"""

import argparse
import logging
import re
import sys
from pathlib import Path


# ── 프로젝트 루트를 sys.path에 추가 ──────────────────
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env UTF-8 직접 파싱 (Windows cp949 환경 대응)
from env_loader import load_env
load_env()


def setup_logging(level: str = "INFO") -> None:
    """로깅 설정 — 중복 핸들러 방지, Windows UTF-8 대응."""
    root = logging.getLogger()
    if root.handlers:
        return  # 이미 설정됨

    # Windows cp949 터미널에서 한글/특수문자 깨짐 방지
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level   = numeric,
        format  = "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt = "%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # 3rd-party 라이브러리 로그 레벨 낮춤
    for lib in ("urllib3", "httpx", "httpcore", "asyncio", "playwright"):
        logging.getLogger(lib).setLevel(logging.WARNING)


_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$", re.IGNORECASE)


def normalize_iso_week(week: str) -> str:
    """'2026-W20' / '2026-w5' → '2026-W20'. 잘못된 형식이면 ValueError."""
    m = _ISO_WEEK_RE.match(week.strip())
    if not m:
        raise ValueError(week)
    year, wnum = int(m.group(1)), int(m.group(2))
    if not 1 <= wnum <= 53:
        raise ValueError(week)
    return f"{year}-W{wnum:02d}"


def print_summary(result: dict) -> None:
    """실행 결과 요약 출력."""
    print("\n" + "=" * 55)
    print("  K-Beauty Intelligence Agent v3 — 실행 결과")
    print("=" * 55)
    for key, val in result.items():
        if key == "top_opps":
            if val:
                print(f"\n  [상위 기회]")
                for i, opp in enumerate(val, 1):
                    print(
                        f"    {i}. [{opp.get('opportunity_type','?')}] "
                        f"'{opp.get('keyword','')}' "
                        f"score={opp.get('priority_score',0):.1f}"
                    )
        elif key not in ("analysis",):
            print(f"  {key:<20}: {val}")
    print("=" * 55 + "\n")


def run_term_command(db_path: str, action: str, terms: list[str], week: str | None,
                     category: str | None, note: str | None) -> None:
    """사전 관리: 후보 보기·승인·거절·목록. 승인하면 저장된 원문에서 과거 주차 신호를 채운다."""
    import sqlite3
    from datetime import date
    from tools.collect.sns_terms import TERM_SCHEMA
    from tools.collect.youtube_tool import YouTubeTool
    with sqlite3.connect(db_path) as conn:
        conn.execute(TERM_SCHEMA)
        if action == "list":
            rows = conn.execute("SELECT term, status, category, added_week, source, note FROM dim_term "
                                "ORDER BY status, added_week, term").fetchall()
            print("\n승인·거절된 용어 (시작 사전은 코드의 GENERIC_TERMS):")
            for r in rows:
                print("  " + " | ".join(str(x or "") for x in r))
            return
        if action == "candidates":
            from tools.analyze.term_candidates import TermCandidates
            wk = week or conn.execute("SELECT MAX(week) FROM fact_sns_raw").fetchone()[0]
            rows = TermCandidates(db_path).run(wk)
            print(f"\n{wk} 사전 밖 용어 후보 (이번 주 게시물 수 · 직전 평균 · 이전 등장 주 수):")
            for i, r in enumerate(rows, 1):
                print(f"  {i:2d}. {r['term']:24s} {r['posts']:4d} · {r['prev_avg']} · {r['weeks_seen']}")
            return
        terms = [t.strip().lower().replace("_", " ") for t in terms]
        if action == "reject":
            for t in terms:
                conn.execute("""INSERT INTO dim_term (term, status, source, note) VALUES (?, 'rejected', 'manual', ?)
                                ON CONFLICT(term) DO UPDATE SET status='rejected', note=excluded.note,
                                decided_at=datetime('now')""", (t, note))
            print(f"\n거절: {', '.join(terms)} (후보 목록에 다시 나오지 않음)")
            return
        # approve: 다음 수집 주차부터 정식 집계 (일간 수집이 오늘 이후 게시물을 넣는 주차)
        added_week = YouTubeTool.run_week_of(date.today())
        cand_week = conn.execute("SELECT MAX(week) FROM fact_term_candidates").fetchone()[0] \
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='fact_term_candidates'").fetchone() else None
        for t in terms:
            in_cand = cand_week and conn.execute(
                "SELECT 1 FROM fact_term_candidates WHERE week=? AND term=?", (cand_week, t)).fetchone()
            conn.execute("""INSERT INTO dim_term (term, status, category, added_week, source, note)
                            VALUES (?, 'active', ?, ?, ?, ?)
                            ON CONFLICT(term) DO UPDATE SET status='active', category=excluded.category,
                            added_week=excluded.added_week, source=excluded.source, note=excluded.note,
                            decided_at=datetime('now')""",
                         (t, category, added_week, f"candidate:{cand_week}" if in_cand else "manual", note))
    from tools.collect.backfill_terms import backfill
    out = backfill(db_path, terms)
    print(f"\n승인: {', '.join(terms)} (정식 집계 {added_week}~) · 과거 주차 재집계:")
    for wk, kws in out.items():
        print(f"  {wk}: " + ", ".join(f"{k} {n}" for k, n in sorted(kws.items(), key=lambda x: -x[1])))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "kbeauty-agent",
        description = "K-Beauty 트렌드 인사이트 에이전트 v3",
    )
    parser.add_argument(
        "mode",
        choices = ["full", "collect", "analyze", "decide", "report", "reshape", "backfill-terms", "term-coverage", "term", "build-catalog"],
        help    = "실행 모드",
    )
    parser.add_argument(
        "terms",
        nargs   = "*",
        help    = "term 모드 전용: 동작(list|candidates|approve|reject) 다음에 용어들",
    )
    parser.add_argument("--category", default=None, help="term approve 전용: 성분 | 제품유형 | 고민 | 루틴 | 기타")
    parser.add_argument("--note", default=None, help="term approve/reject 전용: 근거 메모")
    parser.add_argument(
        "--week",
        default = None,
        metavar = "YYYY-WNN",
        help    = "analyze / decide / reshape / term-coverage 전용: ISO 주차 (예: 2026-W20). 생략 시 달력 기준 현재 주",
    )
    parser.add_argument(
        "--platforms",
        nargs   = "+",
        choices = ["oy_global", "sephora"],
        default = None,
        help    = "build-catalog 대상 플랫폼 (기본: 전체)",
    )
    parser.add_argument(
        "--only",
        nargs   = "+",
        choices = ["tiktok", "youtube", "oy_global"],
        default = None,
        help    = "collect 모드에서 특정 플랫폼만 실행 (예: --only tiktok youtube)",
    )
    parser.add_argument(
        "--no-scrape",
        action  = "store_true",
        default = False,
        help    = "Sephora Playwright 성분 스크랩 비활성화 (build-catalog 한정)",
    )
    parser.add_argument(
        "--dry-run",
        action  = "store_true",
        default = False,
        help    = "backfill-terms 한정: DB에 쓰지 않고 추가될 건수만 출력",
    )
    parser.add_argument(
        "--log-level",
        default = "INFO",
        choices = ["DEBUG", "INFO", "WARNING", "ERROR"],
        help    = "로그 레벨 (기본: INFO)",
    )
    parser.add_argument(
        "--db",
        default = None,
        help    = "DB 경로 재지정 (기본: db/kbeauty.db)",
    )

    args = parser.parse_args()
    setup_logging(args.log_level)

    logger = logging.getLogger("main")
    logger.info(f"실행 모드: {args.mode}")

    if args.week is not None and args.mode not in ("analyze", "decide", "reshape", "term-coverage", "term"):
        parser.error("--week 는 analyze / decide / reshape / term-coverage / term 모드에서만 사용할 수 있습니다.")
    if args.terms and args.mode != "term":
        parser.error(f"알 수 없는 인자: {' '.join(args.terms)}")
    if args.mode == "term" and (not args.terms or args.terms[0] not in ("list", "candidates", "approve", "reject")):
        parser.error("term 다음에 list | candidates | approve | reject 중 하나가 필요합니다.")
    if args.mode == "term" and args.terms[0] in ("approve", "reject") and len(args.terms) < 2:
        parser.error(f"term {args.terms[0]} 다음에 용어가 필요합니다.")
    if args.mode == "term-coverage" and args.week is None:
        parser.error("term-coverage 는 --week 가 필요합니다.")

    analysis_week: str | None = None
    if args.week is not None:
        try:
            analysis_week = normalize_iso_week(args.week)
        except ValueError:
            parser.error(
                f"주차 형식이 올바르지 않습니다: {args.week!r} "
                "(예: 2026-W20, 주 번호 1~53)"
            )

    # Orchestrator 초기화 (DB 경로 옵션 지원)
    from agents.orchestrator import Orchestrator
    orch = Orchestrator(db_path=args.db) if args.db else Orchestrator()

    # ── 모드별 실행 ────────────────────────────────────────
    if args.mode == "full":
        result = orch.run_full(run_type="manual_full")
        print_summary(result)

    elif args.mode == "collect":
        result = orch.run_collect(only=args.only)
        print_summary(result)

    elif args.mode == "analyze":
        result = orch.run_analyze(week=analysis_week)
        print("\n분석 완료:")
        for k, v in result.items():
            if k == "week":
                print(f"  {k}: {v}")
            elif hasattr(v, "__len__") and not isinstance(v, (str, bytes)):
                print(f"  {k}: {len(v)}건")
            else:
                print(f"  {k}: {v}")

    elif args.mode == "decide":
        result = orch.run_decide(week=analysis_week)
        print("\n의사결정 완료:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        if result.get("report_path"):
            print(f"\n  HTML 리포트: {result['report_path']}")

    elif args.mode == "report":
        result = orch.run_report()
        print_summary(result)
        if result.get("report_path"):
            print(f"\n  HTML 리포트: {result['report_path']}")

    elif args.mode == "reshape":
        import sqlite3
        from agents.decision_agent import reshape_trends
        with sqlite3.connect(orch.db_path) as conn:
            weeks = [analysis_week] if analysis_week else [
                r[0] for r in conn.execute("SELECT DISTINCT week FROM fact_trend_insights ORDER BY week")]
        print("\n트렌드 모양 재계산 (바뀐 것만 표시):")
        for wk in weeks:
            changed = reshape_trends(orch.db_path, wk)
            print(f"  {wk}: {len(changed)}건 변경")
            for kw, old, new in changed:
                print(f"    {kw}: {old} → {new}")

    elif args.mode == "backfill-terms":
        from tools.collect.backfill_terms import backfill
        out = backfill(orch.db_path, dry_run=args.dry_run)
        print(f"\n새 용어 과거 신호 {'(dry-run, 쓰지 않음)' if args.dry_run else '추가'}: 주차별 게시물 수")
        for wk, kws in out.items():
            print(f"  {wk}: " + ", ".join(f"{k} {n}" for k, n in sorted(kws.items(), key=lambda x: -x[1])))

    elif args.mode == "term-coverage":
        from tools.collect.backfill_terms import coverage
        print(f"\n{analysis_week} 새 용어 게시물 중 기존 사전 용어가 없는 게시물 (추가 전 규칙이면 저장 안 됐을 것):")
        for k, (n, only) in sorted(coverage(orch.db_path, analysis_week).items(), key=lambda x: -x[1][0]):
            print(f"  {k:14s} {only:4d} / {n:4d}" + (f"  ({only / n:.0%})" if n else ""))

    elif args.mode == "term":
        run_term_command(orch.db_path, args.terms[0], args.terms[1:], analysis_week, args.category, args.note)

    elif args.mode == "build-catalog":
        scrape = not args.no_scrape
        print(f"\n카탈로그 구축 시작 (대상: {args.platforms or '전체'}, 성분스크랩: {scrape})...")
        result = orch.build_catalog(platforms=args.platforms, scrape_ingredients=scrape)
        print("\n카탈로그 구축 결과:")
        for platform, r in result.items():
            print(f"  {platform}: {r}")
        print()

    logger.info("완료.")


if __name__ == "__main__":
    main()
