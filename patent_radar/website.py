"""GitHub Pages 공개 사이트(docs/).

claude.ai 브리핑 페이지(artifact/briefing.html)를 그대로 docs/index.html로 쓰고, 그날그날의 브리핑을
docs/data/ 아래 JSON으로 내보낸다. 페이지는 claude.ai에서는 페이지 데이터베이스를, GitHub Pages에서는
이 JSON 파일들을 읽는다.
"""

import json
import re
import sqlite3
from datetime import date, datetime

from . import briefing, patents, weekly
from .config import KST, ROOT

DOCS = ROOT / "docs"
PAGE = ROOT / "artifact" / "briefing.html"
MAX_DAYS = 60
MAX_WEEKS = 26

SITE = "https://chojiwon00.github.io/patent-radar/"
TITLE = "LFP · 이차전지 특허 레이더 | Patent Radar"
DESCRIPTION = ("리튬인산철(LFP) 양극재를 연구한 배터리 소재 연구자가 만든 특허 레이더. 매일 이차전지·ESS·반도체 뉴스에서 기술 키워드를 뽑아 "
               "한·미·중 특허를 찾고 청구항을 한국어로 해설합니다.")

# artifact 파일은 본문만 있어서(Artifact 도구가 머리말을 붙인다) 사이트용으로 감싼다.
# 제목·설명·링크 미리보기(og) 태그는 <head> 안에 있어야 메신저·메일의 미리보기가 읽는다.
HEAD = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{TITLE}</title>
<meta name="description" content="{DESCRIPTION}">
<meta property="og:type" content="website">
<meta property="og:title" content="{TITLE}">
<meta property="og:description" content="{DESCRIPTION}">
<meta property="og:url" content="{SITE}">
<meta property="og:image" content="{SITE}og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%2315201b'/%3E%3Ctext x='16' y='22' font-size='16' text-anchor='middle' fill='%23f2f5f4' font-family='monospace'%3EPR%3C/text%3E%3C/svg%3E">
<style>body{{margin:0}}[hidden]{{display:none!important}}img{{max-width:100%}}</style>
</head>
<body>
"""


def page_html() -> str:
    """docs/index.html. artifact의 <title>은 머리말에 다시 넣었으니 본문에서 뺀다."""
    body = re.sub(r"<title>.*?</title>\s*", "", PAGE.read_text(encoding="utf-8"), count=1)
    return HEAD + body + "\n</body>\n</html>\n"


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

    (DOCS / "index.html").write_text(page_html(), encoding="utf-8")
    (DOCS / ".nojekyll").touch()  # GitHub Pages가 파일을 그대로 내보내게 한다
    return index
