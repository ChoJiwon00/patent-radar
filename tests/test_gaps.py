import json
from datetime import date

import pytest

from patent_radar import db, gaps

TODAY = date(2026, 11, 17)


def _brief(conn, day, created=None):
    conn.execute("INSERT INTO briefings (day, content, created_at) VALUES (?, ?, ?)",
                 (day, json.dumps({"day": day, "overview": "o", "topics": []}), created or f"{day}T00:15:00+09:00"))


def _patents(conn, day, items=(), created=None, **stops):
    content = {"keywords": [], "items": list(items), "warnings": [], "quota_stop": None, "kipris_down": None, **stops}
    conn.execute("INSERT INTO patent_briefings (day, content, created_at) VALUES (?, ?, ?)",
                 (day, json.dumps(content), created or f"{day}T00:20:00+09:00"))


@pytest.fixture
def conn():
    conn = db.connect(":memory:")
    for d in ("2026-11-08", "2026-11-09", "2026-11-11", "2026-11-12", "2026-11-14", "2026-11-16"):
        _brief(conn, d)
        _patents(conn, d, [{"patent_id": 1, "new": True}])
    return conn


def test_finds_skipped_days_in_the_last_week_oldest_first(conn):
    found = gaps.find(conn, TODAY)

    # 11-10·11-13·11-15는 브리핑이 없다. 11-09는 7일 전보다 앞이라 보지 않는다.
    assert found["news"] == ["2026-11-10", "2026-11-13", "2026-11-15"]
    # 특허는 그날 뉴스 브리핑(키워드)이 생긴 뒤에 채운다
    assert found["patents"] == []
    assert found["today"] == "2026-11-17"

    for d in found["news"]:
        _brief(conn, d)
    assert gaps.find(conn, TODAY)["patents"] == ["2026-11-10", "2026-11-13", "2026-11-15"]


def test_a_day_stopped_by_quota_or_outage_without_new_patents_is_refilled(conn):
    _brief(conn, "2026-11-13")
    _patents(conn, "2026-11-13", [{"patent_id": 2, "new": False, "rank": 1}], kipris_down="KIPRIS 점검")
    _brief(conn, "2026-11-15")
    _patents(conn, "2026-11-15", [{"patent_id": 3, "new": True}], quota_stop="한도")  # 새 특허가 있으면 그대로 둔다
    _brief(conn, "2026-11-10")

    found = gaps.find(conn, TODAY)

    assert found["news"] == []
    assert found["patents"] == ["2026-11-10", "2026-11-13"]


def test_patent_days_are_capped_oldest_first_and_the_rest_wait(conn):
    for d in ("2026-11-10", "2026-11-13", "2026-11-15"):
        _brief(conn, d)

    found = gaps.find(conn, TODAY, max_patent_days=2)

    # 먼저 7일 범위를 벗어날 날부터 채운다
    assert found["patents"] == ["2026-11-10", "2026-11-13"]
    assert found["patents_later"] == ["2026-11-15"]
    assert gaps.find(conn, TODAY, max_patent_days=0)["patents"] == []


def test_a_patent_row_without_any_search_is_refilled(conn):
    _brief(conn, "2026-11-15")
    _patents(conn, "2026-11-15", [{"patent_id": 2, "new": False, "rank": 1}])  # 순위만 다시 매긴 빈 행

    assert gaps.find(conn, TODAY)["patents"] == ["2026-11-15"]


def test_lookback_below_one_does_not_crash(conn):
    assert gaps.find(conn, TODAY, lookback=0)["news"] == []


def test_days_before_the_first_briefing_are_not_gaps():
    conn = db.connect(":memory:")
    _brief(conn, "2026-11-15")

    found = gaps.find(conn, TODAY)

    assert found["news"] == ["2026-11-16"]
    assert found["patents"] == []  # 특허 브리핑은 아직 한 번도 없었다


def test_weekly_report_is_stale_when_its_week_got_a_briefing_after_it(conn):
    conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, '{}', '{}', ?)",
                 ("2026-11-16", "2026-11-16T00:30:00+09:00"))
    conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, '{}', '{}', ?)",
                 ("2026-11-09", "2026-11-09T00:30:00+09:00"))
    assert gaps.stale_weeks(conn) == []

    _brief(conn, "2026-11-15", created="2026-11-17T00:12:00+09:00")  # 11-15를 나중에 채웠다

    assert gaps.find(conn, TODAY)["weeks"] == ["2026-11-16"]


def test_old_weekly_reports_outside_the_window_are_left_alone(conn):
    conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, '{}', '{}', ?)",
                 ("2026-11-09", "2026-11-09T00:30:00+09:00"))
    _brief(conn, "2026-11-05", created="2026-11-12T00:00:00+09:00")  # 오래전 주간에 나중에 저장된 브리핑

    assert gaps.stale_weeks(conn) == ["2026-11-09"]
    assert gaps.find(conn, date(2026, 11, 20))["weeks"] == ["2026-11-09"]  # 한 주 더 거슬러 계속 알려 준다
    assert gaps.find(conn, date(2026, 11, 27))["weeks"] == []  # 그보다 오래된 주간은 두지 않는다


def test_weekly_report_of_a_skipped_monday_is_reported_missing(conn):
    conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, '{}', '{}', ?)",
                 ("2026-11-09", "2026-11-09T00:30:00+09:00"))

    # 11-16(월)을 통째로 건너뛰고 11-18(수)에 실행
    assert gaps.find(conn, date(2026, 11, 18))["weeks_missing"] == ["2026-11-16"]
    # 월요일인 오늘은 오늘 실행이 만든다
    assert gaps.find(conn, date(2026, 11, 16))["weeks_missing"] == []

    conn.execute("INSERT INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, '{}', '{}', ?)",
                 ("2026-11-16", "2026-11-18T01:00:00+09:00"))
    assert gaps.find(conn, date(2026, 11, 18))["weeks_missing"] == []
