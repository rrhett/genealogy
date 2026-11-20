"""Interactive local web viewer & static HTML exporter built for inosculated (endogamous) trees."""

from __future__ import annotations

import datetime
import json
import mimetypes
import re
import webbrowser
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from genealogy.analysis import (
    REGIONAL_CENSUS_YEARS,
    _canonical_tract_group,
    _extract_discrepancy_bullets,
    compute_kinship,
    compute_land_tracts,
    compute_pedigree_collapse,
    detect_individual_regions,
    find_consanguineous_marriages,
)
from genealogy.loader import Dataset, load_dataset
from genealogy.schema import parse_genealogy_date

CARD_W = 216
CARD_H = 82
HUB_SIZE = 18
LAYER_GAP_Y = 165
NODE_GAP_X = 256


def _compute_dag_layout(
    ds: Dataset,
    consanguineous_pairs: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Compute a generation-layered DAG + Marriage-Hub layout minimizing crossings."""
    ind_ids = sorted(ds.individuals.keys())
    if not ind_ids:
        return {"nodes": {}, "hubs": [], "edges": [], "width": 800, "height": 500}

    parents_of: dict[str, list[str]] = defaultdict(list)
    children_of: dict[str, list[str]] = defaultdict(list)
    spouses_of: dict[str, list[str]] = defaultdict(list)

    for rel in ds.relationships:
        if rel.person_1 not in ds.individuals or rel.person_2 not in ds.individuals:
            continue
        if rel.type == "parent_child":
            if rel.person_1 not in parents_of[rel.person_2]:
                parents_of[rel.person_2].append(rel.person_1)
            if rel.person_2 not in children_of[rel.person_1]:
                children_of[rel.person_1].append(rel.person_2)
        elif rel.type == "marriage":
            if rel.person_2 not in spouses_of[rel.person_1]:
                spouses_of[rel.person_1].append(rel.person_2)
            if rel.person_1 not in spouses_of[rel.person_2]:
                spouses_of[rel.person_2].append(rel.person_1)

    # Step 1: Longest-path topological rank (0 = earliest known ancestor)
    rank: dict[str, int] = {}

    def calc_rank(iid: str, visiting: set[str]) -> int:
        if iid in rank:
            return rank[iid]
        if iid in visiting:
            return 0
        visiting.add(iid)
        ps = parents_of.get(iid, [])
        r = 0 if not ps else 1 + max(calc_rank(p, visiting) for p in ps)
        visiting.remove(iid)
        rank[iid] = r
        return r

    for iid in ind_ids:
        calc_rank(iid, set())

    # Step 2: Pull rootless spouses down to match their spouse's generation (without violating parent < child)
    for _ in range(4):
        changed = False
        for iid in ind_ids:
            if parents_of.get(iid):
                continue
            sp_ranks = [rank[s] for s in spouses_of.get(iid, []) if s in rank]
            if not sp_ranks:
                continue
            target = max(sp_ranks)
            # Must stay strictly above any child
            child_ranks = [rank[c] for c in children_of.get(iid, []) if c in rank]
            if child_ranks:
                target = min(target, min(child_ranks) - 1)
            if target > rank[iid]:
                rank[iid] = target
                changed = True
        if not changed:
            break

    # Step 3: Pull FAN-only or unconnected founders near their connected associate's rank
    for rel in ds.relationships:
        if rel.type == "fan_associate" and rel.person_1 in rank and rel.person_2 in rank:
            for a, b in ((rel.person_1, rel.person_2), (rel.person_2, rel.person_1)):
                if not parents_of.get(a) and not children_of.get(a) and not spouses_of.get(a):
                    rank[a] = rank[b]

    # Step 4: Group into layers and run barycenter ordering to minimize crossings
    layers: dict[int, list[str]] = defaultdict(list)
    for iid in ind_ids:
        layers[rank[iid]].append(iid)

    # Initial deterministic order: birth year, then surname/id
    def _sort_key(iid: str) -> tuple[int, str]:
        ind = ds.individuals[iid].data
        b = parse_genealogy_date(ind.birth.date) if ind.birth else None
        return (b.year_min if b and b.year_min else 9999, iid)

    sorted_ranks = sorted(layers.keys())
    for r in sorted_ranks:
        layers[r].sort(key=_sort_key)

    # Barycenter sweeps (downward + upward) keeping spouses adjacent
    def _index_map() -> dict[str, float]:
        pos: dict[str, float] = {}
        for r in sorted_ranks:
            for idx, iid in enumerate(layers[r]):
                pos[iid] = float(idx)
        return pos

    for _ in range(6):
        pos = _index_map()
        for r in sorted_ranks[1:]:
            def _bary_up(
                iid: str, _pos: dict[str, float] = pos, _r: int = r
            ) -> tuple[float, str]:
                neighbors = parents_of.get(iid, []) + [
                    s for s in spouses_of.get(iid, []) if rank.get(s) == _r
                ]
                if not neighbors:
                    return (_pos.get(iid, 0.0), iid)
                return (sum(_pos.get(n, 0.0) for n in neighbors) / len(neighbors), iid)

            layers[r].sort(key=_bary_up)

        pos = _index_map()
        for r in reversed(sorted_ranks[:-1]):
            def _bary_down(
                iid: str, _pos: dict[str, float] = pos, _r: int = r
            ) -> tuple[float, str]:
                neighbors = children_of.get(iid, []) + [
                    s for s in spouses_of.get(iid, []) if rank.get(s) == _r
                ]
                if not neighbors:
                    return (_pos.get(iid, 0.0), iid)
                return (sum(_pos.get(n, 0.0) for n in neighbors) / len(neighbors), iid)

            layers[r].sort(key=_bary_down)

    # Assign centered X/Y coordinates per layer
    max_cols = max((len(v) for v in layers.values()), default=1)
    canvas_w = max(960, max_cols * NODE_GAP_X + 160)
    nodes_pos: dict[str, dict[str, Any]] = {}

    for row_idx, r in enumerate(sorted_ranks):
        row = layers[r]
        row_width = (len(row) - 1) * NODE_GAP_X
        start_x = (canvas_w - row_width) / 2.0
        y = 90 + row_idx * LAYER_GAP_Y
        for col_idx, iid in enumerate(row):
            nodes_pos[iid] = {
                "x": round(start_x + col_idx * NODE_GAP_X, 1),
                "y": round(float(y), 1),
                "generation": r,
            }

    canvas_h = max(560, 140 + len(sorted_ranks) * LAYER_GAP_Y)

    # Step 5: Build Marriage / Family Hubs for clean multi-child & inosculated routing
    # Group children by parent tuple, seeded with marriage relationships
    hub_map: dict[tuple[str, ...], dict[str, Any]] = {}
    for rel in ds.relationships:
        if rel.type == "marriage" and rel.person_1 in nodes_pos and rel.person_2 in nodes_pos:
            key = tuple(sorted((rel.person_1, rel.person_2)))
            kin = consanguineous_pairs.get(key)
            hub_map[key] = {
                "parents": list(key),
                "children": [],
                "marriage": {
                    "date": rel.date,
                    "end_date": rel.end_date,
                    "place": rel.place,
                    "confidence": rel.confidence,
                    "sources": rel.sources,
                    "notes": rel.notes,
                },
                "consanguineous": kin is not None,
                "kinship": kin,
            }

    for cid, ps in sorted(parents_of.items()):
        if cid not in nodes_pos:
            continue
        valid_ps = tuple(sorted(p for p in ps if p in nodes_pos))
        if not valid_ps:
            continue
        hub = hub_map.setdefault(
            valid_ps,
            {
                "parents": list(valid_ps),
                "children": [],
                "marriage": None,
                "consanguineous": False,
                "kinship": None,
            },
        )
        if cid not in hub["children"]:
            hub["children"].append(cid)

    # Map parent_child confidence/subtype lookup
    pc_meta: dict[tuple[str, str], dict[str, Any]] = {}
    for rel in ds.relationships:
        if rel.type == "parent_child":
            pc_meta[(rel.person_1, rel.person_2)] = {
                "confidence": rel.confidence,
                "subtype": rel.subtype or "biological",
                "sources": rel.sources,
                "notes": rel.notes,
            }

    hubs: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    for idx, (_key, hdata) in enumerate(sorted(hub_map.items())):
        hid = f"HUB-{idx + 1}"
        ps = hdata["parents"]
        xs = [nodes_pos[p]["x"] for p in ps]
        ys = [nodes_pos[p]["y"] for p in ps]
        hx = round(sum(xs) / len(xs), 1)
        # Place hub slightly below the lower parent card so marriage lines drop neatly
        hy = round(max(ys) + CARD_H / 2 + 28, 1)

        hubs.append(
            {
                "id": hid,
                "x": hx,
                "y": hy,
                "parents": ps,
                "children": hdata["children"],
                "marriage": hdata["marriage"],
                "consanguineous": hdata["consanguineous"],
                "kinship": hdata["kinship"],
            }
        )

        # Parent -> Hub edges
        m_conf = hdata["marriage"]["confidence"] if hdata["marriage"] else "probable"
        for p in ps:
            edges.append(
                {
                    "id": f"E-P-{p}-{hid}",
                    "kind": "spouse_to_hub",
                    "from_type": "individual",
                    "from": p,
                    "to_type": "hub",
                    "to": hid,
                    "confidence": m_conf,
                    "consanguineous": hdata["consanguineous"],
                }
            )

        # Hub -> Child edges
        for c in hdata["children"]:
            # Determine strongest confidence across parents of this hub for child c
            metas = [pc_meta.get((p, c)) for p in ps if (p, c) in pc_meta]
            conf_order = {"proven": 0, "probable": 1, "speculative": 2}
            best_conf = min((m["confidence"] for m in metas), key=lambda x: conf_order.get(x, 2), default="probable")
            subtype = next((m["subtype"] for m in metas if m["subtype"] != "biological"), "biological")
            edges.append(
                {
                    "id": f"E-C-{hid}-{c}",
                    "kind": "hub_to_child",
                    "from_type": "hub",
                    "from": hid,
                    "to_type": "individual",
                    "to": c,
                    "parents": ps,
                    "confidence": best_conf,
                    "subtype": subtype,
                }
            )

    # Direct sibling / FAN associate edges
    for idx, rel in enumerate(ds.relationships):
        if rel.type in {"sibling", "fan_associate"} and rel.person_1 in nodes_pos and rel.person_2 in nodes_pos:
            edges.append(
                {
                    "id": f"E-R-{idx}",
                    "kind": rel.type,
                    "from_type": "individual",
                    "from": rel.person_1,
                    "to_type": "individual",
                    "to": rel.person_2,
                    "confidence": rel.confidence,
                    "notes": rel.notes,
                    "date": rel.date,
                }
            )

    return {
        "nodes": nodes_pos,
        "hubs": hubs,
        "edges": edges,
        "width": canvas_w,
        "height": canvas_h,
        "card_w": CARD_W,
        "card_h": CARD_H,
    }


def _extract_formal_citation(body: str, fallback: str) -> str:
    """Extract the prose under `## Formal Citation` (or return fallback)."""
    m = re.search(r"^##\s+Formal Citation\s*$", body, re.MULTILINE | re.IGNORECASE)
    if not m:
        return fallback
    rest = body[m.end() :]
    next_h = re.search(r"^##\s+", rest, re.MULTILINE)
    section = (rest[: next_h.start()] if next_h else rest).strip()
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
    return " ".join(lines) if lines else fallback


def _build_living_redactor(ds: Dataset) -> Any:
    """Return a function that replaces living individuals' names/variants with '[Living]'."""
    names: set[str] = set()
    for ind in ds.individuals.values():
        if ind.data.living:
            if ind.data.primary_name.strip():
                names.add(ind.data.primary_name.strip())
            for v in ind.data.name_variants:
                if v.strip():
                    names.add(v.strip())
    if not names:
        return lambda text: text
    pattern = re.compile(
        "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)),
        re.IGNORECASE,
    )
    return lambda text: pattern.sub("[Living]", text) if text else text


def build_viewer_payload(
    root: Path | str | Dataset,
    *,
    redact_living: bool = True,
) -> dict[str, Any]:
    """Build the full JSON payload consumed by the interactive HTML viewer."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    scrub = _build_living_redactor(ds) if redact_living else (lambda t: t)

    consanguineous_list = find_consanguineous_marriages(ds)
    consanguineous_pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for rep in consanguineous_list:
        key = tuple(sorted((rep.person_a, rep.person_b)))
        consanguineous_pairs[key] = rep.to_dict()

    layout = _compute_dag_layout(ds, consanguineous_pairs)

    # Pre-index excluded leads per individual
    excluded_by_ind: dict[str, list[str]] = defaultdict(list)
    for sid, src in ds.sources.items():
        if src.data.relevance == "excluded":
            for iid in src.data.excluded_for:
                excluded_by_ind[iid].append(sid)

    individuals_out: dict[str, dict[str, Any]] = {}
    for iid in sorted(ds.individuals.keys()):
        loaded = ds.individuals[iid]
        d = loaded.data
        collapse = compute_pedigree_collapse(ds, iid)
        collapse_dict = collapse.to_dict()

        # Collect all linked sources (profile + relationships)
        sids = set(d.all_source_ids())
        for rel in ds.relationships:
            if iid in (rel.person_1, rel.person_2):
                sids.update(rel.sources)

        b_parsed = parse_genealogy_date(d.birth.date) if d.birth else None
        d_parsed = parse_genealogy_date(d.death.date) if d.death else None
        b_year = b_parsed.year_min if b_parsed else None
        d_year = d_parsed.year_max if d_parsed else None

        covered_censuses: set[int] = set()
        for sid in sids:
            src_obj = ds.sources.get(sid)
            if src_obj and src_obj.data.record_type == "census" and src_obj.data.relevance != "excluded":
                sd = parse_genealogy_date(src_obj.data.date)
                if sd and sd.year_min:
                    covered_censuses.add(sd.year_min)

        ind_regions = detect_individual_regions(ds, iid)
        applicable_censuses: set[int] = set()
        for reg in ind_regions:
            applicable_censuses.update(REGIONAL_CENSUS_YEARS.get(reg, []))

        ind_tracts = sorted({_canonical_tract_group(ev.tract) for ev in d.events if ev.tract and ev.tract.strip()})

        if redact_living and d.living:
            collapse_dict["primary_name"] = "Living (Redacted)"
            individuals_out[iid] = {
                "id": iid,
                "primary_name": "Living (Redacted)",
                "name_variants": [],
                "sex": "U",
                "living": True,
                "redacted": True,
                "status": d.status,
                "birth": None,
                "death": None,
                "birth_year": None,
                "death_year": None,
                "regions": ind_regions,
                "applicable_census_years": sorted(applicable_censuses),
                "covered_census_years": [],
                "tracts": [],
                "events": [],
                "external_ids": {},
                "body_markdown": "Details redacted because this individual is marked `living: true`.",
                "discrepancies": [],
                "source_ids": [],
                "excluded_source_ids": [],
                "inbreeding_f": round(collapse.inbreeding_coefficient, 6),
                "collapse": collapse_dict,
            }
        else:
            scrubbed_body = scrub(loaded.body.strip())
            individuals_out[iid] = {
                "id": iid,
                "primary_name": d.primary_name,
                "name_variants": d.name_variants,
                "sex": d.sex,
                "living": d.living,
                "redacted": False,
                "status": d.status,
                "birth": d.birth.model_dump() if d.birth else None,
                "death": d.death.model_dump() if d.death else None,
                "birth_year": b_year,
                "death_year": d_year,
                "regions": ind_regions,
                "applicable_census_years": sorted(applicable_censuses),
                "covered_census_years": sorted(covered_censuses),
                "tracts": ind_tracts,
                "events": [ev.model_dump() for ev in d.events],
                "external_ids": d.external_ids,
                "body_markdown": scrubbed_body,
                "discrepancies": [scrub(b) for b in _extract_discrepancy_bullets(loaded.body)],
                "source_ids": sorted(sids),
                "excluded_source_ids": sorted(excluded_by_ind.get(iid, [])),
                "inbreeding_f": round(collapse.inbreeding_coefficient, 6),
                "collapse": collapse_dict,
            }

    sources_out: dict[str, dict[str, Any]] = {}
    for sid in sorted(ds.sources.keys()):
        loaded = ds.sources[sid]
        s = loaded.data
        fallback_cite = f"{s.title}" + (f"; {s.repository}" if s.repository else "")
        sources_out[sid] = {
            "id": sid,
            "title": scrub(s.title),
            "record_type": s.record_type,
            "date": s.date,
            "jurisdiction": s.jurisdiction,
            "repository": s.repository,
            "url": s.url,
            "links": [lnk.model_dump() for lnk in s.links],
            "artifact_path": s.artifact_path,
            "artifact_sha256": s.artifact_sha256,
            "source_type": s.source_type,
            "information_type": s.information_type,
            "relevance": s.relevance,
            "excluded_for": s.excluded_for,
            "exclusion_reason": scrub(s.exclusion_reason) if s.exclusion_reason else None,
            "individuals_cited": s.individuals_cited,
            "formal_citation": scrub(_extract_formal_citation(loaded.body, fallback_cite)),
            "body_markdown": scrub(loaded.body.strip()),
        }

    relationships_out = [r.model_dump() for r in ds.relationships]

    tracts_raw = compute_land_tracts(ds)["tracts"]
    if redact_living:
        for tr in tracts_raw:
            for entry in tr["entries"]:
                iid = entry["individual_id"]
                if iid in ds.individuals and ds.individuals[iid].data.living:
                    entry["primary_name"] = "Living (Redacted)"
                entry["description"] = scrub(entry["description"])

    # Compute dynamic era slider bounds from dataset years (supports colonial 1600s trees!)
    all_years = [
        y
        for ind in individuals_out.values()
        for y in (ind["birth_year"], ind["death_year"])
        if isinstance(y, int)
    ]
    era_min = max(1500, ((min(all_years) // 10) * 10)) if all_years else 1790
    era_max = min(2020, max(1950, ((max(all_years) // 10) + 1) * 10)) if all_years else 1960
    all_applicable_censuses = sorted(
        {y for ind in individuals_out.values() for y in ind.get("applicable_census_years", [])}
    )

    return {
        "meta": {
            "workspace_name": ds.root.name,
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "redact_living": redact_living,
            "era_min": era_min,
            "era_max": era_max,
            "census_years": all_applicable_censuses,
            "counts": {
                "individuals": len(ds.individuals),
                "sources": len(ds.sources),
                "relationships": len(ds.relationships),
                "consanguineous_marriages": len(consanguineous_list),
                "tracts": len(tracts_raw),
            },
        },
        "individuals": individuals_out,
        "sources": sources_out,
        "relationships": relationships_out,
        "consanguineous_marriages": [r.to_dict() for r in consanguineous_list],
        "tracts": tracts_raw,
        "layout": layout,
    }


_VIEWER_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Genealogy Interactive Tree &amp; Inosculation Viewer</title>
  <style>
    :root {
      --bg-light: #f6f7f9;
      --bg-dark: #0f131a;
      --surface-light: #ffffff;
      --surface-dark: #181e28;
      --surface-alt-light: #f0f3f7;
      --surface-alt-dark: #202836;
      --border-light: #d5dce6;
      --border-dark: #2f3a4d;
      --text-light: #161d26;
      --text-dark: #e8edf5;
      --muted-light: #5a6678;
      --muted-dark: #94a3b8;
      --accent-light: #1d4ed8;
      --accent-dark: #60a5fa;
      --confirmed-light: #047857;
      --confirmed-dark: #34d399;
      --tentative-light: #b45309;
      --tentative-dark: #fbbf24;
      --hypo-light: #64748b;
      --hypo-dark: #94a3b8;
      --consang-light: #be123c;
      --consang-dark: #fb7185;
      --collapse-light: #7c3aed;
      --collapse-dark: #a78bfa;
      --fan-light: #4338ca;
      --fan-dark: #818cf8;

      --bg: var(--bg-light);
      --surface: var(--surface-light);
      --surface-alt: var(--surface-alt-light);
      --border: var(--border-light);
      --text: var(--text-light);
      --muted: var(--muted-light);
      --accent: var(--accent-light);
      --confirmed: var(--confirmed-light);
      --tentative: var(--tentative-light);
      --hypo: var(--hypo-light);
      --consang: var(--consang-light);
      --collapse: var(--collapse-light);
      --fan: var(--fan-light);

      @media (prefers-color-scheme: dark) {
        --bg: var(--bg-dark);
        --surface: var(--surface-dark);
        --surface-alt: var(--surface-alt-dark);
        --border: var(--border-dark);
        --text: var(--text-dark);
        --muted: var(--muted-dark);
        --accent: var(--accent-dark);
        --confirmed: var(--confirmed-dark);
        --tentative: var(--tentative-dark);
        --hypo: var(--hypo-dark);
        --consang: var(--consang-dark);
        --collapse: var(--collapse-dark);
        --fan: var(--fan-dark);
      }

      @supports (color: light-dark(white, black)) {
        --bg: light-dark(var(--bg-light), var(--bg-dark));
        --surface: light-dark(var(--surface-light), var(--surface-dark));
        --surface-alt: light-dark(var(--surface-alt-light), var(--surface-alt-dark));
        --border: light-dark(var(--border-light), var(--border-dark));
        --text: light-dark(var(--text-light), var(--text-dark));
        --muted: light-dark(var(--muted-light), var(--muted-dark));
        --accent: light-dark(var(--accent-light), var(--accent-dark));
        --confirmed: light-dark(var(--confirmed-light), var(--confirmed-dark));
        --tentative: light-dark(var(--tentative-light), var(--tentative-dark));
        --hypo: light-dark(var(--hypo-light), var(--hypo-dark));
        --consang: light-dark(var(--consang-light), var(--consang-dark));
        --collapse: light-dark(var(--collapse-light), var(--collapse-dark));
        --fan: light-dark(var(--fan-light), var(--fan-dark));
      }

      color-scheme: light dark;
      accent-color: var(--accent);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }

    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--text); }

    .app {
      display: grid;
      grid-template-rows: auto 1fr;
      height: 100vh;
      overflow: hidden;
    }

    header.toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.6rem;
      padding: 0.6rem 1rem;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      z-index: 5;
    }

    .brand {
      font-weight: 700;
      font-size: 0.95rem;
      margin-right: 0.5rem;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .brand small { font-weight: 500; color: var(--muted); }

    .toolbar input[type="search"] {
      padding: 0.38rem 0.65rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--surface-alt);
      color: var(--text);
      min-width: 220px;
      font-size: 0.86rem;
    }

    .toolbar label.toggle {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-size: 0.82rem;
      color: var(--text);
      cursor: pointer;
      padding: 0.28rem 0.5rem;
      border-radius: 6px;
      background: var(--surface-alt);
      border: 1px solid var(--border);
      user-select: none;
    }

    .toolbar select, .toolbar button, .btn {
      font-size: 0.82rem;
      padding: 0.36rem 0.65rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--surface-alt);
      color: var(--text);
      cursor: pointer;
    }
    .toolbar button:hover, .btn:hover { border-color: var(--accent); }
    .btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }

    .workspace {
      display: grid;
      grid-template-columns: 1fr 410px;
      min-height: 0;
      overflow: hidden;
    }
    @media (max-width: 960px) {
      .workspace { grid-template-columns: 1fr; grid-template-rows: 55% 45%; }
    }

    .canvas-wrap {
      position: relative;
      overflow: hidden;
      background:
        radial-gradient(circle at 1px 1px, var(--border) 1px, transparent 0) 0 0 / 24px 24px,
        var(--bg);
      cursor: grab;
    }
    .canvas-wrap:active { cursor: grabbing; }

    svg#graph { width: 100%; height: 100%; display: block; }

    .canvas-controls {
      position: absolute;
      bottom: 0.85rem;
      left: 0.85rem;
      display: flex;
      gap: 0.4rem;
      background: var(--surface);
      padding: 0.35rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      box-shadow: 0 2px 8px rgb(0 0 0 / 0.12);
    }

    .legend {
      position: absolute;
      top: 0.85rem;
      left: 0.85rem;
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem 0.8rem;
      font-size: 0.75rem;
      background: var(--surface);
      padding: 0.4rem 0.7rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      opacity: 0.94;
      pointer-events: none;
    }
    .legend-item { display: inline-flex; align-items: center; gap: 0.3rem; }
    .swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }

    aside.drawer {
      background: var(--surface);
      border-left: 1px solid var(--border);
      overflow-y: auto;
      padding: 1rem 1.1rem 2rem;
      font-size: 0.88rem;
      line-height: 1.5;
    }

    .drawer h2 { margin: 0 0 0.25rem; font-size: 1.15rem; }
    .drawer h3 {
      margin: 1.1rem 0 0.4rem;
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.25rem;
    }

    .pill-row { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.4rem 0 0.7rem; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      font-size: 0.74rem;
      font-weight: 600;
      padding: 0.15rem 0.5rem;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--surface-alt);
    }
    .pill.confirmed { color: var(--confirmed); border-color: var(--confirmed); }
    .pill.tentative { color: var(--tentative); border-color: var(--tentative); }
    .pill.consang { color: var(--consang); border-color: var(--consang); }
    .pill.collapse { color: var(--collapse); border-color: var(--collapse); }

    .callout {
      padding: 0.6rem 0.75rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--surface-alt);
      margin: 0.55rem 0;
    }
    .callout.warn { border-left: 4px solid var(--tentative); }
    .callout.consang { border-left: 4px solid var(--consang); }
    .callout.collapse { border-left: 4px solid var(--collapse); }
    .callout.excluded { border-left: 4px solid var(--hypo); opacity: 0.9; }

    details.source-card {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.5rem 0.65rem;
      margin: 0.45rem 0;
      background: var(--surface-alt);
    }
    details.source-card summary {
      cursor: pointer;
      font-weight: 600;
      font-size: 0.84rem;
    }
    .source-meta { font-size: 0.76rem; color: var(--muted); margin: 0.3rem 0; }
    .link-list { display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.4rem 0; }
    .link-chip {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      font-size: 0.75rem;
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--accent);
      text-decoration: none;
    }
    .link-chip:hover { border-color: var(--accent); text-decoration: underline; }

    .md-block p { margin: 0.35rem 0; }
    .md-block ul { margin: 0.3rem 0; padding-left: 1.2rem; }
    .md-block h4 { margin: 0.6rem 0 0.25rem; font-size: 0.84rem; }
    .md-block code { font-size: 0.78rem; background: var(--surface); padding: 0.05rem 0.25rem; border-radius: 4px; }

    .kinship-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      align-items: center;
      margin: 0.6rem 0;
    }
    .path-card {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.45rem 0.6rem;
      margin: 0.35rem 0;
      background: var(--surface);
      font-size: 0.8rem;
    }
    .path-chain { color: var(--muted); font-size: 0.76rem; margin-top: 0.2rem; }

    table.tl { width: 100%; border-collapse: collapse; font-size: 0.79rem; margin-top: 0.35rem; }
    table.tl th, table.tl td { border-bottom: 1px solid var(--border); padding: 0.3rem 0.35rem; text-align: left; vertical-align: top; }
    table.tl th { color: var(--muted); font-weight: 600; }

    .jump-link {
      color: var(--accent);
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      cursor: pointer;
      text-decoration: underline;
    }

    /* SVG Graph Nodes & Edges */
    .node-group { cursor: pointer; }
    .node-card {
      fill: var(--surface);
      stroke: var(--border);
      stroke-width: 1.6;
      rx: 10;
      transition: stroke 120ms ease, stroke-width 120ms ease;
    }
    .node-group.status-confirmed .node-card { stroke: var(--confirmed); }
    .node-group.status-tentative .node-card { stroke: var(--tentative); stroke-dasharray: 5 3; }
    .node-group.status-hypothetical .node-card { stroke: var(--hypo); stroke-dasharray: 2 3; }
    .node-group.status-excluded .node-card { stroke: var(--hypo); opacity: 0.6; }
    .node-group.selected .node-card { stroke: var(--accent); stroke-width: 3; stroke-dasharray: none; }
    .node-group.compare-target .node-card { stroke: var(--consang); stroke-width: 3; stroke-dasharray: none; }
    .node-group.in-path .node-card { stroke: var(--collapse); stroke-width: 2.8; stroke-dasharray: none; }
    .node-group.in-tract .node-card { stroke: var(--confirmed); stroke-width: 2.8; stroke-dasharray: none; }
    .node-group.dimmed { opacity: 0.22; }

    .node-name { font-size: 13px; font-weight: 700; fill: var(--text); }
    .node-dates { font-size: 11px; fill: var(--muted); }
    .node-id { font-size: 9.5px; fill: var(--muted); font-family: ui-monospace, monospace; }

    .badge-collapse rect { fill: var(--collapse); rx: 8; }
    .badge-collapse text { fill: #fff; font-size: 10px; font-weight: 700; }
    .badge-census-ok rect { fill: var(--confirmed); rx: 8; }
    .badge-census-miss rect { fill: var(--tentative); rx: 8; }
    .badge-census-ok text, .badge-census-miss text { fill: #fff; font-size: 10px; font-weight: 700; }

    .era-bar {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      font-size: 0.82rem;
      padding: 0.22rem 0.55rem;
      border-radius: 6px;
      background: var(--surface-alt);
      border: 1px solid var(--border);
    }
    .era-bar input[type="range"] {
      width: 118px;
      accent-color: var(--accent);
      cursor: pointer;
    }
    .era-bar input[type="range"]:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 3px;
    }
    .era-year { font-variant-numeric: tabular-nums; font-weight: 700; min-width: 3.1rem; }

    /* Accessible Copy Button per modern-web-guidance */
    .copy-wrap {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      position: relative;
    }
    .copy-button {
      min-inline-size: 24px;
      min-block-size: 24px;
    }
    .copy-button[data-copied] { border-color: var(--confirmed); color: var(--confirmed); }
    .copy-button[data-failed] { border-color: var(--consang); color: var(--consang); }
    .copy-tip {
      font-size: 0.74rem;
      font-weight: 600;
      color: var(--confirmed);
      opacity: 0;
      transition: opacity 150ms ease;
    }
    .copy-button[data-copied] + .copy-tip { opacity: 1; color: var(--confirmed); }
    .copy-button[data-failed] + .copy-tip { opacity: 1; color: var(--consang); }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
      border: 0;
    }
    @media (prefers-reduced-motion: reduce) {
      .node-card, .copy-tip { transition: none; }
    }

    .hub-group { cursor: pointer; }
    .hub-diamond { fill: var(--surface); stroke: var(--muted); stroke-width: 1.8; }
    .hub-group.consang .hub-diamond { fill: var(--consang); stroke: var(--consang); }
    .hub-group.selected .hub-diamond { stroke: var(--accent); stroke-width: 3; }
    .hub-label { font-size: 10px; fill: var(--muted); text-anchor: middle; }
    .hub-group.consang .hub-label { fill: var(--consang); font-weight: 700; }

    .edge-path { fill: none; stroke: var(--muted); stroke-width: 1.7; opacity: 0.72; }
    .edge-path.proven { stroke: var(--confirmed); opacity: 0.85; }
    .edge-path.probable { stroke-dasharray: 6 3; }
    .edge-path.speculative { stroke-dasharray: 2 3; }
    .edge-path.fan { stroke: var(--fan); stroke-dasharray: 4 4; opacity: 0.6; }
    .edge-path.consang { stroke: var(--consang); stroke-width: 2.2; opacity: 0.9; }
    .edge-path.highlighted { stroke: var(--collapse); stroke-width: 3.2; stroke-dasharray: none; opacity: 1; }
    .edge-path.dimmed { opacity: 0.12; }

    /* Native Modal Dialog per modern-web-guidance */
    dialog#artifact-dialog {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 0;
      max-width: min(92vw, 920px);
      max-height: 88vh;
      width: 100%;
      background: var(--surface);
      color: var(--text);
      box-shadow: 0 20px 50px rgb(0 0 0 / 0.35);
    }
    dialog#artifact-dialog[open] {
      display: flex;
      flex-direction: column;
    }
    dialog#artifact-dialog:modal {
      overscroll-behavior: contain;
    }
    dialog#artifact-dialog::backdrop {
      background: rgb(0 0 0 / 0.58);
      backdrop-filter: blur(2px);
      overflow: hidden;
      overscroll-behavior: contain;
    }
    .dialog-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
    }
    .dialog-head h2 { margin: 0; font-size: 1rem; }
    .dialog-body {
      padding: 1rem;
      overflow: auto;
      flex: 1;
    }
    .dialog-body pre {
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.83rem;
      background: var(--surface-alt);
      padding: 0.9rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      margin: 0;
    }
    .dialog-body img { max-width: 100%; height: auto; border-radius: 6px; display: block; margin: 0 auto; }
    .dialog-body iframe { width: 100%; height: 70vh; border: 1px solid var(--border); border-radius: 6px; }
  </style>
</head>
<body>
<div class="app">
  <header class="toolbar">
    <div class="brand">
      <span>Genealogy DAG Viewer</span>
      <small id="meta-counts"></small>
    </div>
    <input id="search-input" type="search" placeholder="Search name, variant, place, tract, source..." aria-label="Search genealogy dataset">
    <select id="focus-mode" aria-label="Subgraph filter">
      <option value="all">View: Full Graph</option>
      <option value="ancestors">View: Ancestors of Selected</option>
      <option value="descendants">View: Descendants of Selected</option>
    </select>
    <select id="tract-filter" aria-label="Land tract filter">
      <option value="">All Land Tracts</option>
    </select>
    <div class="era-bar">
      <label><input id="era-enabled" type="checkbox"> Era</label>
      <label class="sr-only" for="era-slider">Filter by year</label>
      <input id="era-slider" type="range" min="1790" max="1960" step="10" value="1910" disabled aria-valuetext="Era filter disabled">
      <span id="era-year-label" class="era-year">Off</span>
    </div>
    <label class="toggle"><input id="toggle-fan" type="checkbox" checked> FAN Club</label>
    <label class="toggle"><input id="toggle-consang" type="checkbox" checked> Inosculation</label>
    <label class="toggle"><input id="toggle-excluded" type="checkbox"> Excluded</label>
    <label class="toggle"><input id="toggle-redact" type="checkbox" checked> Redact Living</label>
    <button id="btn-export-svg" type="button" title="Download current family chart as SVG">Export SVG</button>
    <button id="btn-reload" type="button" title="Reload dataset from disk">Reload</button>
  </header>

  <main class="workspace">
    <section class="canvas-wrap" id="canvas-wrap" aria-label="Interactive family graph">
      <div class="legend">
        <span class="legend-item"><span class="swatch" style="background:var(--confirmed)"></span>Confirmed / Proven</span>
        <span class="legend-item"><span class="swatch" style="background:var(--tentative)"></span>Tentative / Probable</span>
        <span class="legend-item"><span class="swatch" style="background:var(--consang)"></span>Consanguineous Marriage</span>
        <span class="legend-item"><span class="swatch" style="background:var(--collapse)"></span>Pedigree Collapse / Kinship Path</span>
        <span class="legend-item"><span class="swatch" style="background:var(--fan)"></span>FAN Associate</span>
      </div>
      <svg id="graph" xmlns="http://www.w3.org/2000/svg">
        <g id="viewport">
          <g id="layer-edges"></g>
          <g id="layer-hubs"></g>
          <g id="layer-nodes"></g>
        </g>
      </svg>
      <div class="canvas-controls">
        <button id="btn-zoom-in" type="button" aria-label="Zoom in">+</button>
        <button id="btn-zoom-out" type="button" aria-label="Zoom out">&minus;</button>
        <button id="btn-fit" type="button">Fit View</button>
        <button id="btn-clear-sel" type="button">Clear Selection</button>
      </div>
    </section>

    <aside class="drawer" id="drawer" aria-label="Research details panel"></aside>
  </main>
</div>

<dialog id="artifact-dialog" closedby="any" aria-labelledby="artifact-dialog-title">
  <div class="dialog-head">
    <h2 id="artifact-dialog-title">Artifact Preview</h2>
    <form method="dialog"><button type="submit" class="btn" autofocus>Close</button></form>
  </div>
  <div class="dialog-body" id="artifact-dialog-body"></div>
</dialog>

<script>
const STANDALONE_MODE = __STANDALONE_FLAG__;
const INITIAL_DATA = __INITIAL_JSON__;

const state = {
  data: INITIAL_DATA,
  selectedId: null,
  selectedHubId: null,
  compareMode: false,
  compareId: null,
  kinshipResult: null,
  showFan: true,
  showConsang: true,
  showExcluded: false,
  redactLiving: INITIAL_DATA ? !!INITIAL_DATA.meta.redact_living : true,
  focusMode: "all",
  selectedTract: "",
  eraEnabled: false,
  eraYear: 1910,
  searchQuery: "",
  tx: 0,
  ty: 0,
  scale: 1,
};

let copyCounter = 0;
let lastCopyMessage = "";
const copyTimers = new WeakMap();

function reportCopy(button, ok) {
  const tip = button.nextElementSibling;
  clearTimeout(copyTimers.get(button));
  button.removeAttribute("data-copied");
  button.removeAttribute("data-failed");
  let message = ok ? "Copied" : "Copy failed";
  if (message === lastCopyMessage) message += "\u00A0";
  lastCopyMessage = message;
  if (tip) tip.textContent = "";
  void button.offsetWidth;
  button.setAttribute(ok ? "data-copied" : "data-failed", "");
  if (tip) requestAnimationFrame(() => { tip.textContent = message; });
  copyTimers.set(button, setTimeout(() => {
    button.removeAttribute("data-copied");
    button.removeAttribute("data-failed");
    if (tip) tip.textContent = "";
  }, 2000));
}

async function handleCopyCommand(button, target) {
  const text = target ? (target.textContent || "").trim() : "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    reportCopy(button, true);
  } catch {
    reportCopy(button, false);
  }
}

function initCopyHandlers() {
  if ("CommandEvent" in window) {
    document.addEventListener("command", (event) => {
      if (event.command === "--copy") handleCopyCommand(event.source, event.target);
    }, true);
  } else {
    document.addEventListener("click", (event) => {
      const button = event.target.closest('button[command="--copy"]');
      if (!button) return;
      const target = document.getElementById(button.getAttribute("commandfor"));
      if (target) handleCopyCommand(button, target);
    });
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "className") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  }
  for (const ch of children.flat()) {
    if (ch === null || ch === undefined) continue;
    node.appendChild(typeof ch === "string" ? document.createTextNode(ch) : ch);
  }
  return node;
}

function isSafeUrl(u) {
  try {
    const parsed = new URL(u);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

// Render simple Markdown safely using pure DOM nodes (never innerHTML)
function renderMarkdownSafe(mdText) {
  const wrap = el("div", { className: "md-block" });
  if (!mdText) return wrap;
  const lines = mdText.split(/\r?\n/);
  let currentUl = null;

  function appendInline(parent, text) {
    const tokenRe = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let last = 0;
    let m;
    while ((m = tokenRe.exec(text)) !== null) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      const tok = m[0];
      if (tok.startsWith("**")) parent.appendChild(el("strong", {}, tok.slice(2, -2)));
      else if (tok.startsWith("`")) parent.appendChild(el("code", {}, tok.slice(1, -1)));
      last = m.index + tok.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) { currentUl = null; continue; }
    if (line.startsWith("## ") || line.startsWith("### ")) {
      currentUl = null;
      wrap.appendChild(el("h4", {}, line.replace(/^#+\s+/, "")));
    } else if (line.startsWith("- ") || line.startsWith("* ")) {
      if (!currentUl) { currentUl = el("ul"); wrap.appendChild(currentUl); }
      const li = el("li");
      appendInline(li, line.slice(2));
      currentUl.appendChild(li);
    } else {
      currentUl = null;
      const p = el("p");
      appendInline(p, line);
      wrap.appendChild(p);
    }
  }
  return wrap;
}

// Client-side kinship path solver (used in standalone static HTML mode)
function computeKinshipClient(data, a, b) {
  const parents = {};
  for (const r of data.relationships) {
    if (r.type === "parent_child" && !["adoptive", "step", "foster"].includes(r.subtype)) {
      (parents[r.person_2] ||= []).push(r.person_1);
    }
  }
  function upwardPaths(start) {
    const out = {};
    const q = [[start]];
    while (q.length) {
      const path = q.shift();
      const cur = path[path.length - 1];
      (out[cur] ||= []).push(path);
      if (path.length > 10) continue;
      for (const p of (parents[cur] || [])) {
        if (!path.includes(p)) q.push([...path, p]);
      }
    }
    return out;
  }
  const pa = upwardPaths(a);
  const pb = upwardPaths(b);
  const paths = [];
  for (const anc of Object.keys(pa)) {
    if (!pb[anc]) continue;
    for (const x of pa[anc]) {
      const ix = new Set(x.slice(0, -1));
      for (const y of pb[anc]) {
        const iy = new Set(y.slice(0, -1));
        let overlap = false;
        for (const n of ix) if (iy.has(n)) { overlap = true; break; }
        if (overlap) continue;
        const m = x.length - 1, n = y.length - 1;
        paths.push({
          common_ancestor: anc,
          common_ancestor_name: data.individuals[anc]?.primary_name || anc,
          steps_a: m,
          steps_b: n,
          label: `${m}↑ / ${n}↑ via ${data.individuals[anc]?.primary_name || anc}`,
          path_a: x,
          path_b: y,
          path_contribution: Math.pow(0.5, m + n),
        });
      }
    }
  }
  paths.sort((u, v) => (u.steps_a + u.steps_b) - (v.steps_a + v.steps_b));
  const estR = paths.reduce((acc, p) => acc + p.path_contribution, 0);
  const areMarried = data.relationships.some(
    r => r.type === "marriage" && ((r.person_1 === a && r.person_2 === b) || (r.person_1 === b && r.person_2 === a))
  );
  return {
    person_a: a,
    person_a_name: data.individuals[a]?.primary_name || a,
    person_b: b,
    person_b_name: data.individuals[b]?.primary_name || b,
    are_married: areMarried,
    coancestry: estR / 2,
    wright_relationship: estR,
    expected_offspring_inbreeding: estR / 2,
    path_count: paths.length,
    paths,
  };
}

async function fetchKinship(a, b) {
  if (!STANDALONE_MODE) {
    const res = await fetch(`/api/kinship?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
    if (res.ok) return await res.json();
  }
  return computeKinshipClient(state.data, a, b);
}

function getVisibleIds() {
  const inds = state.data.individuals;
  let visible = new Set(Object.keys(inds).filter(id => state.showExcluded || inds[id].status !== "excluded"));

  if (state.selectedId && state.focusMode !== "all") {
    const rels = state.data.relationships.filter(r => r.type === "parent_child");
    const keep = new Set([state.selectedId]);
    const queue = [state.selectedId];
    while (queue.length) {
      const cur = queue.shift();
      for (const r of rels) {
        const nxt = state.focusMode === "ancestors" ? (r.person_2 === cur ? r.person_1 : null)
                  : (r.person_1 === cur ? r.person_2 : null);
        if (nxt && visible.has(nxt) && !keep.has(nxt)) {
          keep.add(nxt);
          queue.push(nxt);
        }
      }
    }
    visible = keep;
  }
  return visible;
}

function getSearchMatches() {
  const q = state.searchQuery.trim().toLowerCase();
  if (!q) return null;
  const matches = new Set();
  for (const [id, ind] of Object.entries(state.data.individuals)) {
    const hay = [
      id,
      ind.primary_name,
      ...(ind.name_variants || []),
      ind.birth?.place || "",
      ind.death?.place || "",
      ...(ind.events || []).map(e => `${e.place || ""} ${e.tract || ""} ${e.description || ""}`),
      ind.body_markdown || "",
    ].join(" ").toLowerCase();
    if (hay.includes(q)) matches.add(id);
  }
  for (const src of Object.values(state.data.sources)) {
    const hay = `${src.id} ${src.title} ${src.jurisdiction || ""} ${src.body_markdown || ""}`.toLowerCase();
    if (hay.includes(q)) {
      for (const iid of (src.individuals_cited || [])) matches.add(iid);
    }
  }
  return matches;
}

function getHighlightedSets() {
  const pathNodes = new Set();
  const pathPairs = new Set(); // "parent|child"
  const multiPathBadges = {};

  if (state.kinshipResult && state.kinshipResult.paths?.length) {
    for (const p of state.kinshipResult.paths) {
      for (const arr of [p.path_a, p.path_b]) {
        for (let i = 0; i < arr.length; i++) {
          pathNodes.add(arr[i]);
          if (i + 1 < arr.length) pathPairs.add(`${arr[i + 1]}|${arr[i]}`);
        }
      }
    }
  } else if (state.selectedId && state.showConsang) {
    const ind = state.data.individuals[state.selectedId];
    const mpas = ind?.collapse?.multi_path_ancestors || [];
    for (const mpa of mpas) {
      multiPathBadges[mpa.individual_id] = mpa.path_count;
      for (const p of mpa.paths) {
        for (let i = 0; i < p.length; i++) {
          pathNodes.add(p[i]);
          if (i + 1 < p.length) pathPairs.add(`${p[i + 1]}|${p[i]}`);
        }
      }
    }
  }
  return { pathNodes, pathPairs, multiPathBadges };
}

function isAliveInYear(ind, year) {
  if (ind.redacted) return false;
  if (ind.birth_year === null || ind.birth_year === undefined) return false;
  if (ind.birth_year > year) return false;
  if (ind.death_year !== null && ind.death_year !== undefined) return year <= ind.death_year;
  if (ind.living) return true;
  return year <= ind.birth_year + 85;
}

function getTractMatchSet() {
  if (!state.selectedTract) return null;
  const tr = (state.data.tracts || []).find(t => t.tract_group === state.selectedTract);
  return tr ? new Set(tr.individuals_involved) : new Set();
}

function renderGraph() {
  const data = state.data;
  const layout = data.layout;
  const visible = getVisibleIds();
  const searchMatches = getSearchMatches();
  const tractMatches = getTractMatchSet();
  const eraActive = state.eraEnabled;
  const eraYear = state.eraYear;
  const { pathNodes, pathPairs, multiPathBadges } = getHighlightedSets();
  const hasHighlight = pathNodes.size > 0;

  const layerEdges = document.getElementById("layer-edges");
  const layerHubs = document.getElementById("layer-hubs");
  const layerNodes = document.getElementById("layer-nodes");
  layerEdges.replaceChildren();
  layerHubs.replaceChildren();
  layerNodes.replaceChildren();

  const cw = layout.card_w;
  const ch = layout.card_h;

  const visibleHubs = new Map();
  for (const h of layout.hubs) {
    const anyParent = h.parents.some(p => visible.has(p));
    const anyChild = h.children.some(c => visible.has(c));
    if (anyParent && (h.marriage || anyChild)) visibleHubs.set(h.id, h);
  }

  // Draw edges
  for (const edge of layout.edges) {
    if (edge.kind === "fan_associate" && !state.showFan) continue;
    let x1, y1, x2, y2, dStr;
    let isHighlighted = false;

    if (edge.kind === "spouse_to_hub") {
      if (!visible.has(edge.from) || !visibleHubs.has(edge.to)) continue;
      const p = layout.nodes[edge.from];
      const h = visibleHubs.get(edge.to);
      x1 = p.x; y1 = p.y + ch / 2;
      x2 = h.x; y2 = h.y;
      const midY = (y1 + y2) / 2;
      dStr = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
      for (const c of h.children) {
        if (pathPairs.has(`${edge.from}|${c}`)) { isHighlighted = true; break; }
      }
    } else if (edge.kind === "hub_to_child") {
      if (!visibleHubs.has(edge.from) || !visible.has(edge.to)) continue;
      const h = visibleHubs.get(edge.from);
      const c = layout.nodes[edge.to];
      x1 = h.x; y1 = h.y;
      x2 = c.x; y2 = c.y - ch / 2;
      const midY = (y1 + y2) / 2;
      dStr = `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
      for (const p of (edge.parents || [])) {
        if (pathPairs.has(`${p}|${edge.to}`)) { isHighlighted = true; break; }
      }
    } else {
      if (!visible.has(edge.from) || !visible.has(edge.to)) continue;
      const a = layout.nodes[edge.from];
      const b = layout.nodes[edge.to];
      x1 = a.x; y1 = a.y;
      x2 = b.x; y2 = b.y;
      const mx = (x1 + x2) / 2;
      const my = Math.min(y1, y2) - 44;
      dStr = `M ${x1} ${y1} Q ${mx} ${my}, ${x2} ${y2}`;
    }

    const cls = ["edge-path", edge.confidence || ""];
    if (edge.kind === "fan_associate") cls.push("fan");
    if (state.showConsang && edge.consanguineous) cls.push("consang");
    if (isHighlighted) cls.push("highlighted");
    else if (hasHighlight) cls.push("dimmed");

    layerEdges.appendChild(svgEl("path", { d: dStr, class: cls.join(" ") }));
  }

  // Draw Marriage / Family Hubs
  for (const h of visibleHubs.values()) {
    const gCls = ["hub-group"];
    if (state.showConsang && h.consanguineous) gCls.push("consang");
    if (state.selectedHubId === h.id) gCls.push("selected");
    const g = svgEl("g", { class: gCls.join(" "), transform: `translate(${h.x},${h.y})` });
    const s = 8;
    g.appendChild(svgEl("polygon", {
      class: "hub-diamond",
      points: `0,${-s} ${s},0 0,${s} ${-s},0`,
    }));
    if (h.marriage?.date || (state.showConsang && h.consanguineous)) {
      const lblText = (state.showConsang && h.consanguineous ? "⚠ " : "") + (h.marriage?.date ? `m. ${h.marriage.date}` : "related");
      const lbl = svgEl("text", { class: "hub-label", y: 20 });
      lbl.textContent = lblText;
      g.appendChild(lbl);
    }
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectHub(h);
    });
    layerHubs.appendChild(g);
  }

  // Draw Individual Cards
  for (const iid of visible) {
    const pos = layout.nodes[iid];
    const ind = data.individuals[iid];
    if (!pos || !ind) continue;

    const cls = ["node-group", `status-${ind.status}`];
    if (state.selectedId === iid) cls.push("selected");
    if (state.compareId === iid) cls.push("compare-target");
    if (pathNodes.has(iid)) cls.push("in-path");
    if (tractMatches && tractMatches.has(iid)) cls.push("in-tract");
    const aliveNow = eraActive ? isAliveInYear(ind, eraYear) : true;
    if (
      (searchMatches && !searchMatches.has(iid)) ||
      (tractMatches && !tractMatches.has(iid)) ||
      (eraActive && !aliveNow) ||
      (hasHighlight && !pathNodes.has(iid) && state.selectedId !== iid && state.compareId !== iid)
    ) {
      cls.push("dimmed");
    }

    const g = svgEl("g", {
      class: cls.join(" "),
      transform: `translate(${pos.x - cw / 2}, ${pos.y - ch / 2})`,
    });

    g.appendChild(svgEl("rect", { class: "node-card", width: cw, height: ch }));

    const nameEl = svgEl("text", { class: "node-name", x: 12, y: 26 });
    const nameStr = ind.primary_name.length > 24 ? ind.primary_name.slice(0, 23) + "…" : ind.primary_name;
    nameEl.textContent = nameStr;
    g.appendChild(nameEl);

    const b = ind.birth?.date || "?";
    const d = ind.living ? "Living" : (ind.death?.date || "?");
    const datesEl = svgEl("text", { class: "node-dates", x: 12, y: 45 });
    datesEl.textContent = `${b} – ${d} · ${ind.sex}`;
    g.appendChild(datesEl);

    const idEl = svgEl("text", { class: "node-id", x: 12, y: 65 });
    const extra = ind.inbreeding_f > 0 && state.showConsang ? ` · F=${ind.inbreeding_f.toFixed(4)}` : "";
    idEl.textContent = `${iid}${extra}`;
    g.appendChild(idEl);

    // Multi-path ancestor badge (×2, ×3...)
    const mCount = multiPathBadges[iid];
    if (mCount && mCount >= 2) {
      const bg = svgEl("g", { class: "badge-collapse", transform: `translate(${cw - 36}, -9)` });
      bg.appendChild(svgEl("rect", { width: 32, height: 18 }));
      const bt = svgEl("text", { x: 16, y: 12.5, "text-anchor": "middle" });
      bt.textContent = `×${mCount}`;
      bg.appendChild(bt);
      g.appendChild(bg);
    }

    // Era / Regional census coverage badge (only when eraYear is an applicable census year for THIS individual's region!)
    const indApplicableCensuses = ind.applicable_census_years || [];
    if (eraActive && aliveNow && indApplicableCensuses.includes(eraYear)) {
      const hasCensus = (ind.covered_census_years || []).includes(eraYear);
      const cBadge = svgEl("g", {
        class: hasCensus ? "badge-census-ok" : "badge-census-miss",
        transform: `translate(8, -9)`,
      });
      cBadge.appendChild(svgEl("rect", { width: 52, height: 18 }));
      const ct = svgEl("text", { x: 26, y: 12.5, "text-anchor": "middle" });
      ct.textContent = `${hasCensus ? "✓" : "?"} ${eraYear}`;
      cBadge.appendChild(ct);
      g.appendChild(cBadge);
    }

    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onNodeClick(iid, ev.shiftKey);
    });
    layerNodes.appendChild(g);
  }

  updateMetaBar();
}

function updateMetaBar() {
  const c = state.data.meta.counts;
  const cons = c.consanguineous_marriages > 0 ? ` · ${c.consanguineous_marriages} consanguineous marriage(s)` : "";
  document.getElementById("meta-counts").textContent =
    `${state.data.meta.workspace_name}: ${c.individuals} people · ${c.sources} sources · ${c.relationships} edges${cons}`;
}

async function onNodeClick(iid, shiftKey) {
  if ((state.compareMode || shiftKey) && state.selectedId && state.selectedId !== iid) {
    state.compareId = iid;
    state.compareMode = false;
    state.selectedHubId = null;
    state.kinshipResult = await fetchKinship(state.selectedId, iid);
    renderGraph();
    renderDrawer();
    return;
  }
  state.selectedId = iid;
  state.selectedHubId = null;
  state.compareId = null;
  state.compareMode = false;
  state.kinshipResult = null;
  renderGraph();
  renderDrawer();
}

function selectHub(hub) {
  state.selectedHubId = hub.id;
  state.selectedId = null;
  state.compareId = null;
  state.compareMode = false;
  state.kinshipResult = hub.kinship || null;
  renderGraph();
  renderDrawer();
}

function openArtifactPreview(src) {
  const dlg = document.getElementById("artifact-dialog");
  const title = document.getElementById("artifact-dialog-title");
  const body = document.getElementById("artifact-dialog-body");
  title.textContent = `${src.id} — ${src.artifact_path || src.title}`;
  body.replaceChildren();

  if (STANDALONE_MODE || !src.artifact_path) {
    body.appendChild(el("p", {}, "Local artifact preview is available when running `uv run genealogy view` live server. Verbatim transcription:"));
    body.appendChild(renderMarkdownSafe(src.body_markdown));
    dlg.showModal();
    return;
  }

  const artUrl = `/${src.artifact_path}`;
  const lower = src.artifact_path.toLowerCase();
  if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".webp") || lower.endsWith(".gif")) {
    body.appendChild(el("img", { src: artUrl, alt: src.title }));
  } else if (lower.endsWith(".pdf")) {
    body.appendChild(el("iframe", { src: artUrl, title: src.title }));
  } else {
    const pre = el("pre", {}, "Loading artifact...");
    body.appendChild(pre);
    fetch(artUrl).then(r => r.text()).then(txt => { pre.textContent = txt; }).catch(() => {
      pre.textContent = "Unable to load local artifact file.";
    });
  }
  dlg.showModal();
}

function renderSourceCard(src, isExcluded = false) {
  const det = el("details", { className: "source-card" });
  const sum = el("summary", {}, `${isExcluded ? "[EXCLUDED] " : ""}${src.title} (${src.id})`);
  det.appendChild(sum);

  const metaParts = [src.record_type, src.date || "undated", src.source_type, src.information_type].filter(Boolean);
  det.appendChild(el("div", { className: "source-meta" }, metaParts.join(" · ")));
  if (src.repository) det.appendChild(el("div", { className: "source-meta" }, `Repository: ${src.repository}`));
  if (src.exclusion_reason) {
    det.appendChild(el("div", { className: "callout excluded" }, `Exclusion reason: ${src.exclusion_reason}`));
  }

  const linksWrap = el("div", { className: "link-list" });
  if (src.url && isSafeUrl(src.url)) {
    linksWrap.appendChild(el("a", { className: "link-chip", href: src.url, target: "_blank", rel: "noopener noreferrer" }, "Source URL ↗"));
  }
  for (const lnk of (src.links || [])) {
    if (!isSafeUrl(lnk.url)) continue;
    const label = `${lnk.requires_auth ? "🔒 " : ""}${lnk.provider}${lnk.accessed ? ` (${lnk.accessed})` : ""} ↗`;
    linksWrap.appendChild(el("a", {
      className: "link-chip",
      href: lnk.url,
      target: "_blank",
      rel: "noopener noreferrer",
      title: lnk.note || lnk.url,
    }, label));
  }
  if (src.artifact_path) {
    linksWrap.appendChild(el("button", {
      type: "button",
      className: "btn",
      onClick: () => openArtifactPreview(src),
    }, `Preview Local Artifact (${src.artifact_path.split("/").pop()})`));
  }
  if (src.formal_citation) {
    const citeId = `cite-${++copyCounter}`;
    linksWrap.appendChild(el("span", { id: citeId, className: "sr-only" }, src.formal_citation));
    linksWrap.appendChild(el("span", { className: "copy-wrap" },
      el("button", {
        type: "button",
        className: "btn copy-button",
        command: "--copy",
        commandfor: citeId,
        "aria-label": `Copy citation for ${src.id}`,
      }, "Copy Citation"),
      el("span", { className: "copy-tip", role: "status" })
    ));
  }
  if (linksWrap.childNodes.length) det.appendChild(linksWrap);

  if (src.body_markdown) det.appendChild(renderMarkdownSafe(src.body_markdown));
  return det;
}

function renderKinshipSection(rep) {
  const box = el("div", { className: "callout collapse" });
  box.appendChild(el("strong", {}, `Kinship: ${rep.person_a_name} ↔ ${rep.person_b_name}`));
  box.appendChild(el("div", { className: "source-meta" },
    `Wright's r = ${rep.wright_relationship.toFixed(5)} · Expected offspring F = ${rep.expected_offspring_inbreeding.toFixed(5)} · ${rep.path_count} blood path(s)` +
    (rep.are_married ? " · MARRIED" : "")
  ));
  if (!rep.paths.length) {
    box.appendChild(el("p", {}, "No shared blood ancestors found in this dataset."));
  } else {
    for (const p of rep.paths) {
      const pc = el("div", { className: "path-card" });
      pc.appendChild(el("strong", {}, `${p.label} — via ${p.common_ancestor_name}`));
      const chainA = p.path_a.map(id => state.data.individuals[id]?.primary_name || id).join(" → ");
      const chainB = p.path_b.slice(0, -1).reverse().map(id => state.data.individuals[id]?.primary_name || id).join(" → ");
      pc.appendChild(el("div", { className: "path-chain" }, `${chainA}${chainB ? " ← " + chainB : ""}`));
      box.appendChild(pc);
    }
  }
  return box;
}

function renderDrawer() {
  const drawer = document.getElementById("drawer");
  drawer.replaceChildren();

  // Case 1: Marriage / Union Hub selected
  if (state.selectedHubId) {
    const hub = state.data.layout.hubs.find(h => h.id === state.selectedHubId);
    if (!hub) return;
    const names = hub.parents.map(id => state.data.individuals[id]?.primary_name || id).join(" & ");
    drawer.appendChild(el("h2", {}, `Union: ${names}`));
    if (hub.marriage) {
      drawer.appendChild(el("div", { className: "pill-row" },
        el("span", { className: `pill ${hub.marriage.confidence === "proven" ? "confirmed" : "tentative"}` }, hub.marriage.confidence),
        hub.consanguineous ? el("span", { className: "pill consang" }, "Consanguineous Marriage") : null
      ));
      drawer.appendChild(el("p", {}, `Date: ${hub.marriage.date || "Unknown"} · Place: ${hub.marriage.place || "Unknown"}`));
      if (hub.marriage.notes) drawer.appendChild(el("p", {}, hub.marriage.notes));
    }
    if (hub.kinship) drawer.appendChild(renderKinshipSection(hub.kinship));

    drawer.appendChild(el("h3", {}, "Parents / Spouses"));
    const ulP = el("ul");
    for (const pid of hub.parents) {
      ulP.appendChild(el("li", {}, el("button", { className: "jump-link", onClick: () => onNodeClick(pid, false) },
        `${state.data.individuals[pid]?.primary_name || pid} (${pid})`
      )));
    }
    drawer.appendChild(ulP);

    drawer.appendChild(el("h3", {}, `Children (${hub.children.length})`));
    const ulC = el("ul");
    for (const cid of hub.children) {
      ulC.appendChild(el("li", {}, el("button", { className: "jump-link", onClick: () => onNodeClick(cid, false) },
        `${state.data.individuals[cid]?.primary_name || cid} (${cid})`
      )));
    }
    drawer.appendChild(ulC);

    if (hub.marriage?.sources?.length) {
      drawer.appendChild(el("h3", {}, "Marriage Sources"));
      for (const sid of hub.marriage.sources) {
        if (state.data.sources[sid]) drawer.appendChild(renderSourceCard(state.data.sources[sid]));
      }
    }
    return;
  }

  // Case 2: No selection — Workspace Overview, Active Tract Chain-of-Title & Inosculation Summary
  if (!state.selectedId) {
    if (state.selectedTract) {
      const tr = (state.data.tracts || []).find(t => t.tract_group === state.selectedTract);
      if (tr) {
        drawer.appendChild(el("h2", {}, `Land Tract: ${tr.tract_group}`));
        drawer.appendChild(el("div", { className: "source-meta" }, `${tr.event_count} recorded event(s) · ${tr.individuals_involved.length} individual(s)`));
        const tbl = el("table", { className: "tl" },
          el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Person"), el("th", {}, "Tract / Details")))
        );
        const tb = el("tbody");
        for (const e of tr.entries) {
          tb.appendChild(el("tr", {},
            el("td", {}, e.date),
            el("td", {}, el("button", { className: "jump-link", onClick: () => onNodeClick(e.individual_id, false) }, e.primary_name)),
            el("td", {}, `${e.tract}${e.description ? " — " + e.description : ""}`)
          ));
        }
        tbl.appendChild(tb);
        drawer.appendChild(tbl);
        if (tr.source_ids?.length) {
          drawer.appendChild(el("h3", {}, "Deed & Probate Sources"));
          for (const sid of tr.source_ids) {
            if (state.data.sources[sid]) drawer.appendChild(renderSourceCard(state.data.sources[sid]));
          }
        }
        return;
      }
    }

    drawer.appendChild(el("h2", {}, "Workspace Overview"));
    drawer.appendChild(el("p", {}, "Click any person card to inspect notes, discrepancies, sources, Ancestry/FamilySearch links, and pedigree collapse. Shift+Click a second person (or use Compare Kinship) to trace every blood path between them."));

    const consList = state.data.consanguineous_marriages || [];
    drawer.appendChild(el("h3", {}, `Consanguineous Marriages (${consList.length})`));
    if (!consList.length) {
      drawer.appendChild(el("p", { className: "source-meta" }, "None detected in current graph."));
    } else {
      for (const rep of consList) {
        const card = el("div", { className: "callout consang" });
        card.appendChild(el("strong", {}, `${rep.person_a_name} & ${rep.person_b_name}`));
        card.appendChild(el("div", { className: "source-meta" }, `r = ${rep.wright_relationship.toFixed(4)} · ${rep.path_count} shared blood path(s)`));
        card.appendChild(el("button", {
          type: "button",
          className: "btn",
          style: "margin-top:0.35rem",
          onClick: async () => {
            state.selectedId = rep.person_a;
            state.compareId = rep.person_b;
            state.kinshipResult = rep;
            renderGraph();
            renderDrawer();
          },
        }, "Highlight Paths on Tree"));
        drawer.appendChild(card);
      }
    }

    const exclSources = Object.values(state.data.sources).filter(s => s.relevance === "excluded");
    drawer.appendChild(el("h3", {}, `Excluded / Mistaken-Lead Artifacts (${exclSources.length})`));
    if (!exclSources.length) {
      drawer.appendChild(el("p", { className: "source-meta" }, "None logged."));
    } else {
      for (const s of exclSources) drawer.appendChild(renderSourceCard(s, true));
    }
    return;
  }

  // Case 3: Individual selected
  const ind = state.data.individuals[state.selectedId];
  if (!ind) return;

  drawer.appendChild(el("h2", {}, ind.primary_name));
  drawer.appendChild(el("div", { className: "source-meta" }, ind.id));

  const pills = el("div", { className: "pill-row" },
    el("span", { className: `pill ${ind.status}` }, ind.status),
    el("span", { className: "pill" }, `Sex: ${ind.sex}`),
    ind.living ? el("span", { className: "pill" }, "Living") : null,
    ind.inbreeding_f > 0 ? el("span", { className: "pill consang" }, `Inbreeding F=${ind.inbreeding_f.toFixed(4)}`) : null,
    (ind.collapse?.multi_path_ancestors?.length || 0) > 0
      ? el("span", { className: "pill collapse" }, `${ind.collapse.multi_path_ancestors.length} Multi-Path Ancestor(s)`)
      : null
  );
  drawer.appendChild(pills);

  // Kinship compare controls
  const kBar = el("div", { className: "kinship-bar" },
    el("button", {
      type: "button",
      className: state.compareMode ? "btn btn-primary" : "btn",
      onClick: () => { state.compareMode = !state.compareMode; renderDrawer(); },
    }, state.compareMode ? "Now click a 2nd person on the tree..." : "Compare Kinship With..."),
    state.kinshipResult ? el("button", {
      type: "button",
      className: "btn",
      onClick: () => { state.compareId = null; state.kinshipResult = null; renderGraph(); renderDrawer(); },
    }, "Clear Comparison") : null
  );
  drawer.appendChild(kBar);

  if (state.kinshipResult) drawer.appendChild(renderKinshipSection(state.kinshipResult));

  if (ind.name_variants?.length) {
    drawer.appendChild(el("p", {}, el("strong", {}, "Name Variants: "), ind.name_variants.join(", ")));
  }
  if (ind.birth) {
    drawer.appendChild(el("p", {}, el("strong", {}, "Birth: "), `${ind.birth.date || "?"} — ${ind.birth.place || "?"}`));
  }
  if (ind.death) {
    drawer.appendChild(el("p", {}, el("strong", {}, "Death: "), `${ind.death.date || "?"} — ${ind.death.place || "?"}`));
  }

  // Recorded Discrepancies callout
  if (ind.discrepancies?.length) {
    const disc = el("div", { className: "callout warn" }, el("strong", {}, "Recorded Discrepancies"));
    const ul = el("ul", { style: "margin:0.3rem 0 0;padding-left:1.1rem;" });
    for (const d of ind.discrepancies) ul.appendChild(el("li", {}, d));
    disc.appendChild(ul);
    drawer.appendChild(disc);
  }

  // Pedigree Collapse section
  const mpas = ind.collapse?.multi_path_ancestors || [];
  if (mpas.length) {
    drawer.appendChild(el("h3", {}, "Pedigree Collapse (Multi-Path Ancestors)"));
    for (const m of mpas) {
      const box = el("div", { className: "callout collapse" });
      box.appendChild(el("button", { className: "jump-link", onClick: () => onNodeClick(m.individual_id, false) },
        `${m.primary_name} (${m.individual_id})`
      ));
      box.appendChild(el("div", { className: "source-meta" }, `Appears ×${m.path_count} across generation(s) ${m.generations.join(", ")}`));
      drawer.appendChild(box);
    }
  }

  // Events / Timeline
  if (ind.events?.length) {
    drawer.appendChild(el("h3", {}, "Recorded Life Events"));
    const tbl = el("table", { className: "tl" },
      el("thead", {}, el("tr", {}, el("th", {}, "Date"), el("th", {}, "Type"), el("th", {}, "Details / Place")))
    );
    const tb = el("tbody");
    for (const ev of ind.events) {
      const detail = [ev.description, ev.tract ? `Tract: ${ev.tract}` : null, ev.place].filter(Boolean).join(" — ");
      tb.appendChild(el("tr", {}, el("td", {}, ev.date || "—"), el("td", {}, ev.type), el("td", {}, detail)));
    }
    tbl.appendChild(tb);
    drawer.appendChild(tbl);
  }

  // Bio & Notes
  if (ind.body_markdown) {
    drawer.appendChild(el("h3", {}, "Notes & Biography"));
    drawer.appendChild(renderMarkdownSafe(ind.body_markdown));
  }

  // Cited Sources
  drawer.appendChild(el("h3", {}, `Cited Sources (${ind.source_ids?.length || 0})`));
  if (!ind.source_ids?.length) {
    drawer.appendChild(el("p", { className: "source-meta" }, "No cited sources."));
  } else {
    for (const sid of ind.source_ids) {
      const src = state.data.sources[sid];
      if (src) drawer.appendChild(renderSourceCard(src, false));
    }
  }

  // Excluded leads ruled out for this individual
  if (ind.excluded_source_ids?.length) {
    drawer.appendChild(el("h3", {}, `Excluded / Ruled-Out Leads (${ind.excluded_source_ids.length})`));
    for (const sid of ind.excluded_source_ids) {
      const src = state.data.sources[sid];
      if (src) drawer.appendChild(renderSourceCard(src, true));
    }
  }
}

// Pan & Zoom controls
function applyTransform() {
  document.getElementById("viewport").setAttribute("transform", `translate(${state.tx},${state.ty}) scale(${state.scale})`);
}

function fitToView() {
  const wrap = document.getElementById("canvas-wrap");
  const rect = wrap.getBoundingClientRect();
  const lw = state.data.layout.width || 960;
  const lh = state.data.layout.height || 600;
  const pad = 48;
  const s = Math.min((rect.width - pad) / lw, (rect.height - pad) / lh, 1.15);
  state.scale = Math.max(0.25, s);
  state.tx = (rect.width - lw * state.scale) / 2;
  state.ty = Math.max(24, (rect.height - lh * state.scale) / 2);
  applyTransform();
}

function initPanZoom() {
  const wrap = document.getElementById("canvas-wrap");
  let dragging = false, sx = 0, sy = 0;

  wrap.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".node-group, .hub-group, .canvas-controls")) return;
    dragging = true;
    sx = e.clientX - state.tx;
    sy = e.clientY - state.ty;
    wrap.setPointerCapture(e.pointerId);
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    state.tx = e.clientX - sx;
    state.ty = e.clientY - sy;
    applyTransform();
  });
  wrap.addEventListener("pointerup", () => { dragging = false; });

  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    const nextScale = Math.min(2.5, Math.max(0.2, state.scale * factor));
    state.tx = mx - (mx - state.tx) * (nextScale / state.scale);
    state.ty = my - (my - state.ty) * (nextScale / state.scale);
    state.scale = nextScale;
    applyTransform();
  }, { passive: false });

  document.getElementById("btn-zoom-in").addEventListener("click", () => { state.scale = Math.min(2.5, state.scale * 1.2); applyTransform(); });
  document.getElementById("btn-zoom-out").addEventListener("click", () => { state.scale = Math.max(0.2, state.scale / 1.2); applyTransform(); });
  document.getElementById("btn-fit").addEventListener("click", fitToView);
  document.getElementById("btn-clear-sel").addEventListener("click", () => {
    state.selectedId = null; state.selectedHubId = null; state.compareId = null; state.kinshipResult = null;
    renderGraph(); renderDrawer();
  });
}

// Fallback light-dismiss for browsers without <dialog closedby="any"> (e.g. Safari)
function initDialogLightDismissFallback() {
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    const outside = (e, dialog) => {
      const r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
      let pressedOutside = false;
      dialog.addEventListener("pointerdown", (e) => { pressedOutside = outside(e, dialog); });
      dialog.addEventListener("pointerup", (e) => { if (pressedOutside && outside(e, dialog)) dialog.close(); });
    }
  }
}

function populateTractFilter() {
  const sel = document.getElementById("tract-filter");
  const prev = state.selectedTract;
  sel.replaceChildren(el("option", { value: "" }, "All Land Tracts"));
  for (const tr of (state.data.tracts || [])) {
    sel.appendChild(el("option", { value: tr.tract_group }, `Tract: ${tr.tract_group} (${tr.individuals_involved.length})`));
  }
  sel.value = prev;
}

function exportSvg() {
  const orig = document.getElementById("graph");
  const clone = orig.cloneNode(true);
  const lw = state.data.layout.width || 960;
  const lh = state.data.layout.height || 600;
  clone.setAttribute("viewBox", `0 0 ${lw} ${lh}`);
  clone.setAttribute("width", String(lw));
  clone.setAttribute("height", String(lh));
  const vp = clone.querySelector("#viewport");
  if (vp) vp.setAttribute("transform", "translate(0,0) scale(1)");

  const cs = getComputedStyle(document.documentElement);
  const v = (name, fb) => cs.getPropertyValue(name).trim() || fb;
  const inlineCss = `
    .node-card { fill: ${v("--surface", "#fff")}; stroke: ${v("--border", "#ccc")}; stroke-width: 1.6; rx: 10; }
    .node-group.status-confirmed .node-card { stroke: ${v("--confirmed", "#047857")}; }
    .node-group.status-tentative .node-card { stroke: ${v("--tentative", "#b45309")}; stroke-dasharray: 5 3; }
    .node-group.in-path .node-card { stroke: ${v("--collapse", "#7c3aed")}; stroke-width: 2.8; }
    .node-group.in-tract .node-card { stroke: ${v("--confirmed", "#047857")}; stroke-width: 2.8; }
    .node-group.dimmed { opacity: 0.22; }
    .node-name { font: 700 13px system-ui, sans-serif; fill: ${v("--text", "#161d26")}; }
    .node-dates { font: 11px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; }
    .node-id { font: 9.5px monospace; fill: ${v("--muted", "#5a6678")}; }
    .badge-collapse rect { fill: ${v("--collapse", "#7c3aed")}; rx: 8; }
    .badge-collapse text { fill: #fff; font: 700 10px system-ui, sans-serif; }
    .badge-census-ok rect { fill: ${v("--confirmed", "#047857")}; rx: 8; }
    .badge-census-miss rect { fill: ${v("--tentative", "#b45309")}; rx: 8; }
    .badge-census-ok text, .badge-census-miss text { fill: #fff; font: 700 10px system-ui, sans-serif; }
    .hub-diamond { fill: ${v("--surface", "#fff")}; stroke: ${v("--muted", "#5a6678")}; stroke-width: 1.8; }
    .hub-group.consang .hub-diamond { fill: ${v("--consang", "#be123c")}; stroke: ${v("--consang", "#be123c")}; }
    .hub-label { font: 10px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; text-anchor: middle; }
    .hub-group.consang .hub-label { fill: ${v("--consang", "#be123c")}; font-weight: 700; }
    .edge-path { fill: none; stroke: ${v("--muted", "#5a6678")}; stroke-width: 1.7; opacity: 0.72; }
    .edge-path.proven { stroke: ${v("--confirmed", "#047857")}; opacity: 0.85; }
    .edge-path.probable { stroke-dasharray: 6 3; }
    .edge-path.speculative { stroke-dasharray: 2 3; }
    .edge-path.fan { stroke: ${v("--fan", "#4338ca")}; stroke-dasharray: 4 4; }
    .edge-path.consang { stroke: ${v("--consang", "#be123c")}; stroke-width: 2.2; }
    .edge-path.highlighted { stroke: ${v("--collapse", "#7c3aed")}; stroke-width: 3.2; stroke-dasharray: none; opacity: 1; }
    .edge-path.dimmed { opacity: 0.12; }
  `;
  const styleEl = svgEl("style");
  styleEl.textContent = inlineCss;
  const bgRect = svgEl("rect", { width: lw, height: lh, fill: v("--bg", "#f6f7f9") });
  clone.insertBefore(bgRect, clone.firstChild);
  clone.insertBefore(styleEl, clone.firstChild);

  const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: `${state.data.meta.workspace_name || "genealogy"}-tree.svg` });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function reloadFromServer() {
  if (STANDALONE_MODE) return;
  const r = state.redactLiving ? "1" : "0";
  const resp = await fetch(`/api/graph.json?redact_living=${r}`);
  if (!resp.ok) return;
  state.data = await resp.json();
  populateTractFilter();
  renderGraph();
  renderDrawer();
}

function initToolbar() {
  populateTractFilter();

  document.getElementById("search-input").addEventListener("input", (e) => {
    state.searchQuery = e.target.value;
    renderGraph();
  });
  document.getElementById("focus-mode").addEventListener("change", (e) => {
    state.focusMode = e.target.value;
    renderGraph();
  });
  document.getElementById("tract-filter").addEventListener("change", (e) => {
    state.selectedTract = e.target.value;
    if (state.selectedTract) { state.selectedId = null; state.selectedHubId = null; }
    renderGraph();
    renderDrawer();
  });

  const eraCheck = document.getElementById("era-enabled");
  const eraSlider = document.getElementById("era-slider");
  const eraLabel = document.getElementById("era-year-label");
  const configureEraBounds = () => {
    const eMin = state.data.meta.era_min ?? 1790;
    const eMax = state.data.meta.era_max ?? 1960;
    eraSlider.min = String(eMin);
    eraSlider.max = String(eMax);
    const cur = parseInt(eraSlider.value, 10);
    if (cur < eMin || cur > eMax) {
      eraSlider.value = String(Math.min(eMax, Math.max(eMin, 1910)));
    }
  };
  configureEraBounds();
  const syncEra = () => {
    state.eraEnabled = eraCheck.checked;
    state.eraYear = parseInt(eraSlider.value, 10);
    eraSlider.disabled = !state.eraEnabled;
    if (!state.eraEnabled) {
      eraLabel.textContent = "Off";
      eraSlider.setAttribute("aria-valuetext", "Era filter disabled");
    } else {
      const isCen = (state.data.meta.census_years || []).includes(state.eraYear);
      eraLabel.textContent = String(state.eraYear);
      eraSlider.setAttribute("aria-valuetext", isCen ? `Year ${state.eraYear} (National census year)` : `Year ${state.eraYear}`);
    }
    renderGraph();
  };
  eraCheck.addEventListener("change", syncEra);
  eraSlider.addEventListener("input", syncEra);

  document.getElementById("toggle-fan").addEventListener("change", (e) => { state.showFan = e.target.checked; renderGraph(); });
  document.getElementById("toggle-consang").addEventListener("change", (e) => { state.showConsang = e.target.checked; renderGraph(); });
  document.getElementById("toggle-excluded").addEventListener("change", (e) => { state.showExcluded = e.target.checked; renderGraph(); });
  document.getElementById("btn-export-svg").addEventListener("click", exportSvg);

  const redactBox = document.getElementById("toggle-redact");
  redactBox.checked = state.redactLiving;
  if (STANDALONE_MODE) {
    redactBox.disabled = true;
    redactBox.title = "In static HTML export, living redaction is baked in at export time.";
    document.getElementById("btn-reload").hidden = true;
  } else {
    redactBox.addEventListener("change", async (e) => {
      state.redactLiving = e.target.checked;
      await reloadFromServer();
    });
    document.getElementById("btn-reload").addEventListener("click", reloadFromServer);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  initDialogLightDismissFallback();
  initCopyHandlers();
  initPanZoom();
  initToolbar();
  renderGraph();
  renderDrawer();
  fitToView();
});
</script>
</body>
</html>
"""


def render_viewer_html(
    root: Path | str | Dataset,
    *,
    standalone: bool = False,
    redact_living: bool = True,
) -> str:
    """Render the self-contained interactive HTML viewer."""
    payload = build_viewer_payload(root, redact_living=redact_living)
    # Escape closing script tags defensively inside inline JSON
    json_blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return _VIEWER_HTML_TEMPLATE.replace(
        "__STANDALONE_FLAG__", "true" if standalone else "false"
    ).replace("__INITIAL_JSON__", json_blob)


def export_viewer_html(
    root: Path | str,
    out_path: Path | str,
    *,
    redact_living: bool = True,
) -> Path:
    """Write a standalone offline HTML viewer file."""
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    html = render_viewer_html(root, standalone=True, redact_living=redact_living)
    out.write_text(html, encoding="utf-8")
    return out


def make_viewer_server(
    root: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    redact_living: bool = True,
) -> ThreadingHTTPServer:
    """Create a local ThreadingHTTPServer serving the interactive tree viewer & live API."""
    root_path = Path(root).resolve()
    artifacts_root = load_dataset(root_path).artifacts_dir

    class ViewerHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # Keep terminal clean

        def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, obj: Any, status: int = 200) -> None:
            self._send_bytes(
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status=status,
            )

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", "/index.html"):
                html = render_viewer_html(root_path, standalone=False, redact_living=redact_living)
                self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/graph.json":
                redact_q = qs.get("redact_living", [None])[0]
                redact_val = redact_living if redact_q is None else (redact_q != "0")
                payload = build_viewer_payload(root_path, redact_living=redact_val)
                self._send_json(payload)
                return

            if path == "/api/kinship":
                a = qs.get("a", [None])[0]
                b = qs.get("b", [None])[0]
                if not a or not b:
                    self._send_json({"error": "Parameters 'a' and 'b' are required"}, status=400)
                    return
                try:
                    rep = compute_kinship(root_path, a, b)
                    self._send_json(rep.to_dict())
                except KeyError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                return

            if path == "/api/collapse":
                iid = qs.get("id", [None])[0]
                if not iid:
                    self._send_json({"error": "Parameter 'id' is required"}, status=400)
                    return
                try:
                    rep = compute_pedigree_collapse(root_path, iid)
                    self._send_json(rep.to_dict())
                except KeyError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                return

            if path.startswith("/artifacts/"):
                rel = unquote(path[len("/artifacts/") :])
                candidate = (artifacts_root / rel).resolve()
                try:
                    candidate.relative_to(artifacts_root)
                except ValueError:
                    self._send_bytes(b"Forbidden", "text/plain", status=HTTPStatus.FORBIDDEN)
                    return
                if not candidate.is_file():
                    self._send_bytes(b"Not Found", "text/plain", status=HTTPStatus.NOT_FOUND)
                    return
                mime, _ = mimetypes.guess_type(str(candidate))
                self._send_bytes(candidate.read_bytes(), mime or "application/octet-stream")
                return

            self._send_bytes(b"Not Found", "text/plain", status=HTTPStatus.NOT_FOUND)

    return ThreadingHTTPServer((host, port), ViewerHandler)


def serve_viewer(
    root: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    redact_living: bool = True,
) -> None:
    """Start the local viewer HTTP server until interrupted."""
    server = make_viewer_server(root, host=host, port=port, redact_living=redact_living)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"Serving genealogy interactive viewer at {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
