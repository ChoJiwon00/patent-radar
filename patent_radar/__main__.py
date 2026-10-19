"""명령줄 진입점.

    python -m patent_radar news                  뉴스를 수집하고 결과를 요약해서 보여준다
    python -m patent_radar news --json           수집 후 오늘의 새 기사를 JSON으로 출력한다 (스킬이 읽는 형식)
    python -m patent_radar briefing save FILE    AI 브리핑 JSON을 검사해서 저장한다
    python -m patent_radar briefing show         저장된 브리핑을 보여준다
    python -m patent_radar briefing export FILE  claude.ai 브리핑 페이지에 올릴 JSON을 만든다
    python -m patent_radar serve                 웹페이지를 띄운다 (http://127.0.0.1:8000)
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import briefing, db, news
from .config import KST


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

    p_serve = sub.add_parser("serve", help="웹페이지 실행")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="코드가 바뀌면 자동으로 다시 시작")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
