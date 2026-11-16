"""건너뛴 날 찾기.

컴퓨터가 꺼져 있으면 매일 0시 예약 작업은 앱을 열 때 오늘 몫만 한 번 실행한다. 그 사이 날짜와
KIPRIS 한도·점검으로 특허를 하나도 못 찾은 날을 찾아, 예약 작업이 오래된 날부터 채우게 한다.
"""

import json
import sqlite3
from datetime import date, timedelta

LOOKBACK_DAYS = 7  # 오늘 전 며칠까지 살펴볼지
MAX_PATENT_DAYS = 3  # 한 번에 특허를 채울 최대 날 수 (하루 몫마다 KIPRIS 하루 예산을 쓴다)


def _patents_missing(content: dict | None) -> bool:
    """그날 특허 브리핑이 없거나, 새 특허 없이 한도·점검으로 멈췄거나 검색을 하나도 못 한 날이면 True.

    검색 없이 순위만 다시 매기면(rescore) 검색 키워드가 빈 행이 생기므로 그것도 빈 날로 본다.
    """
    if content is None:
        return True
    if any(i.get("new") for i in content.get("items", [])):
        return False
    return bool(content.get("quota_stop") or content.get("kipris_down") or not content.get("keywords"))


def stale_weeks(conn: sqlite3.Connection, since: str = "") -> list[str]:
    """주간 리포트를 만든 뒤에 그 주의 뉴스·특허 브리핑이 새로 저장된(채워진) 주간의 마지막 날.

    since: 이 날짜 뒤에 끝나는 주간만 본다 (채우는 날이 들어 있을 수 있는 최근 주간).
    """
    out = []
    for r in conn.execute("SELECT end_day, created_at FROM weekly_reports WHERE end_day >= ? ORDER BY end_day",
                          (since,)):
        end = date.fromisoformat(r[0])
        start = (end - timedelta(days=6)).isoformat()
        latest = conn.execute(
            """SELECT MAX(created_at) FROM (
                   SELECT created_at FROM briefings WHERE day BETWEEN ? AND ?
                   UNION ALL SELECT created_at FROM patent_briefings WHERE day BETWEEN ? AND ?)""",
            (start, r[0], start, r[0])).fetchone()[0]
        if latest and latest > r[1]:
            out.append(r[0])
    return out


def find(conn: sqlite3.Connection, today: date, lookback: int = LOOKBACK_DAYS,
         max_patent_days: int = MAX_PATENT_DAYS) -> dict:
    """오늘 전 lookback일 가운데 채울 날.

    news: 뉴스 브리핑이 없는 날 (오래된 날부터. 앞날의 기사 기간 끝이 뒷날의 시작이 되므로 이 순서로 채운다)
    patents: 특허 브리핑을 채울 날. 뉴스 브리핑이 있는 날만 (뉴스를 채운 뒤 다시 부르면 그날도 나온다).
             오래된 날부터 max_patent_days일까지 (먼저 범위를 벗어날 날부터 채운다)
    patents_later: 한 번에 채울 수를 넘어 이번에 미룬 날 (다음 실행에서 다시 나온다)
    weeks: 다시 만들 주간 리포트 (만든 뒤에 그 주의 브리핑이 새로 저장됐다)
    weeks_missing: 만들지 못한 주간 리포트 (월요일 실행을 통째로 건너뛰었다). 그 주 브리핑을 채운 뒤 새로 만든다
    """
    lookback = max(1, lookback)
    first_news = conn.execute("SELECT MIN(day) FROM briefings").fetchone()[0]
    first_patents = conn.execute("SELECT MIN(day) FROM patent_briefings").fetchone()[0]
    first_week = conn.execute("SELECT MIN(end_day) FROM weekly_reports").fetchone()[0]
    days = [(today - timedelta(days=n)).isoformat() for n in range(lookback, 0, -1)]
    news_days = {r[0] for r in conn.execute("SELECT day FROM briefings")}
    patent_rows = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT day, content FROM patent_briefings")}
    week_days = {r[0] for r in conn.execute("SELECT end_day FROM weekly_reports")}

    news = [d for d in days if first_news and d >= first_news and d not in news_days]
    patents = [d for d in days if first_patents and d >= first_patents and d in news_days
               and _patents_missing(patent_rows.get(d))]
    cap = max(0, max_patent_days)
    patents, later = patents[:cap], patents[cap:]
    # 주간 리포트는 월요일 실행이 만든다. 오늘(월요일이면 오늘 실행이 만든다)은 빼고 본다.
    missing_weeks = [d for d in days if date.fromisoformat(d).weekday() == 0
                     and first_week and d > first_week and d not in week_days]
    # 다시 만들지 못한 주간이 다음 실행에서도 나오도록 한 주 더 거슬러 본다.
    since = (date.fromisoformat(days[0]) - timedelta(days=7)).isoformat()
    return {"today": today.isoformat(), "news": news, "patents": patents, "patents_later": later,
            "weeks": stale_weeks(conn, since), "weeks_missing": missing_weeks}
