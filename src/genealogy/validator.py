"""Schema, referential integrity, graph, biological sanity, GPS, and privacy validator."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from genealogy.constraints import evaluate_constraint
from genealogy.loader import Dataset, load_dataset
from genealogy.schema import parse_genealogy_date

Severity = Literal["error", "warning"]


@dataclass
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


@dataclass
class ValidationReport:
    root: Path
    individuals_count: int
    sources_count: int
    relationships_count: int
    places_count: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def is_valid(self, strict: bool = False) -> bool:
        if self.errors:
            return False
        if strict and self.warnings:
            return False
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "counts": {
                "individuals": self.individuals_count,
                "sources": self.sources_count,
                "relationships": self.relationships_count,
                "places": self.places_count,
            },
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_lfs_pointer_sha256(path: Path) -> str | None:
    """If `path` is a Git LFS pointer file, return its `oid sha256:` hex digest; else None."""
    try:
        if path.stat().st_size > 1024:
            return None
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("version https://git-lfs.github.com/spec/v1"):
        return None
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            digest = line.split(":", 1)[1].strip().lower()
            if len(digest) == 64:
                return digest
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def validate_dataset(root: Path | str, *, skip_artifacts: bool = False) -> ValidationReport:
    """Run all validation checks against a genealogy workspace root."""
    ds = load_dataset(root)
    return validate_loaded_dataset(ds, skip_artifacts=skip_artifacts)


def validate_loaded_dataset(ds: Dataset, *, skip_artifacts: bool = False) -> ValidationReport:
    """Validate an already-loaded Dataset."""
    report = ValidationReport(
        root=ds.root,
        individuals_count=len(ds.individuals),
        sources_count=len(ds.sources),
        relationships_count=len(ds.relationships),
        places_count=len(ds.places),
    )

    def add_error(code: str, msg: str, path: Path | None = None) -> None:
        report.issues.append(
            ValidationIssue("error", code, msg, _rel(path, ds.root) if path else None)
        )

    def add_warning(code: str, msg: str, path: Path | None = None) -> None:
        report.issues.append(
            ValidationIssue("warning", code, msg, _rel(path, ds.root) if path else None)
        )

    # 1. Surface schema / load errors
    for err in ds.load_errors:
        add_error("SCHEMA_ERROR", err.message, err.path)

    rel_path = ds.data_dir / "relationships.yaml"

    # 2. Referential integrity: Individual -> Sources
    individual_cited_sources: dict[str, set[str]] = defaultdict(set)
    for iid, ind in ds.individuals.items():
        all_sids = ind.data.all_source_ids()
        individual_cited_sources[iid].update(all_sids)
        for sid in sorted(all_sids):
            if sid not in ds.sources:
                add_error(
                    "MISSING_SOURCE_REF",
                    f"Individual '{iid}' references unknown source '{sid}'",
                    ind.path,
                )
            elif ds.sources[sid].data.relevance == "excluded" and ind.data.status != "excluded":
                add_error(
                    "EXCLUDED_SOURCE_CITED",
                    f"Individual '{iid}' cites excluded/mistaken-lead source '{sid}'",
                    ind.path,
                )

        # Unsourced assertion warning (skip for excluded namesake profiles)
        if not all_sids and ind.data.status != "excluded":
            add_warning(
                "UNSOURCED_INDIVIDUAL",
                f"Individual '{iid}' has no source citations in birth, death, or events",
                ind.path,
            )

        for ref_pid in (
            [ind.data.birth.place_id] if ind.data.birth and ind.data.birth.place_id else []
        ) + (
            [ind.data.death.place_id] if ind.data.death and ind.data.death.place_id else []
        ) + [
            ev.place_id for ev in ind.data.events if ev.place_id
        ]:
            if ref_pid not in ds.places:
                add_error(
                    "MISSING_PLACE_REF",
                    f"Individual '{iid}' references unknown place '{ref_pid}'",
                    ind.path,
                )

        # Living person with death record conflict
        if ind.data.living and ind.data.death is not None:
            add_error(
                "LIVING_WITH_DEATH",
                f"Individual '{iid}' is marked living: true but has a death record",
                ind.path,
            )

        # Biological lifespan / birth-vs-death sanity
        b_date = parse_genealogy_date(ind.data.birth.date) if ind.data.birth else None
        d_date = parse_genealogy_date(ind.data.death.date) if ind.data.death else None
        if b_date and d_date:
            if b_date.year_min is not None and d_date.year_max is not None:
                if d_date.year_max < b_date.year_min:
                    add_error(
                        "DEATH_BEFORE_BIRTH",
                        f"Individual '{iid}' death ({d_date.raw}) precedes birth ({b_date.raw})",
                        ind.path,
                    )
            if b_date.year_max is not None and d_date.year_min is not None:
                age = d_date.year_min - b_date.year_max
                if age > 115:
                    add_warning(
                        "IMPLAUSIBLE_LIFESPAN",
                        f"Individual '{iid}' has implied lifespan of ~{age} years ({b_date.raw} to {d_date.raw})",
                        ind.path,
                    )

        # Events before birth or after death (excluding burial/probate after death)
        for ev in ind.data.events:
            if ev.suggested_label and ev.type != "other":
                add_warning(
                    "SUGGESTED_LABEL_ON_NON_OTHER",
                    f"Individual '{iid}' event '{ev.type}' sets suggested_label='{ev.suggested_label}', which is only intended for type='other'",
                    ind.path,
                )
            ev_date = parse_genealogy_date(ev.date)
            if not ev_date:
                continue
            if b_date and b_date.year_min is not None and ev_date.year_max is not None:
                if ev_date.year_max < b_date.year_min:
                    add_error(
                        "EVENT_BEFORE_BIRTH",
                        f"Individual '{iid}' event '{ev.type}' ({ev_date.raw}) precedes birth ({b_date.raw})",
                        ind.path,
                    )
            if (
                ev.type not in {"burial", "probate", "other"}
                and d_date
                and d_date.year_max is not None
                and ev_date.year_min is not None
            ):
                if ev_date.year_min > d_date.year_max:
                    add_warning(
                        "EVENT_AFTER_DEATH",
                        f"Individual '{iid}' event '{ev.type}' ({ev_date.raw}) occurs after death ({d_date.raw})",
                        ind.path,
                    )

        # GPS proof-standard check for confirmed individuals
        if ind.data.status == "confirmed":
            valid_sources = [ds.sources[s] for s in all_sids if s in ds.sources]
            non_compiled = [
                s
                for s in valid_sources
                if s.data.record_type != "compiled" and s.data.source_type != "authored"
            ]
            if len(all_sids) < 2:
                add_warning(
                    "GPS_CONFIRMED_UNDER_SOURCED",
                    f"Individual '{iid}' is status='confirmed' but cites fewer than 2 independent sources ({len(all_sids)})",
                    ind.path,
                )
            elif not non_compiled:
                add_warning(
                    "GPS_COMPILED_ONLY",
                    f"Individual '{iid}' is status='confirmed' using only compiled/authored sources",
                    ind.path,
                )

    # 2b. Referential integrity: Place -> Sources & Owners
    for pid, place in ds.places.items():
        place_sids = place.data.all_source_ids()
        for sid in sorted(place_sids):
            if sid not in ds.sources:
                add_error(
                    "MISSING_SOURCE_REF",
                    f"Place '{pid}' references unknown source '{sid}'",
                    place.path,
                )
            elif ds.sources[sid].data.relevance == "excluded" and place.data.status != "excluded":
                add_error(
                    "EXCLUDED_SOURCE_CITED",
                    f"Place '{pid}' cites excluded/mistaken-lead source '{sid}'",
                    place.path,
                )
        for person in place.data.people:
            if person.individual_id and person.individual_id not in ds.individuals:
                add_error(
                    "MISSING_INDIVIDUAL_REF",
                    f"Place '{pid}' person '{person.name}' references unknown individual '{person.individual_id}'",
                    place.path,
                )
        if not place_sids and place.data.status != "excluded":
            add_warning(
                "UNSOURCED_PLACE",
                f"Place '{pid}' has no source citations",
                place.path,
            )

    # 3. Referential integrity: Sources -> Individuals & Artifacts
    linked_artifacts: set[Path] = set()
    for sid, src in ds.sources.items():
        if src.data.place_id and src.data.place_id not in ds.places:
            add_error(
                "MISSING_PLACE_REF",
                f"Source '{sid}' references unknown place '{src.data.place_id}'",
                src.path,
            )
        for iid in src.data.individuals_cited:
            if iid not in ds.individuals:
                add_error(
                    "MISSING_INDIVIDUAL_REF",
                    f"Source '{sid}' cites unknown individual '{iid}'",
                    src.path,
                )

        for iid in src.data.excluded_for:
            if iid not in ds.individuals:
                add_error(
                    "MISSING_INDIVIDUAL_REF",
                    f"Source '{sid}' excluded_for references unknown individual '{iid}'",
                    src.path,
                )

        if src.data.relevance == "excluded" and not (src.data.exclusion_reason or "").strip():
            add_warning(
                "MISSING_EXCLUSION_REASON",
                f"Source '{sid}' is marked relevance='excluded' without an exclusion_reason",
                src.path,
            )

        if src.data.artifact_path and not skip_artifacts:
            art_file = ds.resolve_artifact_path(src.data.artifact_path)
            if not art_file.exists():
                add_error(
                    "MISSING_ARTIFACT",
                    f"Source '{sid}' references missing artifact '{src.data.artifact_path}'",
                    src.path,
                )
            else:
                linked_artifacts.add(art_file)
                if src.data.artifact_sha256:
                    lfs_sha = _parse_lfs_pointer_sha256(art_file)
                    actual_sha = lfs_sha if lfs_sha is not None else compute_sha256(art_file)
                    if actual_sha != src.data.artifact_sha256:
                        add_error(
                            "ARTIFACT_HASH_MISMATCH",
                            f"Source '{sid}' artifact '{src.data.artifact_path}' SHA-256 mismatch "
                            f"(expected {src.data.artifact_sha256[:12]}..., got {actual_sha[:12]}...)",
                            src.path,
                        )

    # Check for unlinked artifacts in artifacts/
    if not skip_artifacts and ds.artifacts_dir.exists():
        for art_file in sorted(ds.artifacts_dir.rglob("*")):
            if not art_file.is_file() or art_file.name.startswith("."):
                continue
            if art_file.resolve() not in linked_artifacts:
                add_warning(
                    "UNLINKED_ARTIFACT",
                    f"Artifact '{ds.logical_artifact_path(art_file)}' is not linked by any source file in data/sources/",
                    art_file,
                )

    # 4. Relationships: referential integrity, duplicates, DAG cycles, biological checks, GPS checks
    parent_to_children: dict[str, list[str]] = defaultdict(list)
    connected_individuals: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()

    for idx, rel in enumerate(ds.relationships, start=1):
        if rel.place_id and rel.place_id not in ds.places:
            add_error(
                "MISSING_PLACE_REF",
                f"Relationship #{idx} references unknown place '{rel.place_id}'",
                rel_path,
            )
        p1, p2 = rel.person_1, rel.person_2
        connected_individuals.add(p1)
        connected_individuals.add(p2)

        if p1 not in ds.individuals:
            add_error(
                "MISSING_INDIVIDUAL_REF",
                f"Relationship #{idx} ({rel.type}) references unknown person_1 '{p1}'",
                rel_path,
            )
        if p2 not in ds.individuals:
            add_error(
                "MISSING_INDIVIDUAL_REF",
                f"Relationship #{idx} ({rel.type}) references unknown person_2 '{p2}'",
                rel_path,
            )
        for sid in rel.sources:
            if sid not in ds.sources:
                add_error(
                    "MISSING_SOURCE_REF",
                    f"Relationship #{idx} ({rel.type} {p1}->{p2}) references unknown source '{sid}'",
                    rel_path,
                )
            elif ds.sources[sid].data.relevance == "excluded":
                p1_ex = p1 in ds.individuals and ds.individuals[p1].data.status == "excluded"
                p2_ex = p2 in ds.individuals and ds.individuals[p2].data.status == "excluded"
                if not (p1_ex and p2_ex):
                    add_error(
                        "EXCLUDED_SOURCE_CITED",
                        f"Relationship #{idx} ({rel.type} {p1}->{p2}) cites excluded source '{sid}'",
                        rel_path,
                    )

        # Duplicate edge check (symmetric for non-parent_child)
        edge_key = (
            (rel.type, p1, p2)
            if rel.type == "parent_child"
            else (rel.type, min(p1, p2), max(p1, p2))
        )
        if edge_key in seen_edges:
            add_warning(
                "DUPLICATE_RELATIONSHIP",
                f"Duplicate {rel.type} relationship between '{p1}' and '{p2}'",
                rel_path,
            )
        seen_edges.add(edge_key)

        # Unsourced / GPS checks on relationships
        if not rel.sources:
            add_warning(
                "UNSOURCED_RELATIONSHIP",
                f"Relationship #{idx} ({rel.type} {p1}->{p2}) has no cited sources",
                rel_path,
            )
        elif rel.confidence == "proven":
            non_compiled = [
                ds.sources[s]
                for s in rel.sources
                if s in ds.sources
                and ds.sources[s].data.record_type != "compiled"
                and ds.sources[s].data.source_type != "authored"
            ]
            if len(rel.sources) < 2:
                add_warning(
                    "GPS_PROVEN_REL_UNDER_SOURCED",
                    f"Relationship #{idx} ({rel.type} {p1}->{p2}) is confidence='proven' with fewer than 2 sources",
                    rel_path,
                )
            elif not non_compiled:
                add_warning(
                    "GPS_PROVEN_REL_COMPILED_ONLY",
                    f"Relationship #{idx} ({rel.type} {p1}->{p2}) is confidence='proven' using only compiled/authored sources",
                    rel_path,
                )

        # Bidirectional check: if a source cites both individuals, fine; no hard error,
        # but check biological chronology for parent_child & marriage
        if p1 in ds.individuals and p2 in ds.individuals:
            ind1 = ds.individuals[p1].data
            ind2 = ds.individuals[p2].data

            if rel.type == "parent_child":
                parent_to_children[p1].append(p2)
                p_birth = parse_genealogy_date(ind1.birth.date) if ind1.birth else None
                p_death = parse_genealogy_date(ind1.death.date) if ind1.death else None
                c_birth = parse_genealogy_date(ind2.birth.date) if ind2.birth else None
                is_bio = rel.subtype in (None, "biological", "unknown")

                if p_birth and c_birth:
                    if p_birth.year_min is not None and c_birth.year_max is not None:
                        if c_birth.year_max < p_birth.year_min:
                            add_error(
                                "CHILD_BEFORE_PARENT",
                                f"Child '{p2}' ({c_birth.raw}) born before parent '{p1}' ({p_birth.raw})",
                                rel_path,
                            )
                        elif is_bio and (c_birth.year_max - p_birth.year_min) < 12:
                            add_warning(
                                "PARENT_TOO_YOUNG",
                                f"Parent '{p1}' ({p_birth.raw}) was <12 years old at birth of '{p2}' ({c_birth.raw})",
                                rel_path,
                            )
                    if is_bio and ind1.sex == "F" and p_birth.year_max is not None and c_birth.year_min is not None:
                        if (c_birth.year_min - p_birth.year_max) > 55:
                            add_warning(
                                "MOTHER_AGE_IMPLAUSIBLE",
                                f"Mother '{p1}' ({p_birth.raw}) was >55 at birth of '{p2}' ({c_birth.raw})",
                                rel_path,
                            )

                if is_bio and p_death and c_birth:
                    if p_death.year_max is not None and c_birth.year_min is not None:
                        # Allow +1 year buffer for posthumous birth (father dying before birth)
                        allowed_lag = 0 if ind1.sex == "F" else 1
                        if c_birth.year_min > p_death.year_max + allowed_lag:
                            add_error(
                                "CHILD_AFTER_PARENT_DEATH",
                                f"Child '{p2}' ({c_birth.raw}) born after death of parent '{p1}' ({p_death.raw})",
                                rel_path,
                            )

            elif rel.type == "marriage" and rel.date:
                m_date = parse_genealogy_date(rel.date)
                if m_date:
                    for spouse_id, spouse_data in ((p1, ind1), (p2, ind2)):
                        sb = parse_genealogy_date(spouse_data.birth.date) if spouse_data.birth else None
                        sd = parse_genealogy_date(spouse_data.death.date) if spouse_data.death else None
                        if sb and sb.year_min is not None and m_date.year_max is not None:
                            if m_date.year_max < sb.year_min:
                                add_error(
                                    "MARRIAGE_BEFORE_BIRTH",
                                    f"Marriage ({m_date.raw}) precedes birth of '{spouse_id}' ({sb.raw})",
                                    rel_path,
                                )
                            elif (m_date.year_max - sb.year_min) < 12:
                                add_warning(
                                    "MARRIAGE_TOO_YOUNG",
                                    f"Spouse '{spouse_id}' ({sb.raw}) was <12 at marriage ({m_date.raw})",
                                    rel_path,
                                )
                        if sd and sd.year_max is not None and m_date.year_min is not None:
                            if m_date.year_min > sd.year_max:
                                add_error(
                                    "MARRIAGE_AFTER_DEATH",
                                    f"Marriage ({m_date.raw}) occurs after death of '{spouse_id}' ({sd.raw})",
                                    rel_path,
                                )

    # 5. DAG cycle check on parent_child graph (3-color DFS)
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    cycle_reported: set[str] = set()

    def dfs(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in parent_to_children.get(node, []):
            if color[nxt] == GRAY:
                cycle_start = stack.index(nxt)
                cycle_path = " -> ".join(stack[cycle_start:] + [nxt])
                if cycle_path not in cycle_reported:
                    cycle_reported.add(cycle_path)
                    add_error(
                        "ANCESTRY_CYCLE",
                        f"Cycle detected in parent_child graph: {cycle_path}",
                        rel_path,
                    )
            elif color[nxt] == WHITE:
                dfs(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for node in sorted(parent_to_children.keys()):
        if color[node] == WHITE:
            dfs(node, [])

    # 5b. Relationship constraints: referential integrity, GPS, and consistency
    for idx, con in enumerate(ds.constraints, start=1):
        cid = con.id or f"#{idx}"
        p1, p2 = con.person_1, con.person_2
        if p1 not in ds.individuals:
            add_error(
                "MISSING_INDIVIDUAL_REF",
                f"Constraint {cid} ({con.type}) references unknown person_1 '{p1}'",
                rel_path,
            )
        if p2 not in ds.individuals:
            add_error(
                "MISSING_INDIVIDUAL_REF",
                f"Constraint {cid} ({con.type}) references unknown person_2 '{p2}'",
                rel_path,
            )
        for sid in con.sources:
            if sid not in ds.sources:
                add_error(
                    "MISSING_SOURCE_REF",
                    f"Constraint {cid} ({p1} <-> {p2}) references unknown source '{sid}'",
                    rel_path,
                )
            elif ds.sources[sid].data.relevance == "excluded":
                add_error(
                    "EXCLUDED_SOURCE_CITED",
                    f"Constraint {cid} ({p1} <-> {p2}) cites excluded source '{sid}'",
                    rel_path,
                )

        if not con.sources:
            add_warning(
                "UNSOURCED_CONSTRAINT",
                f"Constraint {cid} ({p1} <-> {p2}) has no cited sources",
                rel_path,
            )
        elif con.confidence == "proven":
            non_compiled = [
                ds.sources[s]
                for s in con.sources
                if s in ds.sources
                and ds.sources[s].data.record_type != "compiled"
                and ds.sources[s].data.source_type != "authored"
            ]
            if len(con.sources) < 2:
                add_warning(
                    "GPS_PROVEN_REQUIRES_TWO_SOURCES",
                    f"Constraint {cid} ({p1} <-> {p2}) is confidence='proven' with fewer than 2 sources",
                    rel_path,
                )
            elif not non_compiled:
                add_warning(
                    "GPS_COMPILED_ONLY",
                    f"Constraint {cid} ({p1} <-> {p2}) is confidence='proven' using only compiled/authored sources",
                    rel_path,
                )

        if p1 in ds.individuals and p2 in ds.individuals:
            ev = evaluate_constraint(ds, con, index=idx)
            if ev.status == "violated":
                msg = ev.deductions[0].message if ev.deductions else "Constraint contradicted by pedigree."
                add_warning(
                    "CONSTRAINT_VIOLATED",
                    f"Constraint {cid} ({p1} <-> {p2}, steps {con.steps_1}/{con.steps_2}): {msg}",
                    rel_path,
                )

    # 6. Cross-check bidirectional source <-> individual references (warning only)
    for sid, src in ds.sources.items():
        if src.data.relevance == "excluded":
            continue
        for iid in src.data.individuals_cited:
            if iid not in ds.individuals:
                continue
            cited_in_ind = sid in individual_cited_sources[iid]
            cited_in_rel = any(
                sid in r.sources and iid in (r.person_1, r.person_2) for r in ds.relationships
            )
            cited_in_con = any(
                sid in c.sources and iid in (c.person_1, c.person_2) for c in ds.constraints
            )
            if not cited_in_ind and not cited_in_rel and not cited_in_con:
                add_warning(
                    "ONE_WAY_SOURCE_LINK",
                    f"Source '{sid}' cites '{iid}', but '{iid}' does not reference '{sid}' in its profile or relationships",
                    src.path,
                )

    return report
