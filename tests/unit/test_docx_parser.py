import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from tz_auditor.domain.documents import ParsedDocument
from tz_auditor.parsing.docx_parser import DocxParseError, parse_docx


def test_order_and_exact_source_coordinates(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("До таблицы")
    document.add_paragraph("")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Размер"
    table.cell(0, 1).text = "2000 мм"
    table.cell(1, 0).text = "Первая строка"
    table.cell(1, 0).add_paragraph("Вторая строка")
    document.add_paragraph("Между таблицами: № 12, ±2 °C & < >")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Конец"
    document.add_paragraph("После таблиц")
    path = tmp_path / "порядок.docx"
    document.save(path)
    parsed = parse_docx(path)
    assert [b.text for b in parsed.blocks] == [
        "До таблицы",
        "",
        "Размер",
        "2000 мм",
        "Первая строка\nВторая строка",
        "",
        "Между таблицами: № 12, ±2 °C & < >",
        "Конец",
        "После таблиц",
    ]
    expected = [
        (0, None, None, None),
        (1, None, None, None),
        (None, 0, 0, 0),
        (None, 0, 0, 1),
        (None, 0, 1, 0),
        (None, 0, 1, 1),
        (2, None, None, None),
        (None, 1, 0, 0),
        (3, None, None, None),
    ]
    assert [tuple(b.locator.model_dump().values()) for b in parsed.blocks] == expected
    assert [b.order for b in parsed.blocks] == list(range(9))
    assert [b.block_id for b in parsed.blocks] == [f"b{i:06d}" for i in range(1, 10)]
    assert parsed.warnings == []


def test_heading_paths_include_self_and_reset_nested_levels(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("Преамбула")
    document.add_heading("А", 1)
    document.add_heading("Б", 2)
    document.add_heading("В", 3)
    document.add_paragraph("Текст")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].style = "Heading 1"
    table.cell(0, 0).paragraphs[0].text = "Стиль внутри ячейки"
    document.add_heading("Г", 2)
    document.add_heading("Д", 4)
    document.add_heading("Е", 1)
    document.add_heading("Ж", 3)
    document.add_paragraph("Итог")
    document.add_heading("З", 9)
    path = tmp_path / "заголовки.docx"
    document.save(path)
    result = parse_docx(path)
    assert [b.section_path for b in result.blocks] == [
        [],
        ["А"],
        ["А", "Б"],
        ["А", "Б", "В"],
        ["А", "Б", "В"],
        ["А", "Б", "В"],
        ["А", "Г"],
        ["А", "Г", "Д"],
        ["Е"],
        ["Е", "Ж"],
        ["Е", "Ж"],
        ["Е", "Ж", "З"],
    ]
    assert [b.heading_level for b in result.blocks] == [
        None,
        1,
        2,
        3,
        None,
        None,
        2,
        4,
        1,
        3,
        None,
        9,
    ]
    assert result.blocks[1].section_path == ["А"]  # Earlier paths are independent snapshots.


def test_empty_document(tmp_path: Path) -> None:
    path = tmp_path / "empty.docx"
    Document().save(path)
    parsed = parse_docx(path)
    assert parsed.blocks == []
    assert parsed.warnings == []


def test_stable_ids_hash_and_json(synthetic_dir: Path, tmp_path: Path) -> None:
    path = synthetic_dir / "shelves.docx"
    first = parse_docx(path)
    assert first == parse_docx(path)
    assert first.document_id == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len({b.block_id for b in first.blocks}) == len(first.blocks)
    assert ParsedDocument.model_validate_json(first.model_dump_json()) == first
    renamed = tmp_path / "renamed.DOCX"
    renamed.write_bytes(path.read_bytes())
    assert parse_docx(renamed).document_id == first.document_id
    changed = Document(path)
    changed.add_paragraph("Изменение")
    changed.save(renamed)
    assert parse_docx(renamed).document_id != first.document_id


@pytest.mark.parametrize("content", [b"not a zip", b"", b"%PDF-1.7"])
def test_corrupt_file(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "broken.docx"
    path.write_bytes(content)
    with pytest.raises(DocxParseError, match="Повреждённый"):
        parse_docx(path)


def test_zip_without_docx_parts(tmp_path: Path) -> None:
    path = tmp_path / "archive.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr("unrelated.txt", "synthetic")
    with pytest.raises(DocxParseError):
        parse_docx(path)


def test_invalid_document_xml(tmp_path: Path, synthetic_dir: Path) -> None:
    path = tmp_path / "bad_xml.docx"
    with ZipFile(synthetic_dir / "shelves.docx") as source, ZipFile(path, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"<invalid" if name == "word/document.xml" else source.read(name))
    with pytest.raises(DocxParseError):
        parse_docx(path)


def test_missing_and_unsupported_file(tmp_path: Path) -> None:
    with pytest.raises(DocxParseError, match="прочитать"):
        parse_docx(tmp_path / "missing.docx")
    with pytest.raises(DocxParseError, match="формат"):
        parse_docx(tmp_path / "input.pdf")


def test_document_without_body(tmp_path: Path, synthetic_dir: Path) -> None:
    path = tmp_path / "no_body.docx"
    xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
    with ZipFile(synthetic_dir / "shelves.docx") as source, ZipFile(path, "w") as target:
        for name in source.namelist():
            target.writestr(name, xml if name == "word/document.xml" else source.read(name))
    with pytest.raises(DocxParseError, match="тело"):
        parse_docx(path)


def test_table_without_grid(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table._tbl.remove(table._tbl.tblGrid)
    path = tmp_path / "no_grid.docx"
    document.save(path)
    with pytest.raises(DocxParseError, match="структура"):
        parse_docx(path)


@pytest.mark.parametrize("vertical", [False, True])
def test_merged_tables_warn_and_preserve_subsequent_indices(tmp_path: Path, vertical: bool) -> None:
    document = Document()
    document.add_paragraph("Начало")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(1, 0) if vertical else table.cell(0, 1))
    document.add_paragraph("После")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Обычная"
    path = tmp_path / "merged.docx"
    document.save(path)
    parsed = parse_docx(path)
    assert [b.text for b in parsed.blocks] == ["Начало", "После", "Обычная"]
    assert parsed.blocks[1].locator.paragraph_index == 1
    assert parsed.blocks[2].locator.table_index == 1
    assert "Таблица 0" in parsed.warnings[0]


def test_unsupported_content_is_explicit(tmp_path: Path) -> None:
    document = Document()
    document.sections[0].header.paragraphs[0].text = "Синтетический колонтитул"
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Внешняя ячейка"
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "Вложенная таблица"
    paragraph = document.add_paragraph("Обычный абзац")
    drawing = OxmlElement("w:drawing")
    paragraph._p.append(drawing)
    textbox = OxmlElement("w:txbxContent")
    textbox.append(OxmlElement("w:p"))
    drawing.append(textbox)
    control = OxmlElement("w:sdt")
    document.element.body.insert(0, control)
    path = tmp_path / "unsupported.docx"
    document.save(path)
    parsed = parse_docx(path)
    warnings = " ".join(parsed.warnings)
    assert "Колонтитулы" in warnings
    assert "Изображения" in warnings
    assert "Текстовые поля" in warnings
    assert "вложенные таблицы" in warnings
    assert "Неподдерживаемый элемент" in warnings
    assert all("Вложенная таблица" not in b.text for b in parsed.blocks)
    assert parsed.blocks[-1].locator.paragraph_index == 0


def test_incomplete_table_is_skipped(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    row = table._tbl.tr_lst[0]
    row.remove(row.tc_lst[1])
    path = tmp_path / "incomplete.docx"
    document.save(path)
    result = parse_docx(path)
    assert result.blocks == []
    assert result.warnings


def test_localized_heading_uses_standard_style(tmp_path: Path) -> None:
    document = Document()
    document.styles["Heading 1"].element.find(qn("w:name")).set(qn("w:val"), "heading 1")
    document.add_heading("Русский заголовок", 1)
    path = tmp_path / "ru.docx"
    document.save(path)
    assert parse_docx(path).blocks[0].section_path == ["Русский заголовок"]
