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
