"""SQLite 저장소. 모든 데이터를 파일 하나(data/radar.db)에 보관한다."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id           INTEGER PRIMARY KEY,
    guid         TEXT NOT NULL UNIQUE,
    title_key    TEXT NOT NULL,      -- 같은 기사를 가려내려고 정규화한 제목
    topic        TEXT NOT NULL,
    query        TEXT NOT NULL,
    title        TEXT NOT NULL,
    source       TEXT,
    url          TEXT NOT NULL,
    published_at TEXT,               -- ISO 8601, KST
    fetched_at   TEXT NOT NULL,      -- ISO 8601, KST. 이 날짜의 '새 기사'로 보여준다
    keywords     TEXT                -- AI가 뽑은 기술 키워드(JSON 배열). 특허 검색 단계에서 채운다
);
CREATE INDEX IF NOT EXISTS idx_news_title_key ON news(title_key);
CREATE INDEX IF NOT EXISTS idx_news_fetched_at ON news(fetched_at);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    """연결을 열고, 끝나면 커밋한 뒤 닫는다."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
