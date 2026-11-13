"""Synthetic XML cases; all locators resolve directly to physical source cells."""

from pathlib import Path

import pytest
from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import _Cell

from tz_auditor.domain.documents import ParsedDocument
from tz_auditor.parsing.docx_parser import parse_docx


def property_node(parent: BaseOxmlElement, name: str, value: str | None) -> None:
    node = OxmlElement(f"w:{name}")
    if value is not None:
        node.set(qn("w:val"), value)
    parent.append(node)


def parse_and_verify(document: DocxDocument, tmp_path: Path) -> ParsedDocument:
    path = tmp_path / "complex.docx"
    document.save(path)
    parsed = parse_docx(path)
    assert parse_docx(path) == parsed
    assert ParsedDocument.model_validate_json(parsed.model_dump_json()) == parsed
    assert [block.order for block in parsed.blocks] == list(range(len(parsed.blocks)))
    assert len({block.block_id for block in parsed.blocks}) == len(parsed.blocks)
    addresses = []
    for block in parsed.blocks:
        locator = block.locator
        addresses.append(tuple(locator.model_dump().values()))
        if locator.paragraph_index is not None:
            expected = document.paragraphs[locator.paragraph_index].text
        else:
            table = document.tables[locator.table_index]
            element = table._tbl.tr_lst[locator.row_index].tc_lst[locator.column_index]
            cell = _Cell(element, table)
            expected = "\n".join(paragraph.text for paragraph in cell.paragraphs)
        assert block.text == expected
    assert len(set(addresses)) == len(addresses)
    return parsed


def test_horizontal_merge_keeps_text_once_and_physical_column(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=3)
    for cell, text in zip(table.rows[0].cells, ("Высота", "2000 мм", "Хвост"), strict=True):
        cell.text = text
    table.cell(0, 0).merge(table.cell(0, 1))
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Высота\n2000 мм", "Хвост"]
    assert [b.locator.column_index for b in result.blocks] == [0, 1]
    assert "gridSpan=2" in " ".join(result.warnings)


def test_vertical_merge_retains_all_anchor_paragraphs_without_aliases(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=3, cols=2)
    for i in range(3):
        table.cell(i, 0).text = f"Размер {i}"
        table.cell(i, 1).text = f"Значение {i}"
    table.cell(0, 0).merge(table.cell(2, 0))
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == [
        "Размер 0\nРазмер 1\nРазмер 2",
        "Значение 0",
        "Значение 1",
        "Значение 2",
    ]
    assert [(b.locator.row_index, b.locator.column_index) for b in result.blocks] == [
        (0, 0),
        (0, 1),
        (1, 1),
        (2, 1),
    ]
    assert "из [(0, 0)]" in " ".join(result.warnings)


def test_rectangular_merge_and_interleaving_keep_sections(tmp_path: Path) -> None:
    document = Document()
    document.add_heading("Раздел", 1)
    table = document.add_table(rows=2, cols=3)
    for i in range(2):
        for j in range(3):
            table.cell(i, j).text = f"{i}:{j}"
    table.cell(0, 0).merge(table.cell(1, 1))
    document.add_paragraph("Между")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Вторая таблица"
    document.add_paragraph("")
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == [
        "Раздел",
        "0:0\n0:1\n1:0\n1:1",
        "0:2",
        "1:2",
        "Между",
        "Вторая таблица",
        "",
    ]
    assert all(b.section_path == ["Раздел"] for b in result.blocks)
    assert result.blocks[3].locator.row_index == 1
    assert result.blocks[3].locator.column_index == 1
    assert result.blocks[4].locator.paragraph_index == 1
    assert result.blocks[5].locator.table_index == 1
    assert result.blocks[6].locator.paragraph_index == 2


def test_vertical_continuation_with_own_text_preserves_its_origin(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "Начало"
    table.cell(0, 0).merge(table.cell(1, 0))
    continuation = _Cell(table._tbl.tr_lst[1].tc_lst[0], table)
    continuation.text = "Собственный текст продолжения"
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Начало", "Собственный текст продолжения"]
    assert result.blocks[1].locator.row_index == 1
    assert "собственный текст" in " ".join(result.warnings)


def test_incomplete_row_with_both_grid_omissions(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=2, cols=4)
    table.cell(0, 1).text = "Середина 1"
    table.cell(0, 2).text = "Середина 2"
    row = table._tbl.tr_lst[0]
    row.remove(row.tc_lst[-1])
    row.remove(row.tc_lst[0])
    property_node(row.get_or_add_trPr(), "gridBefore", "1")
    property_node(row.get_or_add_trPr(), "gridAfter", "1")
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks[:2]] == ["Середина 1", "Середина 2"]
    assert [b.locator.column_index for b in result.blocks[:2]] == [0, 1]
    assert len(result.blocks) == 6
    assert "слева=1, справа=1" in " ".join(result.warnings)


def test_vertical_merge_tracks_grid_not_physical_cell_number(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 1).text = "Общая характеристика"
    table.cell(0, 1).merge(table.cell(1, 1))
    row = table._tbl.tr_lst[0]
    row.remove(row.tc_lst[0])
    property_node(row.get_or_add_trPr(), "gridBefore", "1")
    result = parse_and_verify(document, tmp_path)
    assert sum(b.text == "Общая характеристика" for b in result.blocks) == 1
    assert len(result.blocks) == 4
    assert result.blocks[0].locator.column_index == 0
    assert "без однозначного начала" not in " ".join(result.warnings)


@pytest.mark.parametrize("kind", ["vMerge", "hMerge"])
@pytest.mark.parametrize("value", [None, "continue", "unexpected"])
def test_orphan_and_invalid_merges_keep_source_text(
    tmp_path: Path, kind: str, value: str | None
) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Не терять"
    table.cell(0, 1).text = "Сосед"
    property_node(table.cell(0, 0)._tc.get_or_add_tcPr(), kind, value)
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Не терять", "Сосед"]
    assert result.warnings


@pytest.mark.parametrize("continuation_text", ["", "Отдельный текст"])
def test_legacy_horizontal_merges_preserve_content_at_original_cells(
    tmp_path: Path, continuation_text: str
) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=3)
    table.cell(0, 0).text = "Начало"
    table.cell(0, 1).text = continuation_text
    table.cell(0, 2).text = "Сосед"
    property_node(table.cell(0, 0)._tc.get_or_add_tcPr(), "hMerge", "restart")
    property_node(table.cell(0, 1)._tc.get_or_add_tcPr(), "hMerge", None)
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == (
        ["Начало", continuation_text, "Сосед"] if continuation_text else ["Начало", "Сосед"]
    )
    assert result.blocks[-1].locator.column_index == 2
    assert "hMerge" in " ".join(result.warnings)


@pytest.mark.parametrize(
    "name,value",
    [
        ("gridSpan", "0"),
        ("gridSpan", "-1"),
        ("gridSpan", "bad"),
        ("gridBefore", "-2"),
        ("gridAfter", "bad"),
        ("gridSpan", None),
    ],
)
def test_malformed_dimensions_preserve_physical_addresses(
    tmp_path: Path, name: str, value: str | None
) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "А"
    table.cell(0, 1).text = "Б"
    parent = (
        table.cell(0, 0)._tc.get_or_add_tcPr()
        if name == "gridSpan"
        else (table._tbl.tr_lst[0].get_or_add_trPr())
    )
    property_node(parent, name, value)
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["А", "Б"]
    assert [b.locator.column_index for b in result.blocks] == [0, 1]
    assert "некорректный" in " ".join(result.warnings)


def test_mismatching_vertical_spans_do_not_duplicate_or_drop_text(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Широкая"
    property_node(table._tbl.tr_lst[0].tc_lst[0].get_or_add_tcPr(), "vMerge", "restart")
    table.cell(1, 0).text = "Узкая"
    property_node(table._tbl.tr_lst[1].tc_lst[0].get_or_add_tcPr(), "vMerge", None)
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Широкая", "Узкая", ""]
    assert "без однозначного начала" in " ".join(result.warnings)


def test_empty_row_breaks_vertical_merge_chain(tmp_path: Path) -> None:
    document = Document()
    table = document.add_table(rows=3, cols=1)
    table.cell(0, 0).text = "Начало"
    property_node(table._tbl.tr_lst[0].tc_lst[0].get_or_add_tcPr(), "vMerge", "restart")
    table._tbl.tr_lst[1].remove(table._tbl.tr_lst[1].tc_lst[0])
    property_node(table._tbl.tr_lst[2].tc_lst[0].get_or_add_tcPr(), "vMerge", None)
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Начало", ""]
    assert result.blocks[1].locator.row_index == 2
    assert "нет физических ячеек" in " ".join(result.warnings)


def test_nested_merged_table_does_not_hide_outer_text(tmp_path: Path) -> None:
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Внешний текст"
    nested = cell.add_table(rows=1, cols=2)
    nested.cell(0, 0).merge(nested.cell(0, 1)).text = "Вложенный текст"
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Внешний текст\n"]
    assert any("вложенные таблицы" in warning for warning in result.warnings)


@pytest.mark.parametrize("container", ["table", "row", "cell"])
def test_unknown_containers_are_diagnosed_without_dropping_supported_cells(
    tmp_path: Path, container: str
) -> None:
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "Доступный текст"
    parents = {"table": table._tbl, "row": table._tbl.tr_lst[0], "cell": table.cell(0, 0)._tc}
    parents[container].append(OxmlElement("w:sdt"))
    result = parse_and_verify(document, tmp_path)
    assert [b.text for b in result.blocks] == ["Доступный текст"]
    assert "неподдерживаемый контейнер" in " ".join(result.warnings)
