import json
import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document

from tz_auditor.domain.documents import ParsedDocument
from tz_auditor.parsing.docx_parser import parse_docx


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tz_auditor", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_cli_utf8_roundtrip(synthetic_dir: Path, tmp_path: Path) -> None:
    path = synthetic_dir / "shelves.docx"
    output = tmp_path / "результат.json"
    process = run_cli("parse", str(path), "--output", str(output))
    assert process.returncode == 0, process.stderr
    raw = output.read_text(encoding="utf-8")
    assert "стеллажей" in raw
    assert "\\u" not in raw
    assert ParsedDocument.model_validate(json.loads(raw)) == parse_docx(path)


@pytest.mark.parametrize("mode", ["missing", "broken", "unsupported", "bad_output", "same_path"])
def test_cli_errors(mode: str, synthetic_dir: Path, tmp_path: Path) -> None:
    path = synthetic_dir / "shelves.docx"
    output = tmp_path / "out.json"
    if mode == "missing":
        path = tmp_path / "missing.docx"
    elif mode == "broken":
        path = tmp_path / "broken.docx"
        path.write_bytes(b"invalid")
    elif mode == "unsupported":
        path = tmp_path / "input.pdf"
    elif mode == "bad_output":
        output = tmp_path / "absent-directory" / "out.json"
    else:
        output = path
    original = path.read_bytes() if path.exists() else None
    process = run_cli("parse", str(path), "--output", str(output))
    assert process.returncode != 0
    assert "Ошибка:" in process.stderr
    assert "Traceback" not in process.stderr
    if original is not None:
        assert path.read_bytes() == original
    if mode != "same_path":
        assert not output.exists()


def test_generator_values_and_original_coordinates(synthetic_dir: Path) -> None:
    normal = parse_docx(synthetic_dir / "shelves.docx")
    conflict = parse_docx(synthetic_dir / "shelves_conflict.docx")
    assert normal.warnings == conflict.warnings == []
    assert any("высота 2000 мм" in b.text for b in conflict.blocks)
    height = next(b for b in conflict.blocks if b.text == "2200")
    assert height.locator.model_dump() == {
        "paragraph_index": None,
        "table_index": 0,
        "row_index": 1,
        "column_index": 1,
    }
    assert "2000" in [b.text for b in normal.blocks]
    assert [b.locator for b in normal.blocks] == [b.locator for b in conflict.blocks]
    assert all(b.kind in {"paragraph", "table_cell"} for b in conflict.blocks)


def test_cli_complex_table_keeps_text_and_reports_provenance(tmp_path: Path) -> None:
    document = Document()
    document.add_heading("Характеристики", 1)
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "Стеллаж ±2 мм"
    table.cell(0, 0).merge(table.cell(1, 1))
    table.cell(0, 2).text = "2000"
    table.cell(1, 2).text = "2200"
    path = tmp_path / "complex.docx"
    output = tmp_path / "complex.json"
    document.save(path)
    process = run_cli("parse", str(path), "--output", str(output))
    assert process.returncode == 0, process.stderr
    parsed = ParsedDocument.model_validate_json(output.read_text(encoding="utf-8"))
    assert parsed == parse_docx(path)
    assert [b.text for b in parsed.blocks] == ["Характеристики", "Стеллаж ±2 мм", "2000", "2200"]
    assert parsed.blocks[-1].locator.column_index == 1
    assert all(b.section_path == ["Характеристики"] for b in parsed.blocks)
    assert "Предупреждение:" in process.stderr
    assert "vMerge" in process.stderr
