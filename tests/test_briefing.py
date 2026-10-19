from datetime import date

import pytest

from patent_radar import briefing, db, news


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    article = news.Article("AAA", "전고체 배터리 양산", "한국경제", "https://x/1", "2026-10-19T10:30+09:00")
    news.save_article(conn, article, "이차전지", "이차전지", "2026-10-19T09:00:00+09:00")
    return conn


def sample(**overrides):
    data = {
        "day": "2026-10-19",
        "overview": "전고체 배터리 양산 소식이 이어졌다.",
        "topics": [
            {
                "name": "이차전지",
                "stories": [{"headline": "전고체 양산 앞당겨", "summary": "양산 일정이 빨라졌다.", "article_ids": [1]}],
                "keywords": [{"ko": "황화물계 고체전해질", "en": "sulfide solid electrolyte", "reason": "전고체 양산"}],
            }
        ],
    }
    data.update(overrides)
    return data


def test_save_and_get_attaches_articles(conn):
    briefing.save(conn, sample())

    found = briefing.get(conn, date(2026, 10, 19))

    story = found["topics"][0]["stories"][0]
    assert story["articles"][0]["source"] == "한국경제"
    assert found["topics"][0]["keywords"][0]["en"] == "sulfide solid electrolyte"
    assert briefing.get(conn, date(2026, 10, 20)) is None


def test_for_page_swaps_article_ids_for_article_info(conn):
    briefing.save(conn, sample())

    page = briefing.for_page(briefing.get(conn, date(2026, 10, 19)))

    story = page["topics"][0]["stories"][0]
    assert "article_ids" not in story
    assert story["articles"] == [{"title": "전고체 배터리 양산", "source": "한국경제", "url": "https://x/1"}]
    assert page["created_at"]


def test_saving_again_replaces_same_day(conn):
    briefing.save(conn, sample())
    briefing.save(conn, sample(overview="다시 만든 브리핑"))

    assert briefing.get(conn, date(2026, 10, 19))["overview"] == "다시 만든 브리핑"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"day": "어제"}, "day"),
        ({"overview": ""}, "overview"),
        ({"topics": []}, "topics"),
    ],
)
def test_rejects_bad_fields(conn, overrides, message):
    with pytest.raises(briefing.BriefingError, match=message):
        briefing.save(conn, sample(**overrides))


def test_rejects_unknown_article_ids(conn):
    data = sample()
    data["topics"][0]["stories"][0]["article_ids"] = [1, 999]

    with pytest.raises(briefing.BriefingError, match="999"):
        briefing.save(conn, data)
