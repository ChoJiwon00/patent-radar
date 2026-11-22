import json
from datetime import date

import pytest

from patent_radar import db, gaps, monthly, weekly, website

COMMENTARY = {"summary": "11월에는 전고체와 리튬인산철(LFP) 이야기가 이어졌어요.",
              "trends": [{"title": "전고체 양산", "body": "양산 소식이 이어졌어요.", "topic": "이차전지"},
                         {"title": "ESS 수주", "body": "북미 수주가 늘었어요.", "topic": "ESS"},
                         {"title": "HBM 경쟁", "body": "베이스다이 협력이 늘었어요.", "topic": "반도체"}],
              "watch": ["다음 달 실적 발표"]}


def _news(conn, n, day, topic="이차전지"):
    conn.execute("INSERT INTO news (guid, title_key, topic, query, title, source, url, published_at, fetched_at) "
                 "VALUES (?, ?, ?, 'q', ?, 's', ?, NULL, ?)",
                 (f"g{n}", f"k{n}", topic, f"기사 {n}", f"https://x/{n}", f"{day}T00:12:00+09:00"))


def _brief(conn, day, created=None):
    content = {"day": day, "overview": f"{day} 흐름", "topics": [{
        "name": "이차전지", "stories": [{"headline": "h", "summary": "s", "article_ids": []}],
        "keywords": [{"ko": "전고체 전해질", "en": "solid electrolyte", "reason": "r"}]}]}
    conn.execute("INSERT OR REPLACE INTO briefings (day, content, created_at) VALUES (?, ?, ?)",
                 (day, json.dumps(content), created or f"{day}T00:15:00+09:00"))


def _patent(conn, pid, title, first_shown):
    conn.execute("INSERT INTO patents (id, country, doc_key, number, title, applicant, ipc, relevant, title_ko, "
                 "first_shown_day) VALUES (?, 'KR', ?, ?, ?, '삼성에스디아이 주식회사', 'H01M10/0562', 1, ?, ?)",
                 (pid, f"d{pid}", f"10-{pid}", title, title, first_shown))


def _patents(conn, day, items, created=None):
    content = {"keywords": [{"ko": "k"}], "items": items, "warnings": []}
    conn.execute("INSERT OR REPLACE INTO patent_briefings (day, content, created_at) VALUES (?, ?, ?)",
                 (day, json.dumps(content), created or f"{day}T00:20:00+09:00"))


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    _patent(conn, 1, "오래 주목받은 특허", "2026-10-20")  # 10월에 처음 나와 11월에도 주목 특허로 이어짐
    _patent(conn, 2, "하루만 1위", "2026-11-10")
    _patent(conn, 3, "새 특허", "2026-11-10")
    _patent(conn, 4, "10월 새 특허", "2026-10-20")
    n = 0
    for day in ("2026-11-01", "2026-11-02", "2026-11-10", "2026-11-30"):
        _brief(conn, day)
        for _ in range(2):
            n += 1
            _news(conn, n, day)
        hot = [{"patent_id": 1, "keyword": "k", "new": False, "rank": 2, "score": 9, "reasons": ["r"], "company": None}]
        if day == "2026-11-10":
            hot.insert(0, {"patent_id": 2, "keyword": "k", "new": False, "rank": 1, "score": 12, "reasons": ["r"], "company": None})
            hot.append({"patent_id": 3, "keyword": "k", "rank": None, "new": True, "score": 1, "reasons": [],
                        "company": None})
        _patents(conn, day, hot)
    # 지난달 (비교용)
    _brief(conn, "2026-10-20")
    _news(conn, 99, "2026-10-20")
    _patents(conn, "2026-10-20", [{"patent_id": 4, "keyword": "k", "rank": None, "new": True, "score": 1,
                                   "reasons": [], "company": None},
                                  {"patent_id": 1, "keyword": "k", "rank": None, "new": True, "score": 1,
                                   "reasons": [], "company": None}])
    for end, start, created in (("2026-11-02", "2026-10-27", "2026-11-02T00:30:00+09:00"),
                                ("2026-11-09", "2026-11-03", "2026-11-09T00:30:00+09:00")):
        conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, ?, ?, ?)",
                     (end, json.dumps({"start": start, "patents": {"spotlight": [], "companies": []}}),
                      json.dumps({"trends": [{"title": "주간 흐름 A"}, {"title": "주간 흐름 B"}]}), created))
    conn.execute("INSERT INTO api_usage (month, service, calls) VALUES ('2026-11', 'kr', 300), ('2026-11', 'foreign', 320)")
    conn.commit()
    return conn


def test_month_helpers():
    assert monthly.month_days("2026-11")[0] == "2026-11-01" and len(monthly.month_days("2026-11")) == 30
    assert len(monthly.month_days("2028-02")) == 29
    assert monthly.previous_month("2027-01") == "2026-12"
    assert monthly.default_month(date(2026, 12, 1)) == "2026-11"
    with pytest.raises(ValueError):
        monthly.check_month("2026-13")
    # 11-01(일)은 혼자, 11-30(월)도 혼자인 주
    weeks = monthly.week_buckets(monthly.month_days("2026-11"))
    assert weeks[0] == {"start": "2026-11-01", "end": "2026-11-01"}
    assert weeks[1] == {"start": "2026-11-02", "end": "2026-11-08"}
    assert weeks[-1] == {"start": "2026-11-30", "end": "2026-11-30"}
    assert len(weeks) == 6


def test_build_stats(conn):
    s = monthly.build_stats(conn, "2026-11", ["이차전지"])

    assert (s["month"], s["start"], s["end"]) == ("2026-11", "2026-11-01", "2026-11-30")
    assert len(s["briefings"]) == 4 and s["news"]["total"] == 8
    assert s["news"]["topics"][0]["by_week"] == [2, 2, 2, 0, 0, 2]
    # 순위에 오른 날이 많은 특허가 먼저 (하루 1위보다 나흘 2위)
    assert [(x["title"], x["days_hot"], x["rank"]) for x in s["patents"]["spotlight"]] == [
        ("오래 주목받은 특허", 4, 2), ("하루만 1위", 1, 1)]
    assert s["keywords"] == [{"ko": "전고체 전해질", "topic": "이차전지", "days": 4}]
    # 10-27~11-02 주간은 7일 가운데 5일이 10월이라 11월 몫이 아니다
    assert s["weekly_reports"] == [{"end": "2026-11-09", "start": "2026-11-03", "trends": ["주간 흐름 A", "주간 흐름 B"]}]
    # 특허 수는 그달에 처음 보여 준 특허만 (10월부터 이어진 주목 특허 1은 빼고), 주목 특허 전체 수는 따로
    assert s["patents"]["total"] == 2 and s["patents"]["hot_total"] == 2
    assert s["previous"] == {"month": "2026-10", "briefing_days": 1, "news": 1, "patents": 2}
    # 비교에 쓰는 값이 파일 앞쪽에 온다
    assert list(s)[:7] == ["month", "start", "end", "previous", "kipris_calls", "weeks", "weekly_reports"]
    assert s["kipris_calls"] == {"kr": 300, "foreign": 320}


def test_previous_month_without_briefings_is_none(conn):
    assert monthly.build_stats(conn, "2026-10")["previous"] is None


def test_commentary_needs_three_to_eight_trends(conn):
    stats = monthly.build_stats(conn, "2026-11")
    with pytest.raises(weekly.WeeklyError, match="3~8"):
        monthly.save(conn, stats, {**COMMENTARY, "trends": COMMENTARY["trends"][:2]})


def test_save_get_and_report(conn):
    monthly.save(conn, monthly.build_stats(conn, "2026-11", ["이차전지"]), COMMENTARY)

    report = monthly.get(conn, "2026-11")
    text = monthly.report_markdown(report)

    assert report["month"] == "2026-11" and report["commentary"]["trends"][1]["topic"] == "ESS"
    assert text.startswith("# 월간 트렌드 리포트 (2026년 11월)\n")
    assert "| 4일 | 8 | 2 | 2 | 2 | 0 | 0 | 620회 |" in text
    assert "지난달(2026년 10월)에는 브리핑 1일, 뉴스 기사 1건, 새 특허 2건이었어요." in text
    assert "| 오래 주목받은 특허 (" in text and "| 삼성에스디아이 주식회사 | 4일 | 2위 | 9점 |" in text
    assert "| 이차전지 | 2 | 2 | 2 | 0 | 0 | 2 | 8 |" in text
    assert "- [2026-11-03 ~ 2026-11-09](../weekly/2026-11-09.md): 주간 흐름 A · 주간 흐름 B" in text


def test_site_export_includes_months(conn, tmp_path, monkeypatch):
    monthly.save(conn, monthly.build_stats(conn, "2026-11"), COMMENTARY)
    page = tmp_path / "briefing.html"
    page.write_text("<title>t</title><main></main>", encoding="utf-8")
    monkeypatch.setattr(website, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(website, "PAGE", page)

    index = website.export(conn)

    assert index["months"] == ["2026-11"]
    data = json.loads((tmp_path / "docs" / "data" / "months" / "2026-11.json").read_text(encoding="utf-8"))
    assert data["stats"]["month"] == "2026-11" and data["commentary"]["summary"].startswith("11월")


def test_gaps_reports_a_missing_or_stale_monthly_report(conn):
    # 1일에는 그날 실행이 만든다. 1일을 건너뛰었으면 다음 실행이 만든다.
    assert gaps.months_to_make(conn, date(2026, 12, 1)) == ([], [])
    assert gaps.find(conn, date(2026, 12, 3))["months_missing"] == ["2026-11"]
    # 월간 리포트를 처음 만드는 달보다 앞은 보지 않는다.
    assert gaps.months_to_make(conn, date(2026, 11, 23)) == ([], [])

    monthly.save(conn, monthly.build_stats(conn, "2026-11"), COMMENTARY)
    conn.execute("UPDATE monthly_reports SET created_at = '2026-12-01T00:30:00+09:00'")  # 12-01 0시 실행이 만든 것처럼
    assert gaps.months_to_make(conn, date(2026, 12, 3)) == ([], [])

    _brief(conn, "2026-11-15", created="2099-01-01T00:00:00+09:00")  # 그달 날짜를 나중에 채웠다
    assert gaps.find(conn, date(2026, 12, 3))["months"] == ["2026-11"]


def test_weekly_report_made_after_the_monthly_makes_it_stale(conn):
    monthly.save(conn, monthly.build_stats(conn, "2026-11"), COMMENTARY)
    conn.execute("UPDATE monthly_reports SET created_at = '2026-12-01T00:30:00+09:00'")
    # 11-30(월) 주간을 12-01 월간보다 늦게 만들었다 (11-30 실행을 건너뛰어 12-01에 채움)
    conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES "
                 "('2026-11-30', '{}', '{}', '2026-12-01T00:50:00+09:00')")

    assert gaps.months_to_make(conn, date(2026, 12, 1)) == ([], ["2026-11"])


def test_reports_index_lists_monthly_reports(tmp_path, monkeypatch):
    from patent_radar import __main__ as cli

    (tmp_path / "monthly").mkdir()
    (tmp_path / "monthly" / "2026-11.md").write_text("m", encoding="utf-8")
    (tmp_path / "2026-11-30.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path)
    cli._write_reports_index()

    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "주간·월간 트렌드 리포트를 모아 둔 곳이에요." in text
    assert "## 월간 트렌드 리포트\n\n- [2026년 11월](monthly/2026-11.md)\n" in text
