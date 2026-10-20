"""KIPRIS Plus로 한국·미국·중국 특허를 찾아 저장하고, 그날 브리핑에 넣을 특허를 고른다.

하루 브리핑은 두 부분이다.
- 새 특허: 키워드·나라마다 아직 보여준 적 없는 최신 특허. 한 번 보여준 특허는 다음 날부터 다시 넣지 않는다.
- 🔥 주목 특허: 최근 며칠 동안의 검색 결과로 '주목도 점수'를 매겨 기준을 넘은 특허를 순위대로.
  점수가 기준 이상인 동안은 매일 맨 위에 다시 나온다.

주목도 점수 (특허에 공개된 조회수가 없고, 막 공개된 특허는 인용도 거의 없어서 아래 신호로 대신한다)
- 최근 뉴스 키워드 하나에 걸릴 때마다 +2  (여러 뉴스 주제와 이어진 기술)
- 최근 며칠 동안 검색에 걸린 날 하루마다 +1 (첫날 제외, 계속 뉴스와 이어지는 기술)
- 같은 발명을 다른 나라에도 냈으면 나라마다 +2 (여러 나라에 돈을 들여 낸 중요한 발명)
  같은 우선권·같은 국제출원 번호를 가진 특허, 한국 특허가 우선권을 처음 낸 나라를 함께 센다.
- 국제출원(PCT)을 했으면 +1
- 관심 회사의 특허면 +2
- 기사가 많이 나온 뉴스 이야기에서 나온 키워드면 +1 (뉴스 화제성)
- 청구항이 20개 이상이면 +1 (권리 범위를 넓게 잡은 특허)
AI가 '관련 낮음'으로 판정한 특허는 순위에서 뺀다.

청구항·초록 같은 상세 정보는 브리핑에 넣을 특허만 따로 가져와서 무료 호출 한도를 아낀다.
"""

import html
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

import httpx

from .config import KST, PatentSettings

KR_BASE = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice"
FOREIGN_SEARCH = "http://plus.kipris.or.kr/openapi/rest/ForeignPatentAdvencedSearchService/freeSearch"
FOREIGN_BIBLIO = "http://plus.kipris.or.kr/openapi/rest/ForeignPatentBibliographicService"

COUNTRIES = ("KR", "US", "CN")
COUNTRY_NAMES = {"KR": "한국", "US": "미국", "CN": "중국", "JP": "일본", "EP": "유럽", "DE": "독일", "TW": "대만",
                 "GB": "영국", "FR": "프랑스"}
# 무료 한도는 국내 서비스와 해외 서비스를 따로 센다.
SERVICE_NAMES = {"kr": "국내 특허", "foreign": "해외 특허"}
MANY_CLAIMS = 20
BUZZ_ARTICLES = 4  # 키워드가 나온 뉴스 이야기의 기사가 이만큼 많으면 화제 기술로 본다


class KiprisError(RuntimeError):
    """KIPRIS 호출 실패. 메시지에 인증키가 들어가지 않게 만든다."""


@dataclass(frozen=True)
class Keyword:
    ko: str
    en: str
    # 이 키워드로 검색할 나라. 한국은 무료 한도에 여유가 있어 해외보다 키워드를 더 쓴다.
    countries: tuple[str, ...] = ("KR", "US", "CN")
    # 뉴스 브리핑의 주제(이차전지/ESS/반도체). 주제에 맞는 특허 분류(IPC)만 남길 때 쓴다.
    topic: str | None = None
    # 한국 특허가 0건일 때 다시 찾아볼 다른 한국어 표현 (띄어쓰기·용어가 다른 것)
    alt: tuple[str, ...] = ()
    # 이 키워드가 나온 뉴스 이야기의 기사 수. 많을수록 화제가 된 기술이다.
    buzz: int = 0
    # 뉴스와 상관없이 매일 찾는 고정 관심 키워드인지
    interest: bool = False

    @classmethod
    def parse(cls, text: str) -> "Keyword":
        ko, sep, en = text.partition("|")
        if not sep or not ko.strip() or not en.strip():
            raise ValueError(f'키워드는 "한국어|영어" 형식이어야 합니다: {text}')
        return cls(ko.strip(), en.strip())


@dataclass
class Found:
    """검색 결과 한 건."""

    country: str
    doc_key: str
    number: str
    title: str
    applicant: str | None
    filed_at: str | None
    published_at: str | None
    priority_no: str | None
    url: str | None
    abstract: str | None = None
    ipc: str | None = None  # 국제특허분류. "H01M10/0562|H01M4/62"처럼 | 로 구분
    pct_no: str | None = None  # 국제출원(PCT) 번호


# ---------------------------------------------------------------- 응답 해석

# KIPRIS 한국 특허 상세의 우선권 나라 이름 → 나라 코드
PRIORITY_COUNTRIES = {"대한민국": "KR", "미국": "US", "중국": "CN", "일본": "JP", "유럽특허청": "EP", "독일": "DE",
                      "대만": "TW", "영국": "GB", "프랑스": "FR", "세계지적재산권기구": "WO"}


def priority_key(number: str | None, day: str | None) -> str | None:
    """우선권 번호를 나라마다 다른 표기와 상관없이 비교할 수 있게 '숫자|날짜'로 바꾼다.

    한국 상세는 '63/622,744', 해외 검색은 '63622744'처럼 적혀 오지만 숫자와 날짜는 같다.
    """
    digits = re.sub(r"\D", "", number or "")
    return f"{digits}|{re.sub(r'[^0-9]', '', day or '')}" if digits else None


def pct_key(value: str | None) -> str | None:
    """'PCT/US2025/012076' → 'PCTUS2025012076'"""
    key = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return key or None


def _ipc(value: str | None) -> str | None:
    """'H01M 10/0562|H01M 4/62' → 'H01M10/0562|H01M4/62' (나라마다 띄어쓰기가 달라서 맞춘다)."""
    codes = [c.replace(" ", "").upper() for c in (value or "").split("|") if c.strip()]
    return "|".join(codes) or None


def matches_ipc(ipc: str | None, prefixes: tuple[str, ...]) -> bool:
    """분류 코드 중 하나라도 주제의 접두어로 시작하면 True. 분류 정보가 없거나 접두어가 없으면 거르지 않는다."""
    if not prefixes or not ipc:
        return True
    return any(code.startswith(p) for code in ipc.split("|") for p in prefixes)


def _text(el: ET.Element, tag: str) -> str | None:
    # 해외 특허 응답에는 XI&apos;AN 처럼 한 번 더 이스케이프된 글자가 섞여 와서 풀어 준다.
    value = html.unescape((el.findtext(tag) or "").strip())
    return value or None


def _date(yyyymmdd: str | None) -> str | None:
    s = (yyyymmdd or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else None


APPLICANT_SEP = " · "  # 회사 이름에 쉼표가 들어가서(Co., Ltd.) 출원인 여러 명은 가운뎃점으로 잇는다
_CORP_SUFFIX = re.compile(r"^(co|ltd|inc|llc|l\.?p|gmbh|s\.?a|limited|corp|corporation|plc|ag|n\.?v|b\.?v|kg)\.?$", re.I)


def _people(value: str | None) -> str | None:
    return APPLICANT_SEP.join(dict.fromkeys(p.strip() for p in value.split("|") if p.strip())) if value else None


def split_applicants(value: str | None) -> list[str]:
    """출원인 목록. 예전에 쉼표로 이어 저장한 값도 'Co., Ltd.' 같은 회사 꼬리표를 붙여 둔 채 나눈다."""
    if not value:
        return []
    if APPLICANT_SEP in value:
        return [p for p in value.split(APPLICANT_SEP) if p]
    names: list[str] = []
    for token in value.split(", "):
        if names and _CORP_SUFFIX.match(token.strip()):
            names[-1] += f", {token}"
        else:
            names.append(token)
    return names


def check_response(root: ET.Element) -> None:
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("", "00"):
        msg = (root.findtext(".//resultMsg") or "").strip()
        raise KiprisError(f"KIPRIS 오류 {code}: {msg or '자세한 이유가 없어요'}")


def _kr_number(open_no: str | None, reg_no: str | None, app_no: str) -> str:
    if open_no and len(open_no) == 13:
        return f"공개 {open_no[:2]}-{open_no[2:6]}-{open_no[6:]}"
    if reg_no and len(reg_no) >= 9:
        return f"등록 {reg_no[:2]}-{reg_no[2:9]}"
    return f"출원 {app_no[:2]}-{app_no[2:6]}-{app_no[6:]}"


def parse_kr_search(root: ET.Element) -> list[Found]:
    found = []
    for item in root.iter("item"):
        app_no = _text(item, "applicationNumber")
        if not app_no:
            continue
        found.append(
            Found(
                country="KR",
                doc_key=app_no,
                number=_kr_number(_text(item, "openNumber"), _text(item, "registerNumber"), app_no),
                title=_text(item, "inventionTitle") or "(제목 없음)",
                applicant=_people(_text(item, "applicantName")),
                filed_at=_date(_text(item, "applicationDate")),
                published_at=_date(_text(item, "openDate")) or _date(_text(item, "registerDate")),
                priority_no=None,
                url=f"https://doi.org/10.8080/{app_no}",
                abstract=_text(item, "astrtCont"),
                ipc=_ipc(_text(item, "ipcNumber")),
            )
        )
    return found


def parse_foreign_search(root: ET.Element, country: str) -> list[Found]:
    found = []
    for res in root.iter("searchResult"):
        lit = _text(res, "ltrtno")
        if not lit:
            continue
        kind = re.sub(r"^\d+", "", lit)
        num = _text(res, "registerNo") or _text(res, "openNumber")
        if num and country == "US":
            num = num.lstrip("0")  # 등록번호는 09432298처럼 0이 붙어 오지만 공식 번호는 9432298
        if country == "CN" and kind.startswith("A"):
            kind = "A"  # 중국 공개공보는 A
        if num:
            shown = f"{num[:4]}/{num[4:]}" if country == "US" and len(num) == 11 else num
            number = f"{country} {shown} {kind}".strip()
            url = f"https://patents.google.com/patent/{country}{num}{kind}"
        else:
            number, url = f"{country} {lit}", None
        found.append(
            Found(
                country=country,
                doc_key=lit,
                number=number,
                title=_text(res, "inventionName") or "(no title)",
                applicant=_people(_text(res, "applicant")),
                filed_at=_date(_text(res, "applicationDate")),
                published_at=_date(_text(res, "openDate")) or _date(_text(res, "registerDate")),
                priority_no=priority_key(_text(res, "priorityNo"), _text(res, "priorityDate")),
                url=url,
                ipc=_ipc(_text(res, "ipc")),
                pct_no=pct_key(_text(res, "internationalApplicationNo")),
            )
        )
    return found


def parse_kr_family(root: ET.Element) -> dict:
    """한국 특허 상세에서 우선권(처음 낸 나라)·국제출원·진행 상태·특허 분류를 읽는다."""
    info: dict = {"status": (root.findtext(".//registerStatus") or "").strip() or None,
                  "ipc": _ipc("|".join(el.findtext("ipcNumber") or "" for el in root.iter("ipcInfo")))}
    prio = root.find(".//priorityInfo")
    if prio is not None:
        info["priority_no"] = priority_key(prio.findtext("priorityApplicationNumber"),
                                           prio.findtext("priorityApplicationDate"))
        name = (prio.findtext("priorityApplicationCountry") or "").strip()
        info["priority_country"] = PRIORITY_COUNTRIES.get(name, name or None)
    intl = root.find(".//internationalInfo")
    if intl is not None:
        info["pct_no"] = pct_key(intl.findtext("internationalApplicationNumber"))
    return info


def parse_kr_claims(root: ET.Element) -> tuple[str | None, int]:
    claims = [(c.findtext("claim") or "").strip() for c in root.iter("claimInfo")]
    claims = [c for c in claims if c]
    return ("\n".join(claims) or None), len(claims)


def parse_foreign_claims(root: ET.Element) -> tuple[str | None, int]:
    text = "\n".join(t for t in ((c.text or "").strip() for c in root.iter("claimText")) if t)
    # 청구항은 "1. ...", "    2. ..."처럼 공백 여러 칸 뒤 번호로 시작한다.
    # "according to claim 1. " 같은 문장 끝 번호를 세지 않으려고 공백이 두 칸 이상인 것만 센다.
    count = len(re.findall(r"(?:^|\s{2,})\d{1,3}\.\s", text))
    return (text or None), count


def parse_foreign_abstract(root: ET.Element) -> str | None:
    return (root.findtext(".//astrtCont") or "").strip() or None


# ---------------------------------------------------------------- KIPRIS 호출


def calls_this_month(conn: sqlite3.Connection, service: str, month: str | None = None) -> int:
    month = month or datetime.now(KST).strftime("%Y-%m")
    row = conn.execute("SELECT calls FROM api_usage WHERE month = ? AND service = ?", (month, service)).fetchone()
    return row["calls"] if row else 0


class KiprisClient:
    """KIPRIS Plus 호출. 서비스별 월 호출 수를 세고 설정한 상한에서 멈춘다."""

    def __init__(self, conn: sqlite3.Connection, key: str, settings: PatentSettings, http: httpx.Client):
        self.conn, self.key, self.settings, self.http = conn, key, settings, http

    def _call(self, service: str, url: str, params: dict) -> ET.Element:
        month = datetime.now(KST).strftime("%Y-%m")
        used = calls_this_month(self.conn, service, month)
        if used >= self.settings.monthly_call_limit:
            raise KiprisError(
                f"이번 달 {SERVICE_NAMES[service]} 호출이 {used}회라 멈췄어요 (설정한 상한 {self.settings.monthly_call_limit}회)"
            )
        # 실패한 호출도 KIPRIS 쪽에서는 세므로 먼저 기록한다.
        self.conn.execute(
            """INSERT INTO api_usage (month, service, calls) VALUES (?, ?, 1)
               ON CONFLICT (month, service) DO UPDATE SET calls = calls + 1""",
            (month, service),
        )
        self.conn.commit()
        try:
            resp = self.http.get(url, params=params)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
        except httpx.HTTPError as e:
            raise KiprisError(f"{type(e).__name__}: {str(e).replace(self.key, '***')}") from None
        except ET.ParseError:
            raise KiprisError("KIPRIS 응답을 읽지 못했어요") from None
        check_response(root)
        return root

    def search(self, country: str, kw: Keyword) -> list[Found]:
        if country == "KR":
            root = self._call(
                "kr",
                f"{KR_BASE}/getAdvancedSearch",
                {"astrtCont": kw.ko, "sortSpec": "OPD", "descSort": "true", "patent": "true", "utility": "true",
                 "numOfRows": str(self.settings.scan_per_search), "pageNo": "1", "ServiceKey": self.key},
            )
            return parse_kr_search(root)
        # 해외는 따옴표로 묶어야 단어가 모두 들어간 특허만 나온다.
        root = self._call(
            "foreign",
            FOREIGN_SEARCH,
            {"free": f'"{kw.en}"', "collectionValues": country, "sortField": "OPD", "sortState": "true",
             "currentPage": "1", "accessKey": self.key},
        )
        return parse_foreign_search(root, country)[: self.settings.scan_per_search]

    def details(self, country: str, doc_key: str) -> dict:
        """청구항(KR·US) 또는 초록(CN). 중국은 KIPRIS에 청구항이 없어서 영문 초록을 쓴다."""
        if country == "KR":
            root = self._call("kr", f"{KR_BASE}/getBibliographyDetailInfoSearch",
                              {"applicationNumber": doc_key, "ServiceKey": self.key})
            claims, count = parse_kr_claims(root)
            return {"claims": claims, "claim_count": count, **parse_kr_family(root)}
        params = {"literatureNumber": doc_key, "countryCode": country, "accessKey": self.key}
        if country == "US":
            claims, count = parse_foreign_claims(self._call("foreign", f"{FOREIGN_BIBLIO}/demandParagraphInfo", params))
            return {"claims": claims, "claim_count": count}
        return {"abstract": parse_foreign_abstract(self._call("foreign", f"{FOREIGN_BIBLIO}/summation", params))}


# ---------------------------------------------------------------- 주목도


def _upsert(conn: sqlite3.Connection, f: Found) -> int:
    conn.execute(
        """INSERT INTO patents (country, doc_key, number, title, applicant, filed_at, published_at, priority_no, url,
                                abstract, ipc, pct_no)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (country, doc_key) DO UPDATE SET
             number = excluded.number, title = excluded.title, applicant = excluded.applicant,
             published_at = excluded.published_at, url = excluded.url,
             priority_no = COALESCE(excluded.priority_no, patents.priority_no),
             abstract = COALESCE(patents.abstract, excluded.abstract),
             ipc = COALESCE(excluded.ipc, patents.ipc),
             pct_no = COALESCE(excluded.pct_no, patents.pct_no)""",
        (f.country, f.doc_key, f.number, f.title, f.applicant, f.filed_at, f.published_at, f.priority_no, f.url,
         f.abstract, f.ipc, f.pct_no),
    )
    return conn.execute("SELECT id FROM patents WHERE country = ? AND doc_key = ?", (f.country, f.doc_key)).fetchone()["id"]


def watched_company(applicant: str | None, companies: tuple[str, ...]) -> str | None:
    """출원인 이름에 관심 회사 이름이 들어 있으면 그 이름 (대소문자 무시)."""
    text = (applicant or "").upper()
    return next((c for c in companies if c.upper() in text), None)


def family_countries(conn: sqlite3.Connection, patent_id: int) -> list[str]:
    """같은 발명을 낸 나라들. 같은 우선권·같은 국제출원(PCT)인 특허와, 우선권을 처음 낸 나라를 모은다."""
    p = conn.execute("SELECT country, priority_no, priority_country, pct_no FROM patents WHERE id = ?",
                     (patent_id,)).fetchone()
    countries = {p["country"]}
    if p["priority_country"] and p["priority_country"] != "WO":
        countries.add(p["priority_country"])
    for column in ("priority_no", "pct_no"):
        if p[column]:
            countries |= {r[0] for r in conn.execute(f"SELECT DISTINCT country FROM patents WHERE {column} = ?",
                                                     (p[column],))}
    return sorted(countries)


def importance(conn: sqlite3.Connection, patent_id: int, day: date, window_days: int,
               companies: tuple[str, ...] = ()) -> tuple[int, list[str]]:
    """(주목도 점수, 이유 목록). 계산 방법은 이 파일 맨 위 설명 참고."""
    since = (day - timedelta(days=window_days - 1)).isoformat()
    params = (patent_id, since, day.isoformat())
    keywords = [r["keyword"] for r in conn.execute(
        "SELECT DISTINCT keyword FROM patent_matches WHERE patent_id = ? AND day BETWEEN ? AND ? ORDER BY keyword", params)]
    days = conn.execute(
        "SELECT COUNT(DISTINCT day) FROM patent_matches WHERE patent_id = ? AND day BETWEEN ? AND ?", params).fetchone()[0]
    buzz = conn.execute(
        "SELECT COALESCE(MAX(buzz), 0) FROM patent_matches WHERE patent_id = ? AND day BETWEEN ? AND ?", params).fetchone()[0]
    p = conn.execute("SELECT applicant, claim_count, pct_no FROM patents WHERE id = ?", (patent_id,)).fetchone()
    countries = family_countries(conn, patent_id)
    company = watched_company(p["applicant"], companies)

    score = 2 * len(keywords) + max(days - 1, 0) + 2 * (len(countries) - 1)
    reasons = []
    if len(keywords) > 1:
        reasons.append(f"뉴스 키워드 {len(keywords)}개에 걸림({', '.join(keywords)})")
    if days > 1:
        reasons.append(f"최근 {window_days}일 중 {days}일 검색됨")
    if len(countries) > 1:
        reasons.append(f"{'·'.join(COUNTRY_NAMES.get(c, c) for c in countries)}에 함께 출원")
    if p["pct_no"]:
        score += 1
        reasons.append("국제출원(PCT)")
    if company:
        score += 2
        reasons.append(f"관심 회사({company})")
    if buzz >= BUZZ_ARTICLES:
        score += 1
        reasons.append(f"뉴스 화제 기술(기사 {buzz}건 이상)")
    if (p["claim_count"] or 0) >= MANY_CLAIMS:
        score += 1
        reasons.append(f"청구항 {p['claim_count']}개")
    return score, reasons


# ---------------------------------------------------------------- 하루 브리핑 만들기


def _fill_details(conn: sqlite3.Connection, client, patent_ids, warnings: list[str]) -> None:
    for pid in patent_ids:
        p = conn.execute("SELECT country, doc_key, claims, abstract FROM patents WHERE id = ?", (pid,)).fetchone()
        if p["claims"] or (p["country"] == "CN" and p["abstract"]):
            continue
        try:
            d = client.details(p["country"], p["doc_key"])
        except KiprisError as e:
            warnings.append(f"{COUNTRY_NAMES[p['country']]} {p['doc_key']} 상세 조회 실패: {e}")
            continue
        conn.execute(
            """UPDATE patents SET claims = COALESCE(?, claims), claim_count = COALESCE(?, claim_count),
                                  abstract = COALESCE(abstract, ?), priority_no = COALESCE(priority_no, ?),
                                  priority_country = COALESCE(?, priority_country), pct_no = COALESCE(pct_no, ?),
                                  status = COALESCE(?, status), ipc = COALESCE(ipc, ?)
               WHERE id = ?""",
            (d.get("claims"), d.get("claim_count"), d.get("abstract"), d.get("priority_no"),
             d.get("priority_country"), d.get("pct_no"), d.get("status"), d.get("ipc"), pid),
        )


def collect(conn: sqlite3.Connection, client, keywords: list[Keyword], day: date, settings: PatentSettings,
            ipc_by_topic: dict[str, tuple[str, ...]] | None = None, companies: tuple[str, ...] = ()) -> dict:
    """키워드마다 세 나라를 검색해 새 특허와 🔥 주목 특허를 고르고, 그날 특허 브리핑으로 저장한다.

    ipc_by_topic: 주제별 특허 분류(IPC) 접두어. 키워드의 주제에 맞지 않는 분류의 특허는 뺀다.
    companies: 관심 회사. 이 회사 특허는 주목도 점수를 더 받고 '관심 회사'로 표시된다.
    """
    day_s = day.isoformat()
    ipc_by_topic = ipc_by_topic or {}
    row = conn.execute("SELECT content FROM patent_briefings WHERE day = ?", (day_s,)).fetchone()
    old = json.loads(row["content"]) if row else {"keywords": [], "items": []}
    # 키워드 없이 부르면(순위만 다시 매기기) 그날 검색 경고를 그대로 둔다.
    warnings: list[str] = [] if keywords else list(old.get("warnings", []))
    # 같은 날 다시 실행해도 앞서 고른 새 특허는 그대로 둔다.
    new_keyword = {i["patent_id"]: i["keyword"] for i in old["items"] if i.get("new")}
    interest_words = {k["ko"] for k in old["keywords"] if k.get("interest")} | {k.ko for k in keywords if k.interest}
    # 오래된 특허는 빼고 최근 몇 년 안에 공개된 것만 본다.
    cutoff = (day - timedelta(days=365 * settings.recent_years)).isoformat()
    retries_left = settings.max_kr_retries

    for kw in keywords:
        prefixes = ipc_by_topic.get(kw.topic or "", ())
        for country in kw.countries:
            # 한국 특허가 0건이면 다른 표현으로 다시 찾는다 (하루 횟수 제한).
            terms = [kw.ko, *kw.alt] if country == "KR" else [kw.ko]
            found: list[Found] = []
            for n, term in enumerate(terms):
                if n:
                    if retries_left <= 0:
                        break
                    retries_left -= 1
                try:
                    raw = client.search(country, replace(kw, ko=term))
                except KiprisError as e:
                    warnings.append(f"{COUNTRY_NAMES[country]} '{term}' 검색 실패: {e}")
                    break
                found = [f for f in raw
                         if (f.published_at or f.filed_at or "") >= cutoff and matches_ipc(f.ipc, prefixes)]
                if found:
                    if n:
                        warnings.append(f"{COUNTRY_NAMES[country]} '{kw.ko}': 결과가 없어 '{term}'(으)로 다시 찾음")
                    break
            if not found:
                warnings.append(f"{COUNTRY_NAMES[country]} '{kw.ko}': 최근 {settings.recent_years}년 안의 "
                                "주제에 맞는 검색 결과 없음")
                continue
            picked = 0
            for f in found:
                pid = _upsert(conn, f)
                conn.execute(
                    """INSERT INTO patent_matches (patent_id, day, keyword, buzz) VALUES (?, ?, ?, ?)
                       ON CONFLICT (patent_id, day, keyword) DO UPDATE SET buzz = MAX(patent_matches.buzz, excluded.buzz)""",
                    (pid, day_s, kw.ko, kw.buzz))
                shown = conn.execute("SELECT first_shown_day FROM patents WHERE id = ?", (pid,)).fetchone()[0]
                if picked < settings.per_search and pid not in new_keyword and (shown is None or shown == day_s):
                    new_keyword[pid] = kw.ko
                    picked += 1
            if not picked:
                warnings.append(f"{COUNTRY_NAMES[country]} '{kw.ko}': 새 특허 없음 (모두 이미 본 특허)")

    # 새 특허의 청구항부터 가져와야 청구항 수가 점수에 들어간다.
    _fill_details(conn, client, list(new_keyword), warnings)

    since = (day - timedelta(days=settings.hot_window_days - 1)).isoformat()
    recent = [r[0] for r in conn.execute(
        "SELECT DISTINCT patent_id FROM patent_matches WHERE day BETWEEN ? AND ?", (since, day_s))]
    scored = {pid: importance(conn, pid, day, settings.hot_window_days, companies) for pid in recent}
    # AI가 '관련 낮음'으로 판정한 특허는 주목 특허 순위에서 뺀다.
    irrelevant = {r[0] for r in conn.execute("SELECT id FROM patents WHERE relevant = 0")}
    hot = [pid for pid in recent if scored[pid][0] >= settings.hot_threshold and pid not in irrelevant]
    published = {pid: conn.execute("SELECT published_at FROM patents WHERE id = ?", (pid,)).fetchone()[0] or ""
                 for pid in hot}
    hot.sort(key=lambda pid: (scored[pid][0], published[pid]), reverse=True)
    spotlight = hot[: settings.spotlight_size]
    _fill_details(conn, client, spotlight, warnings)

    def latest_keyword(pid: int) -> str:
        return conn.execute(
            "SELECT keyword FROM patent_matches WHERE patent_id = ? ORDER BY day DESC, keyword LIMIT 1", (pid,)
        ).fetchone()[0]

    def item(pid: int, keyword: str, rank: int | None) -> dict:
        score, reasons = scored.get(pid) or importance(conn, pid, day, settings.hot_window_days, companies)
        applicant = conn.execute("SELECT applicant FROM patents WHERE id = ?", (pid,)).fetchone()[0]
        return {"patent_id": pid, "keyword": keyword, "new": pid in new_keyword, "rank": rank, "score": score,
                "reasons": reasons, "interest": keyword in interest_words,
                "company": watched_company(applicant, companies)}

    items = [item(pid, new_keyword.get(pid) or latest_keyword(pid), rank) for rank, pid in enumerate(spotlight, start=1)]
    items += [item(pid, keyword, None) for pid, keyword in new_keyword.items() if pid not in spotlight]

    for it in items:
        conn.execute("UPDATE patents SET first_shown_day = COALESCE(first_shown_day, ?) WHERE id = ?",
                     (day_s, it["patent_id"]))

    new_keywords = ({"ko": k.ko, "en": k.en, "interest": k.interest} for k in keywords)
    all_keywords = old["keywords"] + [k for k in new_keywords
                                      if k["ko"] not in {o["ko"] for o in old["keywords"]}]
    content = {"keywords": all_keywords, "items": items, "warnings": warnings}
    conn.execute(
        "INSERT OR REPLACE INTO patent_briefings (day, content, created_at) VALUES (?, ?, ?)",
        (day_s, json.dumps(content, ensure_ascii=False), datetime.now(KST).isoformat(timespec="seconds")),
    )
    conn.commit()
    return content


# ---------------------------------------------------------------- 키워드, 요약, 내보내기


def keywords_from_briefing(conn: sqlite3.Connection, day: date, kr_per_topic: int,
                           foreign_per_topic: int) -> tuple[str | None, list[Keyword]]:
    """그날(없으면 그 전 가장 최근) 뉴스 브리핑의 키워드를 주제별로 번갈아 고른다.

    주제마다 앞의 kr_per_topic개는 한국 특허를, 그중 앞의 foreign_per_topic개는 미국·중국 특허도 찾는다.
    """
    row = conn.execute("SELECT day, content FROM briefings WHERE day <= ? ORDER BY day DESC LIMIT 1",
                       (day.isoformat(),)).fetchone()
    if row is None:
        return None, []
    topics = json.loads(row["content"])["topics"]
    picked: list[Keyword] = []
    for i in range(max(kr_per_topic, foreign_per_topic)):
        for topic in topics:
            kws = topic["keywords"]
            if i >= len(kws):
                continue
            k = kws[i]
            countries = (("KR",) if i < kr_per_topic else ()) + (("US", "CN") if i < foreign_per_topic else ())
            # 키워드가 나온 이야기의 기사 수 = 뉴스 화제성
            story = k.get("story")
            buzz = len(topic["stories"][story - 1]["article_ids"]) if story else 0
            picked.append(Keyword(k["ko"], k["en"], countries, topic["name"], tuple(k.get("alt", [])), buzz))
    return row["day"], picked


def _clip(text: str | None, n: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= n else text[:n] + " …(생략)"


def _day_content(conn: sqlite3.Connection, day: date) -> tuple[dict | None, str | None]:
    row = conn.execute("SELECT content, created_at FROM patent_briefings WHERE day = ?", (day.isoformat(),)).fetchone()
    return (json.loads(row["content"]), row["created_at"]) if row else (None, None)


def _rows(conn: sqlite3.Connection, content: dict):
    for info in content["items"]:
        yield info, conn.execute("SELECT * FROM patents WHERE id = ?", (info["patent_id"],)).fetchone()


def for_claude(conn: sqlite3.Connection, day: date) -> dict:
    """스킬이 읽고 한국어 요약을 쓸 수 있는 형태. 원문은 길면 자른다."""
    content, _ = _day_content(conn, day)
    if content is None:
        return {"day": day.isoformat(), "keywords": [], "warnings": [], "items": []}
    items = []
    for info, p in _rows(conn, content):
        items.append({
            "id": p["id"], "country": p["country"], "number": p["number"], "title": p["title"],
            "applicant": p["applicant"], "published_at": p["published_at"], "keyword": info["keyword"],
            "rank": info["rank"], "score": info["score"], "reasons": info["reasons"], "new": info["new"],
            "needs_summary": not (p["title_ko"] and p["summary_ko"] and p["key_claim_ko"] and p["detail_ko"]
                                  and p["relevant"] is not None),
            "claim_count": p["claim_count"], "ipc": p["ipc"],
            "abstract": _clip(p["abstract"], 3000), "claims": _clip(p["claims"], 8000),
        })
    return {"day": day.isoformat(), "keywords": content["keywords"], "warnings": content["warnings"], "items": items}


class SummaryError(ValueError):
    """요약 JSON 형식이 잘못됐을 때."""


TEXT_FIELDS = ("title_ko", "summary_ko", "key_claim_ko", "detail_ko")


def _required_text(item: dict, key: str, where: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SummaryError(f"{where}.{key}는 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def save_summaries(conn: sqlite3.Connection, data, day: date) -> int:
    """AI가 쓴 한국어 요약과 관련도 판정을 저장한다.

    relevant·relevance_note는 항상 필요하다. 이미 요약이 있는 특허는 요약을 빼고 판정만 보내도 된다.
    """
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise SummaryError('최상위는 {"items": [...]} 형식이어야 합니다.')
    content, _ = _day_content(conn, day)
    allowed = {i["patent_id"] for i in content["items"]} if content else set()
    updates = []
    for n, item in enumerate(data["items"]):
        where = f"items[{n}]"
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise SummaryError(f"{where}.id는 특허 번호(정수)여야 합니다.")
        if item["id"] not in allowed:
            raise SummaryError(f"{where}.id {item['id']}는 {day} 특허 브리핑에 없는 특허입니다.")
        if not isinstance(item.get("relevant"), bool):
            raise SummaryError(f"{where}.relevant는 true(관련 높음) 또는 false(관련 낮음)여야 합니다.")
        values = {"relevant": int(item["relevant"]), "relevance_note": _required_text(item, "relevance_note", where)}
        stored = conn.execute("SELECT title_ko, summary_ko, key_claim_ko, detail_ko FROM patents WHERE id = ?",
                              (item["id"],)).fetchone()
        if not all(stored[k] for k in TEXT_FIELDS) or any(k in item for k in TEXT_FIELDS):
            for key in ("title_ko", "summary_ko", "key_claim_ko"):
                values[key] = _required_text(item, key, where)
            values["detail_ko"] = json.dumps(_detail(item.get("detail_ko"), f"{where}.detail_ko"), ensure_ascii=False)
        updates.append((values, item["id"]))
    for values, pid in updates:
        columns = ", ".join(f"{k} = ?" for k in values)  # 키는 위에서 정한 고정 이름뿐이다
        conn.execute(f"UPDATE patents SET {columns} WHERE id = ?", (*values.values(), pid))
    conn.commit()
    return len(updates)


def _detail(value, where: str) -> list[dict]:
    """자세한 설명: 꼭지 3~8개, 꼭지마다 제목과 본문. 본문의 "- "로 시작하는 줄은 목록으로 보여준다."""
    if not isinstance(value, list) or not 3 <= len(value) <= 8:
        raise SummaryError(f'{where}는 [{{"heading": ..., "body": ...}}] 꼭지 3~8개짜리 배열이어야 합니다.')
    sections = []
    for n, s in enumerate(value):
        if not isinstance(s, dict):
            raise SummaryError(f"{where}[{n}]는 JSON 객체여야 합니다.")
        heading, body = s.get("heading"), s.get("body")
        if not isinstance(heading, str) or not heading.strip() or not isinstance(body, str) or not body.strip():
            raise SummaryError(f"{where}[{n}]의 heading과 body는 비어 있지 않은 문자열이어야 합니다.")
        sections.append({"heading": heading.strip(), "body": body.strip()})
    return sections


def for_page(conn: sqlite3.Connection, day: date) -> dict | None:
    """claude.ai 브리핑 페이지와 GitHub 보고서에 쓰는 형태. 주목 특허는 순위대로, 새 특허는 나라별 최신순."""
    content, created_at = _day_content(conn, day)
    if content is None:
        return None
    spotlight, fresh, low = [], [], []
    for info, p in _rows(conn, content):
        item = {
            "key": f"{p['country']}-{p['doc_key']}",
            "country": p["country"], "number": p["number"], "title": p["title"], "title_ko": p["title_ko"],
            "applicant": p["applicant"], "published_at": p["published_at"], "url": p["url"], "ipc": p["ipc"],
            "summary_ko": p["summary_ko"], "key_claim_ko": p["key_claim_ko"], "keyword": info["keyword"],
            "detail": json.loads(p["detail_ko"]) if p["detail_ko"] else [],
            "rank": info["rank"], "score": info["score"], "reasons": info["reasons"], "new": info["new"],
            "has_claims": bool(p["claims"]), "claim_count": p["claim_count"],
            "relevant": None if p["relevant"] is None else bool(p["relevant"]),
            "relevance_note": p["relevance_note"], "status": p["status"],
            "family": family_countries(conn, p["id"]),
            "interest": info.get("interest", False), "company": info.get("company"),
        }
        if info["rank"]:
            spotlight.append(item)
        elif p["relevant"] == 0:
            low.append(item)
        else:
            fresh.append(item)
    spotlight.sort(key=lambda i: i["rank"])
    for group in (fresh, low):
        group.sort(key=lambda i: i["published_at"] or "", reverse=True)
        group.sort(key=lambda i: COUNTRIES.index(i["country"]))
    return {"day": day.isoformat(), "created_at": created_at, "keywords": content["keywords"],
            "spotlight": spotlight, "items": fresh, "low": low}


def report_markdown(page: dict) -> str:
    """GitHub에 올릴 하루 특허 브리핑 문서."""
    lines = [f"# {page['day']} 특허 브리핑", ""]
    keywords = ", ".join(f"{k['ko']} ({k['en']})" for k in page["keywords"])
    lines += [f"검색 키워드: {keywords}" if keywords else "검색 키워드: 없음", ""]

    def entry(i: dict, heading: str) -> list[str]:
        title = i["title_ko"] or i["title"]
        link = f"[{i['number']}]({i['url']})" if i["url"] else i["number"]
        out = [f"### {heading}{title}", "",
               f"- {COUNTRY_NAMES[i['country']]} · {link} · {i['applicant'] or '출원인 정보 없음'} · 공개 {i['published_at'] or '-'}"]
        if i["title_ko"] and i["title_ko"] != i["title"]:
            out.append(f"- 원래 제목: {i['title']}")
        tags = [f"관심 회사({i['company']})" if i.get("company") else "",
                "관심 키워드" if i.get("interest") else "",
                f"{'·'.join(COUNTRY_NAMES.get(c, c) for c in i['family'])} 출원" if len(i.get("family", [])) > 1 else ""]
        out.append(f"- 검색 키워드: {i['keyword']}" + "".join(f" · {t}" for t in tags if t))
        if i["rank"]:
            out.append(f"- 주목도 {i['score']}점: {' · '.join(i['reasons']) or '여러 신호'}")
        out.append("")
        if i["summary_ko"]:
            out += [i["summary_ko"], ""]
        if i["key_claim_ko"]:
            label = "핵심 청구항" if i["has_claims"] else "핵심 내용 (청구항 원문 없음, 초록 기준)"
            out += [f"> **{label}**: {i['key_claim_ko']}", ""]
        for section in i["detail"]:
            out += [f"**{section['heading']}**", "", section["body"], ""]
        return out

    if page["spotlight"]:
        lines += ["## 🔥 주목 특허 순위", ""]
        for i in page["spotlight"]:
            lines += entry(i, f"{i['rank']}위 · ")
    for country in COUNTRIES:
        group = [i for i in page["items"] if i["country"] == country]
        if group:
            lines += [f"## {COUNTRY_NAMES[country]} 새 특허", ""]
            for i in group:
                lines += entry(i, "")
    if page.get("low"):
        lines += ["## 관련도가 낮은 특허", "",
                  "검색어에는 걸렸지만 AI가 오늘 주제와 관련이 낮다고 판단한 특허예요.", ""]
        for i in page["low"]:
            link = f"[{i['number']}]({i['url']})" if i["url"] else i["number"]
            note = f" — {i['relevance_note']}" if i["relevance_note"] else ""
            lines.append(f"- {i['title_ko'] or i['title']} ({COUNTRY_NAMES[i['country']]} · {link}){note}")
        lines.append("")
    lines += ["---", "", "자료: KIPRIS Plus. 한국어 제목·요약은 Claude가 청구항과 초록을 바탕으로 작성했어요."]
    return "\n".join(lines).rstrip() + "\n"
