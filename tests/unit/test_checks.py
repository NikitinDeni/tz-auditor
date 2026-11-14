from docx import Document

from tz_auditor.checks import check_numeric
from tz_auditor.parsing.docx_parser import parse_docx


def parsed(tmp_path, paragraphs, rows=()):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    if rows:
        table = doc.add_table(rows=0, cols=len(rows[0]))
        for row in rows:
            for cell, text in zip(table.add_row().cells, row, strict=True):
                cell.text = text
    path = tmp_path / "synthetic.docx"
    doc.save(path)
    return parse_docx(path)


def test_existing_fixtures(synthetic_dir):
    assert check_numeric(parse_docx(synthetic_dir / "shelves.docx")) == []
    doc = parse_docx(synthetic_dir / "shelves_conflict.docx")
    findings = check_numeric(doc)
    assert len(findings) == 1
    assert findings[0].status == "confirmed"
    assert findings == check_numeric(doc)
    sources = {b.block_id: b for b in doc.blocks}
    assert len(findings[0].evidence) == 4
    for e in findings[0].evidence:
        assert e.locator == sources[e.block_id].locator
        assert e.text == sources[e.block_id].text[e.start : e.end]
    assert findings[0].evidence[0].locator.paragraph_index == 4
    assert findings[0].evidence[2].locator.row_index == 1
    assert findings[0].evidence[2].locator.column_index == 1


def test_normalized_units(tmp_path):
    doc = parsed(tmp_path, ["Стеллаж АБ-23: высота 1,75 м"], [("Высота АБ-23", "175", "см")])
    assert not check_numeric(doc)


def test_arbitrary_values(tmp_path):
    doc = parsed(tmp_path, ["Стеллаж АБ-23: высота 1,75 м"], [("Высота АБ-23", "180", "см")])
    assert check_numeric(doc)[0].status == "confirmed"


def test_distinct_models(tmp_path):
    doc = parsed(tmp_path, ["Стеллаж АБ-23: высота 1 м", "Стеллаж АБ-24: высота 2 м"])
    assert not check_numeric(doc)


def test_positions(tmp_path):
    doc = parsed(
        tmp_path, ["Позиция 1. Стеллаж АБ-23: высота 1 м", "Позиция 2. Стеллаж АБ-23: высота 2 м"]
    )
    assert not check_numeric(doc)


def test_unknown_objects(tmp_path):
    assert not check_numeric(parsed(tmp_path, ["Высота 1 м", "Высота 2 м"]))


def test_generic_requires_review(tmp_path):
    doc = parsed(
        tmp_path, ["Высота металлического стеллажа — 2000 мм"], [("Высота стеллажа — 2200 мм",)]
    )
    assert check_numeric(doc)[0].status == "needs_review"


def test_bounds_and_tolerances(tmp_path):
    for text in ["не менее 1 м", "от 1 до 2 м", "1–2 м", "2 м ±2 мм", "2 м ±5%"]:
        doc = parsed(tmp_path, [f"Стеллаж АБ-23: высота {text}", "Стеллаж АБ-23: высота 3 м"])
        assert not check_numeric(doc)


def test_component_is_not_entire_object(tmp_path):
    doc = parsed(tmp_path, ["Стеллаж АБ-23: высота полки 1 м", "Стеллаж АБ-23: высота 2 м"])
    assert not check_numeric(doc)


def test_qualified_bound(tmp_path):
    doc = parsed(tmp_path, ["Стеллаж АБ-23: минимальная высота 1 м", "Стеллаж АБ-23: высота 2 м"])
    assert not check_numeric(doc)


def test_mass(tmp_path):
    doc = parsed(tmp_path, ["Стеллаж АБ-23: масса 1,5 кг", "Стеллаж АБ-23: вес 1500 г"])
    assert not check_numeric(doc)
