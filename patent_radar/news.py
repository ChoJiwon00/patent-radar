"""Google News RSS에서 주제별 최신 기사를 가져와 저장한다.

이미 저장한 기사(같은 링크이거나 제목이 사실상 같은 기사)는 다시 넣지 않으므로,
각 기사는 처음 수집된 날에만 '새 기사'로 나타난다.

컴퓨터가 꺼져 있어 수집을 건너뛴 날은 `collect(conn, day=그날)`로 나중에 채운다 (지난 날짜 채우기).
0시 실행은 그 전 하루의 기사를 모으므로, 그날 몫은 대체로 전날 나온 기사다 (backfill_window).
"""

import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime

import httpx

from .config import KST, NewsSettings, load_news_settings

RSS_URL = "https://news.google.com/rss/search"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) patent-radar/0.1"


@dataclass
class Article:
    guid: str
    title: str
    source: str | None
    url: str
    published_at: str | None  # ISO 8601, KST


@dataclass
class FetchResult:
    topic: str
    query: str
    fetched: int
    added: int
    error: str | None = None


def parse_rss(xml_text: str) -> list[Article]:
    root = ET.fromstring(xml_text)
    articles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url:
            continue
        source = (item.findtext("source") or "").strip() or None
        # Google News는 제목 끝에 " - 언론사"를 붙인다.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        articles.append(
            Article(
                guid=(item.findtext("guid") or url).strip(),
                title=title,
                source=source,
                url=url,
                published_at=_to_kst(item.findtext("pubDate")),
            )
        )
    return articles


def _to_kst(rfc822: str | None) -> str | None:
    if not rfc822:
        return None
    try:
        return parsedate_to_datetime(rfc822).astimezone(KST).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return None


def title_key(title: str) -> str:
    """언론사마다 조금씩 다른 머리말·공백·문장부호를 지워서 같은 기사를 알아본다."""
    t = re.sub(r"^(\[[^\]]*\]\s*)+", "", title)  # [단독], [속보] 같은 머리말
    t = re.sub(r"[\W_]+", "", t)  # 한글·영문·숫자만 남긴다
    return t.lower()


def fetch_query(client: httpx.Client, query: str, settings: NewsSettings,
                window: tuple[datetime, datetime] | None = None) -> list[Article]:
    """검색어 하나로 최신 기사를 가져온다.

    window: (시작, 끝). 주면 그 사이(시작 포함, 끝 미포함)에 나온 기사만 찾는다 (지난 날짜 채우기).
    """
    if window:
        start, end = window
        # Google 뉴스의 after:/before:는 날짜 단위라 앞뒤로 하루씩 넉넉히 찾고, 아래에서 시각으로 다시 거른다.
        q = f"{query} after:{(start - timedelta(days=1)).date()} before:{(end + timedelta(days=1)).date()}"
        # 매일 수집은 하루 치를 검색어당 max_per_query개까지 받으니, 기간이 길면 그 날수만큼 받는다.
        limit = settings.max_per_query * max(1, math.ceil((end - start) / timedelta(days=1)))
    else:
        q = f"{query} when:{settings.lookback_days}d"
        limit = settings.max_per_query
    resp = client.get(
        RSS_URL,
        params={
            "q": q,
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        },
    )
    resp.raise_for_status()
    articles = [a for a in parse_rss(resp.text) if a.source not in settings.exclude_sources]
    if window:
        articles = [a for a in articles
                    if a.published_at and start <= datetime.fromisoformat(a.published_at) < end]
    # RSS는 관련도 순이라서 최신순으로 다시 정렬한다.
    articles.sort(key=lambda a: a.published_at or "", reverse=True)
    return articles[:limit]


def backfill_window(conn: sqlite3.Connection, day: date, lookback_days: int = 1,
                    now: datetime | None = None) -> tuple[datetime, datetime]:
    """건너뛴 날 day를 채울 기간 (시작 포함, 끝 미포함, 한국 시간).

    0시 실행은 그 전 하루에 나온 기사를 모으므로, day 몫은 대체로 전날(day-1)에 나온 기사다.
    기간이 앞뒤 수집과 겹치거나 비지 않도록 이어 붙인다:
    - 시작: 그 전 마지막 수집(또는 앞서 채운 날의 끝). 다만 전날 0시보다 앞으로는 가지 않는다.
    - 끝: 다음 날 수집이 있으면 그 수집이 찾기 시작한 때(수집 시각 - lookback_days),
      다음 날이 오늘이면 오늘 수집이 찾기 시작할 때(지금 - lookback_days),
      다음 날도 건너뛴 날이면 day 0시 (그 뒤는 다음 날 몫).
    여러 날을 건너뛰었으면 오래된 날부터 채운다 (앞날의 끝이 뒷날의 시작이 된다).
    """
    now = now or datetime.now(KST)
    floor = datetime.combine(day - timedelta(days=1), time(), KST)
    prev = conn.execute("SELECT MAX(fetched_at) FROM news WHERE fetched_at < ?", (day.isoformat(),)).fetchone()[0]
    start = max(datetime.fromisoformat(prev), floor) if prev else floor
    next_day = day + timedelta(days=1)
    nxt = conn.execute("SELECT MIN(fetched_at) FROM news WHERE fetched_at >= ?", (next_day.isoformat(),)).fetchone()[0]
    if nxt and nxt[:10] == next_day.isoformat():
        end = datetime.fromisoformat(nxt) - timedelta(days=lookback_days)
    elif next_day >= now.date():
        end = now - timedelta(days=lookback_days)
    else:
        end = datetime.combine(day, time(), KST)
    return start, max(start, end)


def catch_up_window(conn: sqlite3.Connection, now: datetime, lookback_days: int = 1) -> tuple[datetime, datetime] | None:
    """평소 수집의 기간. 마지막 수집이 lookback_days보다 오래됐으면 (마지막 수집, 지금), 아니면 None(최근 lookback_days일).

    앱을 늦게 열어 0시 실행이 늦어지면 '최근 하루'만으로는 그 전 수집과의 사이가 빈다. 그때는 그 사이를 이어서 찾는다.
    건너뛴 날을 채운 기간의 끝도 수집 시각 자리에 적혀 있어, 채운 뒤 오늘 수집이 그 끝에서 이어진다. 최대 사흘 치.
    """
    last = conn.execute("SELECT MAX(fetched_at) FROM news WHERE fetched_at <= ?",
                        (now.isoformat(timespec="seconds"),)).fetchone()[0]
    if not last:
        return None
    since = datetime.fromisoformat(last)
    if since >= now - timedelta(days=lookback_days):
        return None
    return max(since, now - timedelta(days=3)), now


def save_article(conn: sqlite3.Connection, a: Article, topic: str, query: str, fetched_at: str) -> bool:
    """새 기사면 저장하고 True를 돌려준다."""
    key = title_key(a.title)
    if conn.execute("SELECT 1 FROM news WHERE title_key = ?", (key,)).fetchone():
        return False
    cur = conn.execute(
        """INSERT OR IGNORE INTO news
           (guid, title_key, topic, query, title, source, url, published_at, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (a.guid, key, topic, query, a.title, a.source, a.url, a.published_at, fetched_at),
    )
    return cur.rowcount == 1


def collect(conn: sqlite3.Connection, settings: NewsSettings | None = None,
            day: date | None = None, now: datetime | None = None) -> list[FetchResult]:
    """설정된 모든 검색어로 뉴스를 가져와 새 기사만 저장한다.

    day: 건너뛴 지난 날짜. 주면 그날 몫(backfill_window)의 기사를 찾아 그날 수집한 기사로 저장한다.
    """
    settings = settings or load_news_settings()
    if day is None:
        now = now or datetime.now(KST)
        window = catch_up_window(conn, now, settings.lookback_days)
        fetched_at = now.isoformat(timespec="seconds")
    else:
        window = backfill_window(conn, day, settings.lookback_days, now)
        # 수집 시각 자리에 그 기간의 끝을 적는다. 그날 새 기사로 보이고(news_for_day), 다음 날 채울 때 시작점이 된다.
        end = window[1]
        fetched_at = (end if end.date() == day else datetime.combine(day, time(23, 59, 59), KST)).isoformat()
    results = []
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for topic in settings.topics:
            for query in topic.queries:
                try:
                    articles = fetch_query(client, query, settings, window)
                except (httpx.HTTPError, ET.ParseError) as e:
                    results.append(FetchResult(topic.name, query, 0, 0, f"{type(e).__name__}: {e}"))
                    continue
                added = sum(save_article(conn, a, topic.name, query, fetched_at) for a in articles)
                conn.commit()
                results.append(FetchResult(topic.name, query, len(articles), added))
    return results


def news_for_day(conn: sqlite3.Connection, day: date) -> list[dict]:
    """그날 처음 수집된 기사들 (주제별, 최신순)."""
    rows = conn.execute(
        """SELECT id, topic, query, title, source, url, published_at, fetched_at
           FROM news WHERE substr(fetched_at, 1, 10) = ?
           ORDER BY topic, published_at DESC""",
        (day.isoformat(),),
    )
    return [dict(r) for r in rows]


def available_days(conn: sqlite3.Connection, limit: int = 30) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT substr(fetched_at, 1, 10) AS day FROM news
           ORDER BY day DESC LIMIT ?""",
        (limit,),
    )
    return [r["day"] for r in rows]
