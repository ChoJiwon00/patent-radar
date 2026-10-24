"""검색 정확도(분류 코드·재검색·관련도), 주목 점수 강화, 관심 특허 테스트."""

from datetime import date

import pytest

from patent_radar import briefing, db, news, patents, stars
from patent_radar.config import PatentSettings

from test_patents import FakeClient, found

SETTINGS = PatentSettings(per_search=1, hot_threshold=5, hot_window_days=7, spotlight_size=5, max_kr_retries=1)
DAY = date(2026, 10, 21)
DETAIL = [{"heading": "a", "body": "b"}, {"heading": "c", "body": "d"}, {"heading": "e", "body": "f"}]


@pytest.fixture
def conn():
    return db.connect(":memory:")


def summarize(conn, pid, relevant, note="이유"):
    patents.save_summaries(conn, {"items": [{
        "id": pid, "title_ko": "제목", "summary_ko": "요약", "key_claim_ko": "핵심", "detail_ko": DETAIL,
        "relevant": relevant, "relevance_note": note}]}, DAY)


def test_ipc_filter_drops_patents_outside_the_topic(conn):
    kw = patents.Keyword("배터리 교환", "battery swap", ("KR",), topic="이차전지")
    client = FakeClient({("KR", kw.ko): [found("KR", "display", ipc="G09G5/00|B60R16/03"),
                                         found("KR", "cell", ipc="H01M10/0525")]})

    content = patents.collect(conn, client, [kw], DAY, SETTINGS, {"이차전지": ("H01M",)})

    keys = [conn.execute("SELECT doc_key FROM patents WHERE id = ?", (i["patent_id"],)).fetchone()[0]
            for i in content["items"]]
    assert keys == ["cell"]


def test_korean_search_retries_with_other_wording_once_per_limit(conn):
    a = patents.Keyword("황화물계 고체전해질", "x", ("KR",), alt=("황화물 고체 전해질",))
    b = patents.Keyword("고대역폭 플래시", "y", ("KR",), alt=("고대역폭 낸드",))
    client = FakeClient({("KR", "황화물 고체 전해질"): [found("KR", "k1")],
                         ("KR", "고대역폭 낸드"): [found("KR", "k2")]})

    content = patents.collect(conn, client, [a, b], DAY, SETTINGS)

    # 하루 재검색 한도(1회)라서 첫 키워드만 다시 찾는다.
    assert len(content["items"]) == 1
    assert any("다시 찾음" in w for w in content["warnings"])
    match = conn.execute("SELECT keyword FROM patent_matches").fetchone()[0]
    assert match == "황화물계 고체전해질"  # 매칭은 원래 키워드로 기록


def test_low_relevance_patent_is_folded_and_never_ranked(conn):
    shared = "1|20250101"
    a, b = patents.Keyword("가", "a"), patents.Keyword("나", "b")
    client = FakeClient({("US", "가"): [found("US", "u1", priority=shared)],
                         ("US", "나"): [found("US", "u1", priority=shared)],
                         ("CN", "가"): [found("CN", "c1", priority=shared)]})
    content = patents.collect(conn, client, [a, b], DAY, SETTINGS)
    hot = [i for i in content["items"] if i["rank"]][0]["patent_id"]
    other = [i for i in content["items"] if not i["rank"]][0]["patent_id"]
    summarize(conn, hot, relevant=False, note="화면 표시 기술")
    summarize(conn, other, relevant=True)

    # 검색 없이 순위만 다시 매겨도(rescore) 관련 낮음 특허는 빠지고, 그날 특허 목록과 경고는 그대로다.
    before = patents.collect(conn, FakeClient({}), [], DAY, SETTINGS)
    assert not any(i["rank"] for i in before["items"])
    assert {i["patent_id"] for i in before["items"]} == {hot, other}

    page = patents.for_page(conn, DAY)
    assert [i["relevance_note"] for i in page["low"]] == ["화면 표시 기술"]
    assert "## 관련도가 낮은 특허" in patents.report_markdown(page)


def test_patent_judged_irrelevant_later_leaves_past_rankings(conn):
    shared = "1|20250101"
    a, b, c = patents.Keyword("가", "a"), patents.Keyword("나", "b"), patents.Keyword("다", "c")
    client = FakeClient({("US", "가"): [found("US", "u1", priority=shared), found("US", "u2", priority="2|x")],
                         ("US", "나"): [found("US", "u1", priority=shared), found("US", "u2", priority="2|x")],
                         ("US", "다"): [found("US", "u2", priority="2|x")],
                         ("CN", "가"): [found("CN", "c1", priority=shared), found("CN", "c2", priority="2|x")]})
    content = patents.collect(conn, client, [a, b, c], DAY, SETTINGS)
    ranked = sorted((i for i in content["items"] if i["rank"]), key=lambda i: i["rank"])
    assert len(ranked) >= 2
    first = ranked[0]["patent_id"]

    # 다음 날 해설을 쓰다가 1위를 '관련 낮음'으로 판정해도, 지난 날짜의 순위는 저장된 그대로다.
    summarize(conn, first, relevant=False, note="다른 기술")
    page = patents.for_page(conn, DAY)
    first_key = "US-" + conn.execute("SELECT doc_key FROM patents WHERE id = ?", (first,)).fetchone()[0]
    assert first_key not in {i["key"] for i in page["spotlight"]}
    assert [i["rank"] for i in page["spotlight"]] == list(range(1, len(page["spotlight"]) + 1))
    assert [i["relevance_note"] for i in page["low"]] == ["다른 기술"]
    assert page["low"][0]["rank"] is None


def test_old_google_links_for_us_patents_are_shown_as_uspto(conn):
    stored = patents.Found("US", "202600312149A1", "US 2026/0312149 A1", "LFP CELL", "ACME", None, "2026-10-08", None,
                           "https://patents.google.com/patent/US20260312149A1")
    client = FakeClient({("US", "가"): [stored]})
    patents.collect(conn, client, [patents.Keyword("가", "a", ("US",))], DAY, SETTINGS)
    conn.execute("UPDATE patents SET url = ?", (stored.url,))  # 예전 규칙으로 저장된 주소
    pdf = "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/20260312149"

    page = patents.for_page(conn, DAY)
    assert [i["url"] for i in page["spotlight"] + page["items"]] == [pdf]
    assert patents.refresh_links(conn) == 1
    assert conn.execute("SELECT url FROM patents").fetchone()[0] == pdf


def test_relevance_can_be_saved_without_rewriting_summary(conn):
    client = FakeClient({("KR", "가"): [found("KR", "k1")]})
    content = patents.collect(conn, client, [patents.Keyword("가", "a", ("KR",))], DAY, SETTINGS)
    pid = content["items"][0]["patent_id"]
    summarize(conn, pid, relevant=True)

    patents.save_summaries(conn, {"items": [{"id": pid, "relevant": False, "relevance_note": "다시 보니 무관"}]}, DAY)

    row = conn.execute("SELECT summary_ko, relevant FROM patents WHERE id = ?", (pid,)).fetchone()
    assert (row["summary_ko"], row["relevant"]) == ("요약", 0)


def test_score_counts_priority_country_pct_company_and_buzz(conn):
    class Detailed(FakeClient):
        def details(self, country, doc_key):
            return {"claims": "1. x", "claim_count": 1, "priority_no": "63622744|20240119",
                    "priority_country": "US", "pct_no": "PCTUS2025012076", "status": "공개"}

    kw = patents.Keyword("분리막", "separator", ("KR",), buzz=5)
    client = Detailed({("KR", "분리막"): [found("KR", "k1", applicant="셀가드 엘엘씨")]})

    content = patents.collect(conn, client, [kw], DAY, SETTINGS, companies=("셀가드",))

    item = content["items"][0]
    # 키워드 1개(+2) + 한국·미국 출원(+2) + PCT(+1) + 관심 회사(+2) + 뉴스 화제(+1) = 8점
    assert item["score"] == 8
    assert item["rank"] == 1
    assert item["company"] == "셀가드"
    assert "관심 회사(셀가드)" in item["reasons"]
    assert patents.for_page(conn, DAY)["spotlight"][0]["family"] == ["KR", "US"]


def test_interest_keywords_are_marked(conn):
    kw = patents.Keyword("하이브리드 본딩", "hybrid bonding", ("KR",), interest=True)
    client = FakeClient({("KR", kw.ko): [found("KR", "k1")]})

    content = patents.collect(conn, client, [kw], DAY, SETTINGS)

    assert content["items"][0]["interest"] is True
    assert content["keywords"] == [{"ko": "하이브리드 본딩", "en": "hybrid bonding", "interest": True}]


def test_briefing_keywords_carry_topic_alt_and_buzz(conn):
    for n in range(1, 6):
        news.save_article(conn, news.Article(f"g{n}", f"기사 {n}", "언론", f"https://x/{n}", None),
                          "ESS", "ESS", "2026-10-21T00:10:00+09:00")
    briefing.save(conn, {"day": "2026-10-21", "overview": "o", "topics": [{
        "name": "ESS",
        "stories": [{"headline": "h", "summary": "s", "article_ids": [1, 2, 3, 4, 5]}],
        "keywords": [{"ko": "나트륨이온 배터리", "en": "sodium ion battery", "reason": "r",
                      "alt": ["나트륨 이온 전지"], "story": 1}],
    }]})

    _, [kw] = patents.keywords_from_briefing(conn, DAY, 1, 1)

    assert (kw.topic, kw.alt, kw.buzz) == ("ESS", ("나트륨 이온 전지",), 5)


def test_briefing_rejects_bad_story_number(conn):
    news.save_article(conn, news.Article("g", "기사", "언론", "https://x", None), "ESS", "ESS", "2026-10-21T00:10:00+09:00")
    data = {"day": "2026-10-21", "overview": "o", "topics": [{
        "name": "ESS", "stories": [{"headline": "h", "summary": "s", "article_ids": [1]}],
        "keywords": [{"ko": "a", "en": "a", "reason": "r", "story": 3}]}]}
    with pytest.raises(briefing.BriefingError, match="story"):
        briefing.save(conn, data)


def test_split_applicants_keeps_company_suffixes():
    assert patents.split_applicants("Samsung Electronics Co., Ltd.") == ["Samsung Electronics Co., Ltd."]
    assert patents.split_applicants("ZIJIN MINING GROUP CO., LTD., ZIJIN NEW ENERGY CO., LTD.") == [
        "ZIJIN MINING GROUP CO., LTD.", "ZIJIN NEW ENERGY CO., LTD."]
    assert patents.split_applicants("현대자동차주식회사, 포항공과대학교 산학협력단") == ["현대자동차주식회사", "포항공과대학교 산학협력단"]
    assert patents.split_applicants("A사 · B사") == ["A사", "B사"]


def test_stars_sync_and_status_check(conn, tmp_path):
    client = FakeClient({("KR", "가"): [found("KR", "1020250046256")]})
    patents.collect(conn, client, [patents.Keyword("가", "a", ("KR",))], DAY, SETTINGS)
    folder = tmp_path / "stars"
    folder.mkdir()
    (folder / "KR-1020250046256.json").write_text(
        '{"key": "KR-1020250046256", "starred_at": "2026-10-21T09:00:00+09:00"}', encoding="utf-8")
    (folder / "US-x.json").write_text('{"key": "US-x"}', encoding="utf-8")

    assert stars.sync(conn, stars.read_page_docs(tmp_path)) == (2, 0)
    conn.execute("UPDATE stars SET status = '공개'")

    class Granted(FakeClient):
        def details(self, country, doc_key):
            return {"status": "등록"}

    changes, warnings = stars.check(conn, Granted({}))
    assert changes == [{"key": "KR-1020250046256", "before": "공개", "after": "등록"}]
    assert warnings == []

    (folder / "US-x.json").unlink()
    assert stars.sync(conn, stars.read_page_docs(tmp_path)) == (0, 1)


def test_page_uses_standard_material_names_but_keeps_official_titles(conn):
    official = patents.Found("KR", "k2", "KR-k2", "인산철 리튬 정극 재료", "ACME", None, "2026-10-01", None,
                             "https://example.com/k2")
    client = FakeClient({("KR", "가"): [found("KR", "k1")], ("KR", "나"): [official]})
    content = patents.collect(conn, client, [patents.Keyword("가", "a", ("KR",)), patents.Keyword("나", "b", ("KR",))],
                              DAY, SETTINGS)
    ids = {conn.execute("SELECT doc_key FROM patents WHERE id = ?", (i["patent_id"],)).fetchone()[0]: i["patent_id"]
           for i in content["items"]}
    patents.save_summaries(conn, {"items": [
        {"id": ids["k1"], "title_ko": "인산철리튬 양극재", "summary_ko": "리튬 인산철 망간계 화합물을 쓴다",
         "key_claim_ko": "인산망간철 리튬 입자", "relevant": True, "relevance_note": "인산철 리튬 전지",
         "detail_ko": [{"heading": "용어 풀이", "body": "- 리튬 망간 인산철: 망간을 넣은 인산철"}] + DETAIL},
        {"id": ids["k2"], "title_ko": "인산철 리튬 정극 재료", "summary_ko": "요약", "key_claim_ko": "핵심",
         "detail_ko": DETAIL, "relevant": True, "relevance_note": "이유"}]}, DAY)

    page = patents.for_page(conn, DAY)
    items = {i["number"]: i for i in page["spotlight"] + page["items"] + page["low"]}
    a, b = items["KR-k1"], items["KR-k2"]
    assert a["title_ko"] == "리튬인산철 양극재"
    assert a["summary_ko"] == "리튬인산망간철계 화합물을 쓴다"
    assert a["key_claim_ko"] == "리튬인산망간철 입자"
    assert a["detail"][0]["body"] == "- 리튬인산망간철: 망간을 넣은 인산철"
    assert a["relevance_note"] == "리튬인산철 전지"
    assert b["title_ko"] == b["title"] == "인산철 리튬 정극 재료"  # 특허 원래 제목은 그대로


def test_standard_terms_leaves_project_names_and_lithium_ion_alone():
    assert patents.standard_terms("인산망간철 양극재 과제") == "인산망간철 양극재 과제"
    assert patents.standard_terms("인산철 리튬이온 전지") == "인산철 리튬이온 전지"
    assert patents.standard_terms("리튬 인산철") == "리튬 인산철"
    assert patents.standard_terms(None) is None
