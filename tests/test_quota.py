"""KIPRIS 무료 한도 테스트. 한도는 계정 하나에 국내·해외를 합쳐 월 1,000회다.

진짜 KiprisClient에 가짜 KIPRIS 서버(httpx.MockTransport)를 붙여서 네트워크 없이 확인한다.
"""

import contextlib
import json
from argparse import Namespace
from dataclasses import replace
from datetime import date, datetime

import httpx
import pytest

from patent_radar import __main__ as cli
from patent_radar import briefing, db, deep, news, patents
from patent_radar.config import KST, PatentSettings
from patent_radar.patents import KiprisClient, KiprisQuotaError, Keyword

from test_patents import FakeClient, found

DAY = date(2026, 11, 3)
# 명령 테스트에서 httpx.Client를 바꿔 끼우므로 진짜 클래스를 먼저 잡아 둔다.
HttpClient = httpx.Client
REFUSED = ("<response><header><resultCode>22</resultCode><resultMsg>LIMITED NUMBER OF SERVICE REQUESTS EXCEEDS"
           "</resultMsg></header></response>")


class Clock(datetime):
    """patents 모듈이 보는 '지금'. 달이 바뀌는 것을 시험하려고 고정한다."""

    current = datetime(2026, 11, 3, 10, 0, tzinfo=KST)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(patents, "datetime", Clock)
    monkeypatch.setattr(Clock, "current", datetime(2026, 11, 3, 10, 0, tzinfo=KST))
    return Clock


@pytest.fixture
def conn():
    return db.connect(":memory:")


class FakeKipris:
    """가짜 KIPRIS 서버. 받은 요청을 적어 두고, 정해 둔 검색은 500 오류나 오류 22(한도 초과)로 돌려준다."""

    def __init__(self, kr=None, fail=(), refuse_on=()):
        self.requests: list[httpx.Request] = []
        self.kr = kr or {}  # 한국 검색어 → 돌려줄 출원번호
        self.fail = set(fail)  # 500 오류로 실패시킬 (나라, 검색어)
        self.refuse_on = set(refuse_on)  # 오류 22로 거부할 (나라, 검색어)
        self.refuse = False  # 켜면 모든 호출을 거부

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, q = request.url.path, request.url.params
        if path.endswith("/getAdvancedSearch"):
            key = ("KR", q["astrtCont"])
        elif path.endswith("/freeSearch"):
            key = (q["collectionValues"], q["free"].strip('"'))
        else:
            key = ("상세", q.get("applicationNumber") or q.get("literatureNumber"))
        if self.refuse or key in self.refuse_on:
            return httpx.Response(200, text=REFUSED)
        if key in self.fail:
            return httpx.Response(500)
        if path.endswith("/getAdvancedSearch"):
            items = "".join(f"<item><applicationNumber>{n}</applicationNumber><openDate>20261001</openDate>"
                            f"<inventionTitle>t{n}</inventionTitle></item>" for n in self.kr.get(key[1], []))
            body = f"<response><header><resultCode>00</resultCode></header><body><items>{items}</items></body></response>"
        elif path.endswith("/freeSearch"):
            body = "<response></response>"
        else:
            body = "<response><claimInfo><claim>1. 청구항</claim></claimInfo><claimText>1. a claim</claimText></response>"
        return httpx.Response(200, text=body)

    def http(self) -> httpx.Client:
        return HttpClient(transport=httpx.MockTransport(self.handle))

    def paths(self, suffix: str) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith(suffix))


def usage(conn, kr: int, foreign: int, month: str = "2026-11") -> None:
    conn.execute("INSERT INTO api_usage (month, service, calls) VALUES (?, 'kr', ?), (?, 'foreign', ?)",
                 (month, kr, month, foreign))


KW = Keyword("가", "a")


# ---------------------------------------------------------------- 월 상한·한도 초과


def test_check_response_raises_quota_error_only_for_code_22():
    import xml.etree.ElementTree as ET

    with pytest.raises(KiprisQuotaError, match="KIPRIS 오류 22"):
        patents.check_response(ET.fromstring(REFUSED))
    with pytest.raises(patents.KiprisError) as other:
        patents.check_response(ET.fromstring("<r><resultCode>30</resultCode></r>"))
    assert not isinstance(other.value, KiprisQuotaError)


def test_monthly_cap_counts_domestic_and_overseas_together(conn, clock):
    usage(conn, kr=600, foreign=349)
    server = FakeKipris()
    client = KiprisClient(conn, "KEY", PatentSettings(monthly_call_limit=950), server.http())

    client.search("US", KW)  # 합계 949회 → 950번째 호출까지는 한다
    with pytest.raises(KiprisQuotaError, match="합계 950회가 설정한 상한 950회"):
        client.search("KR", KW)
    with pytest.raises(KiprisQuotaError):
        client.details("US", "x")

    # 상한에 닿은 뒤로는 부르지도 세지도 않는다. 우리 상한은 KIPRIS가 거부한 것이 아니라 api_blocks에 적지 않는다.
    assert len(server.requests) == 1 and patents.calls_total_this_month(conn) == 950
    assert client.blocked and client.run_calls == {"kr": 0, "foreign": 1}
    assert patents.quota_block(conn) is None


def test_quota_refusal_blocks_the_rest_of_the_month(conn, clock, monkeypatch):
    server = FakeKipris()
    server.refuse = True
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http())

    with pytest.raises(KiprisQuotaError, match="22"):
        client.search("KR", KW)
    # 거부된 호출도 KIPRIS가 세므로 한 번은 센다. 그 달은 api_blocks에 적는다.
    assert "22" in client.blocked and patents.quota_block(conn, "2026-11") == client.blocked
    assert len(server.requests) == 1 and patents.calls_total_this_month(conn) == 1

    # 같은 클라이언트도, 새로 만든 클라이언트도 그 달에는 부르지 않고 세지도 않는다.
    with pytest.raises(KiprisQuotaError):
        client.details("KR", "1020250046256")
    with pytest.raises(KiprisQuotaError):
        KiprisClient(conn, "KEY", PatentSettings(), server.http()).search("US", KW)
    assert len(server.requests) == 1 and patents.calls_total_this_month(conn) == 1
    assert patents.daily_budget(conn, PatentSettings(), DAY) == 0

    # 다음 달 1일에는 다시 부른다.
    monkeypatch.setattr(Clock, "current", datetime(2026, 12, 1, 0, 5, tzinfo=KST))
    server.refuse = False
    assert patents.quota_block(conn) is None
    KiprisClient(conn, "KEY", PatentSettings(), server.http()).search("KR", KW)
    assert len(server.requests) == 2 and patents.calls_total_this_month(conn) == 1  # 12월 첫 호출


def test_daily_budget_spreads_what_is_left_over_the_remaining_days(conn):
    usage(conn, kr=80, foreign=33)
    s = PatentSettings(monthly_call_limit=950)
    assert patents.daily_budget(conn, s, DAY) == 29                   # (950 - 113) ÷ 28일(3일~30일)
    assert patents.daily_budget(conn, s, date(2026, 11, 30)) == 837   # 마지막 날에는 남은 것 전부
    assert patents.daily_budget(conn, s, date(2026, 12, 1)) == 30     # 새 달: 950 ÷ 31일
    assert patents.daily_budget(conn, replace(s, daily_call_limit=20), DAY) == 20  # 하루 상한이 더 작으면 그 값
    assert patents.daily_budget(conn, replace(s, daily_call_limit=50), DAY) == 29  # 더 크면 자동 값
    assert patents.daily_budget(conn, replace(s, monthly_call_limit=100), DAY) == 0  # 이미 상한을 넘김

    conn.execute("INSERT INTO api_blocks (month, reason, created_at) VALUES ('2026-11', 'KIPRIS 오류 22: x', 't')")
    assert patents.daily_budget(conn, s, DAY) == 0
    assert patents.days_left_in_month(DAY) == 28


# ---------------------------------------------------------------- 하루 예산과 검색 순서


def test_prioritize_puts_first_keywords_then_interests_then_the_rest():
    kws = [Keyword("a1", "a", rank=1), Keyword("a2", "a", rank=2), Keyword("a3", "a", rank=3),
           Keyword("b1", "b", rank=1), Keyword("b2", "b", rank=2), Keyword("lfp", "lfp", interest=True),
           Keyword("a4", "a", rank=4)]
    assert [k.ko for k in patents.prioritize(kws)] == ["a1", "b1", "lfp", "a2", "b2", "a3", "a4"]
    hand = [Keyword("x", "x"), Keyword("y", "y")]  # 직접 준 키워드는 순서 그대로
    assert patents.prioritize(hand) == hand


def test_collect_with_a_budget_searches_important_keywords_first_and_keeps_room_for_details(conn, clock):
    server = FakeKipris(kr={"가": ["K1"], "관심": ["K2"], "나": ["K3"], "다": ["K4"]})
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http(), budget=5)
    keywords = [Keyword("가", "a", ("KR",), rank=1), Keyword("나", "b", ("KR",), rank=2),
                Keyword("다", "c", ("KR",), rank=3), Keyword("관심", "i", ("KR",), interest=True)]

    content = patents.collect(conn, client, keywords, DAY, PatentSettings())

    # 1순위 → 관심 키워드 순으로 찾고, 새 특허 2건의 상세 몫(2회)을 남기려고 2·3순위 검색은 건너뛴다.
    assert [r.url.params["astrtCont"] for r in server.requests if r.url.path.endswith("/getAdvancedSearch")] == ["가", "관심"]
    assert server.paths("/getBibliographyDetailInfoSearch") == 2 and client.run_calls == {"kr": 4, "foreign": 0}
    assert conn.execute("SELECT COUNT(*) FROM patents WHERE claims IS NOT NULL").fetchone()[0] == 2
    assert content["warnings"] == ["하루 호출 예산(5회) 때문에 검색 2개를 건너뛰었어요: 한국 '나', 한국 '다'"]
    assert content["quota_stop"] is None


def test_skipped_keywords_are_not_listed_as_searched(conn, clock):
    server = FakeKipris(kr={"가": ["K1"]})
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http(), budget=3)
    keywords = [Keyword("가", "a", ("KR",), rank=1), Keyword("나", "b", ("KR",), rank=2)]

    content = patents.collect(conn, client, keywords, DAY, PatentSettings())

    # '나'는 상세 몫을 남기려고 건너뛰었으니 페이지·보고서의 '검색 키워드'에 적지 않는다.
    assert [k["ko"] for k in content["keywords"]] == ["가"]
    report = patents.report_markdown(patents.for_page(conn, DAY))
    assert "검색 키워드: 가 (a)\n" in report and "나 (b)" not in report


def test_daily_budget_stops_the_client_without_calling_or_counting(conn, clock):
    server = FakeKipris()
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http(), budget=1)
    client.search("KR", KW)
    with pytest.raises(patents.KiprisBudgetError):
        client.details("KR", "x")
    assert len(server.requests) == 1 and patents.calls_total_this_month(conn) == 1
    assert client.run_calls == {"kr": 1, "foreign": 0} and client.blocked is None and patents.quota_block(conn) is None


def test_collect_checks_the_budget_before_retrying_with_another_term(conn, clock):
    server = FakeKipris()  # 결과 0건 → 다른 표현으로 다시 찾으려 함
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http(), budget=2)
    content = patents.collect(conn, client, [Keyword("가", "a", ("KR",), alt=("나",))], DAY, PatentSettings())
    assert len(server.requests) == 1
    assert "하루 호출 예산(2회) 때문에 검색 1개를 건너뛰었어요: 한국 '나'" in content["warnings"]


def test_collect_marks_quota_stop_when_details_are_refused(conn, clock):
    server = FakeKipris(kr={"가": ["K1"]})
    search = server.handle

    def handle(req):
        if req.url.path.endswith("/getBibliographyDetailInfoSearch"):
            server.refuse = True
        return search(req)

    server.handle = handle
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http(), budget=30)
    content = patents.collect(conn, client, [Keyword("가", "a", ("KR",))], DAY, PatentSettings())
    assert "22" in content["quota_stop"]
    assert sum(w.startswith(patents.QUOTA_STOP) for w in content["warnings"]) == 1


def test_collect_stops_everything_on_quota_refusal(conn, clock):
    server = FakeKipris(kr={"가": ["K1"]}, fail={("KR", "나")}, refuse_on={("US", "na")})
    client = KiprisClient(conn, "KEY", PatentSettings(), server.http(), budget=30)
    keywords = [Keyword("가", "ga", ("KR",)), Keyword("나", "na"), Keyword("다", "da", ("KR",))]

    content = patents.collect(conn, client, keywords, DAY, PatentSettings())

    # 한국 '가' → 한국 '나'(500 실패) → 미국 '나'(거부)에서 멈춘다. 중국 '나'·'다' 검색과 상세 조회는 하지 않는다.
    assert len(server.requests) == 3 and server.paths("/getBibliographyDetailInfoSearch") == 0
    quota = [w for w in content["warnings"] if w.startswith(patents.QUOTA_STOP)]
    assert len(quota) == 1 and "22" in quota[0] and "매월 1일" in quota[0]
    assert any("'나' 검색 실패" in w for w in content["warnings"])
    assert not any("결과 없음" in w for w in content["warnings"])  # 실패한 검색에 '결과 없음'을 덧붙이지 않는다
    assert "22" in content["quota_stop"] and patents.quota_block(conn)
    assert len(content["items"]) == 1  # 멈추기 전에 고른 새 특허는 남는다
    saved = json.loads(conn.execute("SELECT content FROM patent_briefings").fetchone()[0])
    assert saved["quota_stop"] == content["quota_stop"]


def test_fill_details_stops_once_on_budget_or_quota(conn):
    ids = [patents._upsert(conn, found("KR", f"k{n}")) for n in range(3)]

    class Out(FakeClient):
        def __init__(self, error):
            super().__init__({})
            self.error = error

        def details(self, country, doc_key):
            self.detail_calls.append(doc_key)
            raise self.error

    warnings: list[str] = []
    budget = Out(patents.KiprisBudgetError("예산 끝"))
    assert patents._fill_details(conn, budget, ids, warnings) is None
    assert budget.detail_calls == ["k0"] and warnings == ["하루 호출 예산이 끝나 상세 조회 3건을 다음 실행으로 미뤘어요"]

    warnings = []
    quota = Out(KiprisQuotaError("KIPRIS 오류 22: x"))
    assert patents._fill_details(conn, quota, ids, warnings) == "KIPRIS 오류 22: x"
    assert patents._fill_details(conn, quota, ids, warnings) == "KIPRIS 오류 22: x"
    assert len(warnings) == 1 and warnings[0].startswith(patents.QUOTA_STOP)


def test_rescore_never_calls_kipris(conn, monkeypatch):
    class NoDetails(FakeClient):
        def details(self, country, doc_key):
            raise patents.KiprisError("실패")

    # 상세를 못 가져온 주목 특허가 있어도 순위만 다시 매길 때는 KIPRIS를 부르지 않는다.
    shared = "1|20250101"
    client = NoDetails({("US", "가"): [found("US", "u1", priority=shared)], ("US", "나"): [found("US", "u1", priority=shared)],
                        ("CN", "가"): [found("CN", "c1", priority=shared)]})
    first = patents.collect(conn, client, [Keyword("가", "a"), Keyword("나", "b")], DAY, PatentSettings())
    assert any(i["rank"] for i in first["items"])
    warnings: list[str] = []
    assert patents._fill_details(conn, None, [i["patent_id"] for i in first["items"]], warnings) is None and not warnings

    monkeypatch.setattr(cli, "kipris_api_key", lambda: pytest.fail("인증키가 필요 없어야 해요"))
    monkeypatch.setattr(cli.patents, "KiprisClient", lambda *a, **k: pytest.fail("KIPRIS 클라이언트를 만들면 안 돼요"))
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: pytest.fail("HTTP를 열면 안 돼요"))
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    assert cli.cmd_patents_rescore(Namespace(day=DAY.isoformat())) == 0
    again = json.loads(conn.execute("SELECT content FROM patent_briefings").fetchone()[0])
    assert again["warnings"] == first["warnings"] and again["quota_stop"] is None


# ---------------------------------------------------------------- 명령


def _no_http(monkeypatch):
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: pytest.fail("KIPRIS를 부르면 안 돼요"))


def _deep_args(**kw):
    return Namespace(**{"config": "x.toml", "out": "x.json", "rows": 30, "details_kr": 40, "details_foreign": 25,
                        "no_details": False, **kw})


@pytest.mark.parametrize("blocked", [False, True])
def test_patents_deep_refuses_when_the_month_cannot_cover_it(blocked, conn, clock, monkeypatch, capsys):
    usage(conn, kr=60, foreign=30)
    if blocked:
        conn.execute("INSERT INTO api_blocks (month, reason, created_at) VALUES ('2026-11', 'KIPRIS 오류 22: x', 't')")
    topic = deep.DeepTopic("t", [deep.DeepQuery("g1", "가", '"a"'), deep.DeepQuery("g2", "나", '"b"')])
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=100))
    monkeypatch.setattr(cli.deep, "load_topic", lambda path: topic)
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    _no_http(monkeypatch)

    assert cli.cmd_patents_deep(_deep_args()) == 3
    out = capsys.readouterr().out
    # 검색 6회 + 상세 최대 90회(한국 40 + 미국·중국 25씩) = 96회 > 남은 10회
    assert ("거부" in out) if blocked else ("예상 최대 96회 > 남은 10회" in out)
    assert patents.calls_total_this_month(conn) == 90


def test_patents_deep_shows_the_cost_before_it_starts(conn, clock, monkeypatch, capsys, tmp_path):
    from test_deep import fake_kipris

    requests = []
    topic = deep.DeepTopic("t", [deep.DeepQuery("g1", "가", None, ("KR",))])
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=100))
    monkeypatch.setattr(cli.deep, "load_topic", lambda path: topic)
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: HttpClient(transport=httpx.MockTransport(fake_kipris(requests))))

    assert cli.cmd_patents_deep(_deep_args(out=str(tmp_path / "d.json"), no_details=True)) == 0
    assert "예상 최대 1회: 남은 한도 100회 → 99회" in capsys.readouterr().out and len(requests) == 1


def test_patents_deep_counts_details_only_for_countries_it_searches(conn, clock, monkeypatch, capsys, tmp_path):
    from test_deep import fake_kipris

    usage(conn, kr=50, foreign=0)
    requests = []
    topic = deep.DeepTopic("t", [deep.DeepQuery("g1", "가", None, ("KR",))])
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=100))
    monkeypatch.setattr(cli.deep, "load_topic", lambda path: topic)
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: HttpClient(transport=httpx.MockTransport(fake_kipris(requests))))

    # 한국 전용 주제: 검색 1회 + 한국 상세 최대 40회 = 41회 ≤ 남은 50회 (미국·중국 상세 50회는 더하지 않는다)
    assert cli.cmd_patents_deep(_deep_args(out=str(tmp_path / "d.json"))) == 0
    assert "예상 최대 41회: 남은 한도 50회 → 9회" in capsys.readouterr().out
    assert all(r.url.path.endswith(("/getAdvancedSearch", "/getBibliographyDetailInfoSearch")) for r in requests)


def _briefing_db(conn):
    news.save_article(conn, news.Article("g", "기사", "언론", "https://x", None), "이차전지", "이차전지",
                      "2026-11-03T00:10:00+09:00")
    briefing.save(conn, {"day": "2026-11-03", "overview": "o", "topics": [{
        "name": "이차전지", "stories": [{"headline": "h", "summary": "s", "article_ids": [1]}],
        "keywords": [{"ko": "가", "en": "a", "reason": "r"}]}]})


@pytest.mark.parametrize("blocked", [False, True])
def test_patents_search_skips_the_day_when_no_budget_is_left(blocked, conn, clock, monkeypatch, capsys, tmp_path):
    _briefing_db(conn)
    if blocked:
        conn.execute("INSERT INTO api_blocks (month, reason, created_at) VALUES ('2026-11', 'KIPRIS 오류 22: x', 't')")
    else:
        usage(conn, kr=7, foreign=3)  # 상한 10회를 다 씀
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=10))
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    _no_http(monkeypatch)
    out_file = tmp_path / "patents-today.json"
    out_file.write_text('{"items": ["어제 결과"]}', encoding="utf-8")

    assert cli.cmd_patents_search(Namespace(keyword=None, day=DAY.isoformat(), out=str(out_file))) == 3
    assert "오늘은 특허를 찾지 않아요" in capsys.readouterr().out
    assert json.loads(out_file.read_text(encoding="utf-8"))["items"] == []  # 어제 결과를 오늘 것으로 읽지 않게 비운다


def test_patents_search_skips_the_day_when_the_budget_is_one(conn, clock, monkeypatch, capsys, tmp_path):
    # 예산 1회로는 검색 1회 + 상세 1회를 할 수 없어서 collect가 아무것도 찾지 않는다. 그래서 처음부터 코드 3으로 멈춘다.
    _briefing_db(conn)
    usage(conn, kr=900, foreign=10)  # (950 - 910) ÷ 28일 = 1
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=950))
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    _no_http(monkeypatch)

    assert cli.cmd_patents_search(Namespace(keyword=None, day=DAY.isoformat(), out=str(tmp_path / "p.json"))) == 3
    out = capsys.readouterr().out
    assert "이번 달 남은 호출이 모자라 오늘은 특허를 찾지 않아요" in out and "오늘 하루 예산: 1회 중 0회 씀" in out


def test_patents_search_with_keyword_ignores_the_daily_budget(conn, clock, monkeypatch, tmp_path):
    usage(conn, kr=930, foreign=0)  # (950-930)//28 = 0 → 하루 예산 0, 월 상한에는 아직 안 닿음
    server = FakeKipris()
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=950))
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: server.http())
    assert cli.cmd_patents_search(Namespace(keyword=["가|a"], day=DAY.isoformat(), out=str(tmp_path / "p.json"))) == 0
    assert len(server.requests) == 3  # 한국·미국·중국


def test_usage_shows_the_combined_total_and_todays_budget(conn, clock, monkeypatch, capsys):
    usage(conn, kr=80, foreign=33)
    monkeypatch.setattr(cli, "load_patent_settings", lambda: PatentSettings(monthly_call_limit=950))
    monkeypatch.setattr(cli, "datetime", Clock)
    cli._print_usage(conn)
    out = capsys.readouterr().out
    assert "국내 80회 + 해외 33회 = 113회" in out and "계정 합계 1,000회" in out
    assert "지금 실행하면 쓸 하루 예산: 29회 (남은 837회 ÷ 남은 28일)" in out

    # 검색 뒤에는 다시 계산한 값(28회)이 아니라 그 실행에 쓴 예산과 쓴 횟수를 보여 준다.
    conn.execute("UPDATE api_usage SET calls = calls + 29 WHERE month = '2026-11' AND service = 'kr'")
    cli._print_usage(conn, 29, 29)
    out = capsys.readouterr().out
    assert "= 142회" in out and "오늘 하루 예산: 29회 중 29회 씀" in out and "지금 실행하면" not in out


def test_patents_search_returns_3_when_kipris_refuses(conn, clock, monkeypatch, capsys, tmp_path):
    _briefing_db(conn)
    server = FakeKipris()
    server.refuse = True
    monkeypatch.setattr(cli, "kipris_api_key", lambda: "KEY")
    monkeypatch.setattr(cli.db, "session", lambda *a, **k: contextlib.nullcontext(conn))
    monkeypatch.setattr(cli.httpx, "Client", lambda *a, **k: server.http())

    assert cli.cmd_patents_search(Namespace(keyword=None, day=DAY.isoformat(), out=str(tmp_path / "p.json"))) == 3
    out = capsys.readouterr().out
    assert "이번 실행 호출: 국내 1회, 해외 0회" in out and "⚠ KIPRIS 이번 달 한도가 끝나" in out
    assert "회 중 1회 씀" in out
    assert len(server.requests) == 1  # 거부당한 뒤로는 관심 키워드도 부르지 않는다
