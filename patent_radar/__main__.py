"""명령줄 진입점.

    python -m patent_radar news                  뉴스를 수집하고 결과를 요약해서 보여준다
    python -m patent_radar news --json           수집 후 오늘의 새 기사를 JSON으로 출력한다 (스킬이 읽는 형식)
    python -m patent_radar briefing save FILE    AI 브리핑 JSON을 검사해서 저장한다
    python -m patent_radar briefing show         저장된 브리핑을 보여준다
    python -m patent_radar briefing export FILE  claude.ai 브리핑 페이지에 올릴 JSON을 만든다
    python -m patent_radar patents search        오늘 브리핑 키워드로 한·미·중 특허를 찾는다
    python -m patent_radar patents search --keyword "전고체 전해질|solid electrolyte"
    python -m patent_radar patents list          검색 없이 그날 특허 목록을 다시 파일로 만든다
    python -m patent_radar patents save-summaries FILE   AI가 쓴 한국어 요약을 저장한다
    python -m patent_radar patents export FILE   claude.ai 브리핑 페이지에 올릴 JSON을 만든다
    python -m patent_radar patents report        GitHub에 올릴 보고서(reports/날짜.md)를 만든다
    python -m patent_radar patents usage         이번 달 KIPRIS 호출 수를 보여준다
    python -m patent_radar serve                 웹페이지를 띄운다 (http://127.0.0.1:8000)
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

from . import briefing, db, news, patents
from .config import KST, kipris_api_key, load_patent_settings


def cmd_news(args: argparse.Namespace) -> int:
    with db.session() as conn:
        results = news.collect(conn)
        if args.json:
            today = datetime.now(KST).date()
            # 스킬이 읽기 좋게 긴 링크는 빼고 필요한 항목만 내보낸다.
            keep = ("id", "topic", "query", "title", "source", "published_at")
            articles = [{k: a[k] for k in keep} for a in news.news_for_day(conn, today)]
            errors = [f"{r.query}: {r.error}" for r in results if r.error]
            print(json.dumps({"day": today.isoformat(), "errors": errors, "articles": articles}, ensure_ascii=False))
            return 0

    total = 0
    for r in results:
        status = f"오류: {r.error}" if r.error else f"{r.fetched}건 중 새 기사 {r.added}건"
        print(f"[{r.topic}] {r.query}: {status}")
        total += r.added
    print(f"\n새로 저장한 기사: 총 {total}건")
    return 1 if all(r.error for r in results) else 0


def cmd_briefing_save(args: argparse.Namespace) -> int:
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"파일을 읽지 못했어요: {e}")
        return 2
    with db.session() as conn:
        try:
            saved = briefing.save(conn, data)
        except briefing.BriefingError as e:
            print(f"형식 오류: {e}")
            return 2
    parts = [f"{t['name']} 이야기 {len(t['stories'])}개·키워드 {len(t['keywords'])}개" for t in saved["topics"]]
    print(f"{saved['day']} 브리핑을 저장했어요 ({', '.join(parts)})")
    return 0


def cmd_briefing_show(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.day) if args.day else datetime.now(KST).date()
    with db.session() as conn:
        found = briefing.get(conn, day)
    if found is None:
        print(f"{day} 브리핑이 없어요.")
        return 1
    print(json.dumps(found, ensure_ascii=False, indent=2))
    return 0


def cmd_briefing_export(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.day) if args.day else datetime.now(KST).date()
    with db.session() as conn:
        found = briefing.get(conn, day)
    if found is None:
        print(f"{day} 브리핑이 없어요.")
        return 1
    Path(args.file).write_text(json.dumps(briefing.for_page(found), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{day} 브리핑을 페이지용으로 {args.file}에 저장했어요")
    return 0


def _day(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(KST).date()


def _print_usage(conn) -> None:
    settings = load_patent_settings()
    for service, name in patents.SERVICE_NAMES.items():
        used = patents.calls_this_month(conn, service)
        print(f"이번 달 {name} 호출: {used}회 (설정한 상한 {settings.monthly_call_limit}회, 무료 한도 1,000회)")


def cmd_patents_search(args: argparse.Namespace) -> int:
    key = kipris_api_key()
    if not key:
        print(".env 파일에 KIPRIS_API_KEY가 없어요.")
        return 2
    settings = load_patent_settings()
    day = _day(args.day)
    with db.session() as conn:
        if args.keyword:
            try:
                keywords = [patents.Keyword.parse(k) for k in args.keyword]
            except ValueError as e:
                print(e)
                return 2
            print(f"직접 준 키워드 {len(keywords)}개로 찾아요.")
        else:
            source_day, keywords = patents.keywords_from_briefing(conn, day, settings.keywords_per_day)
            if not keywords:
                print("뉴스 브리핑이 없어서 키워드를 고를 수 없어요. 먼저 /daily-briefing 을 실행하거나 --keyword 를 주세요.")
                return 1
            note = "" if source_day == day.isoformat() else f" (오늘 브리핑이 없어 {source_day} 브리핑 사용)"
            print(f"뉴스 브리핑 키워드 {len(keywords)}개로 찾아요{note}.")

        with httpx.Client(timeout=30) as http:
            client = patents.KiprisClient(conn, key, settings, http)
            content = patents.collect(conn, client, keywords, day, settings)

        result = patents.for_claude(conn, day)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        new = [i for i in result["items"] if i["new"] and not i["rank"]]
        counts = {c: sum(1 for i in new if i["country"] == c) for c in patents.COUNTRIES}
        hot = sum(1 for i in result["items"] if i["rank"])
        print(f"🔥 주목 특허 {hot}건, 새 특허 "
              + ", ".join(f"{patents.COUNTRY_NAMES[c]} {n}건" for c, n in counts.items()))
        for w in content["warnings"]:
            print(f"  - {w}")
        print(f"자세한 내용: {args.out}")
        _print_usage(conn)
    return 0


def cmd_patents_list(args: argparse.Namespace) -> int:
    day = _day(args.day)
    with db.session() as conn:
        result = patents.for_claude(conn, day)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pending = sum(1 for i in result["items"] if i["needs_summary"])
    print(f"{day} 특허 {len(result['items'])}건 (요약이 필요한 특허 {pending}건)을 {args.out}에 저장했어요")
    return 0


def cmd_patents_save(args: argparse.Namespace) -> int:
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"파일을 읽지 못했어요: {e}")
        return 2
    with db.session() as conn:
        try:
            n = patents.save_summaries(conn, data, _day(args.day))
        except patents.SummaryError as e:
            print(f"형식 오류: {e}")
            return 2
    print(f"특허 요약 {n}건을 저장했어요")
    return 0


def cmd_patents_export(args: argparse.Namespace) -> int:
    day = _day(args.day)
    with db.session() as conn:
        page = patents.for_page(conn, day)
    if page is None:
        print(f"{day} 특허 브리핑이 없어요.")
        return 1
    everything = page["spotlight"] + page["items"]
    missing = sum(1 for i in everything if not i["summary_ko"])
    Path(args.file).write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{day} 특허 {len(everything)}건을 페이지용으로 {args.file}에 저장했어요"
          + (f" (한국어 요약 없는 특허 {missing}건)" if missing else ""))
    return 0


REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def cmd_patents_report(args: argparse.Namespace) -> int:
    day = _day(args.day)
    with db.session() as conn:
        page = patents.for_page(conn, day)
    if page is None:
        print(f"{day} 특허 브리핑이 없어요.")
        return 1
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{day}.md"
    path.write_text(patents.report_markdown(page), encoding="utf-8")

    days = sorted((p.stem for p in REPORTS_DIR.glob("????-??-??.md")), reverse=True)
    index = ["# 특허 브리핑 모음", "", "Patent Radar가 매일 찾은 한·미·중 특허를 날짜별로 모아 둔 곳이에요.", ""]
    index += [f"- [{d}]({d}.md)" for d in days]
    (REPORTS_DIR / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"GitHub용 보고서를 만들었어요: reports/{day}.md")
    return 0


def cmd_patents_usage(args: argparse.Namespace) -> int:
    with db.session() as conn:
        _print_usage(conn)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"웹페이지 주소: http://127.0.0.1:{args.port}  (끄려면 Ctrl+C)")
    uvicorn.run("patent_radar.web:app", host="127.0.0.1", port=args.port, reload=args.reload)
    return 0


def main() -> int:
    # 콘솔이 표현하지 못하는 글자가 있어도 멈추지 않게 한다.
    sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(prog="patent_radar", description="이차전지·반도체 뉴스와 특허 레이더")
    sub = parser.add_subparsers(dest="command", required=True)

    p_news = sub.add_parser("news", help="뉴스 수집")
    p_news.add_argument("--json", action="store_true", help="오늘의 새 기사를 JSON으로 출력")
    p_news.set_defaults(func=cmd_news)

    p_brief = sub.add_parser("briefing", help="AI 브리핑 저장·조회")
    brief_sub = p_brief.add_subparsers(dest="action", required=True)
    p_save = brief_sub.add_parser("save", help="브리핑 JSON 파일을 검사해서 저장")
    p_save.add_argument("file")
    p_save.set_defaults(func=cmd_briefing_save)
    p_show = brief_sub.add_parser("show", help="저장된 브리핑 보기")
    p_show.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_show.set_defaults(func=cmd_briefing_show)
    p_export = brief_sub.add_parser("export", help="claude.ai 브리핑 페이지에 올릴 JSON 파일 만들기")
    p_export.add_argument("file")
    p_export.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_export.set_defaults(func=cmd_briefing_export)

    p_pat = sub.add_parser("patents", help="한·미·중 특허 찾기·요약 저장·내보내기")
    pat_sub = p_pat.add_subparsers(dest="action", required=True)
    p_search = pat_sub.add_parser("search", help="뉴스 브리핑 키워드(또는 직접 준 키워드)로 특허 찾기")
    p_search.add_argument("--keyword", action="append", help='"한국어|영어" 형식. 여러 번 쓸 수 있음')
    p_search.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_search.add_argument("--out", default="data/patents-today.json", help="스킬이 읽을 결과 파일")
    p_search.set_defaults(func=cmd_patents_search)
    p_list = pat_sub.add_parser("list", help="검색하지 않고 그날 특허 브리핑을 스킬이 읽는 파일로 다시 만들기")
    p_list.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_list.add_argument("--out", default="data/patents-today.json")
    p_list.set_defaults(func=cmd_patents_list)
    p_psave = pat_sub.add_parser("save-summaries", help="AI가 쓴 한국어 요약 저장")
    p_psave.add_argument("file")
    p_psave.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_psave.set_defaults(func=cmd_patents_save)
    p_pexport = pat_sub.add_parser("export", help="claude.ai 브리핑 페이지에 올릴 JSON 만들기")
    p_pexport.add_argument("file")
    p_pexport.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_pexport.set_defaults(func=cmd_patents_export)
    p_report = pat_sub.add_parser("report", help="GitHub에 올릴 보고서(reports/날짜.md) 만들기")
    p_report.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_report.set_defaults(func=cmd_patents_report)
    p_usage = pat_sub.add_parser("usage", help="이번 달 KIPRIS 호출 수")
    p_usage.set_defaults(func=cmd_patents_usage)

    p_serve = sub.add_parser("serve", help="웹페이지 실행")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="코드가 바뀌면 자동으로 다시 시작")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
