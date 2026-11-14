"""Only locally generated fictional documents; no organizer materials."""

import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from tz_auditor.api import app
from tz_auditor.checks import check_numeric
from tz_auditor.parsing.docx_parser import parse_docx

CASES = [
    (
        "ambiguous_performance_deadline",
        (
            "В течение гарантийного срока поставщик устраняет недостатки "
            "в сроки, согласованные сторонами"
        ),
        "Поставщик устраняет недостатки не более 30 календарных дней с даты уведомления",
    ),
    (
        "incomplete_bundle_specification",
        (
            "Комплект поставки: измерительный прибор, кабели, зажимы, держатели, "
            "программное обеспечение"
        ),
        "Комплект поставки: измерительный прибор, 2 кабеля, 4 зажима, 3 держателя",
    ),
    (
        "heterogeneous_bundle_review",
        "Комплект лабораторного оборудования: осциллограф, мультиметр, источник питания, весы",
        "Набор деталей: 4 стойки, 5 полок, 10 креплений",
    ),
    (
        "non_verifiable_requirement",
        "Цифровая индикация; интерфейс передачи данных — по возможности",
        "Цифровая индикация; интерфейс передачи данных — USB или RS-232",
    ),
    (
        "acceptance_dependency_review",
        "Программное обеспечение предоставляется лабораторией. "
        "Приёмка включает проверку работы оборудования с этим ПО",
        "Программное обеспечение предоставляется лабораторией. "
        "Поставщик обеспечивает совместимость и настройку ПО. "
        "Приёмка включает проверку работы оборудования с этим ПО",
    ),
]


def document(tmp_path, texts, table=False):
    doc = Document()
    doc.add_heading("Синтетическое ТЗ: лабораторное оборудование", 1)
    if table:
        t = doc.add_table(rows=0, cols=2)
        for i, text in enumerate(texts):
            cells = t.add_row().cells
            cells[0].text = str(i + 1)
            cells[1].text = text
    else:
        for text in texts:
            doc.add_paragraph(text)
    path = tmp_path / "synthetic-quality.docx"
    doc.save(path)
    return path


@pytest.mark.parametrize("category,bad,fixed", CASES)
@pytest.mark.parametrize("table", [False, True])
def test_quality_detection_and_correction(tmp_path, category, bad, fixed, table):
    path = document(tmp_path, [bad], table)
    parsed = parse_docx(path)
    fs = check_numeric(parsed)
    matching = [f for f in fs if f.category == category]
    assert len(matching) == 1
    f = matching[0]
    assert f.status == "needs_review" and f.explanation and f.recommendation
    sources = {b.block_id: b for b in parsed.blocks}
    for e in f.evidence:
        assert e.block_id in sources
        assert e.locator == sources[e.block_id].locator
        assert e.text == sources[e.block_id].text[e.start : e.end]
    assert check_numeric(parsed) == fs
    assert not any(
        f.category == category
        for f in check_numeric(parse_docx(document(tmp_path, [fixed], table)))
    )


def test_combined_api_and_diagnostic(tmp_path):
    path = document(tmp_path, [bad for _, bad, _ in CASES])
    with TestClient(app) as c:
        response = c.post("/audit", files={"file": ("synthetic.docx", path.read_bytes())})
        assert response.status_code == 200
        result = response.json()
        assert {f["category"] for f in result["findings"]} == {c[0] for c in CASES}
        assert len(result["findings"]) == 5
        assert c.get("/").status_code == 200
        for category, _, _ in CASES:
            assert category in c.get("/").text
        corrected = document(tmp_path, [fixed for _, _, fixed in CASES])
        assert (
            c.post("/audit", files={"file": ("fixed.docx", corrected.read_bytes())}).json()[
                "findings"
            ]
            == []
        )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/diagnose_local.py"),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Программное обеспечение" not in result.stdout
    assert str(path) not in result.stdout


def test_homogeneous_bundle(tmp_path):
    fs = check_numeric(parse_docx(document(tmp_path, ["Комплект креплений: болты, гайки, шайбы"])))
    assert not fs


def test_counts_after_names(tmp_path):
    assert not check_numeric(
        parse_docx(
            document(
                tmp_path, ["Комплект поставки: кабели — 2 шт., зажимы — 4 шт., держатели — 3 шт."]
            )
        )
    )


def test_counts_each(tmp_path):
    assert not check_numeric(
        parse_docx(
            document(
                tmp_path, ["Комплект поставки: кабели, зажимы, держатели — по одному каждого вида"]
            )
        )
    )


def test_bounded_agreed_deadline(tmp_path):
    assert not check_numeric(
        parse_docx(
            document(
                tmp_path,
                [
                    "Поставщик устраняет недостатки в согласованные сроки, "
                    "но не более 30 календарных дней с даты уведомления"
                ],
            )
        )
    )


def test_adjacent_acceptance_paragraphs(tmp_path):
    fs = check_numeric(
        parse_docx(
            document(
                tmp_path,
                [
                    "Программное обеспечение предоставляется лабораторией",
                    "Приёмка включает проверку работы оборудования с этим ПО",
                ],
            )
        )
    )
    assert len(fs) == 1 and fs[0].category == "acceptance_dependency_review"
    assert len(fs[0].evidence) == 2


def test_repeated_lift_type_grouped(tmp_path):
    path = document(
        tmp_path,
        [
            "Новый грузовой лифт.",
            "Тип лифта: грузовой.",
            "Тип лифта: пассажирский.",
            "Тип лифта: пассажирский.",
        ],
    )
    fs = check_numeric(parse_docx(path))
    assert len(fs) == 1
    assert fs[0].category == "categorical_contradiction"
    assert len(fs[0].evidence) == 4


def test_distinct_lifts_remain_independent(tmp_path):
    path = document(
        tmp_path,
        [
            "Лифт №1: грузовой.",
            "Тип лифта №1: пассажирский.",
            "Лифт №2: грузовой.",
            "Тип лифта №2: пассажирский.",
        ],
    )
    fs = check_numeric(parse_docx(path))
    assert len([f for f in fs if f.status == "confirmed"]) == 2


def test_distinct_obligations_not_merged(tmp_path):
    fs = check_numeric(
        parse_docx(
            document(
                tmp_path,
                [
                    "Поставщик устраняет недостатки в сроки, согласованные сторонами",
                    "Поставщик выполняет ремонт в сроки, согласованные сторонами",
                ],
            )
        )
    )
    assert len(fs) == 2


def test_repeated_quality_wording_sources_retained(tmp_path):
    text = CASES[0][1]
    fs = check_numeric(parse_docx(document(tmp_path, [text, text])))
    assert len(fs) == 1 and len(fs[0].evidence) == 2
