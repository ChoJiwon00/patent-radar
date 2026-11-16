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


def _collected(conn, guid, fetched_at):
    news.save_article(conn, news.Article(guid, f"기사 {guid}", None, f"https://x/{guid}", None), "이차전지", "q",
                      fetched_at)


NOW = datetime(2026, 11, 18, 0, 12, tzinfo=KST)  # 11-18 0시 실행


def test_backfill_window_for_one_skipped_day_ends_where_the_next_collection_starts():
    conn = db.connect(":memory:")
    _collected(conn, "g1", "2026-11-14T00:12:30+09:00")
    _collected(conn, "g2", "2026-11-16T21:25:00+09:00")  # 11-16은 밤에 실행 (그 전 24시간을 찾음)

    start, end = news.backfill_window(conn, date(2026, 11, 15), now=NOW)

    assert start == datetime(2026, 11, 14, 0, 12, 30, tzinfo=KST)
    assert end == datetime(2026, 11, 15, 21, 25, tzinfo=KST)


def test_backfill_window_for_yesterday_stops_where_todays_collection_starts():
    conn = db.connect(":memory:")
    _collected(conn, "g1", "2026-11-16T00:12:00+09:00")

    start, end = news.backfill_window(conn, date(2026, 11, 17), now=NOW)

    # 오늘 0시 12분 수집은 어제 0시 12분부터 찾으니, 건너뛴 11-17 몫은 그 전까지 (전날 나온 기사)
    assert (start, end) == (datetime(2026, 11, 16, 0, 12, tzinfo=KST), datetime(2026, 11, 17, 0, 12, tzinfo=KST))


def test_consecutive_skipped_days_are_tiled_without_overlap(monkeypatch):
    conn = db.connect(":memory:")
    _collected(conn, "g1", "2026-11-15T00:12:00+09:00")
    monkeypatch.setattr(news, "fetch_query", lambda *a, **k: [])
    now = datetime(2026, 11, 18, 0, 12, tzinfo=KST)

    first = news.backfill_window(conn, date(2026, 11, 16), now=now)
    news.collect(conn, _settings(), day=date(2026, 11, 16), now=now)
    # 기사가 없어도 다음 날 시작점을 알 수 있게 첫날 몫을 하나 저장해 둔다
    _collected(conn, "g2", first[1].isoformat())
    second = news.backfill_window(conn, date(2026, 11, 17), now=now)

    assert first == (datetime(2026, 11, 15, 0, 12, tzinfo=KST), datetime(2026, 11, 16, tzinfo=KST))
    assert second == (datetime(2026, 11, 16, tzinfo=KST), datetime(2026, 11, 17, 0, 12, tzinfo=KST))


def test_backfill_window_never_reaches_before_the_previous_midnight():
    conn = db.connect(":memory:")
    _collected(conn, "g1", "2026-11-01T00:12:00+09:00")  # 오래 끊겼다

    start, end = news.backfill_window(conn, date(2026, 11, 15), now=NOW)

    # 앞쪽 빈 날 기사는 그날들 몫으로 남겨 둔다 (나중에 오래된 날부터 채울 수 있게)
    assert start == datetime(2026, 11, 14, tzinfo=KST)
    assert end == datetime(2026, 11, 15, tzinfo=KST)  # 11-16도 건너뛴 날이라 그 뒤는 11-16 몫


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
    _collected(conn, "g0", "2026-11-14T00:12:00+09:00")
    seen = []

    def fake_fetch(client, query, settings, window=None):
        seen.append(window)
        return [news.Article("g1", "건너뛴 날 기사", "B", "https://x/1", "2026-11-14T09:00+09:00")]

    monkeypatch.setattr(news, "fetch_query", fake_fetch)
    now = datetime(2026, 11, 16, 0, 12, tzinfo=KST)
    results = news.collect(conn, _settings(), day=date(2026, 11, 15), now=now)

    assert [r.added for r in results] == [1]
    assert seen == [(datetime(2026, 11, 14, 0, 12, tzinfo=KST), datetime(2026, 11, 15, 0, 12, tzinfo=KST))]
    got = news.news_for_day(conn, date(2026, 11, 15))
    assert [a["title"] for a in got] == ["건너뛴 날 기사"]
    # 수집 시각 자리에 기간의 끝을 적어 다음 날 채울 때 시작점이 된다
    assert got[0]["fetched_at"] == "2026-11-15T00:12:00+09:00"
