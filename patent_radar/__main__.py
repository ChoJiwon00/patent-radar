"""명령줄 진입점.

    python -m patent_radar news          뉴스를 수집하고 결과를 요약해서 보여준다
    python -m patent_radar news --json   수집 후 오늘의 새 기사를 JSON으로 출력한다 (스킬이 읽는 형식)
    python -m patent_radar serve         웹페이지를 띄운다 (http://127.0.0.1:8000)
"""

import argparse
import json
import sys
from datetime import datetime

from . import db, news
from .config import KST


def cmd_news(args: argparse.Namespace) -> int:
    with db.session() as conn:
        results = news.collect(conn)
        if args.json:
            today = datetime.now(KST).date()
            print(json.dumps(news.news_for_day(conn, today), ensure_ascii=False, indent=2))
            return 0

    total = 0
    for r in results:
        status = f"오류: {r.error}" if r.error else f"{r.fetched}건 중 새 기사 {r.added}건"
        print(f"[{r.topic}] {r.query}: {status}")
        total += r.added
    print(f"\n새로 저장한 기사: 총 {total}건")
    return 1 if all(r.error for r in results) else 0


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

    p_serve = sub.add_parser("serve", help="웹페이지 실행")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="코드가 바뀌면 자동으로 다시 시작")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
