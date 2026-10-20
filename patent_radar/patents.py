"""KIPRIS Plus로 한국·미국·중국 특허를 찾아 저장하고, 그날 브리핑에 넣을 특허를 고른다.

하루 브리핑은 두 부분이다.
- 새 특허: 키워드·나라마다 아직 보여준 적 없는 최신 특허. 한 번 보여준 특허는 다음 날부터 다시 넣지 않는다.
- 🔥 주목 특허: 최근 며칠 동안의 검색 결과로 '주목도 점수'를 매겨 기준을 넘은 특허를 순위대로.
  점수가 기준 이상인 동안은 매일 맨 위에 다시 나온다.

주목도 점수 (특허에 공개된 조회수가 없고, 막 공개된 특허는 인용도 거의 없어서 아래 신호로 대신한다)
- 최근 뉴스 키워드 하나에 걸릴 때마다 +2  (여러 뉴스 주제와 이어진 기술)
- 최근 며칠 동안 검색에 걸린 날 하루마다 +1 (첫날 제외, 계속 뉴스와 이어지는 기술)
- 같은 발명을 다른 나라에도 냈으면 나라마다 +2 (여러 나라에 돈을 들여 낸 중요한 발명)
- 청구항이 20개 이상이면 +1 (권리 범위를 넓게 잡은 특허)

청구항·초록 같은 상세 정보는 브리핑에 넣을 특허만 따로 가져와서 무료 호출 한도를 아낀다.
"""

import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from .config import KST, PatentSettings

KR_BASE = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice"
FOREIGN_SEARCH = "http://plus.kipris.or.kr/openapi/rest/ForeignPatentAdvencedSearchService/freeSearch"
FOREIGN_BIBLIO = "http://plus.kipris.or.kr/openapi/rest/ForeignPatentBibliographicService"

COUNTRIES = ("KR", "US", "CN")
COUNTRY_NAMES = {"KR": "한국", "US": "미국", "CN": "중국"}
# 무료 한도는 국내 서비스와 해외 서비스를 따로 센다.
SERVICE_NAMES = {"kr": "국내 특허", "foreign": "해외 특허"}
MANY_CLAIMS = 20


class KiprisError(RuntimeError):
    """KIPRIS 호출 실패. 메시지에 인증키가 들어가지 않게 만든다."""


@dataclass(frozen=True)
class Keyword:
    ko: str
    en: str

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


# ---------------------------------------------------------------- 응답 해석


def _text(el: ET.Element, tag: str) -> str | None:
    value = (el.findtext(tag) or "").strip()
    return value or None


def _date(yyyymmdd: str | None) -> str | None:
    s = (yyyymmdd or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else None


def _people(value: str | None) -> str | None:
    return ", ".join(dict.fromkeys(p.strip() for p in value.split("|") if p.strip())) if value else None


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
        priority = _text(res, "priorityNo")
        found.append(
            Found(
                country=country,
                doc_key=lit,
                number=number,
                title=_text(res, "inventionName") or "(no title)",
                applicant=_people(_text(res, "applicant")),
                filed_at=_date(_text(res, "applicationDate")),
                published_at=_date(_text(res, "openDate")) or _date(_text(res, "registerDate")),
                priority_no=f"{priority}|{_text(res, 'priorityDate') or ''}" if priority else None,
                url=url,
            )
        )
    return found


def parse_kr_claims(root: ET.Element) -> tuple[str | None, int]:
    claims = [(c.findtext("claim") or "").strip() for c in root.iter("claimInfo")]
    claims = [c for c in claims if c]
    return ("\n".join(claims) or None), len(claims)


def parse_foreign_claims(root: ET.Element) -> tuple[str | None, int]:
    text = "\n".join(t for t in ((c.text or "").strip() for c in root.iter("claimText")) if t)
    # 청구항은 "1. ...", "2. ..."처럼 번호로 시작한다.
    count = len(re.findall(r"(?:^|\s)\d{1,3}\.\s", text))
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
            return {"claims": claims, "claim_count": count}
        params = {"literatureNumber": doc_key, "countryCode": country, "accessKey": self.key}
        if country == "US":
            claims, count = parse_foreign_claims(self._call("foreign", f"{FOREIGN_BIBLIO}/demandParagraphInfo", params))
            return {"claims": claims, "claim_count": count}
        return {"abstract": parse_foreign_abstract(self._call("foreign", f"{FOREIGN_BIBLIO}/summation", params))}


# ---------------------------------------------------------------- 주목도


def _upsert(conn: sqlite3.Connection, f: Found) -> int:
    conn.execute(
        """INSERT INTO patents (country, doc_key, number, title, applicant, filed_at, published_at, priority_no, url, abstract)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (country, doc_key) DO UPDATE SET
             number = excluded.number, title = excluded.title, applicant = excluded.applicant,
             published_at = excluded.published_at, url = excluded.url,
             priority_no = COALESCE(excluded.priority_no, patents.priority_no),
             abstract = COALESCE(patents.abstract, excluded.abstract)""",
        (f.country, f.doc_key, f.number, f.title, f.applicant, f.filed_at, f.published_at, f.priority_no, f.url, f.abstract),
    )
    return conn.execute("SELECT id FROM patents WHERE country = ? AND doc_key = ?", (f.country, f.doc_key)).fetchone()["id"]


def importance(conn: sqlite3.Connection, patent_id: int, day: date, window_days: int) -> tuple[int, list[str]]:
    """(주목도 점수, 이유 목록). 계산 방법은 이 파일 맨 위 설명 참고."""
    since = (day - timedelta(days=window_days - 1)).isoformat()
    params = (patent_id, since, day.isoformat())
    keywords = [r["keyword"] for r in conn.execute(
        "SELECT DISTINCT keyword FROM patent_matches WHERE patent_id = ? AND day BETWEEN ? AND ? ORDER BY keyword", params)]
    days = conn.execute(
        "SELECT COUNT(DISTINCT day) FROM patent_matches WHERE patent_id = ? AND day BETWEEN ? AND ?", params).fetchone()[0]
    p = conn.execute("SELECT priority_no, claim_count FROM patents WHERE id = ?", (patent_id,)).fetchone()
    countries = []
    if p["priority_no"]:
        countries = [r["country"] for r in conn.execute(
            "SELECT DISTINCT country FROM patents WHERE priority_no = ? ORDER BY country", (p["priority_no"],))]

    score = 2 * len(keywords) + max(days - 1, 0) + 2 * max(len(countries) - 1, 0)
    reasons = []
    if len(keywords) > 1:
        reasons.append(f"뉴스 키워드 {len(keywords)}개에 걸림({', '.join(keywords)})")
    if days > 1:
        reasons.append(f"최근 {window_days}일 중 {days}일 검색됨")
    if len(countries) > 1:
        reasons.append(f"{'·'.join(COUNTRY_NAMES.get(c, c) for c in countries)}에 함께 출원")
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
                                  abstract = COALESCE(abstract, ?) WHERE id = ?""",
            (d.get("claims"), d.get("claim_count"), d.get("abstract"), pid),
        )


def collect(conn: sqlite3.Connection, client, keywords: list[Keyword], day: date, settings: PatentSettings) -> dict:
    """키워드마다 세 나라를 검색해 새 특허와 🔥 주목 특허를 고르고, 그날 특허 브리핑으로 저장한다."""
    day_s = day.isoformat()
    warnings: list[str] = []
    row = conn.execute("SELECT content FROM patent_briefings WHERE day = ?", (day_s,)).fetchone()
    old = json.loads(row["content"]) if row else {"keywords": [], "items": []}
    # 같은 날 다시 실행해도 앞서 고른 새 특허는 그대로 둔다.
    new_keyword = {i["patent_id"]: i["keyword"] for i in old["items"] if i.get("new")}
    # 오래된 특허는 빼고 최근 몇 년 안에 공개된 것만 본다.
    cutoff = (day - timedelta(days=365 * settings.recent_years)).isoformat()

    for kw in keywords:
        for country in COUNTRIES:
            try:
                found = client.search(country, kw)
            except KiprisError as e:
                warnings.append(f"{COUNTRY_NAMES[country]} '{kw.ko}' 검색 실패: {e}")
                continue
            found = [f for f in found if (f.published_at or f.filed_at or "") >= cutoff]
            if not found:
                warnings.append(f"{COUNTRY_NAMES[country]} '{kw.ko}': 최근 {settings.recent_years}년 안의 검색 결과 없음")
                continue
            picked = 0
            for f in found:
                pid = _upsert(conn, f)
                conn.execute("INSERT OR IGNORE INTO patent_matches (patent_id, day, keyword) VALUES (?, ?, ?)",
                             (pid, day_s, kw.ko))
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
    scored = {pid: importance(conn, pid, day, settings.hot_window_days) for pid in recent}
    hot = [pid for pid in recent if scored[pid][0] >= settings.hot_threshold]
    published = {pid: conn.execute("SELECT published_at FROM patents WHERE id = ?", (pid,)).fetchone()[0] or ""
                 for pid in hot}
    hot.sort(key=lambda pid: (scored[pid][0], published[pid]), reverse=True)
    spotlight = hot[: settings.spotlight_size]
    _fill_details(conn, client, spotlight, warnings)

    def latest_keyword(pid: int) -> str:
        return conn.execute(
            "SELECT keyword FROM patent_matches WHERE patent_id = ? ORDER BY day DESC, keyword LIMIT 1", (pid,)
        ).fetchone()[0]

    items = []
    for rank, pid in enumerate(spotlight, start=1):
        score, reasons = scored[pid]
        items.append({"patent_id": pid, "keyword": new_keyword.get(pid) or latest_keyword(pid), "new": pid in new_keyword,
                      "rank": rank, "score": score, "reasons": reasons})
    for pid, keyword in new_keyword.items():
        if pid in spotlight:
            continue
        score, reasons = scored.get(pid) or importance(conn, pid, day, settings.hot_window_days)
        items.append({"patent_id": pid, "keyword": keyword, "new": True, "rank": None, "score": score, "reasons": reasons})

    for item in items:
        conn.execute("UPDATE patents SET first_shown_day = COALESCE(first_shown_day, ?) WHERE id = ?",
                     (day_s, item["patent_id"]))

    all_keywords = old["keywords"] + [k for k in ({"ko": k.ko, "en": k.en} for k in keywords) if k not in old["keywords"]]
    content = {"keywords": all_keywords, "items": items, "warnings": warnings}
    conn.execute(
        "INSERT OR REPLACE INTO patent_briefings (day, content, created_at) VALUES (?, ?, ?)",
        (day_s, json.dumps(content, ensure_ascii=False), datetime.now(KST).isoformat(timespec="seconds")),
    )
    conn.commit()
    return content


# ---------------------------------------------------------------- 키워드, 요약, 내보내기


def keywords_from_briefing(conn: sqlite3.Connection, day: date, limit: int) -> tuple[str | None, list[Keyword]]:
    """그날(없으면 그 전 가장 최근) 뉴스 브리핑의 키워드를 주제별로 번갈아 골라 limit개까지."""
    row = conn.execute("SELECT day, content FROM briefings WHERE day <= ? ORDER BY day DESC LIMIT 1",
                       (day.isoformat(),)).fetchone()
    if row is None:
        return None, []
    topics = [t["keywords"] for t in json.loads(row["content"])["topics"]]
    picked: list[Keyword] = []
    for i in range(max((len(t) for t in topics), default=0)):
        for kws in topics:
            if i < len(kws) and len(picked) < limit:
                picked.append(Keyword(kws[i]["ko"], kws[i]["en"]))
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
            "needs_summary": not (p["title_ko"] and p["summary_ko"] and p["key_claim_ko"]),
            "claim_count": p["claim_count"],
            "abstract": _clip(p["abstract"], 1200), "claims": _clip(p["claims"], 2500),
        })
    return {"day": day.isoformat(), "keywords": content["keywords"], "warnings": content["warnings"], "items": items}


class SummaryError(ValueError):
    """요약 JSON 형식이 잘못됐을 때."""


def save_summaries(conn: sqlite3.Connection, data, day: date) -> int:
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
        fields = []
        for key in ("title_ko", "summary_ko", "key_claim_ko"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise SummaryError(f"{where}.{key}는 비어 있지 않은 문자열이어야 합니다.")
            fields.append(value.strip())
        updates.append((*fields, item["id"]))
    conn.executemany("UPDATE patents SET title_ko = ?, summary_ko = ?, key_claim_ko = ? WHERE id = ?", updates)
    conn.commit()
    return len(updates)


def for_page(conn: sqlite3.Connection, day: date) -> dict | None:
    """claude.ai 브리핑 페이지와 GitHub 보고서에 쓰는 형태. 주목 특허는 순위대로, 새 특허는 나라별 최신순."""
    content, created_at = _day_content(conn, day)
    if content is None:
        return None
    spotlight, fresh = [], []
    for info, p in _rows(conn, content):
        item = {
            "country": p["country"], "number": p["number"], "title": p["title"], "title_ko": p["title_ko"],
            "applicant": p["applicant"], "published_at": p["published_at"], "url": p["url"],
            "summary_ko": p["summary_ko"], "key_claim_ko": p["key_claim_ko"], "keyword": info["keyword"],
            "rank": info["rank"], "score": info["score"], "reasons": info["reasons"], "new": info["new"],
            "has_claims": bool(p["claims"]), "claim_count": p["claim_count"],
        }
        (spotlight if info["rank"] else fresh).append(item)
    spotlight.sort(key=lambda i: i["rank"])
    fresh.sort(key=lambda i: i["published_at"] or "", reverse=True)
    fresh.sort(key=lambda i: COUNTRIES.index(i["country"]))
    return {"day": day.isoformat(), "created_at": created_at, "keywords": content["keywords"],
            "spotlight": spotlight, "items": fresh}


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
        out.append(f"- 검색 키워드: {i['keyword']}")
        if i["rank"]:
            out.append(f"- 주목도 {i['score']}점: {' · '.join(i['reasons']) or '여러 신호'}")
        out.append("")
        if i["summary_ko"]:
            out += [i["summary_ko"], ""]
        if i["key_claim_ko"]:
            label = "핵심 청구항" if i["has_claims"] else "핵심 내용 (청구항 원문 없음, 초록 기준)"
            out += [f"> **{label}**: {i['key_claim_ko']}", ""]
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
    lines += ["---", "", "자료: KIPRIS Plus. 한국어 제목·요약은 Claude가 청구항과 초록을 바탕으로 작성했어요."]
    return "\n".join(lines).rstrip() + "\n"
