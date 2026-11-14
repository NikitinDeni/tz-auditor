from docx import Document
from fastapi.testclient import TestClient

from tz_auditor.api import app
from tz_auditor.checks import check_numeric
from tz_auditor.parsing.docx_parser import parse_docx


def lift_doc(tmp_path, capacity="400", kind="пассажирский", extra=()):
    doc = Document()
    doc.add_heading("Замена оборудования", 1)
    doc.add_heading("Описание работ", 2)
    doc.add_paragraph("Устанавливается новый грузовой лифт грузоподъёмностью 600 кг.")
    doc.add_paragraph("Лифт: скорость 0,5 м/с, высота 2 м.")
    for text in extra:
        doc.add_paragraph(text)
    doc.add_heading("Технические требования", 2)
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].merge(table.rows[0].cells[2]).text = "Характеристики лифта"
    for row in [
        ("№", "Наименование оборудования", "Технические характеристики"),
        ("1", "Тип лифта", kind),
        ("3", "Грузоподъемность, кг", capacity),
        ("4", "Скорость, м/с", "0.5"),
        ("5", "Высота, мм", "2000"),
    ]:
        for cell, text in zip(table.add_row().cells, row, strict=True):
            cell.text = text
    doc.add_heading("Приемка", 1)
    doc.add_paragraph("Проверить комплектность оборудования.")
    path = tmp_path / "lift.docx"
    doc.save(path)
    return path


def test_two_conflicts(tmp_path):
    parsed = parse_docx(lift_doc(tmp_path))
    findings = check_numeric(parsed)
    assert len(findings) == 2
    assert {f.category for f in findings} == {"numeric_contradiction", "categorical_contradiction"}
    assert all(f.status == "confirmed" for f in findings)
    assert all("единственный лифт" in f.explanation for f in findings)
    assert any("600" in f.explanation and "400" in f.explanation for f in findings)
    assert findings == check_numeric(parsed)
    sources = {b.block_id: b for b in parsed.blocks}
    for finding in findings:
        assert finding.explanation and finding.recommendation
        assert any(e.locator.paragraph_index == 2 for e in finding.evidence)
        assert any(e.locator.table_index == 0 for e in finding.evidence)
        for e in finding.evidence:
            assert e.locator == sources[e.block_id].locator
            assert e.text == sources[e.block_id].text[e.start : e.end]


def test_consistent_capacity(tmp_path):
    findings = check_numeric(parse_docx(lift_doc(tmp_path, capacity="600")))
    assert len(findings) == 1 and findings[0].category == "categorical_contradiction"


def test_corrected_and_units(tmp_path):
    assert not check_numeric(parse_docx(lift_doc(tmp_path, capacity="600", kind="грузовой")))


def test_two_lifts_ambiguous(tmp_path):
    findings = check_numeric(
        parse_docx(
            lift_doc(
                tmp_path,
                extra=["Лифт № 1: грузоподъемность 600 кг.", "Лифт № 2: грузоподъемность 400 кг."],
            )
        )
    )
    assert not any(f.status == "confirmed" for f in findings)
    assert any(f.status == "needs_review" for f in findings)


def test_explicit_different_lifts(tmp_path):
    doc = Document()
    doc.add_paragraph("Лифт № 1: грузоподъемность 600 кг, тип лифта грузовой.")
    doc.add_paragraph("Лифт № 2: грузоподъемность 400 кг, тип лифта пассажирский.")
    path = tmp_path / "two.docx"
    doc.save(path)
    assert not check_numeric(parse_docx(path))


def test_api_lift(tmp_path):
    path = lift_doc(tmp_path)
    with TestClient(app) as client:
        response = client.post("/audit", files={"file": ("lift.docx", path.read_bytes())})
        assert response.status_code == 200
        assert len(response.json()["findings"]) == 2


def test_arbitrary_numbers_and_tonnes(tmp_path):
    path = lift_doc(tmp_path, capacity="0,45")
    doc = Document(path)
    doc.tables[0].rows[3].cells[1].text = "Грузоподъемность, т"
    doc.save(path)
    findings = check_numeric(parse_docx(path))
    assert any("450" in f.explanation for f in findings)


def test_two_column_table(tmp_path):
    doc = Document()
    doc.add_paragraph("Новый грузовой лифт грузоподъёмностью 700 кг.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Грузоподъемность, кг"
    table.cell(0, 1).text = "500"
    path = tmp_path / "two-cols.docx"
    doc.save(path)
    assert check_numeric(parse_docx(path))[0].status == "confirmed"


def test_speed_is_actually_extracted(tmp_path):
    path = lift_doc(tmp_path, capacity="600", kind="грузовой")
    doc = Document(path)
    doc.tables[0].rows[4].cells[2].text = "0.7"
    doc.save(path)
    findings = check_numeric(parse_docx(path))
    assert len(findings) == 1 and "скорость" in findings[0].explanation


def test_height_is_actually_extracted(tmp_path):
    path = lift_doc(tmp_path, capacity="600", kind="грузовой")
    doc = Document(path)
    doc.tables[0].rows[5].cells[2].text = "2100"
    doc.save(path)
    findings = check_numeric(parse_docx(path))
    assert len(findings) == 1 and "высота" in findings[0].explanation


def test_old_new_not_confirmed(tmp_path):
    path = lift_doc(tmp_path, extra=["Существующий грузовой лифт демонтируется."])
    assert not any(f.status == "confirmed" for f in check_numeric(parse_docx(path)))


def test_bounds_not_exact(tmp_path):
    path = lift_doc(tmp_path, capacity="не менее 400")
    findings = check_numeric(parse_docx(path))
    assert not any(f.category == "numeric_contradiction" for f in findings)


def test_local_diagnostic_redacts_text(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    original = lift_doc(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "scripts/diagnose_local.py"),
            str(original),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(json.loads(result.stdout)["findings"]) == 2
    assert "600" not in result.stdout and "пассажирский" not in result.stdout
    assert str(original) not in result.stdout
