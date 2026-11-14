"""Conservative, deterministic checks of exact numeric requirements."""

import re
from collections import defaultdict
from decimal import Decimal
from itertools import combinations
from typing import Literal

from pydantic import BaseModel

from tz_auditor.domain.documents import DocumentBlock, ParsedDocument, SourceLocator


class Evidence(BaseModel):
    block_id: str
    locator: SourceLocator
    text: str
    start: int
    end: int


class Finding(BaseModel):
    category: Literal["numeric_contradiction", "categorical_contradiction"] = (
        "numeric_contradiction"
    )
    status: Literal["confirmed", "needs_review"]
    explanation: str
    evidence: list[Evidence]
    recommendation: str


PROPERTY = re.compile(r"\b(высота|ширина|глубина|длина|диаметр|масса|вес)\b", re.I)
VALUE = re.compile(r"(?<![\w.,])([0-9]+(?:[.,][0-9]+)?)\s*(мм|см|км|м|кг|г)\b", re.I)
MODEL = re.compile(r"\b[а-яa-z]{1,8}-\d+[а-яa-z0-9-]*\b", re.I)
POSITION = re.compile(r"\b(?:позиция|лот|изделие)\s*№?\s*(\d+)\b", re.I)
OBJECT = re.compile(r"\b(стеллаж(?:а)?|шкаф(?:а)?|стол(?:а)?|стойк[аи])\b", re.I)
UNITS = {
    "мм": (Decimal(1), "мм"),
    "см": (Decimal(10), "мм"),
    "м": (Decimal(1000), "мм"),
    "км": (Decimal(1000000), "мм"),
    "г": (Decimal(1), "г"),
    "кг": (Decimal(1000), "г"),
}
ALIASES = {"стеллажа": "стеллаж", "шкафа": "шкаф", "стола": "стол", "стойки": "стойка"}


def _rows(document: ParsedDocument):
    rows: dict[tuple[int, int], list[DocumentBlock]] = {}
    for block in document.blocks:
        if block.kind == "table_cell":
            loc = block.locator
            rows.setdefault((loc.table_index, loc.row_index), []).append(block)
    seen = set()
    for block in document.blocks:
        if block.kind == "paragraph":
            yield [block]
        else:
            key = (block.locator.table_index, block.locator.row_index)
            if key not in seen:
                seen.add(key)
                yield sorted(rows[key], key=lambda b: b.locator.column_index)


def check_numeric(document: ParsedDocument) -> list[Finding]:
    """Compare exact values only; never infer objects from neighbouring table rows.

    Model codes provide explicit identity. Generic object names are tentative:
    without a procurement identifier a human must confirm they denote one item.
    Separate explicit positions/lots are never compared.
    """
    facts = defaultdict(list)
    position = ""
    for blocks in _rows(document):
        text = " ".join(b.text for b in blocks).lower().replace("ё", "е")
        positions = POSITION.findall(text)
        heading_positions = POSITION.findall(" ".join(blocks[0].section_path).lower())
        if len(set(positions)) > 1:
            continue
        if positions and blocks[0].kind == "paragraph":
            position = positions[0]
        scope = (
            positions[0]
            if positions
            else (heading_positions[-1] if heading_positions else position)
        )
        models = sorted(set(MODEL.findall(text)))
        objects = sorted(set(ALIASES.get(x, x) for x in OBJECT.findall(text)))
        if len(models) > 1 or len(objects) > 1:
            continue
        # No object identity -> no finding, even when numbers differ.
        if not models and not objects:
            continue
        identity = models[0] if models else objects[0]
        status = "confirmed" if models else "needs_review"
        # Generic names are scoped to their section to avoid merging separate goods.
        section = tuple(blocks[0].section_path[:1] if models else blocks[0].section_path)
        if re.search(r"минималь|максималь|примерно|около|допуск|не менее|не более", text):
            continue
        properties = list(PROPERTY.finditer(text))
        for i, prop in enumerate(properties):
            segment = text[
                prop.end() : properties[i + 1].start() if i + 1 < len(properties) else None
            ]
            # Only exact scalar requirements; bounds, tolerances, ranges are unsupported.
            if re.search(r"не\s|от\s|до\s|[<>≤≥±%]|\d\s*[-–—]\s*\d", segment):
                continue
            values = list(VALUE.finditer(segment))
            if len(values) != 1:
                continue
            value = values[0]
            # Reject component qualifiers (e.g. height of a shelf vs entire rack).
            qualifier = segment[: value.start()]
            qualifier = MODEL.sub("", qualifier)
            qualifier = OBJECT.sub("", qualifier)
            qualifier = re.sub(r"металлическ(?:ого|ий)|[\s:—–,;=-]", "", qualifier)
            if qualifier:
                continue
            factor, unit = UNITS[value[2]]
            attribute = "масса" if prop[1] == "вес" else prop[1]
            if (attribute == "масса") != (unit == "г"):
                continue
            number = Decimal(value[1].replace(",", ".")) * factor
            evidence = [
                Evidence(
                    block_id=b.block_id, locator=b.locator, text=b.text, start=0, end=len(b.text)
                )
                for b in blocks
                if b.text
            ]
            facts[(scope, section, identity, attribute, unit, status)].append((number, evidence))
    findings = []
    for (_, _, identity, attribute, unit, status), entries in facts.items():
        for (left, evidence_left), (right, evidence_right) in combinations(entries, 2):
            if left == right:
                continue
            evidence = {e.block_id: e for e in evidence_left + evidence_right}
            findings.append(
                Finding(
                    status=status,
                    explanation=(
                        f"{identity}: {attribute} — {left.normalize():f} {unit} и "
                        f"{right.normalize():f} {unit}. "
                        + (
                            "Указаны разные точные значения."
                            if status == "confirmed"
                            else "Требуется подтвердить принадлежность одному объекту."
                        )
                    ),
                    evidence=list(evidence.values()),
                    recommendation="Уточнить объект и согласовать единое значение характеристики.",
                )
            )
    from tz_auditor.checks.lifts import check_lifts

    return findings + check_lifts(document, _rows(document))
