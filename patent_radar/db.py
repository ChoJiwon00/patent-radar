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
    fetched_at   TEXT NOT NULL       -- ISO 8601, KST. 이 날짜의 '새 기사'로 보여준다
);
CREATE INDEX IF NOT EXISTS idx_news_title_key ON news(title_key);
CREATE INDEX IF NOT EXISTS idx_news_fetched_at ON news(fetched_at);

-- 하루 한 건. /daily-briefing 스킬이 만든 AI 브리핑(주요 이야기 요약 + 특허 검색 키워드)
CREATE TABLE IF NOT EXISTS briefings (
    day        TEXT PRIMARY KEY,     -- YYYY-MM-DD
    content    TEXT NOT NULL,        -- 검사를 통과한 브리핑 JSON
    created_at TEXT NOT NULL         -- ISO 8601, KST
);

-- KIPRIS에서 찾은 특허. 검색 결과로 본 것은 모두 넣고, 브리핑에 넣은 날을 따로 적는다.
CREATE TABLE IF NOT EXISTS patents (
    id              INTEGER PRIMARY KEY,
    country         TEXT NOT NULL,             -- KR / US / CN
    doc_key         TEXT NOT NULL,             -- 상세 조회 키 (KR: 출원번호, US·CN: 문헌번호)
    number          TEXT NOT NULL,             -- 사람이 읽는 번호
    title           TEXT NOT NULL,
    applicant       TEXT,
    filed_at        TEXT,                      -- YYYY-MM-DD
    published_at    TEXT,                      -- YYYY-MM-DD (공개일, 없으면 등록일)
    priority_no     TEXT,                      -- 우선권 번호. 같으면 같은 발명을 여러 나라에 낸 것
    url             TEXT,
    abstract        TEXT,                      -- 원문 요약
    claims          TEXT,                      -- 원문 청구항
    claim_count     INTEGER,                   -- 청구항 수 (많을수록 권리 범위를 넓게 잡은 특허)
    title_ko        TEXT,                      -- 아래 셋은 AI가 쓴 한국어
    summary_ko      TEXT,
    key_claim_ko    TEXT,
    first_shown_day TEXT,                      -- 처음 브리핑에 넣은 날. 다음 날부터는 새 특허 목록에 다시 넣지 않는다
    UNIQUE (country, doc_key)
);
CREATE INDEX IF NOT EXISTS idx_patents_priority ON patents(priority_no);

-- 어떤 날 어떤 키워드 검색에서 그 특허가 나왔는지. 주목도 계산에 쓴다.
CREATE TABLE IF NOT EXISTS patent_matches (
    patent_id INTEGER NOT NULL REFERENCES patents(id),
    day       TEXT NOT NULL,
    keyword   TEXT NOT NULL,
    PRIMARY KEY (patent_id, day, keyword)
);

-- 하루 한 건. 그날 특허 브리핑에 넣은 특허 목록
CREATE TABLE IF NOT EXISTS patent_briefings (
    day        TEXT PRIMARY KEY,
    content    TEXT NOT NULL,              -- {"keywords": [...], "items": [...], "warnings": [...]}
    created_at TEXT NOT NULL
);

-- KIPRIS 무료 한도(서비스별 월 1,000회)를 넘지 않게 호출 수를 센다.
CREATE TABLE IF NOT EXISTS api_usage (
    month   TEXT NOT NULL,                 -- YYYY-MM
    service TEXT NOT NULL,                 -- kr / foreign
    calls   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, service)
);
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
