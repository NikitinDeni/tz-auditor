"""Local command-line entry point."""

import argparse
import sys
from pathlib import Path

from tz_auditor.parsing.docx_parser import DocxParseError, parse_docx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TZ-Auditor: локальный разбор DOCX")
    commands = parser.add_subparsers(dest="command", required=True)
    parse = commands.add_parser("parse", help="Преобразовать DOCX в JSON")
    parse.add_argument("input", type=Path)
    parse.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.input.resolve() == args.output.resolve():
            raise DocxParseError("Пути входного DOCX и выходного JSON должны различаться.")
        result = parse_docx(args.input)
        args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except (DocxParseError, OSError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    for warning in result.warnings:
        print(f"Предупреждение: {warning}", file=sys.stderr)
    print(f"Сохранено блоков: {len(result.blocks)}. JSON: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
