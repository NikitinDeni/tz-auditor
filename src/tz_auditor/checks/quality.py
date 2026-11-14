"""Small, local quality heuristics. All findings require human review."""

import re

from tz_auditor.domain.documents import ParsedDocument

ACCESSORIES = r"кабел\w*|зажим\w*|держател\w*|адаптер\w*|щуп\w*|переходник\w*"
INSTRUMENTS = [
    r"осциллограф\w*",
    r"мультиметр\w*",
    r"источник\w* питания",
    r"весы",
    r"вольтметр\w*",
    r"генератор\w* сигнал\w*",
]
BOUND = re.compile(
    r"(?:не (?:более|позднее)|в течение|максимум|до)\s*\d+\s*"
    r"(?:(?:календарных|рабочих)\s*)?(?:дн|день|дня|час|сут|недел|месяц)"
)


def check_quality(document: ParsedDocument, rows):
    from tz_auditor.checks import Evidence, Finding

    groups = list(rows)
    findings = []
    seen = {}

    def add(category, explanation, recommendation, blocks):
        # Identical wording repeated in one section is one issue with all sources.
        key = (
            category,
            tuple(blocks[0].section_path),
            explanation,
            tuple(b.text for b in blocks if not b.text.strip().isdigit()),
        )
        evidence = [
            Evidence(block_id=b.block_id, locator=b.locator, text=b.text, start=0, end=len(b.text))
            for b in blocks
            if b.text
        ]
        if key in seen:
            finding = seen[key]
            finding.evidence = list({e.block_id: e for e in finding.evidence + evidence}.values())
        else:
            finding = Finding(
                category=category,
                status="needs_review",
                explanation=explanation,
                evidence=evidence,
                recommendation=recommendation,
            )
            seen[key] = finding
            findings.append(finding)

    for index, blocks in enumerate(groups):
        text = " ".join(b.text for b in blocks).lower().replace("ё", "е")
        if blocks[0].heading_level is not None:
            continue
        for sentence in re.split(r"[.!?;\n]+", text):
            if (
                re.search(r"срок\w*[^.!?]{0,100}(?:согласован|согласов|соглашени)", sentence)
                and re.search(r"устран|исправ|выполн|постав|ремонт|обязатель", sentence)
                and not BOUND.search(sentence)
            ):
                add(
                    "ambiguous_performance_deadline",
                    "Максимальный срок исполнения обязательства не определён: " + sentence.strip(),
                    "Указать предельный срок и событие, от которого он исчисляется.",
                    blocks,
                )
            optional = re.search(r"по возможности|при наличии возможности|по желанию", sentence)
            if optional and re.search(
                r"интерфейс|индикаци|характеристик|точност|диапазон|"
                r"функци|передач\w* данных",
                sentence,
            ):
                add(
                    "non_verifiable_requirement",
                    "Обязательность технического требования не определена: " + sentence.strip(),
                    (
                        "Указать проверяемое обязательное требование "
                        "или явно исключить его из приёмки."
                    ),
                    blocks,
                )
        if re.search(r"комплект\w* поставки|состав\w* поставки", text):
            missing = []
            for item in re.finditer(ACCESSORIES, text):
                prefix, suffix = (
                    text[max(0, item.start() - 30) : item.start()],
                    text[item.end() : item.end() + 25],
                )
                quantity = re.search(
                    r"(?:\d+|один|одна|одно|два|две|три|четыре|пять)\s*$", prefix
                ) or re.match(r"\s*[-—:–]?\s*\d+\s*(?:шт|ед|компл|пар|[,;]|$)", suffix)
                if not quantity and not re.search(
                    r"по (?:одному|одной|\d+)\s+кажд|кажд\w*\s+по\s*\d", text
                ):
                    missing.append(item[0])
            if missing and len(list(re.finditer(ACCESSORIES, text))) >= 2:
                add(
                    "incomplete_bundle_specification",
                    "Не указан количественный состав принадлежностей: " + ", ".join(missing) + ".",
                    "Указать количество каждого вида принадлежностей и параметры комплектации.",
                    blocks,
                )
        devices = [pattern for pattern in INSTRUMENTS if re.search(pattern, text)]
        if "комплект" in text and len(devices) >= 2:
            add(
                "heterogeneous_bundle_review",
                "В одном комплекте перечислены функционально различные самостоятельные приборы: "
                + text.strip(),
                "Проверить обоснованность объединения и детализировать состав, количество "
                "и характеристики приборов. Само объединение не означает нарушения закона.",
                blocks,
            )
        # Acceptance context is bounded to nearby rows/paragraphs in the same section.
        if re.search(r"программное обеспечение|\bпо\b", text) and re.search(
            r"предоставля\w*\s+(?:лаборатор|заказчик|треть)|"
            r"(?:лаборатор\w*|заказчик\w*|треть\w* сторон\w*)\s+предоставля",
            text,
        ):
            nearby = [
                b
                for row in groups[max(0, index - 2) : index + 3]
                for b in row
                if b.section_path == blocks[0].section_path and b.heading_level is None
            ]
            context = " ".join(b.text for b in nearby).lower().replace("ё", "е")
            assigned = re.search(
                r"поставщик[^.!?]{0,100}(?:настр|совместим)", context
            ) and re.search(r"лаборатор|заказчик|треть", context)
            if (
                re.search(r"приемк\w*[^.!?]{0,120}проверк|проверк[^.!?]{0,120}приемк", context)
                and not assigned
            ):
                add(
                    "acceptance_dependency_review",
                    "Приёмка зависит от ПО третьей стороны; распределение обязанностей "
                    "по совместимости и настройке не установлено в ближайшем контексте.",
                    "Уточнить ответственность за предоставление, совместимость и настройку ПО, "
                    "порядок проверки и действия при его недоступности.",
                    nearby,
                )
    return findings
