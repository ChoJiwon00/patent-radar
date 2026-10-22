"""주제 심층 모으기 테스트. KIPRIS는 부르지 않고 검색 결과를 가짜로 넣는다."""

import json
from argparse import Namespace
from datetime import date

import httpx
import pytest

from patent_radar import db, deep
from patent_radar.config import PatentSettings
from patent_radar.patents import Found, KiprisClient


def found(country, key, published, ipc="H01M4/58"):
    return Found(country, key, f"{country} {key}", f"title {key}", "회사", None, published, None, None, ipc=ipc)


def test_load_topic_requires_the_text_each_country_needs(tmp_path):
    good = tmp_path / "good.toml"
    good.write_text('name = "t"\nipc = ["H01M4/58"]\n[[queries]]\ngroup = "g"\nko = "가"\nen = \'"a"\'\n', encoding="utf-8")
    topic = deep.load_topic(good)
    assert topic.name == "t" and topic.ipc == ("H01M4/58",) and topic.queries[0].countries == ("KR", "US", "CN")

    bad = tmp_path / "bad.toml"
    bad.write_text('name = "t"\n[[queries]]\ngroup = "g"\nko = "가"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        deep.load_topic(bad)  # 미국·중국 검색에 en이 없음


def test_collect_merges_queries_and_filters_old_or_off_topic(monkeypatch):
    results = {
        ("KR", "q1"): [found("KR", "A", "2026-10-01"), found("KR", "OLD", "2019-01-01"),
                       found("KR", "OFF", "2026-09-01", ipc="A61K36/00")],
        ("KR", "q2"): [found("KR", "A", "2026-10-01"), found("KR", "B", "2026-08-01")],
    }
    monkeypatch.setattr(deep, "_search", lambda client, country, q, rows: results[(country, q.ko)])
    topic = deep.DeepTopic("t", [deep.DeepQuery("g1", "q1", None, ("KR",)), deep.DeepQuery("g2", "q2", None, ("KR",))],
                           recent_years=3, ipc=("H01M4/58",))
    hits, runs = deep.collect(None, topic, today=date(2026, 10, 23), log=lambda *_: None)

    assert set(k for _, k in hits) == {"A", "B"}             # 오래된 특허·다른 분류는 빠짐
    assert hits[("KR", "A")].groups == {"g1", "g2"}          # 두 검색어에 모두 걸린 특허는 하나로 합침
    assert [(r["found"], r["kept"]) for r in runs] == [(3, 1), (2, 2)]


def test_reports_index_lists_deep_reports_but_not_their_appendix(tmp_path, monkeypatch):
    from patent_radar import __main__ as cli

    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "topic-2026-10.md").write_text("# 주제 리포트\n본문", encoding="utf-8")
    (tmp_path / "deep" / "topic-2026-10-list.md").write_text("# 부록", encoding="utf-8")
    (tmp_path / "2026-10-23.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path)
    cli._write_reports_index()
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "[주제 리포트](deep/topic-2026-10.md)" in text and "부록" not in text and "[2026-10-23](2026-10-23.md)" in text


def test_pick_for_details_prefers_patents_found_by_more_queries():
    a, b, c = (deep.Hit(found("KR", k, d)) for k, d in (("A", "2026-01-01"), ("B", "2026-10-01"), ("C", "2026-05-01")))
    a.queries = {"KR:x", "KR:y"}
    b.queries = {"KR:x"}
    c.queries = {"KR:y"}
    hits = {("KR", h.found.doc_key): h for h in (a, b, c)}
    chosen = deep.pick_for_details(hits, {"KR": 2})
    assert [h.found.doc_key for h in chosen] == ["A", "B"]   # 많이 걸린 것 먼저, 그다음 최신


# ---------------------------------------------------------------- 진짜 KiprisClient + 가짜 KIPRIS 서버 (네트워크 없음)


def _kr_items(*rows):
    items = "".join(f"<item><applicationNumber>{k}</applicationNumber><openDate>{d}</openDate>"
                    f"<inventionTitle>t{k}</inventionTitle><ipcNumber>{ipc}</ipcNumber></item>" for k, d, ipc in rows)
    return f"<response><header><resultCode>00</resultCode></header><body><items>{items}</items></body></response>"


def _foreign_items(*rows):
    items = "".join(f"<searchResult><ltrtno>{k}</ltrtno><openDate>{d}</openDate><inventionName>t{k}</inventionName>"
                    f"<ipc>{ipc}</ipc></searchResult>" for k, d, ipc in rows)
    return f"<response>{items}</response>"


def fake_kipris(requests):
    """주소마다 정해 둔 XML을 돌려줘요. 중국 검색은 500 오류로 실패시켜요."""

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path, q = request.url.path, request.url.params
        if path.endswith("/getAdvancedSearch"):
            body = {"가": _kr_items(("K1", "20261001", "H01M 4/58"), ("KOLD", "20150101", "H01M 4/58")),
                    "나": _kr_items(("K1", "20261001", "H01M 4/58"), ("K2", "20260901", "A61K 36/00"))}[q["astrtCont"]]
        elif path.endswith("/freeSearch"):
            if q["collectionValues"] == "CN":
                return httpx.Response(500)
            body = _foreign_items(("U1A1", "20260801", "H01M 4/58"))
        elif path.endswith("/getBibliographyDetailInfoSearch"):  # 한국 상세: 청구항은 있지만 분류(ipcInfo)가 없음
            body = "<response><claimInfo><claim>1. 청구항</claim></claimInfo></response>"
        elif path.endswith("/demandParagraphInfo"):
            body = "<response><claimText>1. a claim</claimText></response>"
        else:
            raise AssertionError(f"모르는 주소: {request.url}")
        return httpx.Response(200, text=body)

    return handle


def _table_count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_deep_run_only_counts_calls_and_never_touches_the_patents_table(tmp_path):
    conn, requests = db.connect(":memory:"), []
    http = httpx.Client(transport=httpx.MockTransport(fake_kipris(requests)))
    client = KiprisClient(conn, "KEY", PatentSettings(monthly_call_limit=100), http)
    topic = deep.DeepTopic("t", [deep.DeepQuery("g1", "가", '"a"', ("KR", "US", "CN")),
                                 deep.DeepQuery("g2", "나", None, ("KR",))], recent_years=3, ipc=("H01M4/58",))

    hits, runs = deep.collect(client, topic, today=date(2026, 10, 23), log=lambda *_: None)
    chosen = deep.pick_for_details(hits, {"KR": 5, "US": 5, "CN": 5})
    warnings = deep.fill_details(client, chosen, log=lambda *_: None)
    deep.save(deep.to_json(topic, hits, runs, warnings, {}), tmp_path / "out" / "deep.json")

    # 특허 표·매일 브리핑 기록은 그대로, 호출 수만 셈 (실패한 중국 검색도 한 번으로 셈)
    assert _table_count(conn, "patents") == 0 and _table_count(conn, "patent_matches") == 0
    assert _table_count(conn, "patent_briefings") == 0
    usage = {r["service"]: r["calls"] for r in conn.execute("SELECT service, calls FROM api_usage")}
    assert usage == {"kr": 3, "foreign": 3} and len(requests) == 6
    # 중국 검색 오류는 그 검색어에만 기록되고 나머지는 계속 진행
    assert [(r["country"], r["group"]) for r in runs if "error" in r] == [("CN", "g1")]
    assert sorted(k for _, k in hits) == ["K1", "U1A1"]      # 오래된 특허(KOLD)·다른 분류(K2)는 빠짐
    saved = json.loads((tmp_path / "out" / "deep.json").read_text(encoding="utf-8"))
    kr = next(i for i in saved["items"] if i["doc_key"] == "K1")
    assert kr["claims"] == "1. 청구항" and kr["groups"] == ["g1", "g2"]
    assert kr["ipc"] == "H01M4/58"                             # 상세에 분류가 없어도 검색의 분류를 지킴


def test_deep_run_stops_calling_at_the_monthly_limit_and_keeps_going():
    conn, requests = db.connect(":memory:"), []
    http = httpx.Client(transport=httpx.MockTransport(fake_kipris(requests)))
    client = KiprisClient(conn, "KEY", PatentSettings(monthly_call_limit=1), http)
    topic = deep.DeepTopic("t", [deep.DeepQuery("g1", "가", None, ("KR",)), deep.DeepQuery("g2", "나", None, ("KR",))])

    hits, runs = deep.collect(client, topic, today=date(2026, 10, 23), log=lambda *_: None)
    warnings = deep.fill_details(client, deep.pick_for_details(hits, {"KR": 5}), log=lambda *_: None)

    assert len(requests) == 1 and conn.execute("SELECT calls FROM api_usage").fetchone()[0] == 1
    assert "error" not in runs[0] and "상한" in runs[1]["error"]   # 두 번째 검색어는 한도로 멈춤(오류로 기록)
    assert len(warnings) == 1 and "상한" in warnings[0]             # 상세 조회도 부르지 않고 경고만


def test_recent_years_looks_at_published_date_only(monkeypatch):
    rows = [Found("KR", "NOPUB", "n", "t", None, "2026-10-01", None, None, None),     # 출원일만 최근
            Found("KR", "NEW", "n", "t", None, "2010-01-01", "2026-10-01", None, None)]  # 출원은 오래, 공개는 최근
    monkeypatch.setattr(deep, "_search", lambda client, country, q, rows_: rows)
    topic = deep.DeepTopic("t", [deep.DeepQuery("g", "가", None, ("KR",))], recent_years=1)
    hits, _ = deep.collect(None, topic, today=date(2026, 10, 23), log=lambda *_: None)
    assert [k for _, k in hits] == ["NEW"]


def test_details_fill_only_empty_fields_and_never_erase_search_values():
    kr = deep.Hit(found("KR", "A", "2026-10-01"), {"g"}, {"KR:x"},
                  {"claims": "1. c", "claim_count": 1, "status": None, "ipc": None, "priority_no": "1|2"})
    cn = deep.Hit(found("CN", "B", "2026-10-01"), {"g"}, {"CN:y"}, {"abstract": "abs"})
    data = deep.to_json(deep.DeepTopic("t", []), {("KR", "A"): kr, ("CN", "B"): cn}, [], [], {})
    by_key = {i["doc_key"]: i for i in data["items"]}
    assert by_key["A"]["ipc"] == "H01M4/58" and by_key["A"]["claims"] == "1. c" and by_key["A"]["priority_no"] == "1|2"
    assert by_key["B"]["abstract"] == "abs" and by_key["B"]["groups"] == ["g"]


def test_pick_for_details_treats_a_negative_count_as_zero():
    hits = {("KR", k): deep.Hit(found("KR", k, "2026-10-01")) for k in ("A", "B", "C")}
    assert deep.pick_for_details(hits, {"KR": -1}) == []


@pytest.mark.parametrize("rows, kr, foreign", [(0, 5, 5), (30, -1, 5), (30, 5, -2)])
def test_patents_deep_rejects_bad_counts_before_calling_kipris(rows, kr, foreign, monkeypatch):
    from patent_radar import __main__ as cli

    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli.deep, "load_topic", lambda path: deep.DeepTopic("t", []))
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: pytest.fail("DB를 열면 안 돼요"))
    args = Namespace(config="x.toml", out="x.json", rows=rows, details_kr=kr, details_foreign=foreign, no_details=False)
    assert cli.cmd_patents_deep(args) == 2


def test_reports_index_without_deep_reports_is_unchanged(tmp_path, monkeypatch):
    from patent_radar import __main__ as cli

    (tmp_path / "weekly").mkdir()
    (tmp_path / "weekly" / "2026-10-21.md").write_text("w", encoding="utf-8")
    for d in ("2026-10-22", "2026-10-23"):
        (tmp_path / f"{d}.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path)
    cli._write_reports_index()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == (
        "# 특허 브리핑 모음\n\nPatent Radar가 매일 찾은 한·미·중 특허와 주간 트렌드 리포트를 모아 둔 곳이에요.\n\n"
        "## 주간 트렌드 리포트\n\n- [2026-10-21 까지의 일주일](weekly/2026-10-21.md)\n\n"
        "## 매일 특허 브리핑\n\n- [2026-10-23](2026-10-23.md)\n- [2026-10-22](2026-10-22.md)\n")


def test_reports_index_deep_titles_survive_blank_lines_bom_and_missing_heading(tmp_path, monkeypatch):
    from patent_radar import __main__ as cli

    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "b-2026-10.md").write_text("\n\n# 제목 B\n본문", encoding="utf-8-sig")
    (tmp_path / "deep" / "a-2026-10.md").write_text("제목 줄 없이 바로 본문", encoding="utf-8")
    monkeypatch.setattr(cli, "REPORTS_DIR", tmp_path)
    cli._write_reports_index()
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "- [제목 B](deep/b-2026-10.md)\n- [a-2026-10](deep/a-2026-10.md)\n" in text
    assert "## 주간 트렌드 리포트" not in text and "## 매일 특허 브리핑" in text
