"""Generate exclusively fictional, synthetic procurement documents."""

import argparse
from pathlib import Path

from docx import Document

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"


def generate_synthetic(output_dir: Path = DEFAULT_OUTPUT) -> list[Path]:
    """Create a consistent specification and a variant with conflicting heights."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for filename, height in (("shelves.docx", "2000"), ("shelves_conflict.docx", "2200")):
        document = Document()
        document.add_heading("Закупка металлических стеллажей", level=1)
        document.add_paragraph("Синтетическое ТЗ для вымышленной организации «Пример-Склад».")
        document.add_heading("1. Требования к товару", level=1)
        document.add_heading("1.1. Описание", level=2)
        document.add_paragraph("Стеллаж СТ-01: высота 2000 мм, ширина 1000 мм, глубина 500 мм.")
        document.add_paragraph("")
        document.add_heading("1.2. Технические характеристики", level=2)
        document.add_heading("1.2.1. Размеры и нагрузка", level=3)
        table = document.add_table(rows=1, cols=3)
        for cell, value in zip(
            table.rows[0].cells, ("Характеристика", "Значение", "Единица"), strict=True
        ):
            cell.text = value
        for values in (
            ("Высота СТ-01", height, "мм"),
            ("Ширина", "1000", "мм"),
            ("Глубина", "500", "мм"),
            ("Нагрузка на полку", "150", "кг"),
        ):
            for cell, value in zip(table.add_row().cells, values, strict=True):
                cell.text = value
        document.add_paragraph("Покрытие: порошковое; температура эксплуатации от +5 до +35 °C.")
        document.add_heading("2. Количество и комплектность", level=1)
        quantity = document.add_table(rows=2, cols=3)
        for row, values in zip(
            quantity.rows,
            (("Товар", "Количество", "Примечание"), ("Стеллаж СТ-01", "12 шт.", "")),
            strict=True,
        ):
            for cell, value in zip(row.cells, values, strict=True):
                cell.text = value
        document.add_paragraph(
            "Комплект: 4 стойки, 5 полок и крепёж. Маркировка № 01; допуск ±2 мм."
        )
        path = output_dir / filename
        document.save(path)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for path in generate_synthetic(args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
