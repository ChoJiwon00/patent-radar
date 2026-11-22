"""월간 트렌드 리포트.

한 달 동안 쌓인 뉴스·특허 브리핑에서 통계를 뽑고(build_stats), /monthly-report 스킬이 쓴 AI 분석(commentary)과 함께
저장해 페이지·GitHub 보고서로 만든다. 매월 1일 0시 실행이 지난달을 정리한다.
주간 리포트와 같은 계산(weekly.stats_for_days)을 쓰고, 한 달 단위로 볼 것을 더한다:
주차별 기사 수, 가장 오래 주목받은 특허, 지난달과의 비교, 그달의 주간 리포트 목록, KIPRIS 호출 수.
"""

import json
import re
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta

from . import weekly
from .config import KST
from .weekly import WeeklyError

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TRENDS_RANGE = (3, 8)
SPOTLIGHT = 10
KEYWORDS = 20


def check_month(month: str) -> str:
    if not isinstance(month, str) or not MONTH_RE.match(month):
        raise ValueError(f"달은 YYYY-MM 형식이어야 해요: {month!r}")
    return month


def month_days(month: str) -> list[str]:
    """그달의 모든 날짜 (오래된 날부터)."""
    y, m = map(int, check_month(month).split("-"))
    return [date(y, m, d).isoformat() for d in range(1, monthrange(y, m)[1] + 1)]


def previous_month(month: str) -> str:
    y, m = map(int, check_month(month).split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def default_month(today: date) -> str:
    """매월 1일에 정리하는 달: 오늘이 속한 달의 지난달."""
    return previous_month(today.strftime("%Y-%m"))


def week_buckets(days: list[str]) -> list[dict]:
    """그달의 날짜를 월요일~일요일 주로 나눈다. 첫 주·마지막 주는 그달 안의 날만."""
    buckets: list[dict] = []
    for d in days:
        monday = (date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())).isoformat()
        if not buckets or buckets[-1]["monday"] != monday:
            buckets.append({"monday": monday, "days": []})
        buckets[-1]["days"].append(d)
    return [{"start": b["days"][0], "end": b["days"][-1]} for b in buckets]


def weekly_range(month: str) -> tuple[str, str]:
    """그달 몫으로 볼 주간 리포트의 마지막 날 범위. 7일 가운데 4일 이상이 그달인 주간 (마지막 날 - 3일이 그달 안)."""
    days = month_days(month)
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    return (first + timedelta(days=3)).isoformat(), (last + timedelta(days=3)).isoformat()


def _totals(conn: sqlite3.Connection, days: list[str]) -> dict | None:
    """지난달 비교용 합계. 그달 브리핑이 하나도 없으면 None."""
    s = weekly.stats_for_days(conn, days, spotlight_limit=0, new_only=True)
    if not s["briefings"] and not s["patents"]["total"]:
        return None
    return {"briefing_days": len(s["briefings"]), "news": s["news"]["total"], "patents": s["patents"]["total"]}


def build_stats(conn: sqlite3.Connection, month: str, topic_order: list[str] | None = None) -> dict:
    days = month_days(month)
    s = weekly.stats_for_days(conn, days, topic_order, keyword_limit=KEYWORDS, spotlight_limit=None, new_only=True)
    p = s["patents"]
    # 한 달 동안 가장 여러 날 주목 특허 순위에 오른 특허부터 (같으면 가장 높았던 순위, 점수 순)
    p["hot_total"] = len(p["spotlight"])
    p["spotlight"] = sorted(p["spotlight"], key=lambda x: (-x["days_hot"], x["rank"], -x["score"]))[:SPOTLIGHT]

    weeks = week_buckets(days)
    for t in s["news"]["topics"]:
        by_day = dict(zip(days, t["by_day"]))
        t["by_week"] = [sum(n for d, n in by_day.items() if w["start"] <= d <= w["end"]) for w in weeks]
    s["weeks"] = weeks

    reports = []
    for r in conn.execute("SELECT end_day, stats, commentary FROM weekly_reports WHERE end_day BETWEEN ? AND ? "
                          "ORDER BY end_day", weekly_range(month)):
        reports.append({"end": r["end_day"], "start": json.loads(r["stats"])["start"],
                        "trends": [t["title"] for t in json.loads(r["commentary"])["trends"]]})
    s["weekly_reports"] = reports

    prev = previous_month(month)
    s["previous"] = ({"month": prev, **totals} if (totals := _totals(conn, month_days(prev))) else None)
    calls = {r["service"]: r["calls"] for r in conn.execute("SELECT service, calls FROM api_usage WHERE month = ?", (month,))}
    s["kipris_calls"] = {"kr": calls.get("kr", 0), "foreign": calls.get("foreign", 0)}
    s["month"] = month
    # 파일을 앞에서부터 읽어도 한 달을 비교하는 데 필요한 값을 먼저 보게 순서를 정한다 (날짜별 브리핑은 길어서 맨 뒤).
    first = ("month", "start", "end", "previous", "kipris_calls", "weeks", "weekly_reports", "news", "patents", "keywords")
    return {**{k: s[k] for k in first}, **{k: v for k, v in s.items() if k not in first}}


def validate_commentary(data) -> dict:
    return weekly.validate_commentary(data, TRENDS_RANGE)


def save(conn: sqlite3.Connection, stats: dict, commentary) -> dict:
    checked = validate_commentary(commentary)
    conn.execute(
        "INSERT OR REPLACE INTO monthly_reports (month, stats, commentary, created_at) VALUES (?, ?, ?, ?)",
        (stats["month"], json.dumps(stats, ensure_ascii=False), json.dumps(checked, ensure_ascii=False),
         datetime.now(KST).isoformat(timespec="seconds")),
    )
    conn.commit()
    return checked


def get(conn: sqlite3.Connection, month: str) -> dict | None:
    row = conn.execute("SELECT * FROM monthly_reports WHERE month = ?", (month,)).fetchone()
    if row is None:
        return None
    stats = weekly.fix_links(json.loads(row["stats"]))
    return {"month": row["month"], "start": stats["start"], "end": stats["end"], "created_at": row["created_at"],
            "stats": stats, "commentary": json.loads(row["commentary"])}


def month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{y}년 {int(m)}월"


def _short(day: str) -> str:
    return f"{int(day[5:7])}/{int(day[8:10])}"


def report_markdown(report: dict) -> str:
    s, c = report["stats"], report["commentary"]
    p = s["patents"]
    calls = s.get("kipris_calls") or {}
    lines = [f"# 월간 트렌드 리포트 ({month_label(s['month'])})", "", c["summary"], ""]

    lines += ["## 한눈에 보기", "",
              "| 브리핑 | 뉴스 기사 | 새 특허 | 🔥 주목 특허 | " + " | ".join(f"{b['name']} 특허" for b in p["by_country"])
              + " | KIPRIS 호출 |",
              "|---|---|---|---|" + "---|" * len(p["by_country"]) + "---|",
              f"| {len(s['briefings'])}일 | {s['news']['total']} | {p['total']} | {p.get('hot_total', len(p['spotlight']))} | "
              + " | ".join(str(b["count"]) for b in p["by_country"])
              + f" | {calls.get('kr', 0) + calls.get('foreign', 0)}회 |", ""]
    if s.get("previous"):
        q = s["previous"]
        lines += [f"지난달({month_label(q['month'])})에는 브리핑 {q['briefing_days']}일, 뉴스 기사 {q['news']}건, "
                  f"새 특허 {q['patents']}건이었어요.", ""]

    lines += ["## 이번 달 흐름", ""]
    for t in c["trends"]:
        tag = f" `{t['topic']}`" if t.get("topic") else ""
        lines += [f"### {t['title']}{tag}", "", t["body"], ""]
    lines += ["## 다음 달에 지켜볼 것", ""] + [f"- {w}" for w in c["watch"]] + [""]

    if p["spotlight"]:
        lines += ["## 🔥 이번 달 가장 오래 주목받은 특허", "",
                  "| 특허 | 출원인 | 순위에 오른 날 | 가장 높은 순위 | 주목도 |", "|---|---|---|---|---|"]
        for x in p["spotlight"]:
            link = (f"[{x['number']}]({x['url']})" if x["url"] else x["number"]) + weekly._espacenet(x)
            lines.append(f"| {x['title']} ({link}) | {x['applicant'] or '-'} | {x['days_hot']}일 | {x['rank']}위 | "
                         f"{x['score']}점 |")
        lines.append("")

    lines += ["## 자주 나온 기술 키워드", "", "| 키워드 | 주제 | 나온 날 |", "|---|---|---|"]
    lines += [f"| {k['ko']} | {k['topic']} | {k['days']}일 |" for k in s["keywords"]] + [""]

    if p["top_applicants"]:
        lines += ["## 특허를 많이 낸 출원인", "", "| 출원인 | 특허 수 |", "|---|---|"]
        lines += [f"| {a['name']} | {a['count']} |" for a in p["top_applicants"]] + [""]
    if p["top_ipc"]:
        lines += ["## 많이 나온 기술 분류 (IPC)", "", "| 분류 | 뜻 | 특허 수 |", "|---|---|---|"]
        lines += [f"| {i['code']} | {i['label']} | {i['count']} |" for i in p["top_ipc"]] + [""]

    lines += ["## 주차별 뉴스 기사 수", "",
              "| 주제 | " + " | ".join(f"{_short(w['start'])}~{_short(w['end'])}" for w in s["weeks"]) + " | 합계 |",
              "|---|" + "---|" * (len(s["weeks"]) + 1)]
    lines += [f"| {t['name']} | " + " | ".join(str(n) for n in t["by_week"]) + f" | {t['total']} |"
              for t in s["news"]["topics"]] + [""]

    if s.get("weekly_reports"):
        lines += ["## 이번 달 주간 리포트", ""]
        lines += [f"- [{w['start']} ~ {w['end']}](../weekly/{w['end']}.md): " + " · ".join(w["trends"])
                  for w in s["weekly_reports"]] + [""]

    if s["stars"]:
        lines += ["## ★ 관심 특허", "", "| 특허 | 진행 상태 | 이번 달 변화 |", "|---|---|---|"]
        for x in s["stars"]:
            title = x["title_ko"] or x["title"] or x["key"]
            link = (f"[{x['number']}]({x['url']})" if x.get("url") else x["key"]) + weekly._espacenet(x)
            lines.append(f"| {title} ({link}) | {x['status'] or '-'} | {'바뀜' if x['changed_this_week'] else ''} |")
        lines.append("")

    lines += ["---", "", "자료: Google News, KIPRIS Plus. 분석은 Claude가 한 달치 브리핑을 바탕으로 작성했어요."]
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["WeeklyError", "build_stats", "default_month", "get", "month_days", "previous_month", "report_markdown",
           "save", "validate_commentary", "week_buckets"]
