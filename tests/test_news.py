from datetime import date

from patent_radar import db, news

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>[단독] 전고체 배터리 양산 앞당긴다 - 한국경제</title>
    <link>https://news.google.com/rss/articles/AAA</link>
    <guid isPermaLink="false">AAA</guid>
    <pubDate>Sun, 19 Oct 2026 01:30:00 GMT</pubDate>
    <source url="https://www.hankyung.com">한국경제</source>
  </item>
  <item>
    <title>HBM4 공급 확대 - 전자신문</title>
    <link>https://news.google.com/rss/articles/BBB</link>
    <guid isPermaLink="false">BBB</guid>
    <pubDate>not a date</pubDate>
    <source url="https://www.etnews.com">전자신문</source>
  </item>
  <item><title></title><link>https://example.com</link></item>
</channel></rss>"""


def test_parse_rss_strips_source_and_converts_to_kst():
    items = news.parse_rss(SAMPLE_RSS)

    assert [a.guid for a in items] == ["AAA", "BBB"]
    assert items[0].title == "[단독] 전고체 배터리 양산 앞당긴다"
    assert items[0].source == "한국경제"
    assert items[0].published_at == "2026-10-19T10:30+09:00"
    assert items[1].published_at is None


def test_title_key_ignores_tags_spaces_and_punctuation():
    assert news.title_key("[단독] 전고체 배터리, 양산 앞당긴다!") == news.title_key("전고체 배터리 양산 앞당긴다")
    assert news.title_key("HBM4 공급") != news.title_key("HBM3 공급")


def test_save_article_skips_duplicates():
    conn = db.connect(":memory:")
    a = news.Article("AAA", "전고체 배터리 양산", "한국경제", "https://x/1", "2026-10-19T10:30+09:00")
    same_story = news.Article("CCC", "[속보] 전고체 배터리, 양산", "매일경제", "https://x/2", None)

    assert news.save_article(conn, a, "이차전지", "이차전지", "2026-10-19T09:00:00+09:00") is True
    assert news.save_article(conn, a, "이차전지", "이차전지", "2026-10-20T09:00:00+09:00") is False
    assert news.save_article(conn, same_story, "이차전지", "전고체 배터리", "2026-10-20T09:00:00+09:00") is False

    assert len(news.news_for_day(conn, date(2026, 10, 19))) == 1
    assert news.news_for_day(conn, date(2026, 10, 20)) == []


# ---------------------------------------------------------------- 건너뛴 지난 날짜 채우기

from datetime import datetime  # noqa: E402

from patent_radar.config import KST, NewsSettings, Topic  # noqa: E402

WINDOW_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>그 전 기사 - A</title><link>https://x/a</link><guid>a</guid>
    <pubDate>Fri, 13 Nov 2026 14:00:00 GMT</pubDate><source url="https://a">A</source></item>
  <item><title>건너뛴 날 기사 1 - B</title><link>https://x/b</link><guid>b</guid>
    <pubDate>Sat, 14 Nov 2026 03:00:00 GMT</pubDate><source url="https://b">B</source></item>
  <item><title>건너뛴 날 기사 2 - C</title><link>https://x/c</link><guid>c</guid>
    <pubDate>Sun, 15 Nov 2026 09:00:00 GMT</pubDate><source url="https://c">C</source></item>
  <item><title>다음 날 기사 - D</title><link>https://x/d</link><guid>d</guid>
    <pubDate>Sun, 15 Nov 2026 16:00:00 GMT</pubDate><source url="https://d">D</source></item>
</channel></rss>"""


class FakeHttp:
    def __init__(self, text):
        self.text, self.params = text, []

    def get(self, url, params):
        self.params.append(params)
        return self

    def raise_for_status(self):
        pass


def _settings(per_query=30):
    return NewsSettings(lookback_days=1, max_per_query=per_query, topics=[Topic("이차전지", ["리튬인산철"])])


def test_backfill_window_starts_at_last_collection_before_the_day():
    conn = db.connect(":memory:")
    a = news.Article("g1", "앞 수집 기사", None, "https://x/1", None)
    b = news.Article("g2", "뒤 수집 기사", None, "https://x/2", None)
    news.save_article(conn, a, "이차전지", "q", "2026-11-14T00:12:30+09:00")
    news.save_article(conn, b, "이차전지", "q", "2026-11-16T21:25:00+09:00")

    start, end = news.backfill_window(conn, date(2026, 11, 15))

    assert start == datetime(2026, 11, 14, 0, 12, 30, tzinfo=KST)
    assert end == datetime(2026, 11, 16, tzinfo=KST)


def test_backfill_window_is_at_most_three_days():
    conn = db.connect(":memory:")
    news.save_article(conn, news.Article("g1", "오래전 기사", None, "https://x/1", None), "이차전지", "q",
                      "2026-11-01T00:12:00+09:00")

    start, end = news.backfill_window(conn, date(2026, 11, 15))

    assert start == datetime(2026, 11, 13, tzinfo=KST)


def test_fetch_query_with_window_uses_date_operators_and_keeps_only_that_period():
    http = FakeHttp(WINDOW_RSS)
    window = (datetime(2026, 11, 14, 0, 12, tzinfo=KST), datetime(2026, 11, 16, tzinfo=KST))

    got = news.fetch_query(http, "리튬인산철", _settings(), window)

    q = http.params[0]["q"]
    assert q == "리튬인산철 after:2026-11-13 before:2026-11-17"
    assert "when:" not in q
    # 11-13 23:00(KST)은 기간 앞, 11-16 01:00(KST)은 기간 뒤라서 빠진다.
    assert [a.guid for a in got] == ["c", "b"]


def test_fetch_query_with_window_takes_max_per_query_for_each_day():
    http = FakeHttp(WINDOW_RSS)
    window = (datetime(2026, 11, 14, 0, 12, tzinfo=KST), datetime(2026, 11, 16, tzinfo=KST))

    # 이틀 가까운 기간이라 검색어당 1개 × 2일 = 2개
    assert len(news.fetch_query(http, "q", _settings(per_query=1), window)) == 2
    assert len(news.fetch_query(http, "q", _settings(per_query=1))) == 1


def test_collect_for_a_skipped_day_saves_articles_as_that_days_news(monkeypatch):
    conn = db.connect(":memory:")
    news.save_article(conn, news.Article("g0", "앞 수집 기사", None, "https://x/0", None), "이차전지", "q",
                      "2026-11-14T00:12:00+09:00")
    seen = []

    def fake_fetch(client, query, settings, window=None):
        seen.append(window)
        return [news.Article("g1", "건너뛴 날 기사", "B", "https://x/1", "2026-11-15T09:00+09:00")]

    monkeypatch.setattr(news, "fetch_query", fake_fetch)
    results = news.collect(conn, _settings(), day=date(2026, 11, 15))

    assert [r.added for r in results] == [1]
    assert seen == [news.backfill_window(conn, date(2026, 11, 15))]
    assert [a["title"] for a in news.news_for_day(conn, date(2026, 11, 15))] == ["건너뛴 날 기사"]
    assert news.news_for_day(conn, date(2026, 11, 15))[0]["fetched_at"] == "2026-11-15T23:59:59+09:00"
