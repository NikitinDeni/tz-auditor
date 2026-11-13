from itertools import product

import pytest
from pydantic import ValidationError

from tz_auditor.domain.documents import DocumentBlock, ParsedDocument, SourceLocator


@pytest.mark.parametrize("present", list(product((False, True), repeat=4)))
def test_locator_accepts_exactly_two_coordinate_forms(present: tuple[bool, ...]) -> None:
    fields = ("paragraph_index", "table_index", "row_index", "column_index")
    values = {field: 0 if active else None for field, active in zip(fields, present, strict=True)}
    if present in ((True, False, False, False), (False, True, True, True)):
        assert SourceLocator(**values).model_dump() == values
    else:
        with pytest.raises(ValidationError):
            SourceLocator(**values)


@pytest.mark.parametrize("field", ["paragraph_index", "table_index", "row_index", "column_index"])
@pytest.mark.parametrize("invalid", [-1, 1.5, True, "0"])
def test_locator_rejects_invalid_indices(field: str, invalid: object) -> None:
    values = {"table_index": 0, "row_index": 0, "column_index": 0}
    if field == "paragraph_index":
        values = {}
    values[field] = invalid
    with pytest.raises(ValidationError):
        SourceLocator(**values)


def test_block_kind_must_match_locator() -> None:
    with pytest.raises(ValidationError, match="kind"):
        DocumentBlock(
            block_id="b000001",
            order=0,
            kind="table_cell",
            text="",
            section_path=[],
            locator=SourceLocator(paragraph_index=0),
        )


def test_cell_cannot_define_heading() -> None:
    with pytest.raises(ValidationError, match="heading"):
        DocumentBlock(
            block_id="b000001",
            order=0,
            kind="table_cell",
            text="",
            section_path=[],
            heading_level=1,
            locator=SourceLocator(table_index=0, row_index=0, column_index=0),
        )


def test_contract_roundtrip_with_defaults() -> None:
    block = DocumentBlock(
        block_id="b000001",
        order=0,
        kind="paragraph",
        text="Тест ±2 °C",
        section_path=[],
        locator=SourceLocator(paragraph_index=0),
    )
    document = ParsedDocument(
        document_id="synthetic-id", filename="тест.docx", blocks=[block], warnings=[]
    )
    assert ParsedDocument.model_validate_json(document.model_dump_json()) == document
    assert block.heading_level is None
