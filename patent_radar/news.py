"""Google News RSS에서 주제별 최신 기사를 가져와 저장한다.

이미 저장한 기사(같은 링크이거나 제목이 사실상 같은 기사)는 다시 넣지 않으므로,
각 기사는 처음 수집된 날에만 '새 기사'로 나타난다.
"""

import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
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


def fetch_query(client: httpx.Client, query: str, settings: NewsSettings) -> list[Article]:
    resp = client.get(
        RSS_URL,
        params={
            "q": f"{query} when:{settings.lookback_days}d",
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        },
    )
    resp.raise_for_status()
    articles = [a for a in parse_rss(resp.text) if a.source not in settings.exclude_sources]
    # RSS는 관련도 순이라서 최신순으로 다시 정렬한다.
    articles.sort(key=lambda a: a.published_at or "", reverse=True)
    return articles[: settings.max_per_query]


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


def collect(conn: sqlite3.Connection, settings: NewsSettings | None = None) -> list[FetchResult]:
    """설정된 모든 검색어로 뉴스를 가져와 새 기사만 저장한다."""
    settings = settings or load_news_settings()
    fetched_at = datetime.now(KST).isoformat(timespec="seconds")
    results = []
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for topic in settings.topics:
            for query in topic.queries:
                try:
                    articles = fetch_query(client, query, settings)
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
