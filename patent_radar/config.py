"""경로, 비밀 값(.env), 뉴스 주제 설정."""

import os
import tomllib
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "radar.db"
TOPICS_PATH = ROOT / "config" / "topics.toml"

# 한국은 서머타임이 없어서 고정 오프셋이면 충분하다.
KST = timezone(timedelta(hours=9), "KST")

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Topic:
    name: str
    queries: list[str]


@dataclass(frozen=True)
class NewsSettings:
    lookback_days: int
    max_per_query: int
    topics: list[Topic]
    exclude_sources: frozenset[str] = frozenset()


def load_news_settings(path: Path = TOPICS_PATH) -> NewsSettings:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    news = raw.get("news", {})
    return NewsSettings(
        lookback_days=int(news.get("lookback_days", 1)),
        max_per_query=int(news.get("max_per_query", 30)),
        topics=[Topic(t["name"], list(t["queries"])) for t in raw.get("topics", [])],
        exclude_sources=frozenset(news.get("exclude_sources", [])),
    )


def kipris_api_key() -> str | None:
    return os.getenv("KIPRIS_API_KEY") or None
