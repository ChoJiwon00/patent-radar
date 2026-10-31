"""청구항 숫자 지도 테스트. 값 읽기·분류 규칙은 작은 예로, 전체 흐름은 저장소의 심층 리포트로 확인한다."""

import json
from pathlib import Path

import pytest

from patent_radar import claimmap as cm

REPORTS = Path(__file__).resolve().parents[1] / "reports" / "deep"


def row(item, value, patent="공개 10-2026-0000001", stage=None, source="청구항"):
    return cm.Row("r", stage, item, value, patent, "", source, cm.country_of(patent))


@pytest.mark.parametrize("text, expected", [
    ("2.48–2.76 g/cm³", [(2.48, 2.76, None)]),
    ("2.45 g/cm³ 이상", [(2.45, None, "ge")]),
    ("800℃ 미만", [(None, 800.0, "le")]),
    ("3", [(3.0, 3.0, None)]),
    ("1,000–2,000", [(1000.0, 2000.0, None)]),
    ("0.4–0.8 μm, 1.0–1.5 μm", [(0.4, 0.8, None), (1.0, 1.5, None)]),
    ("없음", []),
])
def test_parse_value(text, expected):
    assert cm.parse_value(text) == expected


def test_part_skips_heating_rate_when_looking_for_temperature():
    assert cm._part("1–10℃/min / 600–700℃ / 8 h", "℃") == "600–700℃"
    assert cm._part("2–5 ℃/분 / 750℃", "℃") == "750℃"
    assert cm._part("150–400 / 80–150", 1) == "80–150"


def test_mixed_nm_and_um_range_becomes_um():
    assert cm.parse_value(cm._nm_to_um("100nm–2μm")) == [(0.1, 2.0, None)]
    assert cm._nm_to_um("50–200 nm") == "50–200 nm"  # nm만 있으면 그대로 두고 _convert가 바꾼다


@pytest.mark.parametrize("item, stage, panel, group", [
    ("분말 압축 밀도(3T)", None, "density", "분말 압축 밀도 (누르는 힘 적힘)"),
    ("극판 압축 밀도", None, "density", "극판·막층 밀도"),
    ("본 소결 1단계 온도 / 보온 시간", "소성", "sinter", cm.SINTER_LATE),
    ("1차 소결 온도", "소성", "sinter", cm.SINTER_EARLY),
    ("2차 소성 승온 속도 / 도달 온도", "소성", "sinter", cm.SINTER_LATE),
    ("소결 온도", "소성", "sinter", "단계 구분 없음"),
    ("분쇄 뒤 D50", "분쇄·혼합", "d50", "분쇄 뒤 슬러리"),
    ("작은 입자 D50", None, "d50", "완제품 속 큰·작은 입자"),
    ("자유 탄소 함량", None, "carbon", "자유 탄소"),
    ("수산화인산철 Fe/P 몰비", "원료·전구체", "fep", "수산화인산철·인산제1철"),
])
def test_classify_puts_items_in_the_right_group(item, stage, panel, group):
    targets, reason = cm._classify(row(item, "1", stage=stage))
    assert reason is None and (targets[0][0], targets[0][1]) == (panel, group)


@pytest.mark.parametrize("item, stage", [
    ("혼합물 최대 압축 밀도(계산식 적합치)", None),
    ("전구체 소결(공기) 온도 / 시간", "원료·전구체"),
    ("결정화 소성 온도", "원료·전구체"),
    ("체분리 뒤 최대 입자 크기(Dmax)", "분급"),
])
def test_classify_leaves_out_values_that_cannot_be_compared_with_a_reason(item, stage):
    targets, reason = cm._classify(row(item, "1", stage=stage))
    assert targets == [] and reason


def test_spray_inlet_outlet_pair_splits_into_two_groups():
    targets, _ = cm._classify(row("분무건조 입구 / 출구 온도", "150–400 / 80–150"))
    assert [(t[1], t[2]) for t in targets] == [("입구 온도", 0), ("출구 온도", 1)]


def test_dedupe_keeps_one_mark_and_both_item_names():
    a = cm.Mark("sinter", "g", 750, 800, None, "℃", "750–800℃", row("마지막 고온 소결 온도", "750–800℃"))
    b = cm.Mark("sinter", "g", 750, 800, None, "℃", "750–800℃", row("고온 구간 소결 온도", "750–800℃"))
    other = cm.Mark("sinter", "g", 760, 800, None, "℃", "760–800℃", row("소결 온도", "760–800℃"))
    kept = cm._dedupe([a, b, other])
    assert len(kept) == 2 and kept[0].row.item == "마지막 고온 소결 온도 · 고온 구간 소결 온도"


def test_reports_build_into_seven_panels_without_heating_rates_or_bad_units():
    marks, left_out = cm.build(REPORTS)
    data = cm.to_json(marks, left_out, "2026-10-31")
    assert [p["id"] for p in data["panels"]] == cm.PANEL_IDS and all(p["count"] for p in data["panels"])
    assert all(m.row.applicant and m.row.country in cm.COUNTRY for m in marks)  # 부록 목록에서 출원인을 다 찾음
    assert all(r["reason"] for r in left_out)
    for m in marks:
        if m.panel == "sinter":
            assert "/min" not in m.text and 150 <= (m.low or m.high) <= 950
        if m.panel == "density":
            assert 0.3 <= (m.low or m.high) <= 4.0  # 탭 밀도는 1 아래도 있음
        if m.panel == "carbon":
            assert (m.high or m.low) <= 10
    keys = [(m.panel, m.group, m.row.patent, m.low, m.high, m.open) for m in marks]
    assert len(keys) == len(set(keys))


def test_export_writes_json_and_svg(tmp_path):
    data = cm.export(tmp_path, REPORTS, "2026-10-31")
    saved = json.loads((tmp_path / "data" / "claims.json").read_text(encoding="utf-8"))
    assert saved["made"] == "2026-10-31" and len(saved["panels"]) == len(data["panels"])
    svg = (tmp_path / "claims-density.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>") and "한국" in svg
