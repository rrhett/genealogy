"""Canonical Pydantic schemas, genealogical date parser, and Markdown frontmatter I/O."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Identifier patterns matching AGENTS.md
INDIVIDUAL_ID_RE = re.compile(r"^I-[A-Z0-9]+(?:-[A-Z0-9]+)*-(?:\d{4}|UNKNOWN)(?:-\d{2})?$")
SOURCE_ID_RE = re.compile(r"^S-(?:\d{4}|UNDATED)-[A-Z0-9]+(?:-[A-Z0-9]+)*$")

# Genealogical date patterns:
#   YYYY, YYYY-MM, YYYY-MM-DD
#   abt|bef|aft|est|cal <iso_date>
#   bet <iso_date> and <iso_date>
_ISO_PART = r"(?P<y>\d{4})(?:-(?P<m>\d{2})(?:-(?P<d>\d{2}))?)?"
_SINGLE_DATE_RE = re.compile(
    rf"^(?:(?P<qual>abt|bef|aft|est|cal)\s+)?{_ISO_PART}$",
    re.IGNORECASE,
)
_RANGE_DATE_RE = re.compile(
    r"^bet\s+(?P<start>\d{4}(?:-\d{2}(?:-\d{2})?)?)\s+and\s+(?P<end>\d{4}(?:-\d{2}(?:-\d{2})?)?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedDate:
    """Structured representation of a genealogical date string."""

    raw: str
    qualifier: str | None  # None | abt | bef | aft | est | cal | bet
    year_min: int | None
    year_max: int | None
    month: int | None = None
    day: int | None = None
    end_year: int | None = None
    end_month: int | None = None
    end_day: int | None = None

    @property
    def sort_key(self) -> tuple[int, int, int]:
        y = self.year_min if self.year_min is not None else (self.year_max or 9999)
        m = self.month or 0
        d = self.day or 0
        return (y, m, d)

    def to_gedcom(self) -> str:
        """Convert to standard GEDCOM 5.5.1 / 7.0 date format."""
        months = [
            "",
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        ]

        def _fmt_part(y: int, m: int | None, d: int | None) -> str:
            if m and d:
                return f"{d} {months[m]} {y}"
            if m:
                return f"{months[m]} {y}"
            return str(y)

        if self.qualifier == "bet" and self.year_min is not None and self.end_year is not None:
            start_s = _fmt_part(self.year_min, self.month, self.day)
            end_s = _fmt_part(self.end_year, self.end_month, self.end_day)
            return f"BET {start_s} AND {end_s}"

        y = self.year_min if self.year_min is not None else self.year_max
        if y is None:
            return ""
        core = _fmt_part(y, self.month, self.day)
        if self.qualifier:
            return f"{self.qualifier.upper()} {core}"
        return core


def _validate_ymd(y: int, m: int | None, d: int | None, raw: str) -> None:
    if y < 1 or y > 2100:
        raise ValueError(f"Year out of valid range (1-2100) in date '{raw}'")
    if m is not None:
        if not 1 <= m <= 12:
            raise ValueError(f"Invalid month '{m}' in date '{raw}'")
        if d is not None:
            max_day = calendar.monthrange(y, m)[1]
            if not 1 <= d <= max_day:
                raise ValueError(f"Invalid day '{d}' for {y:04d}-{m:02d} in date '{raw}'")


def parse_genealogy_date(value: str | None) -> ParsedDate | None:
    """Parse and validate a genealogical date string.

    Returns None for empty/None or 'undated'. Raises ValueError on invalid format.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw.lower() == "undated":
        return None

    range_match = _RANGE_DATE_RE.match(raw)
    if range_match:
        start_s = range_match.group("start")
        end_s = range_match.group("end")
        s_parts = [int(p) for p in start_s.split("-")]
        e_parts = [int(p) for p in end_s.split("-")]
        sy, sm, sd = s_parts[0], (s_parts[1] if len(s_parts) > 1 else None), (s_parts[2] if len(s_parts) > 2 else None)
        ey, em, ed = e_parts[0], (e_parts[1] if len(e_parts) > 1 else None), (e_parts[2] if len(e_parts) > 2 else None)
        _validate_ymd(sy, sm, sd, raw)
        _validate_ymd(ey, em, ed, raw)
        if (ey, em or 12, ed or 31) < (sy, sm or 1, sd or 1):
            raise ValueError(f"Range end precedes start in date '{raw}'")
        return ParsedDate(
            raw=raw,
            qualifier="bet",
            year_min=sy,
            year_max=ey,
            month=sm,
            day=sd,
            end_year=ey,
            end_month=em,
            end_day=ed,
        )

    single_match = _SINGLE_DATE_RE.match(raw)
    if single_match:
        qual = single_match.group("qual").lower() if single_match.group("qual") else None
        y = int(single_match.group("y"))
        m = int(single_match.group("m")) if single_match.group("m") else None
        d = int(single_match.group("d")) if single_match.group("d") else None
        _validate_ymd(y, m, d, raw)
        if qual == "bef":
            return ParsedDate(raw=raw, qualifier=qual, year_min=None, year_max=y, month=m, day=d)
        if qual == "aft":
            return ParsedDate(raw=raw, qualifier=qual, year_min=y, year_max=None, month=m, day=d)
        return ParsedDate(raw=raw, qualifier=qual, year_min=y, year_max=y, month=m, day=d)

    raise ValueError(
        f"Invalid genealogical date '{raw}'. Expected YYYY, YYYY-MM, YYYY-MM-DD, "
        "'abt|bef|aft|est|cal <date>', or 'bet <date> and <date>'."
    )


def _coerce_date_str(v: Any) -> str | None:
    if v is None:
        return None
    # PyYAML may parse unquoted YYYY-MM-DD or YYYY as datetime.date or int
    if hasattr(v, "isoformat"):
        return v.isoformat()
    s = str(v).strip()
    return s if s else None


class VitalEvent(BaseModel):
    """Birth or death event on an Individual."""

    model_config = ConfigDict(extra="forbid")

    date: str | None = None
    place: str | None = None
    sources: list[str] = Field(default_factory=list)

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> str | None:
        s = _coerce_date_str(v)
        if s is not None:
            parse_genealogy_date(s)
        return s

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not SOURCE_ID_RE.match(sid):
                raise ValueError(f"Invalid source ID '{sid}'")
        return v


EventType = Literal[
    "residence",
    "occupation",
    "military",
    "immigration",
    "naturalization",
    "burial",
    "baptism",
    "census",
    "probate",
    "land",
    "education",
    "marriage",
    "other",
]


class LifeEvent(BaseModel):
    """Chronological event beyond birth/death (e.g., census residence, military draft, land)."""

    model_config = ConfigDict(extra="forbid")

    type: EventType
    date: str | None = None
    place: str | None = None
    tract: str | None = None
    description: str | None = None
    sources: list[str] = Field(default_factory=list)

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> str | None:
        s = _coerce_date_str(v)
        if s is not None:
            parse_genealogy_date(s)
        return s

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not SOURCE_ID_RE.match(sid):
                raise ValueError(f"Invalid source ID '{sid}'")
        return v


class IndividualFrontmatter(BaseModel):
    """YAML frontmatter schema for data/individuals/<ID>.md."""

    model_config = ConfigDict(extra="forbid")

    id: str
    primary_name: str = Field(min_length=1)
    given_name: str | None = None
    surname: str | None = None
    maternal_surname: str | None = None
    name_variants: list[str] = Field(default_factory=list)
    sex: Literal["M", "F", "X", "U"] = "U"
    living: bool = False
    birth: VitalEvent | None = None
    death: VitalEvent | None = None
    events: list[LifeEvent] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    custom: dict[str, Any] = Field(default_factory=dict)
    status: Literal["confirmed", "tentative", "hypothetical", "excluded"] = "tentative"

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not INDIVIDUAL_ID_RE.match(v):
            raise ValueError(
                f"Invalid individual ID '{v}'. Expected I-<SURNAME>-<GIVEN>-<YEAR|UNKNOWN>[-NN]."
            )
        return v

    def all_source_ids(self) -> set[str]:
        ids: set[str] = set()
        if self.birth:
            ids.update(self.birth.sources)
        if self.death:
            ids.update(self.death.sources)
        for ev in self.events:
            ids.update(ev.sources)
        return ids


RecordType = Literal[
    "census",
    "vital",
    "church",
    "military",
    "newspaper",
    "cemetery",
    "probate",
    "land",
    "immigration",
    "compiled",
    "oral",
    "other",
]

SourceType = Literal["original", "derivative", "authored"]
InformationType = Literal["primary", "secondary", "mixed", "undetermined"]
SourceRelevance = Literal["relevant", "pending", "excluded"]
LinkProvider = Literal[
    "ancestry",
    "familysearch",
    "newspapers",
    "findagrave",
    "fold3",
    "nara",
    "loc",
    "wikitree",
    "state_archive",
    "personal_collection",
    "other",
]


class SourceLink(BaseModel):
    """Online or archival provenance link for a source/artifact (including authenticated links)."""

    model_config = ConfigDict(extra="forbid")

    provider: LinkProvider = "other"
    url: str = Field(min_length=1)
    accessed: str | None = None
    requires_auth: bool = False
    note: str | None = None

    @field_validator("accessed", mode="before")
    @classmethod
    def validate_accessed(cls, v: Any) -> str | None:
        s = _coerce_date_str(v)
        if s is not None:
            parse_genealogy_date(s)
        return s


class SourceFrontmatter(BaseModel):
    """YAML frontmatter schema for data/sources/<ID>.md."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=1)
    record_type: RecordType
    date: str | None = None
    jurisdiction: str | None = None
    repository: str | None = None
    url: str | None = None
    links: list[SourceLink] = Field(default_factory=list)
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    source_type: SourceType | None = None
    information_type: InformationType | None = None
    relevance: SourceRelevance = "relevant"
    excluded_for: list[str] = Field(default_factory=list)
    exclusion_reason: str | None = None
    individuals_cited: list[str] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not SOURCE_ID_RE.match(v):
            raise ValueError(
                f"Invalid source ID '{v}'. Expected S-<YEAR|UNDATED>-<TYPE>-<SLUG>."
            )
        return v

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v: Any) -> str | None:
        s = _coerce_date_str(v)
        if s is not None:
            parse_genealogy_date(s)
        return s

    @field_validator("artifact_sha256")
    @classmethod
    def validate_sha256(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v_clean = v.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v_clean):
            raise ValueError("artifact_sha256 must be a 64-character lowercase hex SHA-256 digest")
        return v_clean

    @field_validator("individuals_cited", "excluded_for")
    @classmethod
    def validate_individual_id_lists(cls, v: list[str]) -> list[str]:
        for iid in v:
            if not INDIVIDUAL_ID_RE.match(iid):
                raise ValueError(f"Invalid individual ID '{iid}'")
        return v


RelationshipType = Literal["parent_child", "marriage", "sibling", "fan_associate"]
ParentChildSubtype = Literal["biological", "adoptive", "step", "foster", "unknown"]
ConfidenceLevel = Literal["proven", "probable", "speculative"]
ConstraintType = Literal["common_ancestor", "consanguinity", "affinity"]


class Relationship(BaseModel):
    """Single graph edge in data/relationships.yaml."""

    model_config = ConfigDict(extra="forbid")

    type: RelationshipType
    person_1: str
    person_2: str
    subtype: ParentChildSubtype | None = None
    date: str | None = None
    end_date: str | None = None
    place: str | None = None
    confidence: ConfidenceLevel = "speculative"
    sources: list[str] = Field(default_factory=list)
    notes: str = ""
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("person_1", "person_2")
    @classmethod
    def validate_person_id(cls, v: str) -> str:
        if not INDIVIDUAL_ID_RE.match(v):
            raise ValueError(f"Invalid individual ID '{v}'")
        return v

    @field_validator("date", "end_date", mode="before")
    @classmethod
    def validate_dates(cls, v: Any) -> str | None:
        s = _coerce_date_str(v)
        if s is not None:
            parse_genealogy_date(s)
        return s

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not SOURCE_ID_RE.match(sid):
                raise ValueError(f"Invalid source ID '{sid}'")
        return v

    @model_validator(mode="after")
    def validate_edge(self) -> Relationship:
        if self.person_1 == self.person_2:
            raise ValueError(f"Self-referential relationship is invalid ({self.person_1})")
        if self.subtype is not None and self.type != "parent_child":
            raise ValueError("subtype is only valid for parent_child relationships")
        return self


class RelationshipConstraint(BaseModel):
    """Deductive relationship constraint (e.g., from marriage dispensations / dispensas).

    Formalizes partial genealogical knowledge such as: 'one grandparent of person_1
    (steps_1=2) is a great-grandparent of person_2 (steps_2=3)'.
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    type: ConstraintType = "common_ancestor"
    person_1: str
    person_2: str
    steps_1: int = Field(ge=0, le=15)
    steps_2: int = Field(ge=0, le=15)
    symmetric: bool = False
    common_ancestor_sex: Literal["M", "F"] | None = None
    common_ancestor_surname: str | None = None
    confidence: ConfidenceLevel = "probable"
    sources: list[str] = Field(default_factory=list)
    notes: str = ""
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("person_1", "person_2")
    @classmethod
    def validate_person_id(cls, v: str) -> str:
        if not INDIVIDUAL_ID_RE.match(v):
            raise ValueError(f"Invalid individual ID '{v}'")
        return v

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        for sid in v:
            if not SOURCE_ID_RE.match(sid):
                raise ValueError(f"Invalid source ID '{sid}'")
        return v

    @model_validator(mode="after")
    def validate_constraint(self) -> RelationshipConstraint:
        if self.person_1 == self.person_2:
            raise ValueError(f"Self-referential constraint is invalid ({self.person_1})")
        if self.steps_1 + self.steps_2 < 1:
            raise ValueError("steps_1 + steps_2 must be at least 1")
        return self


class RelationshipsFile(BaseModel):
    """Top-level schema for data/relationships.yaml."""

    model_config = ConfigDict(extra="forbid")

    relationships: list[Relationship] = Field(default_factory=list)
    constraints: list[RelationshipConstraint] = Field(default_factory=list)


# --- Markdown Frontmatter Helpers ---

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a Markdown file with YAML frontmatter into (metadata_dict, body_markdown)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("Missing or malformed YAML frontmatter delimited by '---'")
    yaml_str, body = match.group(1), match.group(2)
    data = yaml.safe_load(yaml_str)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("YAML frontmatter must be a mapping/dictionary")
    return data, body


def render_frontmatter(metadata: dict[str, Any], body: str) -> str:
    """Serialize metadata dict + markdown body into a Markdown file with YAML frontmatter."""
    yaml_text = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    body_clean = body.lstrip("\n")
    if body_clean and not body_clean.endswith("\n"):
        body_clean += "\n"
    return f"---\n{yaml_text}\n---\n{body_clean}"


def read_markdown_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Read and split a Markdown file from disk."""
    return split_frontmatter(path.read_text(encoding="utf-8"))
