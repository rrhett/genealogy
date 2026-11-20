"""Chronological timeline builder and Mermaid / Graphviz family graph renderer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

from genealogy.loader import Dataset, load_dataset
from genealogy.schema import parse_genealogy_date


@dataclass
class TimelineEntry:
    sort_key: tuple[int, int, int]
    date_display: str
    category: str
    summary: str
    place: str
    sources: list[str]
    age_note: str = ""


def _estimate_age(birth_year: int | None, event_year: int | None) -> str:
    if birth_year is None or event_year is None:
        return ""
    diff = event_year - birth_year
    if diff < 0:
        return ""
    return f"~{diff}"


def build_timeline_markdown(
    root: Path | str,
    individual_id: str,
    *,
    include_family: bool = False,
) -> str:
    """Build a Markdown chronological timeline for an individual."""
    ds = load_dataset(root)
    if individual_id not in ds.individuals:
        raise KeyError(f"Individual '{individual_id}' not found in {ds.data_dir / 'individuals'}")

    ind = ds.individuals[individual_id].data
    b_parsed = parse_genealogy_date(ind.birth.date) if ind.birth else None
    birth_year = b_parsed.year_min if b_parsed else None

    entries: list[TimelineEntry] = []

    def add_entry(
        date_str: str | None,
        category: str,
        summary: str,
        place: str | None,
        sources: list[str],
    ) -> None:
        parsed = parse_genealogy_date(date_str)
        sort_key = parsed.sort_key if parsed else (9999, 99, 99)
        ev_year = parsed.year_min if parsed else None
        entries.append(
            TimelineEntry(
                sort_key=sort_key,
                date_display=date_str or "Undated",
                category=category,
                summary=summary,
                place=place or "",
                sources=sources,
                age_note=_estimate_age(birth_year, ev_year),
            )
        )

    if ind.birth:
        add_entry(ind.birth.date, "Birth", f"Birth of {ind.primary_name}", ind.birth.place, ind.birth.sources)

    for ev in ind.events:
        desc = ev.description or ev.type.replace("_", " ").title()
        if ev.tract:
            desc = f"{desc} [Tract: {ev.tract}]"
        add_entry(ev.date, ev.type.title(), desc, ev.place, ev.sources)

    if ind.death:
        add_entry(ind.death.date, "Death", f"Death of {ind.primary_name}", ind.death.place, ind.death.sources)

    # Relationships involving this person
    for rel in ds.relationships:
        if rel.type == "marriage" and individual_id in (rel.person_1, rel.person_2):
            other_id = rel.person_2 if rel.person_1 == individual_id else rel.person_1
            other_name = (
                ds.individuals[other_id].data.primary_name if other_id in ds.individuals else other_id
            )
            add_entry(rel.date, "Marriage", f"Married {other_name} ({other_id})", rel.place, rel.sources)
        elif include_family and rel.type == "parent_child":
            if rel.person_1 == individual_id and rel.person_2 in ds.individuals:
                child = ds.individuals[rel.person_2].data
                if child.birth:
                    add_entry(
                        child.birth.date,
                        "Family",
                        f"Birth of child {child.primary_name} ({child.id})",
                        child.birth.place,
                        child.birth.sources,
                    )
            elif rel.person_2 == individual_id and rel.person_1 in ds.individuals:
                parent = ds.individuals[rel.person_1].data
                if parent.death:
                    add_entry(
                        parent.death.date,
                        "Family",
                        f"Death of parent {parent.primary_name} ({parent.id})",
                        parent.death.place,
                        parent.death.sources,
                    )

    entries.sort(key=lambda e: (e.sort_key, e.category))

    lines = [
        f"# Timeline: {ind.primary_name} (`{ind.id}`)",
        "",
        f"- **Status:** {ind.status}",
        f"- **Sex:** {ind.sex}",
    ]
    if ind.name_variants:
        lines.append(f"- **Name Variants:** {', '.join(ind.name_variants)}")
    lines.extend(
        [
            "",
            "| Date | Est. Age | Event | Details | Place | Sources |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for e in entries:
        src_str = ", ".join(f"`{s}`" for s in e.sources) if e.sources else "—"
        lines.append(
            f"| {e.date_display} | {e.age_note or '—'} | {e.category} | {e.summary} | {e.place or '—'} | {src_str} |"
        )
    return "\n".join(lines) + "\n"


def _select_subgraph(
    ds: Dataset, center_id: str | None, depth: int, *, include_excluded: bool = False
) -> set[str]:
    eligible = {
        iid
        for iid, ind in ds.individuals.items()
        if include_excluded or ind.data.status != "excluded"
    }
    if center_id is None:
        return eligible
    if center_id not in ds.individuals:
        raise KeyError(f"Individual '{center_id}' not found")
    eligible.add(center_id)

    adj: dict[str, set[str]] = {iid: set() for iid in eligible}
    for rel in ds.relationships:
        if rel.person_1 in adj and rel.person_2 in adj:
            adj[rel.person_1].add(rel.person_2)
            adj[rel.person_2].add(rel.person_1)

    visited = {center_id}
    queue: deque[tuple[str, int]] = deque([(center_id, 0)])
    while queue:
        curr, dist = queue.popleft()
        if dist >= depth:
            continue
        for nxt in adj.get(curr, set()):
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, dist + 1))
    return visited


def _node_label(ds: Dataset, iid: str) -> str:
    ind = ds.individuals[iid].data
    b = ind.birth.date if ind.birth and ind.birth.date else "?"
    d = "Living" if ind.living else (ind.death.date if ind.death and ind.death.date else "?")
    return f"{ind.primary_name} ({b} - {d})"


def _safe_id(iid: str) -> str:
    return iid.replace("-", "_")


def render_tree(
    root: Path | str,
    center_id: str | None = None,
    *,
    fmt: str = "mermaid",
    depth: int = 3,
) -> str:
    """Render family relationships as Mermaid flowchart or Graphviz DOT."""
    ds = load_dataset(root)
    nodes = _select_subgraph(ds, center_id, depth)

    if fmt == "mermaid":
        lines = ["flowchart TD"]
        for iid in sorted(nodes):
            label = _node_label(ds, iid).replace('"', "'")
            lines.append(f'    {_safe_id(iid)}["{label}"]')
        for rel in ds.relationships:
            if rel.person_1 not in nodes or rel.person_2 not in nodes:
                continue
            a, b = _safe_id(rel.person_1), _safe_id(rel.person_2)
            if rel.type == "parent_child":
                arrow = "-->" if rel.confidence == "proven" else "-.->"
                lbl = f"|{rel.subtype}|" if rel.subtype and rel.subtype != "biological" else ""
                lines.append(f"    {a} {arrow}{lbl} {b}")
            elif rel.type == "marriage":
                lbl = f"m. {rel.date}" if rel.date else "married"
                lines.append(f'    {a} ---|"{lbl}"| {b}')
            elif rel.type == "sibling":
                lines.append(f'    {a} -.-|"sibling"| {b}')
            elif rel.type == "fan_associate":
                lines.append(f'    {a} -.-|"FAN"| {b}')
        return "\n".join(lines) + "\n"

    if fmt == "dot":
        lines = ['digraph Genealogy {', '    rankdir="TB";', '    node [shape=box, style=rounded];']
        for iid in sorted(nodes):
            label = _node_label(ds, iid).replace('"', '\\"')
            lines.append(f'    "{iid}" [label="{label}"];')
        for rel in ds.relationships:
            if rel.person_1 not in nodes or rel.person_2 not in nodes:
                continue
            if rel.type == "parent_child":
                style = "solid" if rel.confidence == "proven" else "dashed"
                lines.append(f'    "{rel.person_1}" -> "{rel.person_2}" [style={style}];')
            elif rel.type == "marriage":
                lbl = f"m. {rel.date}" if rel.date else "married"
                lines.append(f'    "{rel.person_1}" -> "{rel.person_2}" [dir=none, label="{lbl}"];')
            elif rel.type == "sibling":
                lines.append(f'    "{rel.person_1}" -> "{rel.person_2}" [dir=none, style=dotted, label="sibling"];')
            elif rel.type == "fan_associate":
                lines.append(f'    "{rel.person_1}" -> "{rel.person_2}" [dir=none, style=dotted, color=gray, label="FAN"];')
        lines.append("}")
        return "\n".join(lines) + "\n"

    raise ValueError(f"Unsupported format '{fmt}'. Expected 'mermaid' or 'dot'.")
