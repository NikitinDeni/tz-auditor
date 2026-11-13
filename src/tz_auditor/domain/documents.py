"""Data contracts for extracted document fragments."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

Index = Annotated[int, Field(ge=0, strict=True)]


class SourceLocator(BaseModel):
    """Zero-based address of a top-level paragraph or a table cell."""

    paragraph_index: Index | None = None
    table_index: Index | None = None
    row_index: Index | None = None
    column_index: Index | None = None

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        cell = (self.table_index, self.row_index, self.column_index)
        if self.paragraph_index is not None:
            if any(value is not None for value in cell):
                raise ValueError("Paragraph and table coordinates cannot be combined")
        elif not all(value is not None for value in cell):
            raise ValueError("Supply paragraph_index or all three table cell coordinates")
        return self


class DocumentBlock(BaseModel):
    """One fragment, in document order, with its section and source address."""

    block_id: str
    order: Index
    kind: Literal["paragraph", "table_cell"]
    text: str
    section_path: list[str]
    heading_level: Annotated[int, Field(ge=1, le=9, strict=True)] | None = None
    locator: SourceLocator

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        is_paragraph = self.locator.paragraph_index is not None
        if (self.kind == "paragraph") != is_paragraph:
            raise ValueError("Block kind must match its source coordinates")
        if self.kind == "table_cell" and self.heading_level is not None:
            raise ValueError("Table cells cannot be section headings")
        return self


class ParsedDocument(BaseModel):
    """Structured document, including explicit parser limitations."""

    document_id: str
    filename: str
    blocks: list[DocumentBlock]
    warnings: list[str]
