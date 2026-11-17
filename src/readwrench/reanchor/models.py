"""Versioned review contract. A confidence score never constitutes approval."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

Finite = Annotated[float, Field(allow_inf_nan=False)]
Score = Annotated[Finite, Field(ge=0, le=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Asset(Contract):
    path: str
    sha256: Sha256


class InputPDF(Contract):
    path: str
    sha256: Sha256
    page_count: Annotated[int, Field(ge=1)]


class Evidence(Contract):
    text: Score
    context: Score
    layout: Score
    page_proximity: Score
    length: Score
    page_delta: int
    method: Literal["normalized-exact", "fuzzy"]


class Candidate(Contract):
    id: str
    page_index: Annotated[int, Field(ge=0)]
    quads: list[list[Finite]]
    matched_text: str
    context_before: str
    context_after: str
    score: Score
    evidence: Evidence
    image: Asset | None = None

    @model_validator(mode="after")
    def valid_quads(self):
        if not self.quads or any(len(q) != 8 for q in self.quads):
            raise ValueError("a candidate requires one or more eight-coordinate quads")
        return self


class Annotation(Contract):
    id: str
    source_xref: int
    page_index: Annotated[int, Field(ge=0)]
    kind: str
    selected_text: str
    context_before: str = ""
    context_after: str = ""
    anchor_basis: Literal["selected text", "nearby text", "unavailable"]
    source_rect: list[Finite]
    info: dict[str, str]
    colors: dict[str, list[Finite]]
    opacity: Finite
    flags: int
    supported: bool
    warnings: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    candidate_count: int = 0
    image: Asset | None = None
    page_image: Asset | None = None


class Proposal(Contract):
    schema_version: Literal[1] = 1
    proposal_id: str = ""
    tool_version: str
    upstream_version: Literal["0.2.2"] = "0.2.2"
    created_utc: str
    old_pdf: InputPDF
    new_pdf: InputPDF
    matching: dict[str, str | int | float]
    annotations: list[Annotation]
    warnings: list[str] = Field(default_factory=list)

    def fingerprint(self) -> str:
        data = self.model_dump(exclude={"proposal_id"})
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode()).hexdigest()


class Choice(Contract):
    annotation_id: str
    action: Literal["accept", "reject", "unresolved"]
    candidate_id: str | None = None
    note: str = ""

    @model_validator(mode="after")
    def candidate_only_when_accepted(self):
        if (self.action == "accept") != (self.candidate_id is not None):
            raise ValueError("only accepted choices must specify a candidate_id")
        return self


class Decisions(Contract):
    schema_version: Literal[1] = 1
    proposal_id: str
    confirmed: StrictBool = False
    choices: list[Choice] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_duplicates(self):
        ids = [choice.annotation_id for choice in self.choices]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate annotation decisions are not allowed")
        return self
