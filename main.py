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
  python main.py build-catalog     # 카탈로그 구축 (월 1회)
  python main.py build-catalog --platforms oy_global sephora

환경변수 (.env):
  APIFY_TOKEN       — TikTok 수집 (Apify)
  YOUTUBE_API_KEY   — YouTube Data API v3
  OPENAI_API_KEY    — VOC 감성 분석 (선택)
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import os

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env UTF-8 직접 파싱 (Windows cp949 환경 대응)
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _v = _v.split(" #")[0].strip().strip('"').strip("'")
                os.environ[_k.strip()] = _v


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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog        = "kbeauty-agent",
        description = "K-Beauty 트렌드 인사이트 에이전트 v3",
    )
    parser.add_argument(
        "mode",
        choices = ["full", "collect", "analyze", "decide", "report", "build-catalog", "agentic"],
        help    = "실행 모드",
    )
    parser.add_argument(
        "--week",
        default = None,
        metavar = "YYYY-WNN",
        help    = "analyze / decide / agentic 전용: ISO 주차 (예: 2026-W20). 생략 시 달력 기준 현재 주",
    )
    parser.add_argument(
        "--platforms",
        nargs   = "+",
        choices = ["oy_global", "oy_kr", "sephora"],
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

    if args.week is not None and args.mode not in ("analyze", "decide", "agentic"):
        parser.error("--week 는 analyze / decide / agentic 모드에서만 사용할 수 있습니다.")

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

    elif args.mode == "agentic":
        result = orch.run_full_agentic(week=analysis_week)
        print("\nAgentic 파이프라인 완료:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        if result.get("report_path"):
            print(f"\n  HTML 리포트: {result['report_path']}")

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
