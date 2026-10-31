"""청구항 숫자 지도: 심층 리포트의 '청구항에 나온 숫자' 표를 그래프용 데이터로 바꾼다.

원문 대조는 리포트를 쓸 때 이미 했다(숫자마다 청구항·초록 문장과 글자 그대로 대조). 여기서는 그 표만 읽는다.
- 리포트 표의 항목 이름은 특허마다 제각각이라, 비교해도 뜻이 있는 7가지(PANELS)에만 넣는다.
  어느 항목을 어디에 넣고 무엇을 뺐는지는 아래 규칙(_classify)에 그대로 적혀 있다.
- 출원인·공개일은 리포트 부록 목록에서 찾는다.

    python -m patent_radar patents claimmap      # docs/data/claims.json, docs/claims-density.svg
"""

import json
import math
import re
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path

from .config import ROOT

REPORTS = [  # (리포트 파일, 숫자 표 제목, 표에 '공정 단계' 칸이 있나)
    ("high-density-lfp-2026-10", r"^## 7\. 청구항에 나온 숫자 모음", False),
    ("lfp-process-2026-10", r"^## 8\. 청구항에 나온 공정 숫자 모음", True),
]
REPORT_TITLES = {
    "high-density-lfp-2026-10": "고밀도 LFP와 표면 코팅",
    "lfp-process-2026-10": "LFP 양산 공정",
}
GITHUB = "https://github.com/ChoJiwon00/patent-radar/blob/main/reports/deep/"

PANELS = [
    {"id": "density", "title": "밀도", "unit": "g/cm³", "scale": "linear", "axis": "group",
     "note": "압축 밀도는 가루를 정해진 힘으로 눌렀을 때, 탭 밀도는 통을 두드려 다졌을 때, 극판 밀도는 전극으로 만든 뒤의 밀도예요. "
             "재는 대상이 달라 무리마다 눈금을 따로 그렸어요. 누르는 힘(3T, 3kN, 40–60 MPa 등)도 특허마다 달라서, 같은 무리 안에서도 조건이 같은 값끼리만 비교하세요."},
    {"id": "d50", "title": "입자 크기", "unit": "μm", "scale": "log",
     "note": "D50은 입자를 크기순으로 줄 세웠을 때 가운데 입자의 크기이고, 1차 입자는 뭉치기 전 낱알 하나의 크기예요. "
             "원료부터 완제품까지 단계마다 크기가 수백 배 달라서 눈금을 10배 간격(로그)으로 그렸어요."},
    {"id": "carbon", "title": "탄소 함량", "unit": "wt%", "scale": "linear",
     "note": "전체 탄소인지, 코팅층 속 탄소인지, 2단 탄소의 1차 소결 뒤 탄소인지가 달라서 무리를 나눴어요. "
             "2단 탄소는 처음 구울 때 탄소를 아주 적게 두고, 두 번째 구울 때 코팅을 채우는 방식이에요."},
    {"id": "coating", "title": "코팅 두께", "unit": "nm", "scale": "linear", "note": "탄소층(껍질) 두께예요."},
    {"id": "sinter", "title": "소성 온도", "unit": "℃", "scale": "linear",
     "note": "가마에서 굽는 온도예요. 청구항에서 둘 이상으로 나눠 굽는 특허는 앞 단계(1단·1차·예비)와 뒤 단계(2단·2차·본 소결)로 나눴고, "
             "한 번 굽는 특허(온도를 올리는 구간과 유지하는 구간만 있는 경우 포함)는 '단계 구분 없음'에 넣었어요. 뒤 단계에는 탄소 코팅을 입히려고 다시 굽는 단계도 있어요. "
             "CN 122393268 A처럼 탄소를 두 번에 나눠 입히는 특허는 앞 단계가 700℃를 넘기도 해요. 전구체(원료) 단계의 열처리는 뺐어요."},
    {"id": "spray", "title": "분무건조 온도", "unit": "℃", "scale": "linear",
     "note": "분무건조는 슬러리를 뜨거운 기체 속에 안개처럼 뿌려 말리는 공정이에요. 기체가 들어가는 입구 온도와 나오는 출구 온도를 나눴어요."},
    {"id": "fep", "title": "철-인 비 (Fe/P)", "unit": "몰비", "scale": "linear",
     "note": "원료 속 철과 인의 몰 비율이에요. 인산철(FePO4) 쪽과 수산화인산철·인산제1철 쪽은 원래 값이 달라 무리를 나눴어요."},
]
PANEL_IDS = [p["id"] for p in PANELS]
SINTER_EARLY = "앞 단계 (1단·1차·예비)"
SINTER_LATE = "뒤 단계 (2단·2차·본 소결)"
GROUP_ORDER = {  # 그래프에서 무리를 보여 줄 순서 (공정 순서). 여기 없는 무리는 맨 뒤.
    "density": ["분말 압축 밀도 (누르는 힘 적힘)", "압축·다짐 밀도 (조건 안 적힘·초록 값)", "극판·막층 밀도", "탭 밀도"],
    "d50": ["원료·전구체", "원료 1차 입자·결정립", "분쇄 뒤 슬러리", "분무건조 알갱이", "완제품 1차 입자", "완제품", "완제품 속 큰·작은 입자"],
    "carbon": ["전체 탄소", "코팅층 속 탄소", "1차 소결 뒤 (2단 탄소)", "자유 탄소", "넣는 탄소 (기체 탄소원)"],
    "sinter": [SINTER_EARLY, SINTER_LATE, "단계 구분 없음"],
    "spray": ["입구 온도", "출구 온도", "입구·출구 구분 없음"],
    "fep": ["인산철·슬러리·용액", "수산화인산철·인산제1철"],
}

COUNTRY = {"KR": "한국", "US": "미국", "CN": "중국"}


@dataclass
class Row:
    report: str
    stage: str | None
    item: str
    value_raw: str
    patent: str
    link: str
    source: str
    country: str = ""
    applicant: str | None = None
    published: str | None = None


@dataclass
class Mark:
    panel: str
    group: str
    low: float | None
    high: float | None
    open: str | None  # "ge"(이상·초과) / "le"(이하·미만) / None
    unit: str
    text: str          # 그래프에 쓴 부분 (값 칸의 일부일 수 있음)
    row: Row = field(repr=False)


# ---------------------------------------------------------------- 리포트 표 읽기

_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _table(path: Path, heading: str) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(heading, line))
    rows = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("|") and not line.startswith("|---"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows[1:]  # 머리줄 빼기


def country_of(patent: str) -> str:
    if patent.startswith(("공개", "등록", "출원")):
        return "KR"
    return patent.split()[0] if patent.split() and patent.split()[0] in ("US", "CN") else ""


def read_rows(reports_dir: Path) -> list[Row]:
    rows = []
    for name, heading, has_stage in REPORTS:
        for cells in _table(reports_dir / f"{name}.md", heading):
            stage = cells[0] if has_stage else None
            item, value, patent_cell, source = cells[1:5] if has_stage else cells[0:4]
            m = _LINK.search(patent_cell)
            patent, link = (m.group(1), m.group(2)) if m else (patent_cell, "")
            rows.append(Row(name, stage, item, value, patent, link, source, country_of(patent)))
    return rows


def read_listing(reports_dir: Path) -> dict[str, tuple[str, str]]:
    """부록 목록에서 특허 번호 → (공개일, 출원인)."""
    found = {}
    for name, _, _ in REPORTS:
        path = reports_dir / f"{name}-list.md"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\| \[([^\]]+)\]\([^)]*\) \| ([0-9-]+) \| ([^|]+) \|", line)
            if m:
                found.setdefault(m.group(1).strip(), (m.group(2), m.group(3).strip()))
    return found


# ---------------------------------------------------------------- 값 읽기

_UNITS = [(r"g/cm³|g/cm3|g/cc|g/mL|g/ml", "g/cm³"), (r"μm|㎛|\bum\b", "μm"), (r"nm", "nm"), (r"Å", "Å"),
          (r"ppm", "ppm"), (r"wt%|중량%|wt\.%", "wt%"), (r"℃|°C", "℃"), (r"%", "%")]
_NUM = r"(\d+(?:\.\d+)?)"


def _nm_to_um(text: str) -> str:
    """'100nm–2μm'처럼 한 범위에 nm와 μm가 섞이면 nm 값을 μm로 바꿔 단위를 맞춘다."""
    if "nm" in text and re.search(r"μm|㎛", text):
        return re.sub(_NUM + r"\s*nm", lambda m: f"{float(m.group(1)) / 1000:g}μm", text)
    return text


def unit_of(text: str) -> str:
    for pattern, unit in _UNITS:
        if re.search(pattern, text):
            return unit
    return ""


def parse_value(text: str) -> list[tuple[float | None, float | None, str | None]]:
    """'2.48–2.76' → [(2.48, 2.76, None)], '2.45 이상' → [(2.45, None, 'ge')], '3' → [(3, 3, None)].
    쉼표로 범위가 여러 개 적혀 있으면 모두 돌려준다."""
    t = text.replace("−", "-").replace("~", "–").replace("\\", "")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 2,000 → 2000
    out = []
    for m in re.finditer(_NUM + r"\s*(?:[A-Za-zμ㎛Å℃°%/³]*\s*)?[–-]\s*" + _NUM, t):
        lo, hi = float(m.group(1)), float(m.group(2))
        out.append((min(lo, hi), max(lo, hi), None))
    if out:
        return out
    m = re.search(r"(≥|>|이상|초과)", t)
    n = re.search(r"(≤|<|이하|미만)", t)
    nums = [float(x) for x in re.findall(_NUM, t)]
    if not nums:
        return []
    if m and not n:
        return [(nums[0], None, "ge")]
    if n and not m:
        return [(None, nums[0], "le")]
    if len(nums) == 1:
        return [(nums[0], nums[0], None)]
    return []


def _part(text: str, want: str | int) -> str | None:
    parts = [p.strip() for p in text.split(" / ")]
    if isinstance(want, int):
        return parts[want] if want < len(parts) else None
    # '℃'를 찾을 때 승온 속도(℃/min, ℃/분)는 온도가 아니라서 건너뛴다.
    hits = [p for p in parts if want in p and not re.search(r"℃\s*/\s*(min|분)", p)]
    return hits[0] if hits else None


# ---------------------------------------------------------------- 분류 규칙 (어느 항목을 어느 그래프에)

def _classify(row: Row) -> tuple[list[tuple[str, str, str | int | None]], str | None]:
    """[(그래프, 무리, 값 칸에서 쓸 부분)] 과 뺀 이유. 부분: None = 값 칸 전체, int = ' / '로 나눈 순서, '℃' = ℃가 든 부분."""
    item, stage = row.item, row.stage or ""

    # 밀도
    if "밀도" in item:
        if "계산식" in item:
            return [], "계산식 적합치라 실제 잰 값과 비교할 수 없어요"
        if "탭" in item:
            return [("density", "탭 밀도", None)], None
        # '압연 밀도'는 낱말만 보면 극판 같지만, 표의 LG화학 특허들은 양극 활물질(가루)의 값이라 극판으로 보지 않는다.
        if re.search(r"막층|극판|활물질층|재료층", item):
            return [("density", "극판·막층 밀도", None)], None
        if re.search(r"3T|3톤|3kN|kgf|MPa|N/m", item):
            return [("density", "분말 압축 밀도 (누르는 힘 적힘)", None)], None
        return [("density", "압축·다짐 밀도 (조건 안 적힘·초록 값)", None)], None

    # 철-인 비
    if re.search(r"철-인 비|Fe/P|Fe:P|철/인", item) and "Li:" not in item:
        group = "수산화인산철·인산제1철" if re.search(r"수산화|제1철", item) else "인산철·슬러리·용액"
        return [("fep", group, None)], None

    # 분무건조 온도
    if re.search(r"분무 ?건조", item) and "온도" in item or item.startswith("구체 운전점") or (stage == "분무건조" and "온도" in item):
        if "입구 / 출구" in item or item.startswith("구체 운전점"):
            first, second = (1, 2) if item.startswith("구체 운전점") else (0, 1)
            return [("spray", "입구 온도", first), ("spray", "출구 온도", second)], None
        if "입구" in item:
            return [("spray", "입구 온도", None)], None
        if "출구" in item:
            return [("spray", "출구 온도", None)], None
        return [("spray", "입구·출구 구분 없음", None)], None

    # 코팅 두께
    if re.search(r"코팅 두께|껍질 두께", item):
        return [("coating", "탄소층 두께", None)], None

    # 탄소 함량
    if re.search(r"탄소 함량|중간체 탄소|탄소량", item):
        if "기체 탄소원" in item:
            return [("carbon", "넣는 탄소 (기체 탄소원)", None)], None
        if re.search(r"1차 소결|중간체", item):
            return [("carbon", "1차 소결 뒤 (2단 탄소)", None)], None
        if "코팅층" in item:
            return [("carbon", "코팅층 속 탄소", None)], None
        if "자유" in item:
            return [("carbon", "자유 탄소", None)], None
        return [("carbon", "전체 탄소", None)], None

    # 소성 온도
    if "온도" in item and re.search(r"소성|소결|항온|유지|고온|정온", item) and not re.search(r"냉각|폭|낮추는|가스|유량", item):
        if stage == "원료·전구체" or "전구체 소결" in item or "겔 경로" in item:
            return [], "전구체·재활용·다른 합성 경로의 열처리라 리튬인산철 소성과 따로 봐야 해요"
        # '본 소결 1단계'는 본 소결의 앞부분이라 뒤 단계로 본다(그래서 '본 소결'을 먼저 확인).
        # '고온 구간·정온 구간'은 한 번 굽는 동안의 구간일 때가 많아 단계 이름으로 보지 않는다.
        if re.search(r"2단|본 소결|2차|T2", item) and "불규칙" not in item:
            return [("sinter", SINTER_LATE, "℃")], None
        if re.search(r"1단|예비|1차|결정수|T1(?!\d)", item) and "구형" not in item:
            return [("sinter", SINTER_EARLY, "℃")], None
        return [("sinter", "단계 구분 없음", "℃")], None

    # 입자 크기
    if re.search(r"D50|Dv50|DV50|입도|입경|입자 크기|변 길이|결정립", item):
        if re.search(r"Dmax|최대|보류|비즈|비드|메시", item):
            return [], "입자 크기가 아니거나(장비·거름종이) 최대값이라 D50과 비교할 수 없어요"
        if re.search(r"1차 입자|결정립|변 길이", item):
            raw = stage == "원료·전구체" or (re.search(r"인산철|산화철|전구체", item) and "리튬인산철" not in item)
            return [("d50", "원료 1차 입자·결정립" if raw else "완제품 1차 입자", None)], None
        if stage == "원료·전구체" or "원료" in item:
            return [("d50", "원료·전구체", None)], None
        if stage == "분쇄·혼합" or re.search(r"분쇄 뒤|슬러리", item):
            return [("d50", "분쇄 뒤 슬러리", None)], None
        if stage == "분무건조":
            return [("d50", "분무건조 알갱이", None)], None
        if re.search(r"큰 입자|작은 입자", item):
            return [("d50", "완제품 속 큰·작은 입자", None)], None
        return [("d50", "완제품", None)], None

    return [], None  # 그래프 주제가 아님 (표에만)


def _convert(value: float | None, unit: str, panel: str) -> float | None:
    if value is None:
        return None
    if panel == "d50" and unit == "nm":
        return value / 1000
    if panel == "coating" and unit == "Å":
        return value / 10
    if panel == "carbon" and unit == "ppm":
        return value / 10000  # 10,000 ppm = 1 wt%
    return value


def build(reports_dir: Path) -> tuple[list[Mark], list[dict]]:
    rows = read_rows(reports_dir)
    listing = read_listing(reports_dir)
    marks, left_out = [], []
    for row in rows:
        row.published, row.applicant = listing.get(row.patent, (None, None))
        targets, reason = _classify(row)
        if not targets:
            if reason:
                left_out.append({"item": row.item, "value": row.value_raw, "patent": row.patent, "reason": reason})
            continue
        for panel, group, want in targets:
            text = row.value_raw if want is None else _part(row.value_raw, want)
            if text and panel == "d50":
                text = _nm_to_um(text)
            values = parse_value(text) if text else []
            unit = unit_of(text or "") or unit_of(row.value_raw)
            if not values:
                left_out.append({"item": row.item, "value": row.value_raw, "patent": row.patent,
                                 "reason": "값을 숫자 범위로 읽지 못했어요"})
                continue
            expected = {"density": "g/cm³", "carbon": ("wt%", "%", "ppm"), "spray": "℃", "sinter": "℃"}.get(panel)
            if expected and unit and unit not in (expected if isinstance(expected, tuple) else (expected,)):
                left_out.append({"item": row.item, "value": row.value_raw, "patent": row.patent,
                                 "reason": f"단위({unit})가 이 그래프({expected})와 달라요"})
                continue
            for lo, hi, opn in values:
                marks.append(Mark(panel, group, _convert(lo, unit, panel), _convert(hi, unit, panel), opn,
                                  unit, text, row))
    return _dedupe(marks), left_out


def _dedupe(marks: list[Mark]) -> list[Mark]:
    """두 리포트가 같은 특허의 같은 숫자를 함께 실은 경우 한 번만 그린다(항목 이름은 둘 다 남김)."""
    kept: dict[tuple, Mark] = {}
    for m in marks:
        key = (m.panel, m.group, m.row.patent, m.low, m.high, m.open)
        if key in kept:
            first = kept[key]
            if m.row.item not in first.row.item.split(" · "):
                first.row = Row(**{**asdict(first.row), "item": f"{first.row.item} · {m.row.item}"})
        else:
            kept[key] = m
    return list(kept.values())


def to_json(marks: list[Mark], left_out: list[dict], made: str) -> dict:
    panels = []
    for spec in PANELS:
        mine = [m for m in marks if m.panel == spec["id"]]
        groups = []
        order = GROUP_ORDER.get(spec["id"], [])
        for g in sorted(dict.fromkeys(m.group for m in mine), key=lambda g: order.index(g) if g in order else len(order)):
            rows = [{"patent": m.row.patent, "link": m.row.link, "country": m.row.country,
                     "applicant": m.row.applicant, "published": m.row.published, "item": m.row.item,
                     "stage": m.row.stage, "value": m.text, "low": m.low, "high": m.high, "open": m.open,
                     "source": m.row.source, "report": m.row.report} for m in mine if m.group == g]
            rows.sort(key=lambda r: (r["low"] if r["low"] is not None else r["high"] or 0, r["high"] or 0))
            groups.append({"label": g, "rows": rows})
        panels.append({**spec, "count": len(mine), "patents": len({m.row.patent for m in mine}), "groups": groups})
    return {"made": made, "reports": [{"id": r, "title": REPORT_TITLES[r], "url": GITHUB + r + ".md"} for r, _, _ in REPORTS],
            "panels": panels, "left_out": left_out}


# ---------------------------------------------------------------- README용 그림 (SVG, 의존성 없음)

SERIES = {"KR": "#2a78d6", "CN": "#eb6834", "US": "#1baf7a"}  # dataviz 기본 범주색 1–3번 (세 색은 모든 쌍 검증 통과)


def density_svg(data: dict, width: int = 760) -> str:
    """분말 압축 밀도 무리(누르는 힘 적힘 + 조건 안 적힘)를 가로 범위 막대로."""
    panel = next(p for p in data["panels"] if p["id"] == "density")
    groups = [g for g in panel["groups"] if g["label"].startswith(("분말", "압축"))]
    rows = [(g["label"], r) for g in groups for r in g["rows"]]
    # 눈금은 0.2 간격, 모든 값이 들어가게 (눈금 밖 값을 끝에 눌러 그리면 범위가 틀려 보인다)
    values = [v for _, r in rows for v in (r["low"], r["high"]) if v is not None]
    lo_axis, hi_axis = math.floor(min(values) / 0.2) * 0.2, math.ceil(max(values) / 0.2) * 0.2
    if any(r["open"] == "ge" and r["low"] > hi_axis - 0.12 for _, r in rows):
        hi_axis += 0.2  # '이상' 화살표가 보일 자리
    left, right, top, row_h = 330, 28, 56, 20
    plot_w = width - left - right
    height = top + row_h * len(rows) + len(groups) * 22 + 60

    def x(v):
        return left + (min(max(v, lo_axis), hi_axis) - lo_axis) / (hi_axis - lo_axis) * plot_w

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           'font-family="Pretendard, \'Noto Sans KR\', \'Malgun Gothic\', sans-serif">',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
           '<text x="16" y="24" font-size="15" font-weight="700" fill="#0b0b0b">특허 청구항·초록에 적힌 LFP 분말 압축 밀도 (g/cm³)</text>',
           '<text x="16" y="42" font-size="11.5" fill="#52514e">꽉 찬 막대는 청구항의 권리 범위, 속이 빈 막대는 초록에 적힌 값이에요. 누르는 힘이 특허마다 달라 바로 비교하면 안 돼요.</text>']
    for v in [round(lo_axis + 0.2 * i, 1) for i in range(round((hi_axis - lo_axis) / 0.2) + 1)]:
        out.append(f'<line x1="{x(v):.1f}" y1="{top - 6}" x2="{x(v):.1f}" y2="{height - 44}" stroke="#e3e2de" stroke-width="1"/>')
        out.append(f'<text x="{x(v):.1f}" y="{height - 28}" font-size="11" fill="#52514e" text-anchor="middle">{v:.1f}</text>')
    y = top
    for label in dict.fromkeys(g for g, _ in rows):
        out.append(f'<text x="16" y="{y + 10}" font-size="11.5" font-weight="700" fill="#0b0b0b">{escape(label)}</text>')
        y += 22
        for _, r in [(g, r) for g, r in rows if g == label]:
            color = SERIES.get(r["country"], "#52514e")
            applicant = r["applicant"] or ""
            name = escape(f'{r["patent"]} · {applicant[:11] + "…" if len(applicant) > 12 else applicant}')
            out.append(f'<text x="{left - 8}" y="{y + 4}" font-size="10.5" fill="#52514e" text-anchor="end">{name}</text>')
            lo = r["low"] if r["low"] is not None else lo_axis
            hi = r["high"] if r["high"] is not None else hi_axis
            solid = r["source"].startswith("청구항")
            fill, stroke = (color, color) if solid else ("#ffffff", color)
            if lo == hi:
                out.append(f'<circle cx="{x(lo):.1f}" cy="{y}" r="4.5" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
            else:
                out.append(f'<rect x="{x(lo):.1f}" y="{y - 5}" width="{max(x(hi) - x(lo), 3):.1f}" height="10" rx="4" '
                           f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
            if r["open"] == "ge":
                out.append(f'<text x="{x(hi_axis) + 2:.1f}" y="{y + 4}" font-size="11" fill="{color}">→</text>')
            y += row_h
    lx = 16
    for code in ("KR", "US", "CN"):
        out.append(f'<rect x="{lx}" y="{height - 16}" width="12" height="8" rx="3" fill="{SERIES[code]}"/>'
                   f'<text x="{lx + 16}" y="{height - 8}" font-size="11" fill="#52514e">{COUNTRY[code]} 특허</text>')
        lx += 86
    out.append(f'<rect x="{lx + 8}" y="{height - 16}" width="12" height="8" rx="3" fill="#ffffff" stroke="#52514e" stroke-width="1.5"/>'
               f'<text x="{lx + 24}" y="{height - 8}" font-size="11" fill="#52514e">속이 빈 막대 = 초록의 값 · → = 하한만 적힘</text>')
    out.append("</svg>")
    return "\n".join(out)


def export(docs_dir: Path, reports_dir: Path, made: str) -> dict:
    marks, left_out = build(reports_dir)
    data = to_json(marks, left_out, made)
    (docs_dir / "data").mkdir(parents=True, exist_ok=True)
    (docs_dir / "data" / "claims.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (docs_dir / "claims-density.svg").write_text(density_svg(data), encoding="utf-8")
    return data


def default_paths() -> tuple[Path, Path]:
    return ROOT / "docs", ROOT / "reports" / "deep"
