"""주간 트렌드 리포트.

일주일 동안 쌓인 뉴스 브리핑·특허 브리핑에서 숫자로 된 통계를 뽑고(build_stats),
/weekly-report 스킬이 쓴 AI 분석(commentary)과 함께 저장해 페이지·GitHub 보고서로 만든다.
"""

import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from . import stars
from .config import KST
from .patents import COUNTRIES, COUNTRY_NAMES, split_applicants

DAYS = 7
IPC_LABELS = {
    "H01M": "전지", "H01L": "반도체 소자", "H10B": "메모리 소자", "H10D": "반도체 소자", "H10N": "전자 소자",
    "H10W": "반도체 패키징",
    "G11C": "메모리 회로", "H02J": "충전·전력망", "H02M": "전력 변환", "B60L": "전기차", "C01B": "탄소·비금속 소재",
    "C01D": "리튬 화합물", "C01G": "금속 화합물(양극 소재)", "C22B": "금속 회수·재활용", "G03F": "노광",
    "C23C": "박막 증착", "G06F": "컴퓨팅", "G06N": "AI 연산", "H01G": "커패시터", "G01R": "측정",
    "H01J": "플라즈마 장비", "G01N": "분석·검사", "B65G": "운반·공급 장치", "B05C": "도포 장치", "G05D": "제어",
    "H02P": "전동기 제어", "G06Q": "비즈니스 방법", "B01J": "화학 공정", "C08J": "고분자 가공",
}


class WeeklyError(ValueError):
    """주간 분석 JSON 형식이 잘못됐을 때."""


def week_days(end: date) -> list[str]:
    return [(end - timedelta(days=n)).isoformat() for n in range(DAYS - 1, -1, -1)]


def _first_applicant(applicant: str | None) -> str | None:
    names = split_applicants(applicant)
    return names[0].strip() if names else None


def build_stats(conn: sqlite3.Connection, end: date, topic_order: list[str] | None = None) -> dict:
    days = week_days(end)
    marks = ",".join("?" * len(days))

    # 뉴스: 주제별·날짜별 기사 수
    by_topic: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        f"SELECT topic, substr(fetched_at, 1, 10) AS day, COUNT(*) AS n FROM news "
        f"WHERE substr(fetched_at, 1, 10) IN ({marks}) GROUP BY topic, day", days):
        by_topic.setdefault(r["topic"], {})[r["day"]] = r["n"]
    order = [t for t in (topic_order or []) if t in by_topic] + sorted(set(by_topic) - set(topic_order or []))
    news = {
        "total": sum(sum(d.values()) for d in by_topic.values()),
        "topics": [{"name": t, "total": sum(by_topic[t].values()), "by_day": [by_topic[t].get(d, 0) for d in days]}
                   for t in order],
    }

    # 뉴스 브리핑: 날짜별 주요 이야기, 자주 나온 기술 키워드
    briefings, keyword_days = [], Counter()
    keyword_topic = {}
    for r in conn.execute(f"SELECT day, content FROM briefings WHERE day IN ({marks}) ORDER BY day", days):
        b = json.loads(r["content"])
        briefings.append({"day": r["day"], "overview": b["overview"],
                          "topics": [{"name": t["name"], "headlines": [s["headline"] for s in t["stories"]]}
                                     for t in b["topics"]]})
        for t in b["topics"]:
            for k in t["keywords"]:
                keyword_days[k["ko"]] += 1
                keyword_topic.setdefault(k["ko"], t["name"])
    keywords = [{"ko": k, "topic": keyword_topic[k], "days": n} for k, n in keyword_days.most_common(15)]

    # 특허 브리핑: 이번 주에 보여준 특허
    shown: dict[int, dict] = {}
    for r in conn.execute(f"SELECT content FROM patent_briefings WHERE day IN ({marks})", days):
        for item in json.loads(r["content"])["items"]:
            seen = shown.setdefault(item["patent_id"], {"rank": None, "score": 0, "reasons": [], "company": None})
            if item.get("rank") and (seen["rank"] is None or item["rank"] < seen["rank"]):
                seen["rank"] = item["rank"]
            if item.get("score", 0) >= seen["score"]:
                seen["score"], seen["reasons"] = item.get("score", 0), item.get("reasons", [])
            seen["company"] = seen["company"] or item.get("company")

    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM patents WHERE id IN ({','.join('?' * len(shown))})", list(shown))} if shown else {}
    relevant = [pid for pid in shown if rows[pid]["relevant"] != 0]
    by_country = Counter(rows[pid]["country"] for pid in relevant)
    applicants = Counter(a for a in (_first_applicant(rows[pid]["applicant"]) for pid in relevant) if a)
    ipc = Counter()
    for pid in relevant:
        for sub in {code[:4] for code in (rows[pid]["ipc"] or "").split("|") if code}:
            ipc[sub] += 1

    def brief(pid: int) -> dict:
        p = rows[pid]
        return {"country": p["country"], "number": p["number"], "title": p["title_ko"] or p["title"], "url": p["url"],
                "applicant": _first_applicant(p["applicant"]), "rank": shown[pid]["rank"],
                "score": shown[pid]["score"], "reasons": shown[pid]["reasons"], "company": shown[pid]["company"]}

    spotlight = sorted((brief(pid) for pid in relevant if shown[pid]["rank"]),
                       key=lambda p: (p["rank"], -p["score"]))
    companies = [brief(pid) for pid in relevant if shown[pid]["company"]]

    star_list = stars.listing(conn)
    for s in star_list:
        s["changed_this_week"] = bool(s["changed_at"] and s["changed_at"][:10] >= days[0])

    return {
        "start": days[0], "end": days[-1], "days": days,
        "news": news, "briefings": briefings, "keywords": keywords,
        "patents": {
            "total": len(relevant), "low": len(shown) - len(relevant),
            "by_country": [{"country": c, "name": COUNTRY_NAMES[c], "count": by_country.get(c, 0)} for c in COUNTRIES],
            "top_applicants": [{"name": n, "count": c} for n, c in applicants.most_common(8)],
            "top_ipc": [{"code": k, "label": IPC_LABELS.get(k, "기타 분류"), "count": c} for k, c in ipc.most_common(8)],
            "spotlight": spotlight[:10], "companies": companies[:10],
        },
        "stars": star_list,
    }


def _text(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WeeklyError(f"{where}는 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def validate_commentary(data) -> dict:
    """{"summary": str, "trends": [{"title", "body", "topic"?}] 2~6개, "watch": [str] 1~5개}"""
    if not isinstance(data, dict):
        raise WeeklyError("최상위는 JSON 객체여야 합니다.")
    trends = data.get("trends")
    if not isinstance(trends, list) or not 2 <= len(trends) <= 6:
        raise WeeklyError("trends는 2~6개짜리 배열이어야 합니다.")
    watch = data.get("watch")
    if not isinstance(watch, list) or not 1 <= len(watch) <= 5:
        raise WeeklyError("watch는 1~5개짜리 배열이어야 합니다.")
    out_trends = []
    for n, t in enumerate(trends):
        if not isinstance(t, dict):
            raise WeeklyError(f"trends[{n}]는 JSON 객체여야 합니다.")
        entry = {"title": _text(t.get("title"), f"trends[{n}].title"), "body": _text(t.get("body"), f"trends[{n}].body")}
        if t.get("topic"):
            entry["topic"] = _text(t["topic"], f"trends[{n}].topic")
        out_trends.append(entry)
    return {"summary": _text(data.get("summary"), "summary"), "trends": out_trends,
            "watch": [_text(w, f"watch[{n}]") for n, w in enumerate(watch)]}


def save(conn: sqlite3.Connection, stats: dict, commentary) -> dict:
    checked = validate_commentary(commentary)
    conn.execute(
        "INSERT OR REPLACE INTO weekly_reports (end_day, stats, commentary, created_at) VALUES (?, ?, ?, ?)",
        (stats["end"], json.dumps(stats, ensure_ascii=False), json.dumps(checked, ensure_ascii=False),
         datetime.now(KST).isoformat(timespec="seconds")),
    )
    conn.commit()
    return checked


def get(conn: sqlite3.Connection, end: date) -> dict | None:
    row = conn.execute("SELECT * FROM weekly_reports WHERE end_day = ?", (end.isoformat(),)).fetchone()
    if row is None:
        return None
    stats = json.loads(row["stats"])
    return {"end": row["end_day"], "start": stats["start"], "created_at": row["created_at"],
            "stats": stats, "commentary": json.loads(row["commentary"])}


def report_markdown(report: dict) -> str:
    s, c = report["stats"], report["commentary"]
    p = s["patents"]
    lines = [f"# 주간 트렌드 리포트 ({s['start']} ~ {s['end']})", "", c["summary"], ""]

    lines += ["## 한눈에 보기", "",
              "| 뉴스 기사 | 새 특허 | 🔥 주목 특허 | " + " | ".join(f"{b['name']} 특허" for b in p["by_country"]) + " |",
              "|---|---|---|" + "---|" * len(p["by_country"]),
              f"| {s['news']['total']} | {p['total']} | {len(p['spotlight'])} | "
              + " | ".join(str(b["count"]) for b in p["by_country"]) + " |", ""]

    lines += ["## 이번 주 흐름", ""]
    for t in c["trends"]:
        tag = f" `{t['topic']}`" if t.get("topic") else ""
        lines += [f"### {t['title']}{tag}", "", t["body"], ""]
    lines += ["## 다음 주에 지켜볼 것", ""] + [f"- {w}" for w in c["watch"]] + [""]

    if p["spotlight"]:
        lines += ["## 🔥 이번 주 주목 특허", "", "| 순위 | 특허 | 출원인 | 주목도 |", "|---|---|---|---|"]
        for x in p["spotlight"]:
            link = f"[{x['number']}]({x['url']})" if x["url"] else x["number"]
            lines.append(f"| {x['rank']} | {x['title']} ({link}) | {x['applicant'] or '-'} | {x['score']}점 |")
        lines.append("")

    lines += ["## 자주 나온 기술 키워드", "", "| 키워드 | 주제 | 나온 날 |", "|---|---|---|"]
    lines += [f"| {k['ko']} | {k['topic']} | {k['days']}일 |" for k in s["keywords"]] + [""]

    if p["top_applicants"]:
        lines += ["## 특허를 많이 낸 출원인", "", "| 출원인 | 특허 수 |", "|---|---|"]
        lines += [f"| {a['name']} | {a['count']} |" for a in p["top_applicants"]] + [""]
    if p["top_ipc"]:
        lines += ["## 많이 나온 기술 분류 (IPC)", "", "| 분류 | 뜻 | 특허 수 |", "|---|---|---|"]
        lines += [f"| {i['code']} | {i['label']} | {i['count']} |" for i in p["top_ipc"]] + [""]

    lines += ["## 주제별 뉴스 기사 수", "",
              "| 주제 | " + " | ".join(d[5:] for d in s["days"]) + " | 합계 |",
              "|---|" + "---|" * (len(s["days"]) + 1)]
    lines += [f"| {t['name']} | " + " | ".join(str(n) for n in t["by_day"]) + f" | {t['total']} |"
              for t in s["news"]["topics"]] + [""]

    if s["stars"]:
        lines += ["## ★ 관심 특허", "", "| 특허 | 진행 상태 | 이번 주 변화 |", "|---|---|---|"]
        for x in s["stars"]:
            title = x["title_ko"] or x["title"] or x["key"]
            link = f"[{x['number']}]({x['url']})" if x.get("url") else x["key"]
            lines.append(f"| {title} ({link}) | {x['status'] or '-'} | {'바뀜' if x['changed_this_week'] else ''} |")
        lines.append("")

    lines += ["---", "", "자료: Google News, KIPRIS Plus. 분석은 Claude가 일주일치 브리핑을 바탕으로 작성했어요."]
    return "\n".join(lines).rstrip() + "\n"
