"""명령줄 진입점.

    python -m patent_radar news                  뉴스를 수집하고 결과를 요약해서 보여준다
    python -m patent_radar news --json           수집 후 오늘의 새 기사를 JSON으로 출력한다 (스킬이 읽는 형식)
    python -m patent_radar briefing save FILE    AI 브리핑 JSON을 검사해서 저장한다
    python -m patent_radar briefing show         저장된 브리핑을 보여준다
    python -m patent_radar briefing export FILE  claude.ai 브리핑 페이지에 올릴 JSON을 만든다
    python -m patent_radar patents search        오늘 브리핑 키워드로 한·미·중 특허를 찾는다
    python -m patent_radar patents search --keyword "전고체 전해질|solid electrolyte"
    python -m patent_radar patents list          검색 없이 그날 특허 목록을 다시 파일로 만든다
    python -m patent_radar patents save-summaries FILE   AI가 쓴 한국어 요약을 저장한다
    python -m patent_radar patents export FILE   claude.ai 브리핑 페이지에 올릴 JSON을 만든다
    python -m patent_radar patents report        GitHub에 올릴 보고서(reports/날짜.md)를 만든다
    python -m patent_radar patents usage         이번 달 KIPRIS 호출 수(국내+해외 합계)와 오늘 하루 예산을 보여준다
    python -m patent_radar patents deep CONFIG --out FILE   주제 하나를 깊게 모은다 (매일 브리핑 기록에는 넣지 않음)
    python -m patent_radar patents claimmap      심층 리포트의 청구항 숫자를 사이트 그래프용 데이터로 만든다
    python -m patent_radar stars sync FOLDER     페이지에서 별표한 관심 특허 목록과 맞춘다
    python -m patent_radar stars check           한국 관심 특허의 진행 상태를 다시 확인한다
    python -m patent_radar weekly stats          일주일 통계를 뽑는다 (주간 리포트용)
    python -m patent_radar weekly save FILE      AI 분석을 저장하고 reports/weekly/날짜.md를 만든다
    python -m patent_radar site export           GitHub Pages 공개 사이트(docs/)를 만든다
    python -m patent_radar serve                 웹페이지를 띄운다 (http://127.0.0.1:8000)
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

from . import briefing, claimmap, db, deep, news, patents, stars, website, weekly
from .config import KST, kipris_api_key, load_interests, load_news_settings, load_patent_settings


def cmd_news(args: argparse.Namespace) -> int:
    with db.session() as conn:
        results = news.collect(conn)
        if args.json:
            today = datetime.now(KST).date()
            # 스킬이 읽기 좋게 긴 링크는 빼고 필요한 항목만 내보낸다.
            keep = ("id", "topic", "query", "title", "source", "published_at")
            articles = [{k: a[k] for k in keep} for a in news.news_for_day(conn, today)]
            errors = [f"{r.query}: {r.error}" for r in results if r.error]
            topics = [t.name for t in load_news_settings().topics]
            print(json.dumps({"day": today.isoformat(), "topics": topics, "errors": errors, "articles": articles},
                             ensure_ascii=False))
            return 0

    total = 0
    for r in results:
        status = f"오류: {r.error}" if r.error else f"{r.fetched}건 중 새 기사 {r.added}건"
        print(f"[{r.topic}] {r.query}: {status}")
        total += r.added
    print(f"\n새로 저장한 기사: 총 {total}건")
    return 1 if all(r.error for r in results) else 0


def cmd_briefing_save(args: argparse.Namespace) -> int:
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"파일을 읽지 못했어요: {e}")
        return 2
    with db.session() as conn:
        try:
            saved = briefing.save(conn, data)
        except briefing.BriefingError as e:
            print(f"형식 오류: {e}")
            return 2
    parts = [f"{t['name']} 이야기 {len(t['stories'])}개·키워드 {len(t['keywords'])}개" for t in saved["topics"]]
    print(f"{saved['day']} 브리핑을 저장했어요 ({', '.join(parts)})")
    return 0


def cmd_briefing_show(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.day) if args.day else datetime.now(KST).date()
    with db.session() as conn:
        found = briefing.get(conn, day)
    if found is None:
        print(f"{day} 브리핑이 없어요.")
        return 1
    print(json.dumps(found, ensure_ascii=False, indent=2))
    return 0


def cmd_briefing_export(args: argparse.Namespace) -> int:
    day = date.fromisoformat(args.day) if args.day else datetime.now(KST).date()
    with db.session() as conn:
        found = briefing.get(conn, day)
    if found is None:
        print(f"{day} 브리핑이 없어요.")
        return 1
    Path(args.file).write_text(json.dumps(briefing.for_page(found), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{day} 브리핑을 페이지용으로 {args.file}에 저장했어요")
    return 0


def _day(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(KST).date()


def _print_usage(conn, budget: int | None = None, used: int | None = None) -> None:
    """이번 달 호출 수와 하루 예산. 특허 검색 뒤에는 그 실행의 예산(budget)과 쓴 횟수(used)를 넘긴다."""
    settings = load_patent_settings()
    kr, foreign = (patents.calls_this_month(conn, s) for s in ("kr", "foreign"))
    total = kr + foreign
    print(f"이번 달 KIPRIS 호출: 국내 {kr}회 + 해외 {foreign}회 = {total}회 "
          f"(설정한 상한 {settings.monthly_call_limit}회, 무료 한도는 계정 합계 {settings.free_monthly_limit:,}회)")
    blocked = patents.quota_block(conn)
    if blocked:
        print("⚠ KIPRIS가 이번 달 한도 초과로 호출을 거부했어요. 매월 1일에 다시 열려요.")
    if used is not None:
        # 실행 뒤에 다시 계산하면 방금 쓴 만큼 줄어든 값이 나와서, 실행을 시작할 때 정한 예산을 그대로 보여 준다.
        print(f"오늘 하루 예산: {budget}회 중 {used}회 씀" if budget is not None
              else "오늘 하루 예산: 없음 (직접 준 키워드라 월 상한만 지켜요)")
        return
    if blocked:
        print("지금 실행하면 쓸 하루 예산: 0회 (거부당한 달이라 부르지 않아요)")
        return
    today = datetime.now(KST).date()
    remaining = max(settings.monthly_call_limit - total, 0)
    cap = f", 하루 상한 {settings.daily_call_limit}회" if settings.daily_call_limit > 0 else ""
    print(f"지금 실행하면 쓸 하루 예산: {patents.daily_budget(conn, settings, today)}회 "
          f"(남은 {remaining}회 ÷ 남은 {patents.days_left_in_month(today)}일{cap})")


def cmd_patents_search(args: argparse.Namespace) -> int:
    key = kipris_api_key()
    if not key:
        print(".env 파일에 KIPRIS_API_KEY가 없어요.")
        return 2
    settings = load_patent_settings()
    day = _day(args.day)
    with db.session() as conn:
        if args.keyword:
            try:
                keywords = [patents.Keyword.parse(k) for k in args.keyword]
            except ValueError as e:
                print(e)
                return 2
            print(f"직접 준 키워드 {len(keywords)}개로 찾아요.")
        else:
            source_day, keywords = patents.keywords_from_briefing(
                conn, day, settings.kr_keywords_per_topic, settings.foreign_keywords_per_topic)
            if not keywords:
                print("뉴스 브리핑이 없어서 키워드를 고를 수 없어요. 먼저 /daily-briefing 을 실행하거나 --keyword 를 주세요.")
                return 1
            note = "" if source_day == day.isoformat() else f" (오늘 브리핑이 없어 {source_day} 브리핑 사용)"
            kr = sum(1 for k in keywords if "KR" in k.countries)
            foreign = sum(1 for k in keywords if "US" in k.countries)
            print(f"뉴스 브리핑 키워드로 찾아요: 한국 {kr}개, 미국·중국 {foreign}개{note}.")

        interests = load_interests()
        if not args.keyword and interests.keywords:
            keywords += [patents.Keyword(k.ko, k.en, k.countries, k.topic, k.alt, interest=True)
                         for k in interests.keywords]
            print(f"고정 관심 키워드 {len(interests.keywords)}개도 함께 찾아요.")
        ipc_by_topic = {t.name: t.ipc for t in load_news_settings().topics}

        # 매일 브리핑은 하루 예산 안에서만 부른다. 직접 준 키워드는 예산 없이 월 상한만 지킨다.
        # 호출 수는 실제 날짜의 달로 세므로 --day와 상관없이 오늘로 예산을 정한다.
        budget = None
        if not args.keyword:
            budget = patents.daily_budget(conn, settings)
            # 검색 한 번과 그 검색에서 고른 새 특허의 상세 한 번을 할 수 있어야 찾는다 (collect도 같은 규칙).
            if budget < 2:
                print("이번 달 남은 호출이 모자라 오늘은 특허를 찾지 않아요 (KIPRIS를 부르지 않았어요).")
                # 전날 결과 파일을 오늘 것으로 잘못 읽지 않게 오늘 목록(없으면 빈 목록)으로 바꿔 둔다.
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(json.dumps(patents.for_claude(conn, day), ensure_ascii=False, indent=2),
                                          encoding="utf-8")
                _print_usage(conn, budget, 0)
                return 3
            print(f"오늘 하루 호출 예산 {budget}회 안에서 중요한 검색부터 해요.")

        with httpx.Client(timeout=30) as http:
            client = patents.KiprisClient(conn, key, settings, http, budget)
            content = patents.collect(conn, client, keywords, day, settings, ipc_by_topic, interests.companies)

        result = patents.for_claude(conn, day)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        new = [i for i in result["items"] if i["new"] and not i["rank"]]
        counts = {c: sum(1 for i in new if i["country"] == c) for c in patents.COUNTRIES}
        hot = sum(1 for i in result["items"] if i["rank"])
        print(f"🔥 주목 특허 {hot}건, 새 특허 "
              + ", ".join(f"{patents.COUNTRY_NAMES[c]} {n}건" for c, n in counts.items()))
        for w in content["warnings"]:
            print(f"  - {w}")
        print(f"자세한 내용: {args.out}")
        print(f"이번 실행 호출: 국내 {client.run_calls['kr']}회, 해외 {client.run_calls['foreign']}회")
        _print_usage(conn, budget, sum(client.run_calls.values()))
        if content["quota_stop"]:
            print("⚠ KIPRIS 이번 달 한도가 끝나 특허 검색을 멈췄어요. 새 특허가 0건이면 페이지·GitHub에 올리지 말고 보고만 하세요.")
            return 3
        if content["kipris_down"]:
            print("⚠ KIPRIS가 제대로 답하지 않아(점검 중일 수 있어요) 특허 검색을 멈췄어요. "
                  "새 특허가 0건이면 페이지·GitHub에 올리지 말고 보고만 하세요.")
            return 3
    return 0


def cmd_patents_rescore(args: argparse.Namespace) -> int:
    """검색 없이 그날 특허의 주목도 순위를 다시 매긴다 (관련도 판정을 저장한 뒤에 쓴다). KIPRIS는 부르지 않는다."""
    day = _day(args.day)
    with db.session() as conn:
        content = patents.collect(conn, None, [], day, load_patent_settings(), companies=load_interests().companies)
    hot = sum(1 for i in content["items"] if i["rank"])
    print(f"{day} 순위를 다시 매겼어요: 🔥 주목 특허 {hot}건")
    return 0


def cmd_patents_list(args: argparse.Namespace) -> int:
    day = _day(args.day)
    with db.session() as conn:
        result = patents.for_claude(conn, day)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    pending = sum(1 for i in result["items"] if i["needs_summary"])
    print(f"{day} 특허 {len(result['items'])}건 (요약이 필요한 특허 {pending}건)을 {args.out}에 저장했어요")
    return 0


def cmd_patents_save(args: argparse.Namespace) -> int:
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"파일을 읽지 못했어요: {e}")
        return 2
    with db.session() as conn:
        try:
            n = patents.save_summaries(conn, data, _day(args.day))
        except patents.SummaryError as e:
            print(f"형식 오류: {e}")
            return 2
    print(f"특허 요약 {n}건을 저장했어요")
    return 0


def cmd_patents_export(args: argparse.Namespace) -> int:
    day = _day(args.day)
    with db.session() as conn:
        page = patents.for_page(conn, day)
    if page is None:
        print(f"{day} 특허 브리핑이 없어요.")
        return 1
    everything = page["spotlight"] + page["items"]
    missing = sum(1 for i in everything if not i["summary_ko"])
    Path(args.file).write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{day} 특허 {len(everything)}건을 페이지용으로 {args.file}에 저장했어요"
          + (f" (한국어 요약 없는 특허 {missing}건)" if missing else ""))
    return 0


REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def cmd_patents_report(args: argparse.Namespace) -> int:
    day = _day(args.day)
    with db.session() as conn:
        page = patents.for_page(conn, day)
    if page is None:
        print(f"{day} 특허 브리핑이 없어요.")
        return 1
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{day}.md"
    path.write_text(patents.report_markdown(page), encoding="utf-8")
    _write_reports_index()
    print(f"GitHub용 보고서를 만들었어요: reports/{day}.md")
    return 0


def _write_reports_index() -> None:
    days = sorted((p.stem for p in REPORTS_DIR.glob("????-??-??.md")), reverse=True)
    weeks = sorted((p.stem for p in (REPORTS_DIR / "weekly").glob("????-??-??.md")), reverse=True)
    index = ["# 특허 브리핑 모음", "", "Patent Radar가 매일 찾은 한·미·중 특허와 주간 트렌드 리포트를 모아 둔 곳이에요.", ""]
    deep_reports = sorted((p for p in (REPORTS_DIR / "deep").glob("*.md") if not p.stem.endswith("-list")), reverse=True)

    def deep_title(p: Path) -> str:
        # 첫 줄이 비었거나 제목(# )이 아니면 링크 글자가 비지 않게 파일 이름을 써요. 메모장이 붙이는 BOM도 떼어 내요.
        first = next((s for s in p.read_text(encoding="utf-8-sig").splitlines() if s.strip()), "")
        return first[2:].strip() if first.startswith("# ") and first[2:].strip() else p.stem

    if deep_reports:
        index += ["## 주제 심층 리포트", ""] + [f"- [{deep_title(p)}](deep/{p.name})" for p in deep_reports] + [""]
    if weeks:
        index += ["## 주간 트렌드 리포트", ""] + [f"- [{w} 까지의 일주일](weekly/{w}.md)" for w in weeks] + [""]
    index += ["## 매일 특허 브리핑", ""] + [f"- [{d}]({d}.md)" for d in days]
    (REPORTS_DIR / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")


def cmd_weekly_stats(args: argparse.Namespace) -> int:
    end = _day(args.end)
    with db.session() as conn:
        stats = weekly.build_stats(conn, end, [t.name for t in load_news_settings().topics])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    p = stats["patents"]
    print(f"{stats['start']} ~ {stats['end']}: 브리핑 {len(stats['briefings'])}일, 기사 {stats['news']['total']}건, "
          f"특허 {p['total']}건(🔥 {len(p['spotlight'])}건) → {args.out}")
    return 0


def cmd_weekly_save(args: argparse.Namespace) -> int:
    end = _day(args.end)
    try:
        commentary = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"파일을 읽지 못했어요: {e}")
        return 2
    with db.session() as conn:
        stats = weekly.build_stats(conn, end, [t.name for t in load_news_settings().topics])
        try:
            weekly.save(conn, stats, commentary)
        except weekly.WeeklyError as e:
            print(f"형식 오류: {e}")
            return 2
        report = weekly.get(conn, end)
    (REPORTS_DIR / "weekly").mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "weekly" / f"{end}.md").write_text(weekly.report_markdown(report), encoding="utf-8")
    _write_reports_index()
    if args.page:
        Path(args.page).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"주간 리포트를 저장했어요: reports/weekly/{end}.md" + (f", 페이지용 {args.page}" if args.page else ""))
    return 0


def cmd_stars_sync(args: argparse.Namespace) -> int:
    docs = stars.read_page_docs(Path(args.folder))
    with db.session() as conn:
        added, removed = stars.sync(conn, docs)
        total = conn.execute("SELECT COUNT(*) FROM stars").fetchone()[0]
    print(f"★ 관심 특허 {total}건 (새로 {added}건, 별표 해제 {removed}건)")
    return 0


def cmd_stars_check(args: argparse.Namespace) -> int:
    key = kipris_api_key()
    if not key:
        print(".env 파일에 KIPRIS_API_KEY가 없어요.")
        return 2
    with db.session() as conn, httpx.Client(timeout=30) as http:
        client = patents.KiprisClient(conn, key, load_patent_settings(), http)
        changes, warnings = stars.check(conn, client)
    for c in changes:
        print(f"상태 변화: {c['key']} {c['before']} → {c['after']}")
    for w in warnings:
        print(f"  - {w}")
    print(f"★ 관심 특허 상태 확인 완료 (바뀐 특허 {len(changes)}건)")
    return 0


def cmd_patents_usage(args: argparse.Namespace) -> int:
    with db.session() as conn:
        _print_usage(conn)
    return 0


def cmd_patents_deep(args: argparse.Namespace) -> int:
    if args.rows < 1 or min(args.details_kr, args.details_foreign) < 0:
        print("--rows는 1 이상, --details-kr·--details-foreign은 0 이상이어야 해요.")
        return 2
    key = kipris_api_key()
    if not key:
        print(".env 파일에 KIPRIS_API_KEY가 없어요.")
        return 2
    topic = deep.load_topic(args.config)
    per_country = {"KR": args.details_kr, "US": args.details_foreign, "CN": args.details_foreign}
    settings = load_patent_settings()
    with db.session() as conn:
        # 심층 모으기는 하루 예산 없이 한 번에 많이 부르므로, 시작하기 전에 이번 달 남은 횟수와 견준다.
        blocked = patents.quota_block(conn)
        if blocked:
            print(f"KIPRIS가 이번 달 한도 초과로 호출을 거부해서 시작하지 않아요 ({blocked}). 매월 1일에 다시 열려요.")
            return 3
        # 상세 조회는 검색어에 실제로 나오는 나라만 센다 (한국 전용 주제에 미국·중국 몫을 더하지 않게).
        countries = {c for q in topic.queries for c in q.countries}
        estimate = sum(len(q.countries) for q in topic.queries) + (
            0 if args.no_details else sum(n for c, n in per_country.items() if c in countries))
        remaining = max(settings.monthly_call_limit - patents.calls_total_this_month(conn), 0)
        if estimate > remaining:
            print(f"이번 달 남은 호출이 모자라 시작하지 않아요 (예상 최대 {estimate}회 > 남은 {remaining}회). "
                  "상세 조회 수(--details-kr·--details-foreign)를 줄이거나 --no-details를 쓰세요.")
            return 3
        today = datetime.now(KST).date()
        print(f"예상 최대 {estimate}회: 남은 한도 {remaining}회 → {remaining - estimate}회, 이번 달 남은 날의 하루 예산 약 "
              f"{patents.per_day_budget(remaining, settings, today)}회 → "
              f"{patents.per_day_budget(remaining - estimate, settings, today)}회")
        with httpx.Client(timeout=30) as http:
            before = {s: patents.calls_this_month(conn, s) for s in ("kr", "foreign")}
            client = patents.KiprisClient(conn, key, settings, http)
            hits, runs = deep.collect(client, topic, rows=args.rows)
            chosen = deep.pick_for_details(hits, per_country) if not args.no_details else []
            warnings = deep.fill_details(client, chosen)
            if client.down and not any(w.startswith(patents.NOT_ANSWERING) for w in warnings):
                # 상세 조회할 특허가 없었어도(--no-details 포함) 저장한 JSON에 모으기가 중간에 멈춘 것을 남긴다.
                warnings.insert(0, f"{patents.NOT_ANSWERING} 심층 모으기를 멈췄어요 ({client.down})")
            after ={s: patents.calls_this_month(conn, s) for s in ("kr", "foreign")}
            data = deep.to_json(topic, hits, runs, warnings, {"before": before, "after": after})
    deep.save(data, args.out)
    used = {s: after[s] - before[s] for s in after}
    print(f"{topic.name}: 특허 {len(hits)}건(상세 {len(chosen)}건) → {args.out} · 이번 호출 국내 {used['kr']}회, 해외 {used['foreign']}회")
    return 0


def cmd_patents_claimmap(args: argparse.Namespace) -> int:
    docs_dir, reports_dir = claimmap.default_paths()
    data = claimmap.export(docs_dir, reports_dir, date.today().isoformat())
    counts = ", ".join(f"{p['title']} {p['count']}" for p in data["panels"])
    print(f"청구항 숫자 지도를 만들었어요: docs/data/claims.json, docs/claims-density.svg ({counts} · 뺀 값 {len(data['left_out'])}개)")
    return 0


def cmd_site_export(args: argparse.Namespace) -> int:
    with db.session() as conn:
        index = website.export(conn)
    print(f"GitHub Pages 사이트를 만들었어요: docs/ (브리핑 {len(index['days'])}일, 주간 {len(index['weeks'])}주)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"웹페이지 주소: http://127.0.0.1:{args.port}  (끄려면 Ctrl+C)")
    uvicorn.run("patent_radar.web:app", host="127.0.0.1", port=args.port, reload=args.reload)
    return 0


def main() -> int:
    # 콘솔이 표현하지 못하는 글자가 있어도 멈추지 않게 한다.
    sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(prog="patent_radar", description="이차전지·반도체 뉴스와 특허 레이더")
    sub = parser.add_subparsers(dest="command", required=True)

    p_news = sub.add_parser("news", help="뉴스 수집")
    p_news.add_argument("--json", action="store_true", help="오늘의 새 기사를 JSON으로 출력")
    p_news.set_defaults(func=cmd_news)

    p_brief = sub.add_parser("briefing", help="AI 브리핑 저장·조회")
    brief_sub = p_brief.add_subparsers(dest="action", required=True)
    p_save = brief_sub.add_parser("save", help="브리핑 JSON 파일을 검사해서 저장")
    p_save.add_argument("file")
    p_save.set_defaults(func=cmd_briefing_save)
    p_show = brief_sub.add_parser("show", help="저장된 브리핑 보기")
    p_show.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_show.set_defaults(func=cmd_briefing_show)
    p_export = brief_sub.add_parser("export", help="claude.ai 브리핑 페이지에 올릴 JSON 파일 만들기")
    p_export.add_argument("file")
    p_export.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_export.set_defaults(func=cmd_briefing_export)

    p_pat = sub.add_parser("patents", help="한·미·중 특허 찾기·요약 저장·내보내기")
    pat_sub = p_pat.add_subparsers(dest="action", required=True)
    p_search = pat_sub.add_parser("search", help="뉴스 브리핑 키워드(또는 직접 준 키워드)로 특허 찾기")
    p_search.add_argument("--keyword", action="append", help='"한국어|영어" 형식. 여러 번 쓸 수 있음')
    p_search.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_search.add_argument("--out", default="data/patents-today.json", help="스킬이 읽을 결과 파일")
    p_search.set_defaults(func=cmd_patents_search)
    p_rescore = pat_sub.add_parser("rescore", help="검색 없이 그날 주목도 순위만 다시 매기기")
    p_rescore.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_rescore.set_defaults(func=cmd_patents_rescore)
    p_list = pat_sub.add_parser("list", help="검색하지 않고 그날 특허 브리핑을 스킬이 읽는 파일로 다시 만들기")
    p_list.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_list.add_argument("--out", default="data/patents-today.json")
    p_list.set_defaults(func=cmd_patents_list)
    p_psave = pat_sub.add_parser("save-summaries", help="AI가 쓴 한국어 요약 저장")
    p_psave.add_argument("file")
    p_psave.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_psave.set_defaults(func=cmd_patents_save)
    p_pexport = pat_sub.add_parser("export", help="claude.ai 브리핑 페이지에 올릴 JSON 만들기")
    p_pexport.add_argument("file")
    p_pexport.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_pexport.set_defaults(func=cmd_patents_export)
    p_report = pat_sub.add_parser("report", help="GitHub에 올릴 보고서(reports/날짜.md) 만들기")
    p_report.add_argument("--day", help="YYYY-MM-DD (기본: 오늘)")
    p_report.set_defaults(func=cmd_patents_report)
    p_usage = pat_sub.add_parser("usage", help="이번 달 KIPRIS 호출 수와 오늘 하루 예산")
    p_usage.set_defaults(func=cmd_patents_usage)
    p_deep = pat_sub.add_parser("deep", help="주제 하나를 깊게 모으기 (심층 리포트용, 매일 브리핑 기록에는 넣지 않음)")
    p_deep.add_argument("config", help="config/deep/ 아래 주제 설정(TOML)")
    p_deep.add_argument("--out", required=True, help="결과 JSON (예: data/deep/high_density_lfp.json)")
    p_deep.add_argument("--rows", type=int, default=30, help="검색어마다 살펴볼 최신 특허 수")
    p_deep.add_argument("--details-kr", type=int, default=40, help="청구항을 가져올 한국 특허 수")
    p_deep.add_argument("--details-foreign", type=int, default=25, help="나라(미국·중국)마다 상세를 가져올 특허 수")
    p_deep.add_argument("--no-details", action="store_true", help="검색만 하고 상세 조회는 하지 않기")
    p_deep.set_defaults(func=cmd_patents_deep)
    p_cmap = pat_sub.add_parser("claimmap", help="심층 리포트의 청구항 숫자를 사이트 그래프(docs/claims.html)용 데이터로 만들기")
    p_cmap.set_defaults(func=cmd_patents_claimmap)

    p_stars = sub.add_parser("stars", help="★ 관심 특허 맞추기·상태 확인")
    stars_sub = p_stars.add_subparsers(dest="action", required=True)
    p_ssync = stars_sub.add_parser("sync", help="페이지에서 내려받은 별표 목록과 맞추기")
    p_ssync.add_argument("folder", help="ArtifactData로 stars 문서를 내려받은 폴더")
    p_ssync.set_defaults(func=cmd_stars_sync)
    p_scheck = stars_sub.add_parser("check", help="한국 관심 특허의 진행 상태 다시 확인 (KIPRIS 호출)")
    p_scheck.set_defaults(func=cmd_stars_check)

    p_week = sub.add_parser("weekly", help="주간 트렌드 리포트")
    week_sub = p_week.add_subparsers(dest="action", required=True)
    p_wstats = week_sub.add_parser("stats", help="일주일 통계를 뽑아 AI가 읽을 파일로 저장")
    p_wstats.add_argument("--end", help="일주일의 마지막 날 YYYY-MM-DD (기본: 오늘)")
    p_wstats.add_argument("--out", default="data/weekly-stats.json")
    p_wstats.set_defaults(func=cmd_weekly_stats)
    p_wsave = week_sub.add_parser("save", help="AI 분석을 저장하고 보고서(reports/weekly/날짜.md)를 만든다")
    p_wsave.add_argument("file")
    p_wsave.add_argument("--end", help="일주일의 마지막 날 YYYY-MM-DD (기본: 오늘)")
    p_wsave.add_argument("--page", help="claude.ai 페이지에 올릴 JSON도 이 파일로 저장")
    p_wsave.set_defaults(func=cmd_weekly_save)

    p_site = sub.add_parser("site", help="GitHub Pages 공개 사이트")
    site_sub = p_site.add_subparsers(dest="action", required=True)
    p_sexport = site_sub.add_parser("export", help="docs/에 페이지와 데이터 JSON을 만든다")
    p_sexport.set_defaults(func=cmd_site_export)

    p_serve = sub.add_parser("serve", help="웹페이지 실행")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="코드가 바뀌면 자동으로 다시 시작")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
