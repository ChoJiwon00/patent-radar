import xml.etree.ElementTree as ET
from datetime import date

import pytest

from patent_radar import db, patents
from patent_radar.config import PatentSettings

KR_XML = """<response><header><resultCode>00</resultCode></header><body><items><item>
<applicantName>삼성에스디아이 주식회사|삼성에스디아이 주식회사</applicantName>
<applicationDate>20250409</applicationDate><applicationNumber>1020250046256</applicationNumber>
<astrtCont>황화물계 고체 전해질 슬러리</astrtCont><inventionTitle>전고체 전지용 슬러리</inventionTitle>
<openDate>20261016</openDate><openNumber>1020260151015</openNumber><registerNumber></registerNumber>
</item></items></body></response>"""

FOREIGN_XML = """<response><header><resultCode></resultCode></header><body><items><searchResult>
<ltrtno>202600312050A1</ltrtno><applicationNo>19561896</applicationNo><registerNo></registerNo>
<openNumber>20260312050</openNumber><openDate>20261008</openDate><applicationDate>20260310</applicationDate>
<priorityNo>2025-062014</priorityNo><priorityDate>20250403</priorityDate>
<applicant>TOYOTA JIDOSHA KABUSHIKI KAISHA|TOYOTA JIDOSHA KABUSHIKI KAISHA</applicant>
<inventionName>NEGATIVE ELECTRODE</inventionName></searchResult></items></body></response>"""


def test_parse_kr_search():
    [f] = patents.parse_kr_search(ET.fromstring(KR_XML))
    assert f.number == "공개 10-2026-0151015"
    assert f.applicant == "삼성에스디아이 주식회사"
    assert f.published_at == "2026-10-16"
    assert f.url == "https://doi.org/10.8080/1020250046256"
    assert f.abstract == "황화물계 고체 전해질 슬러리"


def test_parse_foreign_search_builds_number_link_and_priority():
    [f] = patents.parse_foreign_search(ET.fromstring(FOREIGN_XML), "US")
    assert f.number == "US 2026/0312050 A1"
    assert f.url == "https://patents.google.com/patent/US20260312050A1"
    assert f.priority_no == "2025-062014|20250403"


def test_us_grant_number_drops_leading_zero():
    xml = FOREIGN_XML.replace("<registerNo></registerNo>", "<registerNo>09432298</registerNo>").replace(
        "202600312050A1", "09432298B1")
    [f] = patents.parse_foreign_search(ET.fromstring(xml), "US")
    assert f.url == "https://patents.google.com/patent/US9432298B1"


def test_old_patents_are_skipped(conn):
    client = FakeClient({("KR", A.ko): [found("KR", "old", published="2016-08-30"), found("KR", "k1")]})
    content = patents.collect(conn, client, [A], date(2026, 10, 21), SETTINGS)
    keys = [conn.execute("SELECT doc_key FROM patents WHERE id = ?", (i["patent_id"],)).fetchone()[0]
            for i in content["items"]]
    assert keys == ["k1"]


def test_error_code_raises_without_details():
    root = ET.fromstring("<response><header><resultCode>30</resultCode><resultMsg>미등록</resultMsg></header></response>")
    with pytest.raises(patents.KiprisError, match="30"):
        patents.check_response(root)


def test_foreign_claim_count():
    root = ET.fromstring("<r><claimText>1. A film.    2. The film of claim 1.   3. A film made by claim 1.   "
                         "4. An electrode using the film of claim 3.</claimText></r>")
    assert patents.parse_foreign_claims(root)[1] == 4


def found(country, key, published="2026-10-01", priority=None):
    return patents.Found(country, key, f"{country}-{key}", f"title {key}", "ACME", None, published, priority,
                         f"https://example.com/{key}")


class FakeClient:
    def __init__(self, results):
        self.results = results  # {(country, keyword_ko): [Found]}
        self.detail_calls = []

    def search(self, country, kw):
        return self.results.get((country, kw.ko), [])

    def details(self, country, doc_key):
        self.detail_calls.append(doc_key)
        if country == "CN":
            return {"abstract": "abstract"}
        return {"claims": "1. claim", "claim_count": 1}


SETTINGS = PatentSettings(per_search=1, hot_threshold=5, hot_window_days=7, spotlight_size=5)
A = patents.Keyword("황화물 전해질", "sulfide electrolyte")
B = patents.Keyword("양극재", "cathode")


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_new_patents_are_not_repeated_next_day(conn):
    client = FakeClient({("KR", A.ko): [found("KR", "k1"), found("KR", "k2")]})

    day1 = patents.collect(conn, client, [A], date(2026, 10, 21), SETTINGS)
    day2 = patents.collect(conn, client, [A], date(2026, 10, 22), SETTINGS)

    assert [i["new"] for i in day1["items"]] == [True]
    first = day1["items"][0]["patent_id"]
    assert [i["patent_id"] for i in day2["items"]] != [first]
    assert client.detail_calls == ["k1", "k2"]


def test_patent_hit_by_two_keywords_and_two_countries_is_ranked_hot(conn):
    shared = "JP-1|20250101"
    client = FakeClient({
        ("US", A.ko): [found("US", "u1", priority=shared)],
        ("US", B.ko): [found("US", "u1", priority=shared)],
        ("CN", A.ko): [found("CN", "c1", priority=shared)],
        ("KR", B.ko): [found("KR", "k9")],
    })

    content = patents.collect(conn, client, [A, B], date(2026, 10, 21), SETTINGS)

    ranked = [i for i in content["items"] if i["rank"]]
    assert ranked[0]["rank"] == 1
    # 키워드 2개(+4) + 미국·중국 함께 출원(+2) = 6점
    assert ranked[0]["score"] == 6
    assert any("함께 출원" in r for r in ranked[0]["reasons"])
    plain = [i for i in content["items"] if not i["rank"]]
    assert all(i["score"] < SETTINGS.hot_threshold for i in plain)


def test_hot_patent_stays_on_top_on_following_days(conn):
    shared = "JP-1|20250101"
    day1 = FakeClient({("US", A.ko): [found("US", "u1", priority=shared)],
                       ("US", B.ko): [found("US", "u1", priority=shared)],
                       ("CN", A.ko): [found("CN", "c1", priority=shared)]})
    patents.collect(conn, day1, [A, B], date(2026, 10, 21), SETTINGS)

    day2 = FakeClient({("US", A.ko): [found("US", "u1", priority=shared), found("US", "u2")]})
    content = patents.collect(conn, day2, [A], date(2026, 10, 22), SETTINGS)

    top = [i for i in content["items"] if i["rank"] == 1][0]
    assert top["new"] is False
    assert conn.execute("SELECT doc_key FROM patents WHERE id = ?", (top["patent_id"],)).fetchone()[0] == "u1"


def test_save_summaries_and_report(conn):
    client = FakeClient({("KR", A.ko): [found("KR", "k1")]})
    content = patents.collect(conn, client, [A], date(2026, 10, 21), SETTINGS)
    pid = content["items"][0]["patent_id"]

    detail = [{"heading": "어떤 발명인가요", "body": "슬러리에 관한 발명"},
              {"heading": "구성·방법 자세히", "body": "- 황화물 전해질\n- 바인더"},
              {"heading": "용어 풀이", "body": "- 슬러리: 걸쭉한 혼합물"}]
    summary = {"title_ko": "전고체 슬러리", "summary_ko": "요약", "key_claim_ko": "핵심", "detail_ko": detail}

    with pytest.raises(patents.SummaryError, match="999"):
        patents.save_summaries(conn, {"items": [{"id": 999, **summary}]}, date(2026, 10, 21))
    with pytest.raises(patents.SummaryError, match="detail_ko"):
        patents.save_summaries(conn, {"items": [{"id": pid, **summary, "detail_ko": detail[:1]}]}, date(2026, 10, 21))
    patents.save_summaries(conn, {"items": [{"id": pid, **summary}]}, date(2026, 10, 21))

    assert patents.for_claude(conn, date(2026, 10, 21))["items"][0]["needs_summary"] is False
    page = patents.for_page(conn, date(2026, 10, 21))
    assert page["items"][0]["detail"][1]["body"] == "- 황화물 전해질\n- 바인더"
    text = patents.report_markdown(page)
    assert "# 2026-10-21 특허 브리핑" in text
    assert "## 한국 새 특허" in text
    assert "### 전고체 슬러리" in text
    assert "> **핵심 청구항**: 핵심" in text
    assert "**구성·방법 자세히**\n\n- 황화물 전해질" in text


def test_old_database_gets_new_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    # detail_ko 칸이 생기기 전의 표 구조
    old.executescript("\n".join(line for line in db.SCHEMA.splitlines() if "detail_ko" not in line))
    old.close()

    conn = db.connect(path)

    assert "detail_ko" in {r["name"] for r in conn.execute("PRAGMA table_info(patents)")}


def test_keywords_from_briefing_alternate_topics(conn):
    from patent_radar import briefing, news

    news.save_article(conn, news.Article("g", "기사", "언론", "https://x", None), "반도체", "반도체", "2026-10-21T00:10:00+09:00")
    kw = lambda ko: {"ko": ko, "en": ko + "-en", "reason": "r"}
    story = [{"headline": "h", "summary": "s", "article_ids": [1]}]
    briefing.save(conn, {"day": "2026-10-21", "overview": "o", "topics": [
        {"name": "반도체", "stories": story, "keywords": [kw("a1"), kw("a2"), kw("a3")]},
        {"name": "이차전지", "stories": story, "keywords": [kw("b1"), kw("b2")]},
    ]})

    source, picked = patents.keywords_from_briefing(conn, date(2026, 10, 22), kr_per_topic=2, foreign_per_topic=1)

    assert source == "2026-10-21"
    assert [k.ko for k in picked] == ["a1", "b1", "a2", "b2"]
    assert [k.countries for k in picked] == [("KR", "US", "CN"), ("KR", "US", "CN"), ("KR",), ("KR",)]


def test_collect_searches_only_the_keywords_countries(conn):
    class Recorder(FakeClient):
        def __init__(self):
            super().__init__({})
            self.searched = []

        def search(self, country, kw):
            self.searched.append((country, kw.ko))
            return []

    client = Recorder()
    patents.collect(conn, client, [patents.Keyword("가", "a"), patents.Keyword("나", "b", ("KR",))],
                    date(2026, 10, 21), SETTINGS)

    assert client.searched == [("KR", "가"), ("US", "가"), ("CN", "가"), ("KR", "나")]
