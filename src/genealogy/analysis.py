"""Kinship path analysis, pedigree collapse / inbreeding (Malecot/Wright), research gaps, and reports."""

from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from genealogy.loader import Dataset, load_dataset
from genealogy.schema import parse_genealogy_date

# Regional surviving public national census years
US_CENSUS_YEARS = [
    1790,
    1800,
    1810,
    1820,
    1830,
    1840,
    1850,
    1860,
    1870,
    1880,
    1900,
    1910,
    1920,
    1930,
    1940,
    1950,
]

REGIONAL_CENSUS_YEARS: dict[str, list[int]] = {
    "us": US_CENSUS_YEARS,
    "mexico": [1930],  # 1930 Censo de Poblacion is the sole surviving public national census
    "uk": [1841, 1851, 1861, 1871, 1881, 1891, 1901, 1911, 1921],
    "canada": [1851, 1861, 1871, 1881, 1891, 1901, 1911, 1921, 1931],
    "general": [],
}

_MEXICO_KEYWORDS = (
    "mexico",
    "méxico",
    "mejico",
    "méjico",
    "nueva españa",
    "nueva espana",
    "nueva galicia",
    "aguascalientes",
    "baja california",
    "campeche",
    "chiapas",
    "chihuahua",
    "coahuila",
    "colima",
    "durango",
    "guanajuato",
    "guerrero",
    "hidalgo",
    "jalisco",
    "michoacán",
    "michoacan",
    "morelos",
    "nayarit",
    "nuevo león",
    "nuevo leon",
    "oaxaca",
    "puebla",
    "querétaro",
    "queretaro",
    "quintana roo",
    "san luis potosí",
    "san luis potosi",
    "sinaloa",
    "sonora",
    "tabasco",
    "tamaulipas",
    "tlaxcala",
    "veracruz",
    "yucatán",
    "yucatan",
    "zacatecas",
    "ciudad de méxico",
    "distrito federal",
)

_US_KEYWORDS = (
    "usa",
    "u.s.a.",
    "united states",
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
)

_UK_KEYWORDS = ("united kingdom", "england", "scotland", "wales", "great britain")
_CANADA_KEYWORDS = (
    "canada",
    "ontario",
    "quebec",
    "québec",
    "nova scotia",
    "new brunswick",
    "manitoba",
    "british columbia",
    "saskatchewan",
    "alberta",
)


def detect_individual_regions(
    ds: Dataset,
    individual_id: str,
    *,
    regions_override: list[str] | None = None,
) -> list[str]:
    """Resolve active regional record profiles for an individual (`auto`, `mexico`, `us`, `uk`, `canada`, `general`)."""
    configured = regions_override
    if configured is None:
        cfg_val = ds.config.get("regions")
        if isinstance(cfg_val, str):
            configured = [cfg_val.strip().lower()]
        elif isinstance(cfg_val, list):
            configured = [str(x).strip().lower() for x in cfg_val if str(x).strip()]

    if configured and configured != ["auto"]:
        return [r for r in configured if r in REGIONAL_CENSUS_YEARS] or ["general"]

    ind = ds.individuals[individual_id].data
    places: list[str] = []
    if ind.birth and ind.birth.place:
        places.append(ind.birth.place)
    if ind.death and ind.death.place:
        places.append(ind.death.place)
    for ev in ind.events:
        if ev.place:
            places.append(ev.place)
    for sid in ind.all_source_ids():
        src = ds.sources.get(sid)
        if src and src.data.jurisdiction:
            places.append(src.data.jurisdiction)

    hay = " | ".join(places).lower()
    detected: list[str] = []
    # Remove U.S. "new mexico" before checking Mexico keywords
    hay_no_nm = hay.replace("new mexico", "")
    if any(kw in hay_no_nm for kw in _MEXICO_KEYWORDS):
        detected.append("mexico")
    if any(kw in hay for kw in _US_KEYWORDS):
        detected.append("us")
    if any(kw in hay for kw in _UK_KEYWORDS):
        detected.append("uk")
    if any(kw in hay for kw in _CANADA_KEYWORDS):
        detected.append("canada")

    return detected or ["general"]


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]}"


def label_kinship_steps(m: int, n: int) -> str:
    """Return a human-readable genealogical relationship label for upward step counts (m, n)."""
    if m == 0 and n == 0:
        return "Self"
    if m == 0:
        if n == 1:
            return "Parent"
        if n == 2:
            return "Grandparent"
        if n == 3:
            return "Great-Grandparent"
        return f"{n - 2}x Great-Grandparent"
    if n == 0:
        if m == 1:
            return "Child"
        if m == 2:
            return "Grandchild"
        if m == 3:
            return "Great-Grandchild"
        return f"{m - 2}x Great-Grandchild"
    if m == 1 and n == 1:
        return "Sibling (via shared parent)"
    if m == 1 and n == 2:
        return "Aunt/Uncle (to second person)"
    if m == 2 and n == 1:
        return "Niece/Nephew (of second person)"
    if min(m, n) == 1:
        k = max(m, n)
        prefix = "Great-" if k == 3 else f"{k - 2}x Great-"
        return f"{prefix}Aunt/Uncle" if m == 1 else f"{prefix}Niece/Nephew"

    degree = min(m, n) - 1
    removed = abs(m - n)
    base = f"{_ordinal(degree)} Cousin"
    if removed == 0:
        return base
    return f"{base} {removed}x Removed"


def _bio_parents_map(ds: Dataset) -> dict[str, list[str]]:
    """Map child_id -> sorted list of biological/unknown parent IDs (excluding excluded profiles)."""
    parents: dict[str, list[str]] = defaultdict(list)
    for rel in ds.relationships:
        if rel.type != "parent_child":
            continue
        if rel.subtype in {"adoptive", "step", "foster"}:
            continue
        if rel.person_1 not in ds.individuals or rel.person_2 not in ds.individuals:
            continue
        if rel.person_1 not in parents[rel.person_2]:
            parents[rel.person_2].append(rel.person_1)
    for k in parents:
        parents[k].sort()
    return parents


def _generation_depths(ds: Dataset, parents_map: dict[str, list[str]]) -> dict[str, int]:
    """Compute longest upward depth (0 = founder with no known parents) safely even if cycles exist."""
    depths: dict[str, int] = {}

    def depth(node: str, visiting: set[str]) -> int:
        if node in depths:
            return depths[node]
        if node in visiting:
            return 0
        visiting.add(node)
        ps = parents_map.get(node, [])
        d = 0 if not ps else 1 + max(depth(p, visiting) for p in ps)
        visiting.remove(node)
        depths[node] = d
        return d

    for iid in ds.individuals:
        depth(iid, set())
    return depths


def _build_coancestry_calculator(ds: Dataset) -> tuple[Any, Any]:
    """Return (coancestry(a, b), inbreeding(x)) using Malecot's recursive recurrence."""
    parents_map = _bio_parents_map(ds)
    depths = _generation_depths(ds, parents_map)

    @cache
    def coancestry(a: str, b: str) -> float:
        if a > b:
            a, b = b, a
        if a == b:
            return 0.5 * (1.0 + inbreeding(a))
        # Expand the deeper node (guaranteed not to be an ancestor of the shallower one in a DAG)
        if depths.get(a, 0) < depths.get(b, 0):
            a, b = b, a
        pa = parents_map.get(a, [])
        if not pa:
            return 0.0
        # Standard diploid assumption: each known parent contributes weight 1/2
        return 0.5 * sum(coancestry(p, b) for p in pa[:2])

    @cache
    def inbreeding(x: str) -> float:
        px = parents_map.get(x, [])
        if len(px) < 2:
            return 0.0
        return coancestry(px[0], px[1])

    return coancestry, inbreeding


def _all_upward_paths(
    start: str, parents_map: dict[str, list[str]], max_depth: int = 12
) -> dict[str, list[list[str]]]:
    """Enumerate simple upward paths from `start` to every reachable ancestor (including `start`)."""
    paths_by_ancestor: dict[str, list[list[str]]] = defaultdict(list)
    queue: deque[list[str]] = deque([[start]])
    while queue:
        path = queue.popleft()
        curr = path[-1]
        paths_by_ancestor[curr].append(path)
        if len(path) - 1 >= max_depth:
            continue
        for p in parents_map.get(curr, []):
            if p not in path:
                queue.append(path + [p])
    return paths_by_ancestor


@dataclass
class KinshipPath:
    common_ancestor: str
    common_ancestor_name: str
    steps_a: int
    steps_b: int
    label: str
    path_a: list[str]
    path_b: list[str]
    path_contribution: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "common_ancestor": self.common_ancestor,
            "common_ancestor_name": self.common_ancestor_name,
            "steps_a": self.steps_a,
            "steps_b": self.steps_b,
            "label": self.label,
            "path_a": self.path_a,
            "path_b": self.path_b,
            "path_contribution": round(self.path_contribution, 6),
        }


@dataclass
class KinshipReport:
    person_a: str
    person_a_name: str
    person_b: str
    person_b_name: str
    are_married: bool
    coancestry: float
    wright_relationship: float
    expected_offspring_inbreeding: float
    paths: list[KinshipPath] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "person_a": self.person_a,
            "person_a_name": self.person_a_name,
            "person_b": self.person_b,
            "person_b_name": self.person_b_name,
            "are_married": self.are_married,
            "coancestry": round(self.coancestry, 6),
            "wright_relationship": round(self.wright_relationship, 6),
            "expected_offspring_inbreeding": round(self.expected_offspring_inbreeding, 6),
            "path_count": len(self.paths),
            "paths": [p.to_dict() for p in self.paths],
        }


def compute_kinship(
    root: Path | str | Dataset,
    person_a: str,
    person_b: str,
    *,
    max_depth: int = 10,
) -> KinshipReport:
    """Compute all distinct blood kinship paths, coancestry, and Wright's r between two individuals."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    if person_a not in ds.individuals:
        raise KeyError(f"Individual '{person_a}' not found")
    if person_b not in ds.individuals:
        raise KeyError(f"Individual '{person_b}' not found")

    parents_map = _bio_parents_map(ds)
    coancestry_fn, inbreeding_fn = _build_coancestry_calculator(ds)

    are_married = any(
        r.type == "marriage" and {r.person_1, r.person_2} == {person_a, person_b}
        for r in ds.relationships
    )

    paths_a = _all_upward_paths(person_a, parents_map, max_depth=max_depth)
    paths_b = _all_upward_paths(person_b, parents_map, max_depth=max_depth)

    common = set(paths_a.keys()) & set(paths_b.keys())
    found: list[KinshipPath] = []

    for anc in sorted(common):
        for pa in paths_a[anc]:
            interior_a = set(pa[:-1])
            for pb in paths_b[anc]:
                interior_b = set(pb[:-1])
                # Only keep vertex-disjoint paths meeting solely at `anc`
                if interior_a & interior_b:
                    continue
                if anc in interior_a or anc in interior_b:
                    continue
                m = len(pa) - 1
                n = len(pb) - 1
                contrib = (0.5 ** (m + n)) * (1.0 + inbreeding_fn(anc))
                found.append(
                    KinshipPath(
                        common_ancestor=anc,
                        common_ancestor_name=ds.individuals[anc].data.primary_name,
                        steps_a=m,
                        steps_b=n,
                        label=label_kinship_steps(m, n),
                        path_a=pa,
                        path_b=pb,
                        path_contribution=contrib,
                    )
                )

    found.sort(key=lambda p: (p.steps_a + p.steps_b, p.steps_a, p.common_ancestor))

    phi = coancestry_fn(person_a, person_b)
    fa = inbreeding_fn(person_a)
    fb = inbreeding_fn(person_b)
    wright_r = (2.0 * phi) / math.sqrt((1.0 + fa) * (1.0 + fb)) if person_a != person_b else 1.0

    return KinshipReport(
        person_a=person_a,
        person_a_name=ds.individuals[person_a].data.primary_name,
        person_b=person_b,
        person_b_name=ds.individuals[person_b].data.primary_name,
        are_married=are_married,
        coancestry=phi,
        wright_relationship=wright_r,
        expected_offspring_inbreeding=phi,
        paths=found,
    )


@dataclass
class GenerationCollapse:
    generation: int
    theoretical_slots: int
    filled_slots: int
    unique_ancestors: int
    collapse_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "theoretical_slots": self.theoretical_slots,
            "filled_slots": self.filled_slots,
            "unique_ancestors": self.unique_ancestors,
            "collapse_ratio": round(self.collapse_ratio, 4),
        }


@dataclass
class MultiPathAncestor:
    individual_id: str
    primary_name: str
    path_count: int
    generations: list[int]
    paths: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "individual_id": self.individual_id,
            "primary_name": self.primary_name,
            "path_count": self.path_count,
            "generations": self.generations,
            "paths": self.paths,
        }


@dataclass
class CollapseReport:
    individual_id: str
    primary_name: str
    inbreeding_coefficient: float
    generations: list[GenerationCollapse] = field(default_factory=list)
    multi_path_ancestors: list[MultiPathAncestor] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "individual_id": self.individual_id,
            "primary_name": self.primary_name,
            "inbreeding_coefficient": round(self.inbreeding_coefficient, 6),
            "generations": [g.to_dict() for g in self.generations],
            "multi_path_ancestors": [a.to_dict() for a in self.multi_path_ancestors],
        }


def compute_pedigree_collapse(
    root: Path | str | Dataset,
    individual_id: str,
    *,
    max_gen: int = 8,
) -> CollapseReport:
    """Compute per-generation pedigree collapse, multi-path ancestors, and Wright's F."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    if individual_id not in ds.individuals:
        raise KeyError(f"Individual '{individual_id}' not found")

    parents_map = _bio_parents_map(ds)
    _, inbreeding_fn = _build_coancestry_calculator(ds)
    paths_by_anc = _all_upward_paths(individual_id, parents_map, max_depth=max_gen)

    # Per-generation slot counts
    gen_slots: dict[int, list[str]] = defaultdict(list)
    for anc, paths in paths_by_anc.items():
        if anc == individual_id:
            continue
        for p in paths:
            g = len(p) - 1
            if 1 <= g <= max_gen:
                gen_slots[g].append(anc)

    generations: list[GenerationCollapse] = []
    for g in sorted(gen_slots.keys()):
        slots = gen_slots[g]
        uniq = len(set(slots))
        filled = len(slots)
        collapse_ratio = (1.0 - (uniq / filled)) if filled > 0 else 0.0
        generations.append(
            GenerationCollapse(
                generation=g,
                theoretical_slots=2**g,
                filled_slots=filled,
                unique_ancestors=uniq,
                collapse_ratio=collapse_ratio,
            )
        )

    multi_path: list[MultiPathAncestor] = []
    for anc, paths in sorted(paths_by_anc.items()):
        if anc == individual_id or len(paths) < 2:
            continue
        gens = sorted({len(p) - 1 for p in paths})
        multi_path.append(
            MultiPathAncestor(
                individual_id=anc,
                primary_name=ds.individuals[anc].data.primary_name,
                path_count=len(paths),
                generations=gens,
                paths=paths,
            )
        )
    multi_path.sort(key=lambda a: (-a.path_count, min(a.generations), a.individual_id))

    return CollapseReport(
        individual_id=individual_id,
        primary_name=ds.individuals[individual_id].data.primary_name,
        inbreeding_coefficient=inbreeding_fn(individual_id),
        generations=generations,
        multi_path_ancestors=multi_path,
    )


def find_consanguineous_marriages(ds: Dataset) -> list[KinshipReport]:
    """Find all marriage edges where spouses share at least one blood ancestor."""
    results: list[KinshipReport] = []
    for rel in ds.relationships:
        if rel.type != "marriage":
            continue
        if rel.person_1 not in ds.individuals or rel.person_2 not in ds.individuals:
            continue
        rep = compute_kinship(ds, rel.person_1, rel.person_2)
        if rep.paths:
            results.append(rep)
    return results


def _extract_discrepancy_bullets(body: str) -> list[str]:
    """Extract bullet lines under `## Recorded Discrepancies`."""
    m = re.search(r"^##\s+Recorded Discrepancies\s*$", body, re.MULTILINE | re.IGNORECASE)
    if not m:
        return []
    rest = body[m.end() :]
    next_h = re.search(r"^##\s+", rest, re.MULTILINE)
    section = rest[: next_h.start()] if next_h else rest
    bullets = []
    for line in section.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            item = s[2:].strip()
            if item and not item.lower().startswith("no conflicts"):
                bullets.append(item)
    return bullets


def compute_research_gaps(
    root: Path | str | Dataset,
    *,
    regions: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze region-aware census/parish/civil gaps, promotion-ready profiles, discrepancies, and excluded leads."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)

    census_gaps: list[dict[str, Any]] = []
    record_gaps: list[dict[str, Any]] = []
    promotion_ready: list[dict[str, Any]] = []
    discrepancies: list[dict[str, Any]] = []

    for iid in sorted(ds.individuals.keys()):
        ind = ds.individuals[iid]
        d = ind.data
        if d.status == "excluded":
            continue

        ind_regions = detect_individual_regions(ds, iid, regions_override=regions)

        all_sids = set(d.all_source_ids())
        for rel in ds.relationships:
            if iid in (rel.person_1, rel.person_2):
                all_sids.update(rel.sources)

        valid_srcs = [
            ds.sources[sid].data
            for sid in all_sids
            if sid in ds.sources and ds.sources[sid].data.relevance != "excluded"
        ]

        # Region-aware census gap check
        b_date = parse_genealogy_date(d.birth.date) if d.birth else None
        d_date = parse_genealogy_date(d.death.date) if d.death else None
        b_year = b_date.year_min if b_date else None
        d_year = d_date.year_max if d_date else (1950 if d.living else (b_year + 85 if b_year else None))

        applicable_census_years: set[int] = set()
        for reg in ind_regions:
            applicable_census_years.update(REGIONAL_CENSUS_YEARS.get(reg, []))

        if applicable_census_years and b_year is not None and d_year is not None:
            expected_years = [y for y in sorted(applicable_census_years) if b_year <= y <= d_year]
            covered_years: set[int] = set()
            for s in valid_srcs:
                if s.record_type != "census":
                    continue
                sd = parse_genealogy_date(s.date)
                if sd and sd.year_min:
                    covered_years.add(sd.year_min)
            missing = [y for y in expected_years if y not in covered_years]
            if missing:
                census_gaps.append(
                    {
                        "individual_id": iid,
                        "primary_name": d.primary_name,
                        "regions": ind_regions,
                        "lifespan": f"{d.birth.date if d.birth else '?'} - {'Living' if d.living else (d.death.date if d.death else '?')}",
                        "covered_censuses": sorted(covered_years),
                        "missing_censuses": missing,
                    }
                )

        # Region-aware vital / parish record gaps (especially for Mexico / international lines)
        missing_milestones: list[str] = []
        has_church = any(s.record_type == "church" for s in valid_srcs) or any(
            ev.type == "baptism" for ev in d.events
        )
        has_vital = any(s.record_type == "vital" for s in valid_srcs)
        if "mexico" in ind_regions:
            if not has_church:
                missing_milestones.append(
                    "Catholic Parish record (Bautismo / Matrimonio / Defunción)"
                )
            if ((b_year and b_year >= 1859) or (d_year and d_year >= 1859)) and not has_vital:
                missing_milestones.append(
                    "Mexican Registro Civil (Acta de Nacimiento / Matrimonio / Defunción, 1859+)"
                )
        elif ind_regions == ["general"] and not has_vital and not has_church:
            missing_milestones.append("Primary vital or church record (birth/baptism or death/burial)")

        if missing_milestones:
            record_gaps.append(
                {
                    "individual_id": iid,
                    "primary_name": d.primary_name,
                    "regions": ind_regions,
                    "missing_records": missing_milestones,
                }
            )

        # Promotion-ready check
        if d.status == "tentative":
            orig_sources = [
                sid
                for sid in sorted(all_sids)
                if sid in ds.sources
                and ds.sources[sid].data.relevance != "excluded"
                and ds.sources[sid].data.record_type != "compiled"
                and ds.sources[sid].data.source_type != "authored"
            ]
            if len(orig_sources) >= 2:
                promotion_ready.append(
                    {
                        "individual_id": iid,
                        "primary_name": d.primary_name,
                        "non_compiled_sources": orig_sources,
                    }
                )

        # Discrepancies digest
        for bullet in _extract_discrepancy_bullets(ind.body):
            discrepancies.append(
                {
                    "individual_id": iid,
                    "primary_name": d.primary_name,
                    "note": bullet,
                }
            )

    pending_sources = [
        {"source_id": sid, "title": src.data.title, "artifact_path": src.data.artifact_path}
        for sid, src in sorted(ds.sources.items())
        if src.data.relevance == "pending"
    ]
    excluded_sources = [
        {
            "source_id": sid,
            "title": src.data.title,
            "excluded_for": src.data.excluded_for,
            "exclusion_reason": src.data.exclusion_reason or "",
        }
        for sid, src in sorted(ds.sources.items())
        if src.data.relevance == "excluded"
    ]
    consanguineous = []
    for rep in find_consanguineous_marriages(ds):
        d_rep = rep.to_dict()
        r_a = detect_individual_regions(ds, rep.person_a, regions_override=regions)
        r_b = detect_individual_regions(ds, rep.person_b, regions_override=regions)
        if "mexico" in r_a or "mexico" in r_b:
            d_rep["recommended_record"] = (
                "Search diocesan Informaciones / Dispensas Matrimoniales "
                "(often includes a drawn 3–4 generation pedigree tree & elderly witness testimony)."
            )
        consanguineous.append(d_rep)

    return {
        "root": str(ds.root),
        "census_gaps": census_gaps,
        "record_gaps": record_gaps,
        "promotion_ready": promotion_ready,
        "discrepancies": discrepancies,
        "pending_sources": pending_sources,
        "excluded_sources": excluded_sources,
        "consanguineous_marriages": consanguineous,
    }


def build_family_report_markdown(
    root: Path | str | Dataset,
    individual_id: str,
    *,
    redact_living: bool = True,
) -> str:
    """Generate a printable/shareable Markdown Family Group Sheet & Research Dossier."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    if individual_id not in ds.individuals:
        raise KeyError(f"Individual '{individual_id}' not found")

    ind_obj = ds.individuals[individual_id]
    ind = ind_obj.data

    def fmt_person(iid: str) -> str:
        if iid not in ds.individuals:
            return iid
        p = ds.individuals[iid].data
        if redact_living and p.living:
            return f"Living (Redacted) (`{iid}`)"
        b = p.birth.date if p.birth and p.birth.date else "?"
        d = "Living" if p.living else (p.death.date if p.death and p.death.date else "?")
        return f"**{p.primary_name}** ({b} – {d}) [`{iid}`]"

    if redact_living and ind.living:
        return (
            f"# Family Group Sheet: Living (Redacted) (`{ind.id}`)\n\n"
            "Details redacted because this individual is marked `living: true`. "
            "Pass `--include-living` to generate an unredacted private copy.\n"
        )

    parents = [
        r.person_1 for r in ds.relationships if r.type == "parent_child" and r.person_2 == individual_id
    ]
    children = [
        r.person_2 for r in ds.relationships if r.type == "parent_child" and r.person_1 == individual_id
    ]
    marriages = [
        r for r in ds.relationships if r.type == "marriage" and individual_id in (r.person_1, r.person_2)
    ]
    fans = [
        r for r in ds.relationships if r.type == "fan_associate" and individual_id in (r.person_1, r.person_2)
    ]
    excluded_leads = [
        src.data for src in ds.sources.values() if src.data.relevance == "excluded" and individual_id in src.data.excluded_for
    ]

    collapse = compute_pedigree_collapse(ds, individual_id)

    # Collect cited sources for numbered footnotes
    cited_sids: list[str] = []

    def _add_sids(sids: list[str]) -> None:
        for s in sids:
            if s not in cited_sids and s in ds.sources:
                cited_sids.append(s)

    if ind.birth:
        _add_sids(ind.birth.sources)
    if ind.death:
        _add_sids(ind.death.sources)
    for ev in ind.events:
        _add_sids(ev.sources)
    for rel in ds.relationships:
        if individual_id in (rel.person_1, rel.person_2):
            _add_sids(rel.sources)

    lines = [
        f"# Family Group Sheet: {ind.primary_name} (`{ind.id}`)",
        "",
        f"- **GPS Status:** `{ind.status}` | **Sex:** `{ind.sex}`",
    ]
    if ind.name_variants:
        lines.append(f"- **Observed Name Variants:** {', '.join(ind.name_variants)}")
    if ind.birth:
        lines.append(
            f"- **Birth:** {ind.birth.date or 'Unknown'} — {ind.birth.place or 'Unknown'}"
        )
    if ind.death:
        lines.append(
            f"- **Death:** {ind.death.date or 'Unknown'} — {ind.death.place or 'Unknown'}"
        )
    if ind.external_ids:
        ext = ", ".join(f"{k}: `{v}`" for k, v in ind.external_ids.items())
        lines.append(f"- **External Tree IDs (leads):** {ext}")

    lines.extend(["", "## Parents"])
    if parents:
        for pid in parents:
            lines.append(f"- {fmt_person(pid)}")
    else:
        lines.append("- *None recorded*")

    lines.extend(["", "## Marriages & Spouses"])
    if marriages:
        for m in marriages:
            other = m.person_2 if m.person_1 == individual_id else m.person_1
            kin = compute_kinship(ds, individual_id, other) if other in ds.individuals else None
            kin_note = ""
            if kin and kin.paths:
                labels = ", ".join(sorted({p.label for p in kin.paths}))
                kin_note = f" *(Consanguineous: {labels}; r={kin.wright_relationship:.4f})*"
            lines.append(
                f"- {fmt_person(other)} — m. {m.date or 'Unknown'} ({m.place or 'Unknown'}) [{m.confidence}]{kin_note}"
            )
    else:
        lines.append("- *None recorded*")

    lines.extend(["", "## Children"])
    if children:
        for cid in children:
            lines.append(f"- {fmt_person(cid)}")
    else:
        lines.append("- *None recorded*")

    if collapse.multi_path_ancestors or collapse.inbreeding_coefficient > 0:
        lines.extend(
            [
                "",
                "## Pedigree Collapse & Inosculation",
                f"- **Wright's Inbreeding Coefficient (F):** `{collapse.inbreeding_coefficient:.5f}`",
            ]
        )
        for mpa in collapse.multi_path_ancestors:
            lines.append(
                f"- **{mpa.primary_name}** (`{mpa.individual_id}`): appears **×{mpa.path_count}** across generations {mpa.generations}"
            )

    if fans:
        lines.extend(["", "## FAN Club Associates"])
        for f in fans:
            other = f.person_2 if f.person_1 == individual_id else f.person_1
            lines.append(f"- {fmt_person(other)} — {f.notes or 'Associate'} ({f.date or 'undated'})")

    lines.extend(["", "## Notes & Discrepancies", ind_obj.body.strip()])

    if excluded_leads:
        lines.extend(["", "## Excluded / Ruled-Out Leads"])
        for ex in excluded_leads:
            lines.append(
                f"- **`{ex.id}`** — {ex.title}: {ex.exclusion_reason or 'Ruled out as unrelated'}"
            )

    lines.extend(["", "## Sources & Provenance Links"])
    if cited_sids:
        for idx, sid in enumerate(cited_sids, start=1):
            src = ds.sources[sid].data
            meta_bits = [b for b in (src.record_type, src.source_type, src.information_type) if b]
            lines.append(f"{idx}. **`{sid}`** — {src.title} *({', '.join(meta_bits)})*")
            if src.repository:
                lines.append(f"   - Repository: {src.repository}")
            if src.artifact_path:
                lines.append(f"   - Local artifact: `{src.artifact_path}`")
            if src.url:
                lines.append(f"   - URL: {src.url}")
            for lnk in src.links:
                auth = " *(login required)*" if lnk.requires_auth else ""
                acc = f", accessed {lnk.accessed}" if lnk.accessed else ""
                note = f" — {lnk.note}" if lnk.note else ""
                lines.append(f"   - [{lnk.provider}]{auth}: {lnk.url}{acc}{note}")
    else:
        lines.append("- *No sources cited*")

    return "\n".join(lines) + "\n"


def _canonical_tract_group(tract: str) -> str:
    """Group partitioned sub-tracts (e.g. 'French Broad River — East 60 acres') under their parent tract."""
    for sep in (" — ", " -- ", " - "):
        if sep in tract:
            return tract.split(sep, 1)[0].strip()
    return tract.strip()


def compute_land_tracts(
    root: Path | str | Dataset,
    *,
    tract_filter: str | None = None,
) -> dict[str, Any]:
    """Build chronological chain-of-title records grouped by canonical land tract."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    filt = tract_filter.strip().lower() if tract_filter else None

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for iid in sorted(ds.individuals.keys()):
        ind = ds.individuals[iid].data
        if ind.status == "excluded":
            continue
        for ev in ind.events:
            if not ev.tract or not ev.tract.strip():
                continue
            raw_tract = ev.tract.strip()
            group_name = _canonical_tract_group(raw_tract)
            if filt and filt not in raw_tract.lower() and filt not in group_name.lower():
                continue
            parsed = parse_genealogy_date(ev.date)
            sort_key = parsed.sort_key if parsed else (9999, 99, 99)
            grouped[group_name].append(
                {
                    "_sort": sort_key,
                    "date": ev.date or "Undated",
                    "individual_id": iid,
                    "primary_name": ind.primary_name,
                    "event_type": ev.type,
                    "tract": raw_tract,
                    "place": ev.place or "",
                    "description": ev.description or "",
                    "sources": list(ev.sources),
                }
            )

    tracts_out: list[dict[str, Any]] = []
    for group_name in sorted(grouped.keys()):
        entries = sorted(grouped[group_name], key=lambda e: (e["_sort"], e["individual_id"]))
        clean_entries = [{k: v for k, v in e.items() if k != "_sort"} for e in entries]
        inds = sorted({e["individual_id"] for e in clean_entries})
        srcs = sorted({s for e in clean_entries for s in e["sources"]})
        tracts_out.append(
            {
                "tract_group": group_name,
                "individuals_involved": inds,
                "source_ids": srcs,
                "event_count": len(clean_entries),
                "entries": clean_entries,
            }
        )

    return {
        "root": str(ds.root),
        "tract_count": len(tracts_out),
        "tracts": tracts_out,
    }


def compute_dna_corroboration(
    root: Path | str | Dataset,
    person_a: str,
    person_b: str,
    *,
    observed_cm: float | None = None,
    genome_cm: float = 6800.0,
    max_depth: int = 10,
) -> dict[str, Any]:
    """Compare endogamy-aware expected shared autosomal DNA (cM) across all paths vs observed cM."""
    kin = compute_kinship(root, person_a, person_b, max_depth=max_depth)
    # Expected shared autosomal DNA from Malecot coancestry: 2 * phi * genome_cm
    expected_cm = round(2.0 * kin.coancestry * genome_cm, 1)

    path_breakdown = [
        {
            "common_ancestor": p.common_ancestor,
            "common_ancestor_name": p.common_ancestor_name,
            "label": p.label,
            "steps_a": p.steps_a,
            "steps_b": p.steps_b,
            "expected_cm": round(p.path_contribution * genome_cm, 1),
        }
        for p in kin.paths
    ]

    # Detect potential IBD2 overlap (full siblings or double-cousin-like multiple paths)
    sibling_paths = [p for p in kin.paths if p.steps_a == 1 and p.steps_b == 1]
    has_ibd2_note = len(sibling_paths) >= 2 or len(kin.paths) >= 4

    if expected_cm <= 0.0:
        low_cm, high_cm = 0.0, 0.0
        upper_ceiling = 0.0
    elif len(kin.paths) == 1 and {kin.paths[0].steps_a, kin.paths[0].steps_b} == {0, 1}:
        low_cm, high_cm = 3150.0, 3650.0
        upper_ceiling = 3750.0
    else:
        min_meioses = min((p.steps_a + p.steps_b) for p in kin.paths) if kin.paths else 6
        rel_spread = min(0.85, max(0.28, 0.25 + 0.09 * min_meioses))
        low_cm = max(0.0, round(expected_cm * (1.0 - rel_spread), 1))
        high_cm = min(round(genome_cm * 0.55, 1), round(expected_cm * (1.0 + rel_spread), 1))
        upper_ceiling = min(round(genome_cm * 0.58, 1), round(max(high_cm, expected_cm * 1.45 + 35.0), 1))

    assessment: str | None = None
    interpretation = ""
    if observed_cm is not None:
        if expected_cm <= 0.0:
            if observed_cm > 0.0:
                assessment = "unexplained_match"
                interpretation = (
                    f"Observed {observed_cm:.1f} cM with 0 documented blood paths in data/. "
                    "Investigate an undocumented ancestral connection or FAN/collateral link."
                )
            else:
                assessment = "consistent"
                interpretation = "0 cM observed matches 0 documented blood paths."
        elif observed_cm > upper_ceiling:
            assessment = "higher_than_expected"
            interpretation = (
                f"Observed {observed_cm:.1f} cM exceeds the multi-path expectation "
                f"({expected_cm:.1f} cM; band {low_cm:.1f}–{high_cm:.1f} cM). "
                "Strong signal of an undiscovered additional cousin path (deeper endogamy) or a closer relationship."
            )
        elif observed_cm < low_cm:
            assessment = "lower_than_expected"
            interpretation = (
                f"Observed {observed_cm:.1f} cM falls below the documented expectation "
                f"({expected_cm:.1f} cM; band {low_cm:.1f}–{high_cm:.1f} cM). "
                "Check for a half-relationship, non-paternity event (NPE), adoption, or misattached branch."
            )
        else:
            assessment = "consistent"
            interpretation = (
                f"Observed {observed_cm:.1f} cM is consistent with the {len(kin.paths)} documented "
                f"blood path(s) (expected ~{expected_cm:.1f} cM; band {low_cm:.1f}–{high_cm:.1f} cM)."
            )

    notes: list[str] = []
    if len(kin.paths) > 1:
        notes.append(
            "Endogamy effect: standard single-path DNA charts overestimate closeness because "
            f"shared DNA compounds across all {len(kin.paths)} known blood paths."
        )
    if has_ibd2_note:
        notes.append(
            "IBD2 caution: Full siblings and multi-path double cousins share fully identical (IBD2) regions. "
            "AncestryDNA counts IBD2 segments once (~2,600 cM for full siblings), whereas 23andMe/FTDNA count both copies (~3,400 cM)."
        )

    return {
        "person_a": kin.person_a,
        "person_a_name": kin.person_a_name,
        "person_b": kin.person_b,
        "person_b_name": kin.person_b_name,
        "are_married": kin.are_married,
        "coancestry": round(kin.coancestry, 6),
        "wright_relationship": round(kin.wright_relationship, 6),
        "genome_cm": genome_cm,
        "expected_cm": expected_cm,
        "expected_range_cm": [low_cm, high_cm],
        "observed_cm": observed_cm,
        "assessment": assessment,
        "interpretation": interpretation,
        "path_count": len(path_breakdown),
        "paths": path_breakdown,
        "notes": notes,
    }

