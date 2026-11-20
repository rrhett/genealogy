"""Deductive reasoning engine for genealogical relationship constraints (e.g., marriage dispensations)."""

from __future__ import annotations

import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from genealogy.loader import Dataset, load_dataset
from genealogy.schema import RelationshipConstraint, parse_genealogy_date

ConstraintStatus = Literal[
    "satisfied",
    "narrowed",
    "open",
    "violated",
    "not_applicable",
]


def _normalize_text(text: str) -> str:
    """Lowercase ASCII-folded string for fuzzy name comparison."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(ascii_only.lower().split())


def _bio_parents_map(ds: Dataset) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for rel in ds.relationships:
        if rel.type == "parent_child" and rel.subtype in (None, "biological", "unknown"):
            if rel.person_1 in ds.individuals and rel.person_2 in ds.individuals:
                if rel.person_1 not in parents[rel.person_2]:
                    parents[rel.person_2].append(rel.person_1)
    return parents


def _bio_children_map(ds: Dataset) -> dict[str, list[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for rel in ds.relationships:
        if rel.type == "parent_child" and rel.subtype in (None, "biological", "unknown"):
            if rel.person_1 in ds.individuals and rel.person_2 in ds.individuals:
                if rel.person_2 not in children[rel.person_1]:
                    children[rel.person_1].append(rel.person_2)
    return children


def _all_descendants(start: str, children_map: dict[str, list[str]]) -> set[str]:
    visited: set[str] = {start}
    queue: deque[str] = deque([start])
    while queue:
        curr = queue.popleft()
        for ch in children_map.get(curr, []):
            if ch not in visited:
                visited.add(ch)
                queue.append(ch)
    return visited


def _birth_year_bounds(ds: Dataset, iid: str) -> tuple[int | None, int | None]:
    ind = ds.individuals.get(iid)
    if not ind or not ind.data.birth or not ind.data.birth.date:
        return None, None
    parsed = parse_genealogy_date(ind.data.birth.date)
    if not parsed:
        return None, None
    return parsed.year_min, parsed.year_max


def _death_year_bounds(ds: Dataset, iid: str) -> tuple[int | None, int | None]:
    ind = ds.individuals.get(iid)
    if not ind or not ind.data.death or not ind.data.death.date:
        return None, None
    parsed = parse_genealogy_date(ind.data.death.date)
    if not parsed:
        return None, None
    return parsed.year_min, parsed.year_max


def _generation_label(steps: int) -> str:
    if steps == 0:
        return "self"
    if steps == 1:
        return "parent"
    if steps == 2:
        return "grandparent"
    if steps == 3:
        return "great-grandparent"
    return f"{steps - 2}x great-grandparent"


@dataclass
class MissingSlotGroup:
    """Represents one or more unknown ancestor slots rooted at a known descendant."""

    anchor_person: str
    anchor_name: str
    anchor_depth: int
    remaining_steps: int
    slot_count: int
    missing_parent_sex: Literal["M", "F"] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_person": self.anchor_person,
            "anchor_name": self.anchor_name,
            "anchor_depth": self.anchor_depth,
            "remaining_steps": self.remaining_steps,
            "slot_count": self.slot_count,
            "missing_parent_sex": self.missing_parent_sex,
        }


@dataclass
class SideFrontier:
    """Known ancestors and missing pedigree slots for a person at a given generation depth."""

    person_id: str
    steps: int
    known_ancestors: list[str]
    unknown_slots: int
    missing_slots: list[MissingSlotGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_id": self.person_id,
            "steps": self.steps,
            "known_ancestors": self.known_ancestors,
            "unknown_slots": self.unknown_slots,
            "missing_slots": [m.to_dict() for m in self.missing_slots],
        }


def _ancestor_frontier(
    ds: Dataset,
    person_id: str,
    steps: int,
    parents_map: dict[str, list[str]],
) -> SideFrontier:
    """Compute known ancestors and missing pedigree slots exactly `steps` above `person_id`."""
    if steps == 0:
        return SideFrontier(
            person_id=person_id,
            steps=0,
            known_ancestors=[person_id],
            unknown_slots=0,
            missing_slots=[],
        )

    # Track active nodes at each depth d along with path multiplicity
    frontier: dict[str, int] = {person_id: 1}
    unknown_slots = 0
    missing_slots: list[MissingSlotGroup] = []

    for d in range(steps):
        next_frontier: dict[str, int] = defaultdict(int)
        remaining = steps - d
        slots_per_missing_parent = 2 ** (remaining - 1)

        for node, mult in sorted(frontier.items()):
            parents = parents_map.get(node, [])
            for p in parents:
                next_frontier[p] += mult

            missing_parent_count = max(0, 2 - len(parents))
            if missing_parent_count > 0:
                slot_count = mult * missing_parent_count * slots_per_missing_parent
                unknown_slots += slot_count

                inferred_sex: Literal["M", "F"] | None = None
                if missing_parent_count == 1 and len(parents) == 1:
                    known_sex = ds.individuals[parents[0]].data.sex
                    if known_sex == "M":
                        inferred_sex = "F"
                    elif known_sex == "F":
                        inferred_sex = "M"

                missing_slots.append(
                    MissingSlotGroup(
                        anchor_person=node,
                        anchor_name=ds.individuals[node].data.primary_name,
                        anchor_depth=d,
                        remaining_steps=remaining,
                        slot_count=slot_count,
                        missing_parent_sex=inferred_sex,
                    )
                )

        frontier = dict(next_frontier)

    return SideFrontier(
        person_id=person_id,
        steps=steps,
        known_ancestors=sorted(frontier.keys()),
        unknown_slots=unknown_slots,
        missing_slots=missing_slots,
    )


@dataclass
class CandidateAssessment:
    """Compatibility assessment for a known ancestor filling a missing slot on the other side."""

    candidate_id: str
    candidate_name: str
    target_person: str
    compatible: bool
    compatible_slots: list[MissingSlotGroup] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    surname_match: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_name": self.candidate_name,
            "target_person": self.target_person,
            "compatible": self.compatible,
            "compatible_slots": [s.to_dict() for s in self.compatible_slots],
            "rejection_reasons": self.rejection_reasons,
            "surname_match": self.surname_match,
        }


@dataclass
class IdentityOverlapCandidate:
    """Pair of distinct IDs on opposite sides that may represent the same historical person."""

    person_1_ancestor: str
    person_1_ancestor_name: str
    person_2_ancestor: str
    person_2_ancestor_name: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_1_ancestor": self.person_1_ancestor,
            "person_1_ancestor_name": self.person_1_ancestor_name,
            "person_2_ancestor": self.person_2_ancestor,
            "person_2_ancestor_name": self.person_2_ancestor_name,
            "reasons": self.reasons,
        }


@dataclass
class ConstraintDeduction:
    """Structured deductive inference produced by evaluating a relationship constraint."""

    code: str
    message: str
    suggested_action: str | None = None
    inferred_parent: str | None = None
    inferred_child: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "suggested_action": self.suggested_action,
            "inferred_parent": self.inferred_parent,
            "inferred_child": self.inferred_child,
        }


@dataclass
class ConstraintEvaluation:
    """Full deductive evaluation of a single RelationshipConstraint."""

    constraint_id: str
    type: str
    person_1: str
    person_1_name: str
    person_2: str
    person_2_name: str
    steps_1: int
    steps_2: int
    symmetric: bool
    status: ConstraintStatus
    frontier_1: SideFrontier
    frontier_2: SideFrontier
    witnesses: list[str] = field(default_factory=list)
    compatible_candidates: list[CandidateAssessment] = field(default_factory=list)
    ruled_out_candidates: list[CandidateAssessment] = field(default_factory=list)
    potential_identity_overlaps: list[IdentityOverlapCandidate] = field(default_factory=list)
    deductions: list[ConstraintDeduction] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "type": self.type,
            "person_1": self.person_1,
            "person_1_name": self.person_1_name,
            "person_2": self.person_2,
            "person_2_name": self.person_2_name,
            "steps_1": self.steps_1,
            "steps_2": self.steps_2,
            "symmetric": self.symmetric,
            "status": self.status,
            "frontier_1": self.frontier_1.to_dict(),
            "frontier_2": self.frontier_2.to_dict(),
            "witnesses": self.witnesses,
            "compatible_candidates": [c.to_dict() for c in self.compatible_candidates],
            "ruled_out_candidates": [c.to_dict() for c in self.ruled_out_candidates],
            "potential_identity_overlaps": [
                o.to_dict() for o in self.potential_identity_overlaps
            ],
            "deductions": [d.to_dict() for d in self.deductions],
            "sources": self.sources,
            "notes": self.notes,
        }


def _is_candidate_compatible_with_slot(
    ds: Dataset,
    cand_id: str,
    slot: MissingSlotGroup,
    constraint: RelationshipConstraint,
    children_map: dict[str, list[str]],
) -> tuple[bool, list[str], bool]:
    """Check whether `cand_id` can plausibly fill `slot` without violating sex, DAG, or chronology."""
    reasons: list[str] = []
    cand = ds.individuals[cand_id].data
    anchor = ds.individuals[slot.anchor_person].data

    # 1. Explicit constraint sex filter
    if constraint.common_ancestor_sex and cand.sex != "U":
        if cand.sex != constraint.common_ancestor_sex:
            reasons.append(
                f"sex {cand.sex} != required common_ancestor_sex {constraint.common_ancestor_sex}"
            )

    # 2. Direct missing parent sex filter
    if slot.remaining_steps == 1 and slot.missing_parent_sex and cand.sex != "U":
        if cand.sex != slot.missing_parent_sex:
            reasons.append(
                f"sex {cand.sex} != missing parent sex {slot.missing_parent_sex} of {slot.anchor_person}"
            )

    # 3. DAG acyclicity: candidate cannot be anchor or a descendant of anchor
    if cand_id in _all_descendants(slot.anchor_person, children_map):
        reasons.append(f"{cand_id} is already {slot.anchor_person} or a descendant (would create cycle)")

    # 4. Chronological bounds
    c_bmin, c_bmax = _birth_year_bounds(ds, cand_id)
    a_bmin, a_bmax = _birth_year_bounds(ds, slot.anchor_person)
    r = slot.remaining_steps
    if c_bmin is not None and a_bmax is not None:
        if c_bmin + 12 * r > a_bmax:
            reasons.append(
                f"born too late ({c_bmin}) to be {r} gen(s) above {slot.anchor_person} (born <= {a_bmax})"
            )
    if c_bmax is not None and a_bmin is not None:
        if c_bmax + 85 * r < a_bmin:
            reasons.append(
                f"born too early ({c_bmax}) to be {r} gen(s) above {slot.anchor_person} (born >= {a_bmin})"
            )

    if r == 1 and a_bmin is not None:
        _, c_dmax = _death_year_bounds(ds, cand_id)
        if c_dmax is not None:
            cutoff = a_bmin - 1 if cand.sex == "M" else a_bmin
            if c_dmax < cutoff:
                reasons.append(
                    f"died ({c_dmax}) before {slot.anchor_person} birth ({a_bmin})"
                )

    # 5. Soft surname hint
    surname_match = False
    cand_surnames = {
        _normalize_text(s)
        for s in (cand.surname, cand.maternal_surname)
        if s and s.strip()
    }
    if constraint.common_ancestor_surname:
        if _normalize_text(constraint.common_ancestor_surname) in cand_surnames:
            surname_match = True
    if not surname_match and r == 1:
        anchor_surnames = {
            _normalize_text(s)
            for s in (anchor.surname, anchor.maternal_surname)
            if s and s.strip()
        }
        if cand_surnames & anchor_surnames:
            surname_match = True

    return len(reasons) == 0, reasons, surname_match


def _find_potential_overlaps(
    ds: Dataset,
    ancestors_1: list[str],
    ancestors_2: list[str],
    constraint: RelationshipConstraint,
) -> list[IdentityOverlapCandidate]:
    """Identify pairs of distinct IDs on opposite sides that might be the same person."""
    overlaps: list[IdentityOverlapCandidate] = []
    for a1 in ancestors_1:
        d1 = ds.individuals[a1].data
        s1 = _normalize_text(d1.surname or d1.primary_name.split()[-1])
        g1 = _normalize_text(d1.given_name or d1.primary_name.split()[0])
        b1_min, b1_max = _birth_year_bounds(ds, a1)

        for a2 in ancestors_2:
            if a1 == a2:
                continue
            d2 = ds.individuals[a2].data
            if d1.sex != "U" and d2.sex != "U" and d1.sex != d2.sex:
                continue
            if constraint.common_ancestor_sex:
                if (d1.sex != "U" and d1.sex != constraint.common_ancestor_sex) or (
                    d2.sex != "U" and d2.sex != constraint.common_ancestor_sex
                ):
                    continue

            s2 = _normalize_text(d2.surname or d2.primary_name.split()[-1])
            if not s1 or not s2 or s1 != s2:
                continue

            g2 = _normalize_text(d2.given_name or d2.primary_name.split()[0])
            tokens1 = set(g1.split())
            tokens2 = set(g2.split())
            given_similar = bool(tokens1 & tokens2) or (
                len(g1) >= 1 and len(g2) >= 1 and g1[0] == g2[0] and (len(g1) == 1 or len(g2) == 1)
            )
            if not given_similar:
                continue

            b2_min, b2_max = _birth_year_bounds(ds, a2)
            year_note = "birth years unconfirmed"
            if b1_min is not None and b2_min is not None:
                mid1 = (b1_min + (b1_max or b1_min)) / 2.0
                mid2 = (b2_min + (b2_max or b2_min)) / 2.0
                if abs(mid1 - mid2) > 12:
                    continue
                year_note = f"birth years ~{int(mid1)} vs ~{int(mid2)}"

            overlaps.append(
                IdentityOverlapCandidate(
                    person_1_ancestor=a1,
                    person_1_ancestor_name=d1.primary_name,
                    person_2_ancestor=a2,
                    person_2_ancestor_name=d2.primary_name,
                    reasons=[
                        f"matching surname '{s1}'",
                        "compatible given name & sex",
                        year_note,
                    ],
                )
            )
    return overlaps


def _evaluate_directed_pair(
    ds: Dataset,
    constraint: RelationshipConstraint,
    cid: str,
    p1: str,
    steps_1: int,
    p2: str,
    steps_2: int,
    parents_map: dict[str, list[str]],
    children_map: dict[str, list[str]],
) -> ConstraintEvaluation:
    f1 = _ancestor_frontier(ds, p1, steps_1, parents_map)
    f2 = _ancestor_frontier(ds, p2, steps_2, parents_map)
    p1_name = ds.individuals[p1].data.primary_name
    p2_name = ds.individuals[p2].data.primary_name

    # Check direct intersection
    raw_witnesses = sorted(set(f1.known_ancestors) & set(f2.known_ancestors))
    if constraint.common_ancestor_sex:
        witnesses = [
            w
            for w in raw_witnesses
            if ds.individuals[w].data.sex in ("U", constraint.common_ancestor_sex)
        ]
    else:
        witnesses = raw_witnesses

    deductions: list[ConstraintDeduction] = []
    compatible_candidates: list[CandidateAssessment] = []
    ruled_out_candidates: list[CandidateAssessment] = []
    overlaps: list[IdentityOverlapCandidate] = []

    if witnesses:
        status: ConstraintStatus = "satisfied"
        w_labels = ", ".join(
            f"{w} ({ds.individuals[w].data.primary_name})" for w in witnesses
        )
        deductions.append(
            ConstraintDeduction(
                code="SATISFIED",
                message=(
                    f"Constraint satisfied by common ancestor(s): {w_labels} "
                    f"({_generation_label(steps_1)} of {p1_name}; "
                    f"{_generation_label(steps_2)} of {p2_name})."
                ),
            )
        )
    elif f1.unknown_slots == 0 and f2.unknown_slots == 0:
        status = "violated"
        deductions.append(
            ConstraintDeduction(
                code="CONTRADICTION",
                message=(
                    f"All {len(f1.known_ancestors)} {_generation_label(steps_1)}(s) of {p1_name} "
                    f"and all {len(f2.known_ancestors)} {_generation_label(steps_2)}(s) of {p2_name} "
                    f"are known, with no shared ancestor."
                ),
                suggested_action="Verify parent-child links, check for duplicate IDs, or review source accuracy.",
            )
        )
        overlaps = _find_potential_overlaps(
            ds, f1.known_ancestors, f2.known_ancestors, constraint
        )
    else:
        # At least one side has unknown slots. Check if one side is completely known (f1 or f2 == 0).
        def _assess_pool(
            pool: list[str], target_person: str, target_frontier: SideFrontier
        ) -> None:
            for cand_id in pool:
                comp_slots: list[MissingSlotGroup] = []
                all_reasons: list[str] = []
                any_surname = False
                for slot in target_frontier.missing_slots:
                    ok, reasons, s_match = _is_candidate_compatible_with_slot(
                        ds, cand_id, slot, constraint, children_map
                    )
                    if ok:
                        comp_slots.append(slot)
                        any_surname = any_surname or s_match
                    else:
                        all_reasons.extend(reasons)

                assessment = CandidateAssessment(
                    candidate_id=cand_id,
                    candidate_name=ds.individuals[cand_id].data.primary_name,
                    target_person=target_person,
                    compatible=bool(comp_slots),
                    compatible_slots=comp_slots,
                    rejection_reasons=all_reasons,
                    surname_match=any_surname,
                )
                if comp_slots:
                    compatible_candidates.append(assessment)
                else:
                    ruled_out_candidates.append(assessment)

        if f1.unknown_slots == 0 and f2.unknown_slots > 0:
            _assess_pool(f1.known_ancestors, p2, f2)
        elif f2.unknown_slots == 0 and f1.unknown_slots > 0:
            _assess_pool(f2.known_ancestors, p1, f1)

        if f1.unknown_slots == 0 or f2.unknown_slots == 0:
            if not compatible_candidates:
                status = "violated"
                deductions.append(
                    ConstraintDeduction(
                        code="CONTRADICTION",
                        message=(
                            "All candidates from the fully known side were ruled out by sex, "
                            "chronology, or pedigree acyclicity."
                        ),
                        suggested_action="Review dates, sex, or parent-child links on both pedigrees.",
                    )
                )
            else:
                status = "narrowed"
                if len(compatible_candidates) == 1:
                    only = compatible_candidates[0]
                    deductions.append(
                        ConstraintDeduction(
                            code="UNIQUE_CANDIDATE",
                            message=(
                                f"Elimination narrows common ancestor to a single candidate: "
                                f"{only.candidate_id} ({only.candidate_name})."
                            ),
                        )
                    )
                    if (
                        len(only.compatible_slots) == 1
                        and only.compatible_slots[0].remaining_steps == 1
                        and only.compatible_slots[0].slot_count == 1
                    ):
                        slot = only.compatible_slots[0]
                        deductions.append(
                            ConstraintDeduction(
                                code="DIRECT_PARENT_INFERENCE",
                                message=(
                                    f"Only 1 missing slot remains ({slot.anchor_name}'s missing parent); "
                                    f"deduced parent_child link: {only.candidate_id} -> {slot.anchor_person}."
                                ),
                                suggested_action=(
                                    f"Verify with additional records and add parent_child edge "
                                    f"({only.candidate_id} -> {slot.anchor_person})."
                                ),
                                inferred_parent=only.candidate_id,
                                inferred_child=slot.anchor_person,
                            )
                        )
                else:
                    cand_list = ", ".join(
                        f"{c.candidate_id} ({c.candidate_name})"
                        for c in compatible_candidates
                    )
                    deductions.append(
                        ConstraintDeduction(
                            code="FORCE_CANDIDATE_POOL",
                            message=(
                                f"Common ancestor must be one of {len(compatible_candidates)} "
                                f"compatible candidate(s): {cand_list}."
                            ),
                        )
                    )
        else:
            status = "open"

        overlaps = _find_potential_overlaps(
            ds, f1.known_ancestors, f2.known_ancestors, constraint
        )

    for ov in overlaps:
        deductions.append(
            ConstraintDeduction(
                code="POTENTIAL_SAME_PERSON",
                message=(
                    f"Potential identity overlap: {ov.person_1_ancestor} ({ov.person_1_ancestor_name}) "
                    f"and {ov.person_2_ancestor} ({ov.person_2_ancestor_name}) "
                    f"({'; '.join(ov.reasons)})."
                ),
                suggested_action="Investigate whether these two IDs represent the same individual.",
            )
        )

    return ConstraintEvaluation(
        constraint_id=cid,
        type=constraint.type,
        person_1=p1,
        person_1_name=p1_name,
        person_2=p2,
        person_2_name=p2_name,
        steps_1=steps_1,
        steps_2=steps_2,
        symmetric=constraint.symmetric,
        status=status,
        frontier_1=f1,
        frontier_2=f2,
        witnesses=witnesses,
        compatible_candidates=compatible_candidates,
        ruled_out_candidates=ruled_out_candidates,
        potential_identity_overlaps=overlaps,
        deductions=deductions,
        sources=list(constraint.sources),
        notes=constraint.notes,
    )


def evaluate_constraint(
    ds: Dataset,
    constraint: RelationshipConstraint,
    *,
    index: int = 1,
    parents_map: dict[str, list[str]] | None = None,
    children_map: dict[str, list[str]] | None = None,
) -> ConstraintEvaluation:
    """Evaluate a single RelationshipConstraint against the dataset."""
    cid = constraint.id or f"C-{index:03d}"
    p1 = constraint.person_1
    p2 = constraint.person_2
    p1_name = ds.individuals[p1].data.primary_name if p1 in ds.individuals else p1
    p2_name = ds.individuals[p2].data.primary_name if p2 in ds.individuals else p2

    if constraint.type == "affinity" or p1 not in ds.individuals or p2 not in ds.individuals:
        empty_f1 = SideFrontier(person_id=p1, steps=constraint.steps_1, known_ancestors=[], unknown_slots=0)
        empty_f2 = SideFrontier(person_id=p2, steps=constraint.steps_2, known_ancestors=[], unknown_slots=0)
        return ConstraintEvaluation(
            constraint_id=cid,
            type=constraint.type,
            person_1=p1,
            person_1_name=p1_name,
            person_2=p2,
            person_2_name=p2_name,
            steps_1=constraint.steps_1,
            steps_2=constraint.steps_2,
            symmetric=constraint.symmetric,
            status="not_applicable",
            frontier_1=empty_f1,
            frontier_2=empty_f2,
            sources=list(constraint.sources),
            notes=constraint.notes,
        )

    pm = parents_map if parents_map is not None else _bio_parents_map(ds)
    cm = children_map if children_map is not None else _bio_children_map(ds)

    primary = _evaluate_directed_pair(
        ds, constraint, cid, p1, constraint.steps_1, p2, constraint.steps_2, pm, cm
    )

    if not constraint.symmetric or constraint.steps_1 == constraint.steps_2:
        return primary

    if primary.status == "satisfied":
        return primary

    swapped = _evaluate_directed_pair(
        ds, constraint, cid, p1, constraint.steps_2, p2, constraint.steps_1, pm, cm
    )
    rank = {"satisfied": 0, "narrowed": 1, "open": 2, "violated": 3, "not_applicable": 4}
    primary_key = (
        rank[primary.status],
        -len(primary.potential_identity_overlaps),
        -(len(primary.frontier_1.known_ancestors) + len(primary.frontier_2.known_ancestors)),
    )
    swapped_key = (
        rank[swapped.status],
        -len(swapped.potential_identity_overlaps),
        -(len(swapped.frontier_1.known_ancestors) + len(swapped.frontier_2.known_ancestors)),
    )
    if swapped_key < primary_key:
        return swapped
    return primary


def evaluate_all_constraints(
    root: Path | str | Dataset,
    *,
    person_id: str | None = None,
    status: ConstraintStatus | None = None,
) -> list[ConstraintEvaluation]:
    """Evaluate all relationship constraints in a workspace dataset."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    pm = _bio_parents_map(ds)
    cm = _bio_children_map(ds)
    results: list[ConstraintEvaluation] = []
    for idx, c in enumerate(ds.constraints, start=1):
        if person_id and person_id not in (c.person_1, c.person_2):
            continue
        ev = evaluate_constraint(ds, c, index=idx, parents_map=pm, children_map=cm)
        if status and ev.status != status:
            continue
        results.append(ev)
    return results
