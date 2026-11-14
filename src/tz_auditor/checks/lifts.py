"""Lift requirements extracted from paragraphs and characteristic/value rows.

Identity is explicit when numbered/named, or when a document describes one new
lift without evidence of other lifts. Ambiguous table assignments require review.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

from tz_auditor.domain.documents import DocumentBlock, ParsedDocument

LIFT = re.compile(r"\bлифт(?:а|у|ом|е|ы|ов)?\b")
IDENTITY = re.compile(
    r"\bлифт(?:а|у|ом|е)?\s*(?:№\s*(\d[\w-]*|[a-zа-я]{1,8}-\d+)|([a-zа-я]{1,8}-\d+))"
)
ATTRIBUTE = re.compile(
    r"\b(грузоподъемност(?:ь|ью|и)|высота|ширина|глубина|длина|диаметр|скорость)\b"
)
NUMBER = r"\d+(?:[.,]\d+)?"
UNIT = r"(?:мм/с|см/с|м/с|мм|см|км|м|кг|г|т)"
MEASURE = re.compile(rf"(?<![\w.,+-])({NUMBER})\s*({UNIT})(?![\w/])")
UNIT_ONLY = re.compile(rf"\b({UNIT})(?![\w/])")
TYPES = re.compile(r"\b(грузов(?:ой|ого|ым)|пассажирск(?:ий|ого|им))\b")
UNITS = {
    "мм": ("мм", "1"),
    "см": ("мм", "10"),
    "м": ("мм", "1000"),
    "км": ("мм", "1000000"),
    "г": ("кг", ".001"),
    "кг": ("кг", "1"),
    "т": ("кг", "1000"),
    "м/с": ("м/с", "1"),
    "см/с": ("м/с", ".01"),
    "мм/с": ("м/с", ".001"),
}
UNSUPPORTED = re.compile(
    r"не менее|не более|минималь|максималь|допуск|примерно|около|[<>≤≥±%]|"
    r"\bот\s+\d|\bдо\s+\d|\d\s*[-–—]\s*\d"
)


def normalized(text: str) -> str:
    return text.lower().replace("ё", "е").replace("\xa0", " ")


def identities(text: str) -> set[str]:
    return {a or b for a, b in IDENTITY.findall(text)}


@dataclass
class Requirement:
    identity: str
    attribute: str
    value: Decimal | str
    unit: str
    certain: bool
    blocks: list[DocumentBlock]


def extract_lift_requirements(document: ParsedDocument, rows) -> list[Requirement]:
    groups = list(rows)
    all_text = normalized(" ".join(b.text for b in document.blocks))
    if not LIFT.search(all_text):
        return []
    ids = identities(all_text)
    multiple = (
        bool(re.search(r"\b(?:два|двух|три|несколько|\d+)\s+лифт|\bлифт(?:ы|ов)\b", all_text))
        or len(ids) > 1
    )
    old = bool(
        re.search(r"\b(?:стар\w*|существующ\w*|демонтируем\w*)\s+(?:\w+\s+){0,2}лифт", all_text)
    )
    singleton = not multiple and not old
    # Context maps tables/sections, never inherits an arbitrary model from a prior row.
    table_ids: dict[int, set[str]] = {}
    for blocks in groups:
        if blocks[0].kind == "table_cell":
            context = normalized(" ".join(b.text for b in blocks))
            table_ids.setdefault(blocks[0].locator.table_index, set()).update(identities(context))
    facts = []
    for blocks in groups:
        text = normalized(" ".join(b.text for b in blocks))
        heading = normalized(" ".join(blocks[0].section_path))
        direct_ids = identities(text)
        context_ids = identities(heading)
        if blocks[0].kind == "table_cell":
            context_ids |= table_ids[blocks[0].locator.table_index]
        if len(direct_ids) > 1:
            continue
        applicable = direct_ids or context_ids
        if len(applicable) == 1:
            identity = next(iter(applicable))
            certain = True
        elif singleton:
            identity = next(iter(ids)) if len(ids) == 1 else "единственный лифт"
            certain = True
        else:
            identity, certain = "неустановленный лифт", False
        if blocks[0].heading_level is not None:
            continue
        # Skip old equipment values; do not compare replacement with dismantled equipment.
        if re.search(r"\b(?:стар\w*|существующ\w*|демонтируем\w*)\b", text):
            continue
        if not LIFT.search(text + " " + heading) and blocks[0].kind != "table_cell":
            continue
        props = list(ATTRIBUTE.finditer(text))
        for i, match in enumerate(props):
            attr = match[1]
            attr = "грузоподъемность" if attr.startswith("грузоподъемност") else attr
            segment = text[match.end() : props[i + 1].start() if i + 1 < len(props) else None]
            if UNSUPPORTED.search(text):
                continue
            # Preserve component distinctions (e.g. cabin height vs door height).
            component = re.match(r"\s+(кабины|двери|шахты|проема)\b", segment)
            key = attr + (" " + component[1] if component else "")
            measures = MEASURE.findall(segment)
            if len(measures) == 1:
                number, unit = measures[0]
            elif not measures and blocks[0].kind == "table_cell":
                labels = [b for b in blocks if ATTRIBUTE.search(normalized(b.text))]
                numeric = (
                    [
                        normalized(b.text).strip()
                        for b in blocks
                        if re.fullmatch(NUMBER, normalized(b.text).strip())
                        and b.locator.column_index > labels[0].locator.column_index
                    ]
                    if labels
                    else []
                )
                units = UNIT_ONLY.findall(normalized(labels[0].text)) if labels else []
                if len(numeric) != 1 or len(units) != 1:
                    continue
                number, unit = numeric[0], units[0]
            else:
                continue
            base, factor = UNITS[unit]
            expected = "кг" if attr == "грузоподъемность" else "м/с" if attr == "скорость" else "мм"
            if base != expected:
                continue
            facts.append(
                Requirement(
                    identity,
                    key,
                    Decimal(number.replace(",", ".")) * Decimal(factor),
                    base,
                    certain,
                    blocks,
                )
            )
        type_values = TYPES.findall(text)
        if len(type_values) == 1 and (LIFT.search(text) or "тип" in text):
            value = "грузовой" if type_values[0].startswith("грузов") else "пассажирский"
            facts.append(Requirement(identity, "тип лифта", value, "", certain, blocks))
    return facts


def check_lifts(document: ParsedDocument, rows):
    # Import here to keep the public Finding contract in its existing module.
    from tz_auditor.checks import Evidence, Finding

    facts = extract_lift_requirements(document, rows)
    findings = []
    grouped = {}
    for left, right in combinations(facts, 2):
        if left.attribute != right.attribute or left.value == right.value:
            continue
        if left.certain and right.certain and left.identity != right.identity:
            continue
        if {b.block_id for b in left.blocks} == {b.block_id for b in right.blocks}:
            continue
        confirmed = left.certain and right.certain and left.identity == right.identity
        sources = {b.block_id: b for b in left.blocks + right.blocks if b.text}
        finding = Finding(
            category="categorical_contradiction"
            if left.attribute == "тип лифта"
            else "numeric_contradiction",
            status="confirmed" if confirmed else "needs_review",
            explanation=(
                f"{left.attribute}: {left.value} {left.unit} / "
                f"{right.value} {right.unit}. "
                + (
                    f"Один объект: {left.identity}; значения несовместимы."
                    if confirmed
                    else "Принадлежность одному лифту не доказана; требуется проверка объекта."
                )
            ),
            evidence=[
                Evidence(
                    block_id=b.block_id,
                    locator=b.locator,
                    text=b.text,
                    start=0,
                    end=len(b.text),
                )
                for b in sources.values()
            ],
            recommendation=(f"Уточнить принадлежность требований и согласовать {left.attribute}."),
        )
        key = (left.attribute, tuple(sorted((left.identity, right.identity))), confirmed)
        if key not in grouped:
            grouped[key] = finding
            findings.append(finding)
        else:
            previous = grouped[key]
            previous.evidence = list(
                {e.block_id: e for e in previous.evidence + finding.evidence}.values()
            )
            if finding.explanation not in previous.explanation:
                previous.explanation += " " + finding.explanation
    return findings
