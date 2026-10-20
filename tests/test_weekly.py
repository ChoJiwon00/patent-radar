import json
from datetime import date

import pytest

from patent_radar import briefing, db, news, patents, weekly, website
from patent_radar.config import PatentSettings

from test_patents import FakeClient, found

END = date(2026, 10, 21)
DETAIL = [{"heading": "a", "body": "b"}, {"heading": "c", "body": "d"}, {"heading": "e", "body": "f"}]
COMMENTARY = {"summary": "이번 주는 전고체가 화제였다.",
              "trends": [{"title": "전고체 양산", "body": "양산 소식이 이어졌다.", "topic": "이차전지"},
                         {"title": "HBM 경쟁", "body": "베이스다이 협력이 늘었다."}],
              "watch": ["다음 주 실적 발표"]}


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    for n, day in enumerate(["2026-10-19", "2026-10-21"]):
        news.save_article(conn, news.Article(f"g{n}", f"전고체 기사 {n}", "언론", f"https://x/{n}", None),
                          "이차전지", "이차전지", f"{day}T09:00:00+09:00")
        briefing.save(conn, {"day": day, "overview": f"{day} 흐름", "topics": [{
            "name": "이차전지", "stories": [{"headline": f"전고체 {n}", "summary": "s", "article_ids": [n + 1]}],
            "keywords": [{"ko": "전고체 전해질", "en": "solid electrolyte", "reason": "r"}]}]})
    client = FakeClient({("KR", "가"): [found("KR", "k1", applicant="삼성에스디아이 주식회사, 기타"),
                                        found("KR", "k2", applicant="엘지에너지솔루션")]})
    settings = PatentSettings(per_search=2)
    content = patents.collect(conn, client, [patents.Keyword("가", "a", ("KR",))], END, settings)
    k1, k2 = (i["patent_id"] for i in content["items"])
    patents.save_summaries(conn, {"items": [
        {"id": k1, "title_ko": "t", "summary_ko": "s", "key_claim_ko": "k", "detail_ko": DETAIL,
         "relevant": True, "relevance_note": "n"},
        {"id": k2, "title_ko": "t", "summary_ko": "s", "key_claim_ko": "k", "detail_ko": DETAIL,
         "relevant": False, "relevance_note": "무관"}]}, END)
    return conn


def test_build_stats(conn):
    stats = weekly.build_stats(conn, END, ["이차전지"])

    assert stats["start"] == "2026-10-15"
    assert stats["news"]["total"] == 2
    assert stats["news"]["topics"][0]["by_day"] == [0, 0, 0, 0, 1, 0, 1]
    assert stats["keywords"] == [{"ko": "전고체 전해질", "topic": "이차전지", "days": 2}]
    # 관련 낮음 특허는 통계에서 빠지고 따로 센다
    assert (stats["patents"]["total"], stats["patents"]["low"]) == (1, 1)
    assert stats["patents"]["top_applicants"] == [{"name": "삼성에스디아이 주식회사", "count": 1}]


def test_commentary_is_checked(conn):
    stats = weekly.build_stats(conn, END)
    with pytest.raises(weekly.WeeklyError, match="trends"):
        weekly.save(conn, stats, {**COMMENTARY, "trends": COMMENTARY["trends"][:1]})


def test_save_and_report(conn):
    weekly.save(conn, weekly.build_stats(conn, END, ["이차전지"]), COMMENTARY)

    report = weekly.get(conn, END)
    text = weekly.report_markdown(report)

    assert report["commentary"]["trends"][0]["topic"] == "이차전지"
    assert "# 주간 트렌드 리포트 (2026-10-15 ~ 2026-10-21)" in text
    assert "### 전고체 양산 `이차전지`" in text
    assert "| 전고체 전해질 | 이차전지 | 2일 |" in text


def test_site_export(conn, tmp_path, monkeypatch):
    weekly.save(conn, weekly.build_stats(conn, END), COMMENTARY)
    page = tmp_path / "briefing.html"
    page.write_text("<title>t</title><main></main>", encoding="utf-8")
    monkeypatch.setattr(website, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(website, "PAGE", page)

    index = website.export(conn)

    assert index["days"] == ["2026-10-21", "2026-10-19"]
    assert index["weeks"] == ["2026-10-21"]
    day = json.loads((tmp_path / "docs" / "data" / "days" / "2026-10-21.json").read_text(encoding="utf-8"))
    assert day["briefing"]["overview"] == "2026-10-21 흐름"
    assert len(day["patents"]["items"]) == 1 and len(day["patents"]["low"]) == 1
    html = (tmp_path / "docs" / "index.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "<main></main>" in html
