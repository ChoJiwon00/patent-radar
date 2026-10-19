"""AI 뉴스 브리핑 저장·조회.

브리핑 내용은 Claude Code 스킬(/daily-briefing)이 만든다. 이 모듈은 그 결과(JSON)의
형식을 검사해서 저장하고, 웹페이지가 쓰기 좋게 관련 기사 정보를 붙여 돌려준다.

브리핑 JSON 형식:
{
  "day": "2026-10-19",
  "overview": "오늘 전체를 두세 문장으로",
  "topics": [
    {
      "name": "이차전지",
      "stories": [
        {"headline": "...", "summary": "...", "article_ids": [12, 40]}
      ],
      "keywords": [
        {"ko": "황화물계 고체전해질", "en": "sulfide solid electrolyte", "reason": "..."}
      ]
    }
  ]
}
"""

import json
import sqlite3
from datetime import date, datetime

from .config import KST


class BriefingError(ValueError):
    """브리핑 JSON 형식이 잘못됐을 때. 메시지를 보고 고쳐서 다시 저장하면 된다."""


def _obj(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise BriefingError(f"{where}는 JSON 객체여야 합니다.")
    return value


def _list(value, where: str) -> list:
    if not isinstance(value, list) or not value:
        raise BriefingError(f"{where}는 비어 있지 않은 배열이어야 합니다.")
    return value


def _text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BriefingError(f"{where}는 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def validate(data, known_article_ids: set[int]) -> dict:
    """형식을 검사하고 정리한 브리핑을 돌려준다. 문제가 있으면 BriefingError."""
    data = _obj(data, "최상위")
    try:
        day = date.fromisoformat(str(data.get("day"))).isoformat()
    except ValueError:
        raise BriefingError("day는 YYYY-MM-DD 형식이어야 합니다.") from None

    topics = []
    for i, t in enumerate(_list(data.get("topics"), "topics")):
        where = f"topics[{i}]"
        t = _obj(t, where)

        stories = []
        for j, s in enumerate(_list(t.get("stories"), f"{where}.stories")):
            sw = f"{where}.stories[{j}]"
            s = _obj(s, sw)
            ids = s.get("article_ids", [])
            if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids):
                raise BriefingError(f"{sw}.article_ids는 기사 번호(정수) 배열이어야 합니다.")
            unknown = [x for x in ids if x not in known_article_ids]
            if unknown:
                raise BriefingError(f"{sw}.article_ids에 없는 기사 번호가 있습니다: {unknown}")
            stories.append(
                {
                    "headline": _text(s.get("headline"), f"{sw}.headline"),
                    "summary": _text(s.get("summary"), f"{sw}.summary"),
                    "article_ids": ids,
                }
            )

        keywords = []
        for j, k in enumerate(_list(t.get("keywords"), f"{where}.keywords")):
            kw = f"{where}.keywords[{j}]"
            k = _obj(k, kw)
            keywords.append(
                {
                    "ko": _text(k.get("ko"), f"{kw}.ko"),
                    "en": _text(k.get("en"), f"{kw}.en"),
                    "reason": _text(k.get("reason"), f"{kw}.reason"),
                }
            )

        topics.append({"name": _text(t.get("name"), f"{where}.name"), "stories": stories, "keywords": keywords})

    return {"day": day, "overview": _text(data.get("overview"), "overview"), "topics": topics}


def save(conn: sqlite3.Connection, data) -> dict:
    """검사 후 저장한다. 같은 날 브리핑이 이미 있으면 새것으로 바꾼다."""
    known = {r[0] for r in conn.execute("SELECT id FROM news")}
    briefing = validate(data, known)
    conn.execute(
        "INSERT OR REPLACE INTO briefings (day, content, created_at) VALUES (?, ?, ?)",
        (briefing["day"], json.dumps(briefing, ensure_ascii=False), datetime.now(KST).isoformat(timespec="seconds")),
    )
    conn.commit()
    return briefing


def get(conn: sqlite3.Connection, day: date) -> dict | None:
    """그날의 브리핑. 각 이야기에 관련 기사(제목·언론사·링크)를 붙여 준다."""
    row = conn.execute("SELECT content, created_at FROM briefings WHERE day = ?", (day.isoformat(),)).fetchone()
    if row is None:
        return None
    briefing = json.loads(row["content"])
    briefing["created_at"] = row["created_at"]

    ids = {i for t in briefing["topics"] for s in t["stories"] for i in s["article_ids"]}
    articles = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT id, title, source, url FROM news WHERE id IN ({marks})", tuple(ids)):
            articles[r["id"]] = dict(r)
    for t in briefing["topics"]:
        for s in t["stories"]:
            s["articles"] = [articles[i] for i in s["article_ids"] if i in articles]
    return briefing


def for_page(found: dict) -> dict:
    """claude.ai 브리핑 페이지에 올릴 형태(get()의 결과를 받는다). 기사 번호 대신 기사 정보를 담는다."""
    return {
        "day": found["day"],
        "overview": found["overview"],
        "created_at": found["created_at"],
        "topics": [
            {
                "name": t["name"],
                "stories": [
                    {
                        "headline": s["headline"],
                        "summary": s["summary"],
                        "articles": [{k: a[k] for k in ("title", "source", "url")} for a in s["articles"]],
                    }
                    for s in t["stories"]
                ],
                "keywords": t["keywords"],
            }
            for t in found["topics"]
        ],
    }
