"""주제 하나를 깊게 파는 특허 모으기 (심층 리포트용).

매일 브리핑(patents.collect)과 달리 특허 목록(patents 표)에 저장하지 않는다. 그래서 여기서 찾은 특허가
다음 날 브리핑의 '새 특허'·'본 특허' 판단에 영향을 주지 않는다. KIPRIS 호출 수는 같은 한도(api_usage)로 센다.

설정 파일(TOML) 예:
    name = "고밀도 LFP·표면 코팅"
    recent_years = 3
    ipc = ["H01M", "C01B25", "C01G"]
    [[queries]]
    group = "고밀도"
    ko = "리튬인산철 압축밀도"                         # 한국: 초록 검색어 (띄어 쓴 단어는 모두 들어간 것)
    en = '"lithium iron phosphate" "compaction density"'  # 미국·중국: KIPRIS 해외 검색식 그대로
    countries = ["KR", "US", "CN"]
"""

import json
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import KST
from .patents import FOREIGN_SEARCH, KR_BASE, Found, KiprisClient, KiprisError, matches_ipc, parse_foreign_search, \
    parse_kr_search


@dataclass(frozen=True)
class DeepQuery:
    group: str
    ko: str | None = None
    en: str | None = None
    countries: tuple[str, ...] = ("KR", "US", "CN")


@dataclass
class DeepTopic:
    name: str
    queries: list[DeepQuery]
    recent_years: int = 3
    ipc: tuple[str, ...] = ()


@dataclass
class Hit:
    found: Found
    groups: set[str] = field(default_factory=set)
    queries: set[str] = field(default_factory=set)
    details: dict = field(default_factory=dict)


def load_topic(path: Path | str) -> DeepTopic:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    queries = []
    for q in raw.get("queries", []):
        countries = tuple(q.get("countries", ("KR", "US", "CN")))
        if "KR" in countries and not q.get("ko"):
            raise ValueError(f"한국 검색에는 ko가 필요해요: {q}")
        if any(c != "KR" for c in countries) and not q.get("en"):
            raise ValueError(f"해외 검색에는 en이 필요해요: {q}")
        queries.append(DeepQuery(q["group"], q.get("ko"), q.get("en"), countries))
    return DeepTopic(raw["name"], queries, int(raw.get("recent_years", 3)), tuple(raw.get("ipc", ())))


def _search(client: KiprisClient, country: str, q: DeepQuery, rows: int) -> list[Found]:
    if country == "KR":
        root = client._call("kr", f"{KR_BASE}/getAdvancedSearch",
                            {"astrtCont": q.ko, "sortSpec": "OPD", "descSort": "true", "patent": "true", "utility": "true",
                             "numOfRows": str(rows), "pageNo": "1", "ServiceKey": client.key})
        return parse_kr_search(root)
    root = client._call("foreign", FOREIGN_SEARCH,
                        {"free": q.en, "collectionValues": country, "sortField": "OPD", "sortState": "true",
                         "currentPage": "1", "accessKey": client.key})
    return parse_foreign_search(root, country)[:rows]


def collect(client: KiprisClient, topic: DeepTopic, rows: int = 30, today: date | None = None,
            log=print) -> tuple[dict[tuple[str, str], Hit], list[dict]]:
    """검색만 한다(상세 조회 없음). (나라, 문서 키) → Hit, 검색어별 기록."""
    today = today or datetime.now(KST).date()
    since = (today - timedelta(days=365 * topic.recent_years)).isoformat()
    hits: dict[tuple[str, str], Hit] = {}
    runs = []
    for i, q in enumerate(topic.queries):
        for country in q.countries:
            label = q.ko if country == "KR" else q.en
            try:
                found = _search(client, country, q, rows)
            except KiprisError as e:
                runs.append({"query": i, "group": q.group, "country": country, "text": label, "error": str(e)})
                log(f"[{country}] {label}: 오류 {e}")
                continue
            kept = [f for f in found if (f.published_at or "") >= since and matches_ipc(f.ipc, topic.ipc)]
            for f in kept:
                hit = hits.setdefault((f.country, f.doc_key), Hit(f))
                hit.groups.add(q.group)
                hit.queries.add(f"{country}:{label}")
            runs.append({"query": i, "group": q.group, "country": country, "text": label,
                         "found": len(found), "kept": len(kept)})
            log(f"[{country}] {label}: {len(found)}건 중 {len(kept)}건")
    return hits, runs


def pick_for_details(hits: dict[tuple[str, str], Hit], per_country: dict[str, int]) -> list[Hit]:
    """나라마다 여러 검색어에 걸린 특허, 그다음 최신 특허 순으로 상세 조회할 특허를 고른다."""
    chosen = []
    for country, n in per_country.items():
        pool = [h for (c, _), h in hits.items() if c == country]
        pool.sort(key=lambda h: (len(h.queries), len(h.groups), h.found.published_at or ""), reverse=True)
        # 음수를 받으면 pool[:-1]이 '거의 전부'가 되어 호출 한도를 많이 쓰므로 0건으로 봐요.
        chosen += pool[:max(n, 0)]
    return chosen


def fill_details(client: KiprisClient, chosen: list[Hit], log=print) -> list[str]:
    warnings = []
    for h in chosen:
        try:
            h.details = client.details(h.found.country, h.found.doc_key)
        except KiprisError as e:
            warnings.append(f"{h.found.number}: {e}")
            log(f"상세 조회 실패 {h.found.number}: {e}")
    return warnings


def to_json(topic: DeepTopic, hits: dict[tuple[str, str], Hit], runs: list[dict], warnings: list[str],
            calls: dict) -> dict:
    items = []
    for h in sorted(hits.values(), key=lambda h: (h.found.country, h.found.published_at or ""), reverse=True):
        d = asdict(h.found)
        # 상세 조회 값은 검색 결과에 비어 있던 칸만 채워요 (매일 브리핑의 COALESCE와 같은 규칙).
        # 상세에 분류(IPC)가 없어 None이 와도 검색에서 받은 분류를 지우지 않아요.
        for k, v in h.details.items():
            if d.get(k) is None:
                d[k] = v
        d.update(groups=sorted(h.groups), queries=sorted(h.queries))
        items.append(d)
    return {"topic": topic.name, "made_at": datetime.now(KST).isoformat(timespec="minutes"),
            "recent_years": topic.recent_years, "ipc": list(topic.ipc), "calls": calls,
            "runs": runs, "warnings": warnings, "items": items}


def save(data: dict, out: Path | str) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
