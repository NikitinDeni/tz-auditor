"""DOCX body parser. No network access or semantic analysis."""

import hashlib
import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.oxml.exceptions import InvalidXmlError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from tz_auditor.domain.documents import DocumentBlock, ParsedDocument, SourceLocator
from tz_auditor.parsing.docx_tables import iter_table_cells


class DocxParseError(ValueError):
    """Unreadable, damaged, or unsupported DOCX input."""


def parse_docx(path: Path) -> ParsedDocument:
    """Extract top-level paragraphs and physical table cells in source order.

    Paragraph/table counters are independent and include empty source elements.
    Merged-cell text is emitted once; irregular structures produce diagnostics.
    """
    try:
        return _parse_docx(path)
    except InvalidXmlError as exc:
        raise DocxParseError(f"Повреждённая структура DOCX: {path.name}") from exc


def _parse_docx(path: Path) -> ParsedDocument:
    if path.suffix.lower() != ".docx":
        raise DocxParseError(f"Неподдерживаемый формат файла: {path.name}. Ожидается DOCX.")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise DocxParseError(f"Не удалось прочитать файл {path}: {exc}") from exc

    try:
        document = Document(BytesIO(content))
        with ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
    except (BadZipFile, KeyError, ValueError, XMLSyntaxError, OSError) as exc:
        raise DocxParseError(f"Повреждённый или неподдерживаемый DOCX: {path.name}") from exc

    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    headings: list[tuple[int, str]] = []

    def warn(message: str) -> None:
        if message not in warnings:
            warnings.append(message)

    if any(re.fullmatch(r"word/(header|footer)\d+\.xml", name) for name in names):
        warn("Колонтитулы не извлекаются.")
    body = document.element.body
    if body is None:
        raise DocxParseError(f"Повреждённая структура DOCX: отсутствует тело {path.name}")
    if any(node.tag in {qn("w:drawing"), qn("w:pict")} for node in body.iter()):
        warn("Изображения и графические объекты не извлекаются.")
    if any(node.tag == qn("w:txbxContent") for node in body.iter()):
        warn("Текстовые поля не извлекаются.")

    def append(text: str, locator: SourceLocator, level: int | None = None) -> None:
        order = len(blocks)
        blocks.append(
            DocumentBlock(
                block_id=f"b{order + 1:06d}",
                order=order,
                kind="paragraph" if locator.paragraph_index is not None else "table_cell",
                text=text,
                section_path=[title for _, title in headings],
                heading_level=level,
                locator=locator,
            )
        )

    paragraph_index = 0
    table_index = 0
    for element in body.iterchildren():
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            style_name = paragraph.style.name if paragraph.style is not None else ""
            match = re.fullmatch(r"Heading ([1-9])", style_name, flags=re.IGNORECASE)
            level = int(match.group(1)) if match else None
            if level is not None:
                headings[:] = [(n, title) for n, title in headings if n < level]
                headings.append((level, paragraph.text))
            append(paragraph.text, SourceLocator(paragraph_index=paragraph_index), level)
            paragraph_index += 1
        elif element.tag == qn("w:tbl"):
            table = Table(element, document)
            for text, locator in iter_table_cells(table, table_index, warn):
                append(text, locator)
            table_index += 1
        elif element.tag != qn("w:sectPr"):
            warn("Неподдерживаемый элемент основного тела документа пропущен.")

    return ParsedDocument(
        document_id=hashlib.sha256(content).hexdigest(),
        filename=path.name,
        blocks=blocks,
        warnings=warnings,
    )
