"""Extract physical XML cells without python-docx's merged-cell aliases."""

from collections.abc import Callable, Iterator

from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import Table, _Cell

from tz_auditor.domain.documents import SourceLocator


def iter_table_cells(
    table: Table, table_index: int, warn: Callable[[str], None]
) -> Iterator[tuple[str, SourceLocator]]:
    """Use physical row/cell indices; layout-grid coordinates only track merges.

    Valid empty merge continuations are omitted, never replaced with anchor text.
    Unexpected continuation text is retained at its own source address.
    """
    prefix = f"Таблица {table_index}"
    grid = table._tbl.find(qn("w:tblGrid"))
    grid_width = len(grid.findall(qn("w:gridCol"))) if grid is not None else 0
    if not grid_width:
        warn(f"{prefix}: сетка отсутствует или пуста; сохраняются физические координаты XML.")
    if any(child.tag not in {qn("w:tblPr"), qn("w:tblGrid"), qn("w:tr")} for child in table._tbl):
        warn(f"{prefix}: неподдерживаемый контейнер таблицы пропущен.")

    def count(
        parent: BaseOxmlElement | None, name: str, default: int, where: str
    ) -> tuple[int, bool]:
        node = parent.find(qn(f"w:{name}")) if parent is not None else None
        if node is None:
            return default, True
        try:
            value = int(node.get(qn("w:val"), ""))
            if value < (1 if name == "gridSpan" else 0):
                raise ValueError
        except ValueError:
            warn(f"{where}: некорректный {name}; геометрия не определена, текст сохранён.")
            return default, False
        return value, True

    def merge(properties: BaseOxmlElement | None, name: str, where: str) -> str | None:
        node = properties.find(qn(f"w:{name}")) if properties is not None else None
        if node is None:
            return None
        value = node.get(qn("w:val"), "continue")
        if value not in {"restart", "continue"}:
            warn(f"{where}: некорректный {name}={value!r}; текст сохранён отдельно.")
            return "invalid"
        return value

    # (layout start, span) -> physical origin (row_index, column_index).
    vertical: dict[tuple[int, int], tuple[int, int]] = {}
    for row_index, row in enumerate(table._tbl.tr_lst):
        row_prefix = f"{prefix}, строка {row_index}"
        if any(child.tag not in {qn("w:trPr"), qn("w:tc"), qn("w:tblPrEx")} for child in row):
            warn(f"{row_prefix}: неподдерживаемый контейнер строки пропущен.")
        before, layout_known = count(row.trPr, "gridBefore", 0, row_prefix)
        after, after_known = count(row.trPr, "gridAfter", 0, row_prefix)
        if before or after:
            warn(
                f"{row_prefix}: пропуски сетки слева={before}, справа={after}; ячейки не выдуманы."
            )
        position = before
        next_vertical: dict[tuple[int, int], tuple[int, int]] = {}
        horizontal: tuple[int, int] | None = None
        if not row.tc_lst:
            warn(f"{row_prefix}: нет физических ячеек.")
        for column_index, element in enumerate(row.tc_lst):
            where = f"{row_prefix}, ячейка {column_index}"
            cell = _Cell(element, table)
            text = "\n".join(paragraph.text for paragraph in cell.paragraphs)
            if cell.tables:
                warn(f"{prefix}: вложенные таблицы не извлекаются.")
            if any(child.tag not in {qn("w:tcPr"), qn("w:p"), qn("w:tbl")} for child in element):
                warn(f"{where}: неподдерживаемый контейнер внутри ячейки пропущен.")
            span, span_known = count(element.tcPr, "gridSpan", 1, where)
            layout_known &= span_known
            if span > 1:
                warn(f"{where}: gridSpan={span}; текст записан один раз в физической ячейке.")
            key = (position, span)
            origin = (row_index, column_index)
            linked_origins: list[tuple[int, int]] = []
            vertical_merge = merge(element.tcPr, "vMerge", where)
            if vertical_merge == "restart" and layout_known:
                next_vertical[key] = origin
                warn(f"{where}: начало вертикального объединения vMerge.")
            elif vertical_merge == "continue":
                anchor = vertical.get(key) if layout_known else None
                if anchor is None:
                    warn(f"{where}: продолжение vMerge без однозначного начала; ячейка сохранена.")
                else:
                    linked_origins.append(anchor)
                    next_vertical[key] = anchor

            horizontal_merge = merge(element.tcPr, "hMerge", where)
            if horizontal_merge == "restart":
                horizontal = origin
                warn(f"{where}: устаревшее hMerge; текст физических ячеек хранится раздельно.")
            elif horizontal_merge == "continue":
                if horizontal is None:
                    warn(f"{where}: продолжение hMerge без начала; ячейка сохранена.")
                else:
                    linked_origins.append(horizontal)
            else:
                horizontal = None

            if linked_origins:
                warn(f"{where}: продолжение объединения из {linked_origins}; текст не копируется.")
                if text.strip():
                    warn(f"{where}: собственный текст продолжения сохранён по его координатам.")
            if not linked_origins or text.strip():
                yield (
                    text,
                    SourceLocator(
                        table_index=table_index, row_index=row_index, column_index=column_index
                    ),
                )
            position += span
        if grid_width and layout_known and after_known and position + after != grid_width:
            warn(
                f"{row_prefix}: ширина строки {position + after} не равна сетке {grid_width}; "
                "все доступные физические ячейки сохранены."
            )
        vertical = next_vertical
