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
    # 이 주제에 맞는 특허 분류(IPC) 접두어. 비어 있으면 거르지 않는다.
    ipc: tuple[str, ...] = ()


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
        topics=[Topic(t["name"], list(t["queries"]), tuple(t.get("ipc", []))) for t in raw.get("topics", [])],
        exclude_sources=frozenset(news.get("exclude_sources", [])),
    )


@dataclass(frozen=True)
class InterestKeyword:
    ko: str
    en: str
    topic: str | None = None
    alt: tuple[str, ...] = ()
    countries: tuple[str, ...] = ("KR",)


@dataclass(frozen=True)
class Interests:
    """뉴스와 상관없이 늘 지켜보는 것들."""

    companies: tuple[str, ...] = ()
    keywords: tuple[InterestKeyword, ...] = ()


def load_interests(path: Path = TOPICS_PATH) -> Interests:
    with open(path, "rb") as f:
        raw = tomllib.load(f).get("interests", {})
    keywords = tuple(
        InterestKeyword(k["ko"], k["en"], k.get("topic"), tuple(k.get("alt", [])), tuple(k.get("countries", ["KR"])))
        for k in raw.get("keywords", [])
    )
    return Interests(tuple(raw.get("companies", [])), keywords)


@dataclass(frozen=True)
class PatentSettings:
    kr_keywords_per_topic: int = 4
    foreign_keywords_per_topic: int = 2
    max_kr_retries: int = 3
    per_search: int = 1
    scan_per_search: int = 10
    recent_years: int = 3
    hot_threshold: int = 5
    hot_window_days: int = 7
    spotlight_size: int = 5
    monthly_call_limit: int = 950


def load_patent_settings(path: Path = TOPICS_PATH) -> PatentSettings:
    with open(path, "rb") as f:
        raw = tomllib.load(f).get("patents", {})
    defaults = PatentSettings()
    return PatentSettings(**{k: int(raw.get(k, getattr(defaults, k))) for k in defaults.__dataclass_fields__})


def kipris_api_key() -> str | None:
    return os.getenv("KIPRIS_API_KEY") or None
