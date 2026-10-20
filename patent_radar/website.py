"""GitHub Pages 공개 사이트(docs/).

claude.ai 브리핑 페이지(artifact/briefing.html)를 그대로 docs/index.html로 쓰고, 그날그날의 브리핑을
docs/data/ 아래 JSON으로 내보낸다. 페이지는 claude.ai에서는 페이지 데이터베이스를, GitHub Pages에서는
이 JSON 파일들을 읽는다.
"""

import json
import sqlite3
from datetime import date, datetime

from . import briefing, patents, weekly
from .config import KST, ROOT

DOCS = ROOT / "docs"
PAGE = ROOT / "artifact" / "briefing.html"
MAX_DAYS = 60
MAX_WEEKS = 26

# artifact 파일은 본문만 있어서(Artifact 도구가 머리말을 붙인다) 사이트용으로 감싼다.
HEAD = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>
</head>
<body>
"""


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def export(conn: sqlite3.Connection) -> dict:
    days = sorted({r[0] for r in conn.execute("SELECT day FROM briefings UNION SELECT day FROM patent_briefings")},
                  reverse=True)[:MAX_DAYS]
    for d in days:
        day = date.fromisoformat(d)
        _write(DOCS / "data" / "days" / f"{d}.json",
               {"day": d, "briefing": briefing.for_page(found) if (found := briefing.get(conn, day)) else None,
                "patents": patents.for_page(conn, day)})
    weeks = [r[0] for r in conn.execute("SELECT end_day FROM weekly_reports ORDER BY end_day DESC LIMIT ?", (MAX_WEEKS,))]
    for w in weeks:
        _write(DOCS / "data" / "weeks" / f"{w}.json", weekly.get(conn, date.fromisoformat(w)))
    index = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "days": days, "weeks": weeks}
    _write(DOCS / "data" / "index.json", index)

    (DOCS / "index.html").write_text(HEAD + PAGE.read_text(encoding="utf-8") + "\n</body>\n</html>\n", encoding="utf-8")
    (DOCS / ".nojekyll").touch()  # GitHub Pages가 파일을 그대로 내보내게 한다
    return index
