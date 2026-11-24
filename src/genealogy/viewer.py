"""Interactive local web viewer & static HTML exporter built for inosculated (endogamous) trees."""

from __future__ import annotations

import bisect
import datetime
import json
import mimetypes
import os
import re
import threading
import webbrowser
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from genealogy.analysis import (
    REGIONAL_CENSUS_YEARS,
    KinshipContext,
    _canonical_tract_group,
    _extract_discrepancy_bullets,
    compute_kinship,
    compute_land_tracts,
    compute_pedigree_collapse,
    compute_place_profile,
    detect_individual_regions,
    find_consanguineous_marriages,
    list_places_summary,
)
from genealogy.loader import Dataset, load_dataset, load_workspace_config
from genealogy.schema import parse_genealogy_date

CARD_W = 216
CARD_H = 88
HUB_SIZE = 18
LAYER_GAP_Y = 192
COUPLE_GAP_X = 28
UNIT_GAP_X = 56
MARGIN_X = 140
MARGIN_Y = 92


def _pava_place_row(widths: list[float], targets: list[float], gap: float) -> list[float]:
    """Place ordered intervals of given widths as close to targets as possible without overlap."""
    n = len(widths)
    if n == 0:
        return []
    offsets = [0.0] * n
    for i in range(1, n):
        offsets[i] = offsets[i - 1] + (widths[i - 1] + widths[i]) / 2.0 + gap

    # Pool Adjacent Violators on z[i] = targets[i] - offsets[i]
    blocks: list[list[float]] = []
    for i in range(n):
        z = targets[i] - offsets[i]
        blocks.append([z, 1.0, float(i), float(i)])
        while len(blocks) >= 2:
            prev = blocks[-2]
            curr = blocks[-1]
            if (prev[0] / prev[1]) > (curr[0] / curr[1]):
                prev[0] += curr[0]
                prev[1] += curr[1]
                prev[3] = curr[3]
                blocks.pop()
            else:
                break

    centers = [0.0] * n
    for sum_z, count, start_i, end_i in blocks:
        val = sum_z / count
        for i in range(int(start_i), int(end_i) + 1):
            centers[i] = val + offsets[i]
    return centers


def _generation_label(delta: int) -> str:
    """Human-readable generation label relative to a focal person (delta = focal_rank - rank)."""
    if delta == 0:
        return "Focal / Self"
    if delta == 1:
        return "Parents (+1)"
    if delta == 2:
        return "Grandparents (+2)"
    if delta == 3:
        return "Great-Grandparents (+3)"
    if delta > 3:
        return f"{delta - 2}× Great-Grandparents (+{delta})"
    if delta == -1:
        return "Children (-1)"
    if delta == -2:
        return "Grandchildren (-2)"
    return f"{-delta - 2}× Great-Grandchildren ({delta})"


def _pair_cross_diff(a: list[int], b: list[int]) -> int:
    """Return (# pairs x < y) - (# pairs x > y) for sorted external coordinate lists a, b."""
    if not a or not b:
        return 0
    na, nb = len(a), len(b)
    if a[-1] < b[0]:
        return na * nb
    if a[0] > b[-1]:
        return -na * nb
    lo = 0
    hi = 0
    diff = 0
    for x in a:
        while lo < nb and b[lo] < x:
            lo += 1
        if hi < lo:
            hi = lo
        while hi < nb and b[hi] <= x:
            hi += 1
        diff += (nb - hi) - lo
    return diff


def _fast_pair_cost_int(groups: list[list[int]], lxs2: list[int]) -> float:
    """Exact crossing + squared-span cost via Fenwick tree on doubled integer coordinates."""
    span2 = 0
    max_r = 0
    edge_count = 0
    for us, lx2 in zip(groups, lxs2, strict=True):
        for u2 in us:
            d = u2 - lx2
            span2 += d * d
            r = u2 + 1
            if r > max_r:
                max_r = r
            edge_count += 1
    if edge_count <= 1 or max_r <= 1:
        return span2 * 0.0125
    bit = [0] * (max_r + 1)
    crossings = 0
    inserted = 0
    for us in groups:
        if not us:
            continue
        # Query all edges of this node before inserting so shared-endpoint edges never cross.
        for u2 in us:
            idx = u2 + 1
            s = 0
            while idx > 0:
                s += bit[idx]
                idx -= idx & -idx
            crossings += inserted - s
        for u2 in us:
            idx = u2 + 1
            while idx <= max_r:
                bit[idx] += 1
                idx += idx & -idx
            inserted += 1
    return crossings * 100.0 + span2 * 0.0125


def _compute_dag_layout(
    ds: Dataset,
    consanguineous_pairs: dict[tuple[str, str], dict[str, Any]] | set[tuple[str, str]],
) -> dict[str, Any]:
    """Compute a generation-aligned, couple-paired DAG + Marriage-Hub layout minimizing crossings."""
    ind_ids = sorted(ds.individuals.keys())
    if not ind_ids:
        return {
            "nodes": {},
            "hubs": [],
            "edges": [],
            "generations": [],
            "width": 800,
            "height": 500,
            "card_w": CARD_W,
            "card_h": CARD_H,
        }

    parents_of: dict[str, list[str]] = defaultdict(list)
    children_of: dict[str, list[str]] = defaultdict(list)
    spouses_of: dict[str, list[str]] = defaultdict(list)
    partners_of: dict[str, list[str]] = defaultdict(list)
    siblings_of: dict[str, list[str]] = defaultdict(list)
    fan_of: dict[str, list[str]] = defaultdict(list)
    marriage_dates: dict[tuple[str, str], str] = {}

    parents_seen: dict[str, set[str]] = defaultdict(set)
    children_seen: dict[str, set[str]] = defaultdict(set)
    spouses_seen: dict[str, set[str]] = defaultdict(set)
    partners_seen: dict[str, set[str]] = defaultdict(set)
    siblings_seen: dict[str, set[str]] = defaultdict(set)
    fan_seen: dict[str, set[str]] = defaultdict(set)

    def _add_dir(lst_map: dict[str, list[str]], seen_map: dict[str, set[str]], src: str, dst: str) -> None:
        s = seen_map[src]
        if dst not in s:
            s.add(dst)
            lst_map[src].append(dst)

    for rel in ds.relationships:
        if rel.person_1 not in ds.individuals or rel.person_2 not in ds.individuals:
            continue
        if rel.type == "parent_child":
            _add_dir(parents_of, parents_seen, rel.person_2, rel.person_1)
            _add_dir(children_of, children_seen, rel.person_1, rel.person_2)
        elif rel.type == "marriage":
            _add_dir(spouses_of, spouses_seen, rel.person_1, rel.person_2)
            _add_dir(spouses_of, spouses_seen, rel.person_2, rel.person_1)
            _add_dir(partners_of, partners_seen, rel.person_1, rel.person_2)
            _add_dir(partners_of, partners_seen, rel.person_2, rel.person_1)
            if rel.date:
                key = (rel.person_1, rel.person_2) if rel.person_1 <= rel.person_2 else (rel.person_2, rel.person_1)
                marriage_dates[key] = rel.date
        elif rel.type == "sibling":
            _add_dir(siblings_of, siblings_seen, rel.person_1, rel.person_2)
            _add_dir(siblings_of, siblings_seen, rel.person_2, rel.person_1)
        elif rel.type == "fan_associate":
            _add_dir(fan_of, fan_seen, rel.person_1, rel.person_2)
            _add_dir(fan_of, fan_seen, rel.person_2, rel.person_1)

    # Also treat co-parents (sharing a child) as partners so unmarried co-parents align
    for ps in parents_of.values():
        if len(ps) >= 2:
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    a, b = ps[i], ps[j]
                    _add_dir(partners_of, partners_seen, a, b)
                    _add_dir(partners_of, partners_seen, b, a)

    birth_year: dict[str, int] = {}
    sex_order: dict[str, int] = {}
    for iid in ind_ids:
        ind = ds.individuals[iid].data
        b = parse_genealogy_date(ind.birth.date) if ind.birth else None
        birth_year[iid] = b.year_min if b and b.year_min is not None else 9999
        s = ind.sex
        sex_order[iid] = 0 if s == "M" else (1 if s == "F" else 2)

    # Helper: longest ancestor depth for tie-breaking component anchors
    depth_memo: dict[str, int] = {}

    def _anc_depth(iid: str, visiting: set[str]) -> int:
        if iid in depth_memo:
            return depth_memo[iid]
        if iid in visiting:
            return 0
        visiting.add(iid)
        ps = parents_of.get(iid, ())
        d = 0 if not ps else 1 + max(_anc_depth(p, visiting) for p in ps)
        visiting.remove(iid)
        depth_memo[iid] = d
        return d

    for iid in ind_ids:
        _anc_depth(iid, set())

    # Step 1: Connected-component BFS generation assignment anchored on focal person (or deepest sink)
    visited_comp: set[str] = set()
    raw_gen: dict[str, int] = {}
    components: list[list[str]] = []

    for start in ind_ids:
        if start in visited_comp:
            continue
        comp: list[str] = []
        q: deque[str] = deque([start])
        visited_comp.add(start)
        while q:
            u = q.popleft()
            comp.append(u)
            for adj in (parents_of, children_of, partners_of, siblings_of):
                for nb in adj.get(u, ()):
                    if nb not in visited_comp:
                        visited_comp.add(nb)
                        q.append(nb)
        components.append(comp)

    focal_id = ds.focal_person_id if ds.focal_person_id in ds.individuals else None

    for comp in components:
        comp_set = set(comp)
        if focal_id and focal_id in comp_set:
            anchor = focal_id
        else:
            # Prefer a sink (no children) with deepest known ancestry, then youngest birth year
            sinks = [u for u in comp if not children_of.get(u)]
            candidates = sinks if sinks else comp
            anchor = max(
                candidates,
                key=lambda u: (depth_memo.get(u, 0), birth_year[u] if birth_year[u] < 9999 else -1, u),
            )

        # BFS from anchor: parents = -1, children = +1, partners/siblings = 0
        # Prioritize direct blood & partner links before explicit sibling links
        comp_gen: dict[str, int] = {anchor: 0}
        queue: deque[str] = deque([anchor])
        while queue:
            u = queue.popleft()
            gu = comp_gen[u]
            for p in parents_of.get(u, ()):
                if p in comp_set and p not in comp_gen:
                    comp_gen[p] = gu - 1
                    queue.append(p)
            for c in children_of.get(u, ()):
                if c in comp_set and c not in comp_gen:
                    comp_gen[c] = gu + 1
                    queue.append(c)
            for s in partners_of.get(u, ()):
                if s in comp_set and s not in comp_gen:
                    comp_gen[s] = gu
                    queue.append(s)
            for sib in siblings_of.get(u, ()):
                if sib in comp_set and sib not in comp_gen:
                    comp_gen[sib] = gu
                    queue.append(sib)

        # Phase B: Enforce hard DAG invariant gen[parent] <= gen[child] - 1 using active-queue relaxation
        active: deque[str] = deque(comp)
        in_active: set[str] = set(comp)
        relax_count: dict[str, int] = defaultdict(int)
        max_relax = len(comp) + 2
        while active:
            c = active.popleft()
            in_active.discard(c)
            gc = comp_gen[c]
            for p in parents_of.get(c, ()):
                if p in comp_gen and comp_gen[p] >= gc and relax_count[p] < max_relax:
                    comp_gen[p] = gc - 1
                    relax_count[p] += 1
                    if p not in in_active:
                        in_active.add(p)
                        active.append(p)

        # Re-align rootless/unconstrained spouses if their partner was pulled up
        for _ in range(2):
            for u in comp:
                if parents_of.get(u) or children_of.get(u):
                    continue
                sp_gens = [comp_gen[s] for s in partners_of.get(u, ()) if s in comp_gen]
                if sp_gens:
                    comp_gen[u] = sp_gens[0]

        raw_gen.update(comp_gen)

    # Step 2: Pull FAN-only isolated individuals to their connected associate's generation
    for rel in ds.relationships:
        if rel.type == "fan_associate" and rel.person_1 in raw_gen and rel.person_2 in raw_gen:
            for a, b in ((rel.person_1, rel.person_2), (rel.person_2, rel.person_1)):
                if not parents_of.get(a) and not children_of.get(a) and not partners_of.get(a):
                    raw_gen[a] = raw_gen[b]

    # Normalize generations so earliest generation = 0
    min_g = min(raw_gen.values())
    rank: dict[str, int] = {iid: raw_gen[iid] - min_g for iid in ind_ids}

    # Step 3: Group into generation layers and form atomic couple/marriage units
    layers: dict[int, list[str]] = defaultdict(list)
    for iid in ind_ids:
        layers[rank[iid]].append(iid)
    sorted_ranks = sorted(layers.keys())

    # Build atomic horizontal units per layer (spouses/co-parents stay adjacent!)
    row_units: dict[int, list[list[str]]] = {}
    for r in sorted_ranks:
        row_set = set(layers[r])
        same_row_partners: dict[str, list[str]] = {}
        for u in layers[r]:
            # Prefer explicit marriages before co-parent-only links
            married = [s for s in spouses_of.get(u, ()) if s in row_set]
            married_set = set(married)
            other = [s for s in partners_of.get(u, ()) if s in row_set and s not in married_set]
            # Sort partners by marriage date then birth year
            married.sort(
                key=lambda s: (
                    marriage_dates.get((u, s) if u <= s else (s, u), "9999"),
                    birth_year[s],
                    s,
                )
            )
            same_row_partners[u] = married + other

        used: set[str] = set()
        units: list[list[str]] = []
        # Deterministic ordering: process multi-spouse hubs first, then couples, then singles
        ordered_nodes = sorted(
            layers[r],
            key=lambda u: (-len(same_row_partners[u]), birth_year[u], u),
        )
        for u in ordered_nodes:
            if u in used:
                continue
            ps = [p for p in same_row_partners[u] if p not in used]
            if not ps:
                used.add(u)
                units.append([u])
            elif len(ps) == 1:
                v = ps[0]
                used.add(u)
                used.add(v)
                pair = [u, v]
                pair.sort(key=lambda x: (sex_order[x], birth_year[x], x))
                units.append(pair)
            else:
                # 2+ spouses in same row: place u in the middle of its first two spouses [s1, u, s2]
                s1, s2 = ps[0], ps[1]
                used.add(u)
                used.add(s1)
                used.add(s2)
                units.append([s1, u, s2])

        units.sort(key=lambda unit: (min(birth_year[x] for x in unit), unit[0]))
        row_units[r] = units

    # Step 4: Crossing minimization (barycenter sweeps on units + internal couple orientation flip)
    # Doubled coordinates (`pos2`): +2 per node in unit, +1 between units (so all coords are exact integers).
    pos2: dict[str, int] = {}

    def _update_row_pos2(r: int) -> None:
        cur2 = 0
        for unit in row_units[r]:
            for iid in unit:
                pos2[iid] = cur2
                cur2 += 2
            cur2 += 1

    for r in sorted_ranks:
        _update_row_pos2(r)

    for _ in range(8):
        # Downward sweep: order by parents (snapshot matches full-pass _node_x_index semantics)
        snap = dict(pos2)
        for r in sorted_ranks[1:]:
            scored: list[tuple[float, int, str, list[str]]] = []
            for unit in row_units[r]:
                # Flip 2-person couple if both have parents and swapping removes an X-crossing
                if len(unit) == 2:
                    a, b = unit[0], unit[1]
                    pa = [snap[p] for p in parents_of.get(a, ()) if p in snap]
                    pb = [snap[p] for p in parents_of.get(b, ()) if p in snap]
                    if pa and pb and (sum(pa) / len(pa)) > (sum(pb) / len(pb)):
                        unit = [b, a]
                all_p = [snap[p] for m in unit for p in parents_of.get(m, ()) if p in snap]
                if all_p:
                    bary = sum(all_p) / len(all_p)
                else:
                    bary = sum(snap[m] for m in unit) / len(unit)
                scored.append((bary, min(birth_year[m] for m in unit), unit[0], unit))
            scored.sort(key=lambda t: (t[0], t[1], t[2]))
            row_units[r] = [t[3] for t in scored]
            _update_row_pos2(r)

        # Upward sweep: order by children
        snap = dict(pos2)
        for r in reversed(sorted_ranks[:-1]):
            scored_up: list[tuple[float, int, str, list[str]]] = []
            for unit in row_units[r]:
                all_c = [snap[c] for m in unit for c in children_of.get(m, ()) if c in snap]
                if all_c:
                    bary = sum(all_c) / len(all_c)
                else:
                    bary = sum(snap[m] for m in unit) / len(unit)
                scored_up.append((bary, min(birth_year[m] for m in unit), unit[0], unit))
            scored_up.sort(key=lambda t: (t[0], t[1], t[2]))
            row_units[r] = [t[3] for t in scored_up]
            _update_row_pos2(r)

    # Step 4b: Exact crossing-count + span 2-opt optimization (escapes barycenter local minima like double-X inversions)
    row_sets: dict[int, set[str]] = {r: set(layers[r]) for r in sorted_ranks}
    has_intra_row: dict[int, bool] = {}
    for r in sorted_ranks:
        rs = row_sets[r]
        has_intra_row[r] = any(
            (p in rs) or (c in rs)
            for u in rs
            for p, c in ((p0, None) for p0 in parents_of.get(u, ()))
        ) or any(c in rs for u in rs for c in children_of.get(u, ()))
    simple_between: dict[tuple[int, int], bool] = {}
    for idx_r in range(1, len(sorted_ranks)):
        ra, rb = sorted_ranks[idx_r - 1], sorted_ranks[idx_r]
        sa, sb = row_sets[ra], row_sets[rb]
        simple_between[(ra, rb)] = (
            all(c in sb for u in sa for c in children_of.get(u, ()))
            and all(p in sa for u in sb for p in parents_of.get(u, ()))
        )

    def _row_cost_up(r: int, pm2: dict[str, int]) -> float:
        groups_up: list[list[int]] = []
        lxs2: list[int] = []
        cur2 = 0
        for unit in row_units[r]:
            for iid in unit:
                lxs2.append(cur2)
                groups_up.append([pm2[p] for p in parents_of.get(iid, ()) if p in pm2])
                cur2 += 2
            cur2 += 1
        return _fast_pair_cost_int(groups_up, lxs2)

    def _row_cost_down(r: int, pm2: dict[str, int]) -> float:
        groups_dn: list[list[int]] = []
        lxs2: list[int] = []
        cur2 = 0
        for unit in row_units[r]:
            for iid in unit:
                lxs2.append(cur2)
                groups_dn.append([pm2[c] for c in children_of.get(iid, ()) if c in pm2])
                cur2 += 2
            cur2 += 1
        return _fast_pair_cost_int(groups_dn, lxs2)

    def _row_cost(r: int, pm2: dict[str, int]) -> float:
        return _row_cost_up(r, pm2) + _row_cost_down(r, pm2)

    sweep_ranks = list(sorted_ranks) + list(reversed(sorted_ranks))
    for _ in range(6):
        improved_any = False
        for r in sweep_ranks:
            units = row_units[r]
            k = len(units)
            if k == 0:
                continue

            if has_intra_row[r]:
                # Fallback for cyclic datasets containing same-generation parent/child edges
                pm2_snap = dict(pos2)
                best_cost = _row_cost(r, pm2_snap)
                for i in range(k):
                    if len(units[i]) == 2:
                        units[i] = [units[i][1], units[i][0]]
                        c = _row_cost(r, pm2_snap)
                        if c + 1e-6 < best_cost:
                            best_cost = c
                            improved_any = True
                        else:
                            units[i] = [units[i][1], units[i][0]]
                max_dist = k if k <= 30 else 14
                for i in range(k):
                    for j in range(i + 1, min(k, i + max_dist + 1)):
                        units[i], units[j] = units[j], units[i]
                        c = _row_cost(r, pm2_snap)
                        if c + 1e-6 < best_cost:
                            best_cost = c
                            improved_any = True
                        else:
                            units[i], units[j] = units[j], units[i]
                _update_row_pos2(r)
                continue

            # Exact incremental O(1)/O(deg) crossing + span 2-opt for acyclic rows
            descs: list[list[Any]] = []
            x0_2: list[int] = []
            cur2 = 0
            for idx_u, unit in enumerate(units):
                x0_2.append(cur2)
                up_xs2 = sorted(pos2[p] for m in unit for p in parents_of.get(m, ()) if p in pos2)
                dn_xs2 = sorted(pos2[c] for m in unit for c in children_of.get(m, ()) if c in pos2)
                deg = len(up_xs2) + len(dn_xs2)
                sum_ud2 = 0
                for d, m in enumerate(unit):
                    d2 = 2 * d
                    for p in parents_of.get(m, ()):
                        if p in pos2:
                            sum_ud2 += pos2[p] - d2
                    for c in children_of.get(m, ()):
                        if c in pos2:
                            sum_ud2 += pos2[c] - d2
                descs.append([idx_u, up_xs2, dn_xs2, deg, sum_ud2, len(unit)])
                cur2 += 2 * len(unit) + 1

            # Try flipping 2-person couples
            for i in range(k):
                u = units[i]
                if len(u) == 2:
                    a, b = u[0], u[1]
                    up_a2 = sorted(pos2[p] for p in parents_of.get(a, ()) if p in pos2)
                    up_b2 = sorted(pos2[p] for p in parents_of.get(b, ()) if p in pos2)
                    dn_a2 = sorted(pos2[c] for c in children_of.get(a, ()) if c in pos2)
                    dn_b2 = sorted(pos2[c] for c in children_of.get(b, ()) if c in pos2)
                    deg_a = len(up_a2) + len(dn_a2)
                    deg_b = len(up_b2) + len(dn_b2)
                    sum_a2 = sum(up_a2) + sum(dn_a2)
                    sum_b2 = sum(up_b2) + sum(dn_b2)
                    dcross = _pair_cross_diff(up_a2, up_b2) + _pair_cross_diff(dn_a2, dn_b2)
                    dspan2 = 4 * (x0_2[i] + 1) * (deg_a - deg_b) - 4 * (sum_a2 - sum_b2)
                    if dcross * 100.0 + dspan2 * 0.0125 < -1e-6:
                        units[i] = [b, a]
                        descs[i][4] = sum_b2 + sum_a2 - 2 * deg_a
                        improved_any = True

            # Try 2-opt unit swaps (all pairs for k <= 30, windowed for huge layers)
            d_cache: list[dict[int, int]] = [{} for _ in range(k)]

            def _get_cdiff(da: list[Any], db: list[Any], _cache: list[dict[int, int]] = d_cache) -> int:
                ua_id = da[0]
                ub_id = db[0]
                row_d = _cache[ua_id]
                val = row_d.get(ub_id)
                if val is None:
                    val = _pair_cross_diff(da[1], db[1]) + _pair_cross_diff(da[2], db[2])
                    row_d[ub_id] = val
                    _cache[ub_id][ua_id] = -val
                return val

            max_dist = k if k <= 30 else 14
            for i in range(k):
                di = descs[i]
                ui_deg, ui_sud2, ui_w = di[3], di[4], di[5]
                sum_it = 0
                for j in range(i + 1, min(k, i + max_dist + 1)):
                    dj = descs[j]
                    uj_deg, uj_sud2, uj_w = dj[3], dj[4], dj[5]
                    if j > i + 1:
                        sum_it += _get_cdiff(di, descs[j - 1])
                    dcross = _get_cdiff(di, dj) + sum_it
                    shift2 = 2 * (uj_w - ui_w)
                    dspan2 = 0
                    if shift2 == 0:
                        for t in range(i + 1, j):
                            dcross += _get_cdiff(descs[t], dj)
                    else:
                        for t in range(i + 1, j):
                            dt = descs[t]
                            dcross += _get_cdiff(dt, dj)
                            dspan2 += shift2 * ((2 * x0_2[t] + shift2) * dt[3] - 2 * dt[4])
                    xi = x0_2[i]
                    xj = x0_2[j]
                    xi_new = xj + shift2
                    dspan2 += (xi_new - xi) * ((xi_new + xi) * ui_deg - 2 * ui_sud2)
                    dspan2 += (xi - xj) * ((xi + xj) * uj_deg - 2 * uj_sud2)
                    if dcross * 100.0 + dspan2 * 0.0125 < -1e-6:
                        units[i], units[j] = units[j], units[i]
                        descs[i], descs[j] = descs[j], descs[i]
                        if shift2 != 0:
                            for t in range(i + 1, j + 1):
                                x0_2[t] += shift2
                        di = descs[i]
                        ui_deg, ui_sud2, ui_w = di[3], di[4], di[5]
                        sum_it = 0
                        for t in range(i + 1, j):
                            sum_it += _get_cdiff(di, descs[t])
                        improved_any = True

            _update_row_pos2(r)

        # Coupled 2-layer move: flip a 2-person couple [a, b] in row r, swap their two parent units in row r-1,
        # and let unconstrained units in row r-2 re-align by child barycenter before measuring total crossings.
        for idx_r in range(1, len(sorted_ranks)):
            r = sorted_ranks[idx_r]
            r_prev = sorted_ranks[idx_r - 1]
            r_prev2 = sorted_ranks[idx_r - 2] if idx_r >= 2 else None
            s_prev_r = simple_between[(r_prev, r)]
            s_p2_prev = simple_between[(r_prev2, r_prev)] if r_prev2 is not None else False
            node_to_prev_u: dict[str, int] = {
                m: ui for ui, u in enumerate(row_units[r_prev]) for m in u
            }
            cached_before: tuple[float, float, float, float, float, float] | None = None

            for i, unit in enumerate(row_units[r]):
                if len(unit) != 2:
                    continue
                a, b = unit[0], unit[1]
                ua_set = {node_to_prev_u[p] for p in parents_of.get(a, ()) if p in node_to_prev_u}
                ub_set = {node_to_prev_u[p] for p in parents_of.get(b, ()) if p in node_to_prev_u}
                if len(ua_set) != 1 or len(ub_set) != 1:
                    continue
                ua, ub = next(iter(ua_set)), next(iter(ub_set))
                if ua == ub:
                    continue

                if cached_before is None:
                    c_prev_u = _row_cost_up(r_prev, pos2)
                    c_prev_d = _row_cost_down(r_prev, pos2)
                    c_r_u = c_prev_d if s_prev_r else _row_cost_up(r, pos2)
                    c_r_d = _row_cost_down(r, pos2)
                    if r_prev2 is not None:
                        c_p2_u = _row_cost_up(r_prev2, pos2)
                        c_p2_d = c_prev_u if s_p2_prev else _row_cost_down(r_prev2, pos2)
                    else:
                        c_p2_u = 0.0
                        c_p2_d = 0.0
                    cached_before = (c_prev_u, c_prev_d, c_r_u, c_r_d, c_p2_u, c_p2_d)
                cost_before = sum(cached_before)

                if not has_intra_row[r]:
                    dn_a2 = sorted(pos2[c] for c in children_of.get(a, ()) if c in pos2)
                    dn_b2 = sorted(pos2[c] for c in children_of.get(b, ()) if c in pos2)
                    xa2 = pos2[a]
                    d_r_d = (
                        _pair_cross_diff(dn_a2, dn_b2) * 100.0
                        + (4 * (xa2 + 1) * (len(dn_a2) - len(dn_b2)) - 4 * (sum(dn_a2) - sum(dn_b2)))
                        * 0.0125
                    )
                    c_r_d_a = cached_before[3] + d_r_d
                else:
                    c_r_d_a = None  # compute after mutating pos2

                row_units[r][i] = [b, a]
                row_units[r_prev][ua], row_units[r_prev][ub] = (
                    row_units[r_prev][ub],
                    row_units[r_prev][ua],
                )
                _update_row_pos2(r)
                _update_row_pos2(r_prev)

                prev2_changed = False
                saved_prev2: list[list[str]] | None = None
                if r_prev2 is not None:
                    # Re-sort r_prev2 by child barycenter so leaf/unconstrained parents follow their children
                    scored_p2 = []
                    for u2 in row_units[r_prev2]:
                        cs = [pos2[c] for m in u2 for c in children_of.get(m, ()) if c in pos2]
                        bary = sum(cs) / len(cs) if cs else sum(pos2[m] for m in u2) / len(u2)
                        scored_p2.append((bary, min(birth_year[m] for m in u2), u2[0], u2))
                    scored_p2.sort(key=lambda t: (t[0], t[1], t[2]))
                    new_p2 = [t[3] for t in scored_p2]
                    if new_p2 != row_units[r_prev2]:
                        prev2_changed = True
                        saved_prev2 = row_units[r_prev2]
                        row_units[r_prev2] = new_p2
                        _update_row_pos2(r_prev2)

                c_prev_u_a = _row_cost_up(r_prev, pos2)
                c_prev_d_a = _row_cost_down(r_prev, pos2)
                c_r_u_a = c_prev_d_a if s_prev_r else _row_cost_up(r, pos2)
                if c_r_d_a is None:
                    c_r_d_a = _row_cost_down(r, pos2)
                if r_prev2 is not None:
                    c_p2_u_a = _row_cost_up(r_prev2, pos2) if prev2_changed else cached_before[4]
                    c_p2_d_a = c_prev_u_a if s_p2_prev else _row_cost_down(r_prev2, pos2)
                else:
                    c_p2_u_a = 0.0
                    c_p2_d_a = 0.0
                cost_after = c_prev_u_a + c_prev_d_a + c_r_u_a + c_r_d_a + c_p2_u_a + c_p2_d_a

                if cost_after + 1e-6 < cost_before:
                    improved_any = True
                    cached_before = (c_prev_u_a, c_prev_d_a, c_r_u_a, c_r_d_a, c_p2_u_a, c_p2_d_a)
                    node_to_prev_u = {
                        m: ui for ui, u in enumerate(row_units[r_prev]) for m in u
                    }
                else:
                    row_units[r][i] = [a, b]
                    row_units[r_prev][ua], row_units[r_prev][ub] = (
                        row_units[r_prev][ub],
                        row_units[r_prev][ua],
                    )
                    _update_row_pos2(r)
                    _update_row_pos2(r_prev)
                    if prev2_changed and saved_prev2 is not None:
                        row_units[r_prev2] = saved_prev2
                        _update_row_pos2(r_prev2)
        if not improved_any:
            break

    # Step 5: Optimal X-coordinate alignment using PAVA (parents aligned above children, zero overlap)
    def _unit_width(unit: list[str]) -> float:
        return len(unit) * CARD_W + (len(unit) - 1) * COUPLE_GAP_X

    unit_centers: dict[int, list[float]] = {}
    node_x: dict[str, float] = {}

    def _sync_row_nodes(r: int, centers: list[float]) -> None:
        unit_centers[r] = centers
        for unit, cx in zip(row_units[r], centers, strict=True):
            uw = _unit_width(unit)
            left = cx - uw / 2.0 + CARD_W / 2.0
            for idx, iid in enumerate(unit):
                node_x[iid] = left + idx * (CARD_W + COUPLE_GAP_X)

    # Initial compact centered placement
    for r in sorted_ranks:
        widths = [_unit_width(u) for u in row_units[r]]
        total_w = sum(widths) + max(0, len(widths) - 1) * UNIT_GAP_X
        cursor = -total_w / 2.0
        centers: list[float] = []
        for w in widths:
            centers.append(cursor + w / 2.0)
            cursor += w + UNIT_GAP_X
        _sync_row_nodes(r, centers)

    # Iterative downward + upward PAVA coordinate sweeps
    for _ in range(10):
        for r in sorted_ranks:
            units = row_units[r]
            widths = [_unit_width(u) for u in units]
            targets: list[float] = []
            for idx, unit in enumerate(units):
                p_xs = [node_x[p] for m in unit for p in parents_of.get(m, []) if p in node_x]
                c_xs = [node_x[c] for m in unit for c in children_of.get(m, []) if c in node_x]
                if p_xs and c_xs:
                    targets.append(0.55 * (sum(p_xs) / len(p_xs)) + 0.45 * (sum(c_xs) / len(c_xs)))
                elif p_xs:
                    targets.append(sum(p_xs) / len(p_xs))
                elif c_xs:
                    targets.append(sum(c_xs) / len(c_xs))
                else:
                    targets.append(unit_centers[r][idx])
            _sync_row_nodes(r, _pava_place_row(widths, targets, UNIT_GAP_X))

        for r in reversed(sorted_ranks):
            units = row_units[r]
            widths = [_unit_width(u) for u in units]
            targets_up: list[float] = []
            for idx, unit in enumerate(units):
                p_xs = [node_x[p] for m in unit for p in parents_of.get(m, []) if p in node_x]
                c_xs = [node_x[c] for m in unit for c in children_of.get(m, []) if c in node_x]
                if p_xs and c_xs:
                    targets_up.append(0.45 * (sum(p_xs) / len(p_xs)) + 0.55 * (sum(c_xs) / len(c_xs)))
                elif c_xs:
                    targets_up.append(sum(c_xs) / len(c_xs))
                elif p_xs:
                    targets_up.append(sum(p_xs) / len(p_xs))
                else:
                    targets_up.append(unit_centers[r][idx])
            _sync_row_nodes(r, _pava_place_row(widths, targets_up, UNIT_GAP_X))

    # Normalize X so leftmost card edge is at MARGIN_X
    min_left = min(node_x[iid] - CARD_W / 2.0 for iid in ind_ids)
    shift_x = MARGIN_X - min_left
    max_right = 0.0

    nodes_pos: dict[str, dict[str, Any]] = {}
    generations_meta: list[dict[str, Any]] = []
    focal_rank = rank.get(focal_id) if focal_id else (sorted_ranks[-1] if sorted_ranks else None)

    for row_idx, r in enumerate(sorted_ranks):
        y = float(MARGIN_Y + row_idx * LAYER_GAP_Y)
        label = (
            _generation_label(focal_rank - r)
            if focal_rank is not None
            else f"Gen {row_idx + 1}"
        )
        generations_meta.append({"generation": r, "y": round(y, 1), "label": label})
        for unit in row_units[r]:
            for iid in unit:
                x = round(node_x[iid] + shift_x, 1)
                max_right = max(max_right, x + CARD_W / 2.0)
                nodes_pos[iid] = {
                    "x": x,
                    "y": round(y, 1),
                    "generation": r,
                }

    canvas_w = round(max(960.0, max_right + MARGIN_X), 1)
    canvas_h = round(max(560.0, MARGIN_Y + max(0, len(sorted_ranks) - 1) * LAYER_GAP_Y + 120.0), 1)

    # Step 6: Build Marriage / Family Hubs (inline between adjacent couples, staggered bus lanes for children)
    hub_map: dict[tuple[str, ...], dict[str, Any]] = {}
    for rel in ds.relationships:
        if rel.type == "marriage" and rel.person_1 in nodes_pos and rel.person_2 in nodes_pos:
            key = (rel.person_1, rel.person_2) if rel.person_1 <= rel.person_2 else (rel.person_2, rel.person_1)
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
                "consanguineous": key in consanguineous_pairs,
                "kinship": None,
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
    placed_below_hubs: list[tuple[float, float]] = []

    for idx, (_key, hdata) in enumerate(sorted(hub_map.items())):
        hid = f"HUB-{idx + 1}"
        ps = sorted(hdata["parents"], key=lambda p: nodes_pos[p]["x"])
        xs = [nodes_pos[p]["x"] for p in ps]
        ys = [nodes_pos[p]["y"] for p in ps]

        is_inline = (
            len(ps) == 2
            and abs(ys[0] - ys[1]) < 1.0
            and abs(xs[1] - xs[0]) <= CARD_W + COUPLE_GAP_X + 8.0
        )

        if is_inline:
            hx = round((xs[0] + xs[1]) / 2.0, 1)
            hy = round(ys[0], 1)
        elif len(ps) == 1:
            hx = round(xs[0], 1)
            hy = round(ys[0] + CARD_H / 2.0 + 18.0, 1)
        else:
            hx = round(sum(xs) / len(xs), 1)
            hy = round(max(ys) + CARD_H / 2.0 + 24.0, 1)
            # Nudge vertically if colliding with another non-inline hub
            while any(abs(hx - px) < 42.0 and abs(hy - py) < 20.0 for px, py in placed_below_hubs):
                hy = round(hy + 18.0, 1)
            placed_below_hubs.append((hx, hy))

        hubs.append(
            {
                "id": hid,
                "x": hx,
                "y": hy,
                "inline": is_inline,
                "bus_y": None,
                "parents": ps,
                "children": sorted(hdata["children"], key=lambda c: nodes_pos[c]["x"]),
                "marriage": hdata["marriage"],
                "consanguineous": hdata["consanguineous"],
                "kinship": hdata["kinship"],
            }
        )

    # Assign non-overlapping horizontal bus_y lanes per vertical corridor between rows
    corridor_hubs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for h in hubs:
        if not h["children"]:
            continue
        min_child_gen = min(nodes_pos[c]["generation"] for c in h["children"])
        corridor_hubs[min_child_gen].append(h)

    for child_gen, ch_list in corridor_hubs.items():
        # Corridor sits directly above child_gen row (leaving room for marriage date + collateral pill above)
        child_y = min(nodes_pos[c]["y"] for h in ch_list for c in h["children"] if nodes_pos[c]["generation"] == child_gen)
        corridor_top = child_y - LAYER_GAP_Y + CARD_H / 2.0 + 44.0
        corridor_bot = child_y - CARD_H / 2.0 - 22.0
        corridor_mid = (corridor_top + corridor_bot) / 2.0

        # Interval coloring on horizontal span [min_x, max_x]
        spans: list[tuple[float, float, dict[str, Any]]] = []
        for h in ch_list:
            span_xs = [h["x"]] + [nodes_pos[c]["x"] for c in h["children"]]
            spans.append((min(span_xs), max(span_xs), h))
        spans.sort(key=lambda s: (s[0], s[1]))

        lane_ends: list[float] = []
        hub_lane: list[tuple[dict[str, Any], int]] = []
        for s_min, s_max, h in spans:
            assigned = -1
            for li, end_x in enumerate(lane_ends):
                if s_min > end_x + 12.0:
                    assigned = li
                    lane_ends[li] = s_max
                    break
            if assigned == -1:
                assigned = len(lane_ends)
                lane_ends.append(s_max)
            hub_lane.append((h, assigned))

        num_lanes = max(1, len(lane_ends))
        step = min(14.0, max(6.0, (corridor_bot - corridor_top) / max(1, num_lanes)))
        for h, lane in hub_lane:
            by = corridor_mid + (lane - (num_lanes - 1) / 2.0) * step
            h["bus_y"] = round(max(corridor_top, min(corridor_bot, by)), 1)

    # Helper: find a clear vertical gap X across intermediate rows for multi-generation edges
    row_intervals: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for pos in nodes_pos.values():
        row_intervals[pos["generation"]].append((pos["x"] - CARD_W / 2.0 - 10.0, pos["x"] + CARD_W / 2.0 + 10.0))
    for g_iv in row_intervals.values():
        g_iv.sort()

    clear_cands_cache: dict[tuple[int, int], list[float]] = {}

    def _find_clear_route_x(g_start: int, g_end: int, preferred_x: float) -> float | None:
        if g_end - g_start <= 1:
            return None
        key = (g_start, g_end)
        cands = clear_cands_cache.get(key)
        if cands is None:
            blocked: list[tuple[float, float]] = []
            for g in range(g_start + 1, g_end):
                blocked.extend(row_intervals.get(g, ()))
            if not blocked:
                clear_cands_cache[key] = []
                return None
            blocked.sort()
            merged: list[tuple[float, float]] = [blocked[0]]
            for s, e in blocked[1:]:
                ps, pe = merged[-1]
                if s <= pe:
                    merged[-1] = (ps, max(pe, e))
                else:
                    merged.append((s, e))
            # Candidate clear X positions: left of all, midpoints of gaps, right of all
            cands = [merged[0][0] - 18.0]
            for i in range(len(merged) - 1):
                if merged[i + 1][0] - merged[i][1] >= 12.0:
                    cands.append((merged[i][1] + merged[i + 1][0]) / 2.0)
            cands.append(merged[-1][1] + 18.0)
            clear_cands_cache[key] = cands
        if not cands:
            return None
        idx = bisect.bisect_left(cands, preferred_x)
        if idx == 0:
            return round(cands[0], 1)
        if idx == len(cands):
            return round(cands[-1], 1)
        left = cands[idx - 1]
        right = cands[idx]
        best = left if abs(left - preferred_x) <= abs(right - preferred_x) else right
        return round(best, 1)

    for h in hubs:
        hid = h["id"]
        ps = h["parents"]
        m_conf = h["marriage"]["confidence"] if h["marriage"] else "probable"
        parent_gen = max(nodes_pos[p]["generation"] for p in ps) if ps else 0
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
                    "consanguineous": h["consanguineous"],
                }
            )
        for c in h["children"]:
            metas = [pc_meta.get((p, c)) for p in ps if (p, c) in pc_meta]
            conf_order = {"proven": 0, "probable": 1, "speculative": 2}
            best_conf = min(
                (m["confidence"] for m in metas),
                key=lambda x: conf_order.get(x, 2),
                default="probable",
            )
            subtype = next((m["subtype"] for m in metas if m["subtype"] != "biological"), "biological")
            child_gen = nodes_pos[c]["generation"]
            route_x = _find_clear_route_x(parent_gen, child_gen, (h["x"] + nodes_pos[c]["x"]) / 2.0)
            edge_obj: dict[str, Any] = {
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
            if route_x is not None:
                edge_obj["route_x"] = route_x
                edge_obj["bot_bus_y"] = round(nodes_pos[c]["y"] - CARD_H / 2.0 - 24.0, 1)
            edges.append(edge_obj)

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
        "generations": generations_meta,
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


def _build_trie_regex(words: list[str]) -> str:
    """Compile a prefix-trie regex pattern over case-normalized strings for O(N) redaction."""
    trie: dict[str, Any] = {}
    for w in sorted({w.lower() for w in words if w}, key=len, reverse=True):
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = True

    def _emit(node: dict[str, Any]) -> str:
        prefix_parts: list[str] = []
        cur = node
        while "" not in cur and len(cur) == 1:
            ch, nxt = next(iter(cur.items()))
            prefix_parts.append(re.escape(ch))
            cur = nxt
        prefix = "".join(prefix_parts)
        has_end = "" in cur
        children = [(k, v) for k, v in cur.items() if k != ""]
        if not children:
            return prefix
        single_chars = [k for k, v in children if len(v) == 1 and "" in v]
        complex_parts = [re.escape(k) + _emit(v) for k, v in children if not (len(v) == 1 and "" in v)]
        if single_chars:
            if len(single_chars) == 1:
                complex_parts.append(re.escape(single_chars[0]))
            else:
                complex_parts.append("[" + "".join(re.escape(c) for c in single_chars) + "]")
        if len(complex_parts) == 1:
            body = complex_parts[0]
            if has_end:
                suffix = (
                    f"(?:{body})?"
                    if len(body) > 1 and not (body.startswith("[") and body.endswith("]"))
                    else f"{body}?"
                )
            else:
                suffix = body
        else:
            joined = "|".join(complex_parts)
            suffix = f"(?:{joined})?" if has_end else f"(?:{joined})"
        return prefix + suffix

    return _emit(trie)


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
    pattern = re.compile(_build_trie_regex(list(names)), re.IGNORECASE)
    return lambda text: pattern.sub("[Living]", text) if text else text


def build_viewer_payload(
    root: Path | str | Dataset,
    *,
    redact_living: bool = True,
    _ctx: KinshipContext | None = None,
) -> dict[str, Any]:
    """Build the full JSON payload consumed by the interactive HTML viewer."""
    ds = root if isinstance(root, Dataset) else load_dataset(root)
    ctx = _ctx if _ctx is not None else KinshipContext(ds)
    scrub = _build_living_redactor(ds) if redact_living else (lambda t: t)

    consanguineous_list = find_consanguineous_marriages(ds, _ctx=ctx)
    consanguineous_dicts = [r.to_dict() for r in consanguineous_list]
    consanguineous_pair_keys = {
        (rep["person_a"], rep["person_b"])
        if rep["person_a"] <= rep["person_b"]
        else (rep["person_b"], rep["person_a"])
        for rep in consanguineous_dicts
    }

    layout = _compute_dag_layout(ds, consanguineous_pair_keys)

    # Pre-index excluded leads, relationship sources, and census years in single O(S + R) passes
    excluded_by_ind: dict[str, list[str]] = defaultdict(list)
    census_year_by_sid: dict[str, int] = {}
    for sid, src in ds.sources.items():
        if src.data.relevance == "excluded":
            for iid in src.data.excluded_for:
                excluded_by_ind[iid].append(sid)
        elif src.data.record_type == "census":
            sd = parse_genealogy_date(src.data.date)
            if sd and sd.year_min:
                census_year_by_sid[sid] = sd.year_min

    rel_sources_by_ind: dict[str, set[str]] = defaultdict(set)
    for rel in ds.relationships:
        if rel.sources:
            rel_sources_by_ind[rel.person_1].update(rel.sources)
            rel_sources_by_ind[rel.person_2].update(rel.sources)

    living_ids = {iid for iid, ind in ds.individuals.items() if ind.data.living}

    individuals_out: dict[str, dict[str, Any]] = {}
    for iid in sorted(ds.individuals.keys()):
        loaded = ds.individuals[iid]
        d = loaded.data
        collapse = compute_pedigree_collapse(ds, iid, _ctx=ctx)
        collapse_dict = collapse.to_dict()

        # Collect all linked sources (profile + relationships)
        sids = d.all_source_ids()
        rel_sids = rel_sources_by_ind.get(iid)
        if rel_sids:
            sids.update(rel_sids)

        b_parsed = parse_genealogy_date(d.birth.date) if d.birth else None
        d_parsed = parse_genealogy_date(d.death.date) if d.death else None
        b_year = b_parsed.year_min if b_parsed else None
        d_year = d_parsed.year_max if d_parsed else None

        covered_censuses = {census_year_by_sid[sid] for sid in sids if sid in census_year_by_sid}

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
                if iid in living_ids:
                    entry["primary_name"] = "Living (Redacted)"
                entry["description"] = scrub(entry["description"])

    places_raw = [
        _redact_place_profile(
            compute_place_profile(ds, pid, _ctx=ctx),
            ds,
            scrub,
            redact_living,
            living_ids=living_ids,
        )
        for pid in sorted(ds.places)
    ]

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
                "consanguineous_marriages": len(consanguineous_dicts),
                "tracts": len(tracts_raw),
                "places": len(places_raw),
            },
            "focal_person_id": ds.focal_person_id,
        },
        "individuals": individuals_out,
        "sources": sources_out,
        "relationships": relationships_out,
        "consanguineous_marriages": consanguineous_dicts,
        "tracts": tracts_raw,
        "places": places_raw,
        "layout": layout,
    }


def _redact_place_profile(
    profile: dict[str, Any],
    ds: Dataset,
    scrub: Any,
    redact_living: bool,
    *,
    living_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Redact living individuals inside a place profile dictionary."""
    if not redact_living:
        return profile
    if living_ids is None:
        living_ids = {iid for iid, ind in ds.individuals.items() if ind.data.living}
    if profile.get("root_id") in living_ids:
        profile["root_name"] = "Living (Redacted)"
    for ind in profile.get("individuals", []):
        if ind["id"] in living_ids:
            ind["primary_name"] = "Living (Redacted)"
            ind["birth_date"] = None
            ind["death_date"] = None
    for c in profile.get("couples", []):
        if c["person_1"] in living_ids:
            c["person_1_name"] = "Living (Redacted)"
        if c["person_2"] in living_ids:
            c["person_2_name"] = "Living (Redacted)"
    for f in profile.get("fan_links", []):
        if f["person_1"] in living_ids:
            f["person_1_name"] = "Living (Redacted)"
        if f["person_2"] in living_ids:
            f["person_2_name"] = "Living (Redacted)"
    for ev in profile.get("events", []):
        if ev["individual_id"] in living_ids:
            ev["individual_name"] = "Living (Redacted)"
            ev["date"] = None
        ev["description"] = scrub(ev.get("description") or "")
    for person in profile.get("people", []):
        if person.get("individual_id") in living_ids:
            person["name"] = "Living (Redacted)"
    profile["body"] = scrub(profile.get("body") or "")
    return profile


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

    /* SVG Graph Nodes, Bands, Branches & Hover Focus */
    .gen-band-line { stroke: var(--border); stroke-width: 1; stroke-dasharray: 4 6; opacity: 0.45; }
    .gen-band-pill { pointer-events: none; }
    .gen-band-pill rect {
      fill: var(--surface);
      stroke: var(--border);
      stroke-width: 1;
      rx: 6;
      opacity: 0.9;
    }
    .gen-band-label {
      font-size: 11px;
      font-weight: 600;
      fill: var(--muted);
      user-select: none;
    }

    .node-group {
      cursor: pointer;
      transition: opacity 220ms ease-in;
    }
    .node-inner {
      transform-origin: 108px 44px;
      transform: scale(1);
    }
    @media (prefers-reduced-motion: no-preference) {
      .node-inner {
        transition: transform 200ms cubic-bezier(0.22, 1, 0.36, 1);
      }
    }
    svg#graph.compact-collateral .node-group.non-direct .node-inner {
      transform: scale(0.82);
    }
    svg#graph.compact-collateral .node-group.non-direct:hover .node-inner,
    svg#graph.compact-collateral .node-group.non-direct:focus-within .node-inner,
    svg#graph.compact-collateral .node-group.non-direct.selected .node-inner,
    svg#graph.compact-collateral .node-group.non-direct.compare-target .node-inner {
      transform: scale(1);
    }
    .node-group:focus-visible { outline: none; }
    .node-group:focus-visible .node-card {
      stroke: var(--accent);
      stroke-width: 3;
      stroke-dasharray: none;
    }
    .node-card {
      fill: var(--surface);
      stroke: var(--border);
      stroke-width: 1.6;
      rx: 10;
      transition: stroke 140ms ease, stroke-width 140ms ease, filter 140ms ease;
    }
    .node-group:hover .node-card {
      filter: drop-shadow(0 3px 8px rgb(0 0 0 / 0.14));
    }
    .node-group.status-confirmed .node-card { stroke: var(--confirmed); }
    .node-group.status-tentative .node-card { stroke: var(--tentative); stroke-dasharray: 5 3; }
    .node-group.status-hypothetical .node-card { stroke: var(--hypo); stroke-dasharray: 2 3; }
    .node-group.status-excluded .node-card { stroke: var(--hypo); opacity: 0.6; }
    .node-group.selected .node-card { stroke: var(--accent); stroke-width: 3; stroke-dasharray: none; }
    .node-group.compare-target .node-card { stroke: var(--consang); stroke-width: 3; stroke-dasharray: none; }
    .node-group.in-path .node-card { stroke: var(--collapse); stroke-width: 2.8; stroke-dasharray: none; }
    .node-group.in-tract .node-card { stroke: var(--confirmed); stroke-width: 2.8; stroke-dasharray: none; }
    .node-group.dimmed { opacity: 0.36; }

    .node-name { font-size: 13px; font-weight: 700; fill: var(--text); }
    .node-dates { font-size: 11px; fill: var(--muted); }
    .node-id { font-size: 9.5px; fill: var(--muted); font-family: ui-monospace, monospace; }

    .branch-toggle {
      cursor: pointer;
      opacity: 0.78;
      transition: opacity 150ms ease;
    }
    .node-group:hover .branch-toggle,
    .node-group:focus-within .branch-toggle,
    .branch-toggle.is-collapsed {
      opacity: 1;
    }
    @media (hover: none) {
      .branch-toggle { opacity: 1; }
    }
    .branch-toggle rect {
      fill: var(--surface);
      stroke: var(--border);
      stroke-width: 1.5;
      rx: 10;
      transition: fill 140ms ease, stroke 140ms ease;
    }
    .branch-toggle:hover rect {
      stroke: var(--accent);
      fill: var(--surface-alt);
    }
    .branch-toggle.is-collapsed rect {
      fill: var(--accent);
      stroke: var(--accent);
    }
    .branch-toggle text {
      font-size: 10px;
      font-weight: 700;
      fill: var(--muted);
      text-anchor: middle;
      pointer-events: none;
    }
    .branch-toggle:hover text { fill: var(--accent); }
    .branch-toggle.is-collapsed text { fill: #fff; }
    .branch-toggle:focus-visible { outline: none; }
    .branch-toggle:focus-visible rect { stroke: CanvasText; stroke-width: 2.2; }

    .collateral-toggle {
      cursor: pointer;
      opacity: 0.72;
      transition: opacity 150ms ease;
    }
    .hub-group:hover .collateral-toggle,
    .hub-group:focus-within .collateral-toggle,
    .collateral-toggle.is-collapsed {
      opacity: 1;
    }
    @media (hover: none) {
      .collateral-toggle { opacity: 1; }
    }
    .collateral-toggle rect {
      fill: var(--surface);
      stroke: var(--border);
      stroke-width: 1.4;
      rx: 9;
      transition: fill 140ms ease, stroke 140ms ease;
    }
    .collateral-toggle:hover rect {
      stroke: var(--collapse);
      fill: var(--surface-alt);
    }
    .collateral-toggle.is-collapsed rect {
      fill: var(--collapse);
      stroke: var(--collapse);
    }
    .collateral-toggle text {
      font-size: 9.5px;
      font-weight: 700;
      fill: var(--muted);
      text-anchor: middle;
      pointer-events: none;
    }
    .collateral-toggle:hover text { fill: var(--collapse); }
    .collateral-toggle.is-collapsed text { fill: #fff; }
    .collateral-toggle:focus-visible { outline: none; }
    .collateral-toggle:focus-visible rect { stroke: CanvasText; stroke-width: 2.2; }

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
      .node-card, .copy-tip, .branch-toggle, .branch-toggle rect, .collateral-toggle, .collateral-toggle rect { transition: none; }
      .node-group, .hub-group, .edge-path { transition-duration: 80ms; }
    }

    .hub-group {
      cursor: pointer;
      transition: opacity 220ms ease-in;
    }
    .hub-diamond { fill: var(--surface); stroke: var(--muted); stroke-width: 1.8; }
    .hub-group.consang .hub-diamond { fill: var(--consang); stroke: var(--consang); }
    .hub-group.selected .hub-diamond { stroke: var(--accent); stroke-width: 3; }
    .hub-label {
      font-size: 10px;
      fill: var(--muted);
      text-anchor: middle;
      paint-order: stroke;
      stroke: var(--bg);
      stroke-width: 7px;
      stroke-linejoin: round;
    }
    .hub-group.consang .hub-label { fill: var(--consang); font-weight: 700; }

    .edge-path {
      fill: none;
      stroke: var(--muted);
      stroke-width: 1.7;
      opacity: 0.72;
      transition: opacity 220ms ease-in, stroke-width 160ms ease;
    }
    .edge-path.proven { stroke: var(--confirmed); opacity: 0.85; }
    .edge-path.probable { stroke-dasharray: 6 3; }
    .edge-path.speculative { stroke-dasharray: 2 3; }
    .edge-path.fan { stroke: var(--fan); stroke-dasharray: 4 4; opacity: 0.6; }
    .edge-path.consang { stroke: var(--consang); stroke-width: 2.2; opacity: 0.9; }
    .edge-path.highlighted { stroke: var(--collapse); stroke-width: 3.2; stroke-dasharray: none; opacity: 1; }
    .edge-path.dimmed { opacity: 0.18; }

    /* Hover focus spotlight: direct family stays at 100%, others ease smoothly to subtle transparency */
    svg#graph.hover-focus-active .node-group:not(.hover-focus):not(.dimmed) { opacity: 0.28; }
    svg#graph.hover-focus-active .node-group.hover-focus { opacity: 1; }
    svg#graph.hover-focus-active .hub-group:not(.hover-focus) { opacity: 0.22; }
    svg#graph.hover-focus-active .hub-group.hover-focus { opacity: 1; }
    svg#graph.hover-focus-active .edge-path:not(.hover-focus):not(.dimmed) { opacity: 0.14; }
    svg#graph.hover-focus-active .edge-path.hover-focus { opacity: 1; stroke-width: 2.4; }

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
    <select id="place-filter" aria-label="Place / Community filter">
      <option value="">All Places / Communities</option>
    </select>
    <div class="era-bar">
      <label><input id="era-enabled" type="checkbox"> Era</label>
      <label class="sr-only" for="era-slider">Filter by year</label>
      <input id="era-slider" type="range" min="1790" max="1960" step="10" value="1910" disabled aria-valuetext="Era filter disabled">
      <span id="era-year-label" class="era-year">Off</span>
    </div>
    <label class="toggle"><input id="toggle-fan" type="checkbox" checked> FAN Club</label>
    <label class="toggle"><input id="toggle-consang" type="checkbox" checked> Inosculation</label>
    <label class="toggle" title="Render non-direct ancestors of the focal person smaller until hovered or selected"><input id="toggle-compact-collateral" type="checkbox" checked> Compact Collaterals</label>
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
          <g id="layer-bands"></g>
          <g id="layer-edges"></g>
          <g id="layer-hubs"></g>
          <g id="layer-nodes"></g>
        </g>
        <g id="layer-gen-labels"></g>
      </svg>
      <div class="canvas-controls">
        <button id="btn-zoom-in" type="button" aria-label="Zoom in">+</button>
        <button id="btn-zoom-out" type="button" aria-label="Zoom out">&minus;</button>
        <button id="btn-fit" type="button">Fit View</button>
        <button id="btn-collapse-collaterals" type="button" title="Collapse all collateral branches (keep direct ancestral line)">Hide Collaterals</button>
        <button id="btn-expand-all" type="button" hidden>Expand All</button>
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
const INITIAL_VIEWER_STATE = __INITIAL_VIEWER_STATE__;

const state = {
  data: INITIAL_DATA,
  activeLayout: INITIAL_DATA ? INITIAL_DATA.layout : null,
  collapsedAncestors: new Set(),
  collapsedCollaterals: new Set(),
  rowOffsets: {},
  rowAlignments: {}, // canonical server generation -> child individual ID whose parents are aligned over them
  selectedId: null,
  selectedHubId: null,
  compareMode: false,
  compareId: null,
  kinshipResult: null,
  showFan: true,
  showConsang: true,
  compactCollateral: true,
  showExcluded: false,
  redactLiving: INITIAL_DATA ? !!INITIAL_DATA.meta.redact_living : true,
  focusMode: "all",
  selectedTract: "",
  selectedPlace: "",
  placeFocalId: INITIAL_DATA?.meta?.focal_person_id || "",
  placeProfile: null,
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
  const children = {};
  let areMarried = false;
  for (const r of (data.relationships || [])) {
    if (r.type === "parent_child" && !["adoptive", "step", "foster"].includes(r.subtype)) {
      (parents[r.person_2] ||= []).push(r.person_1);
      (children[r.person_1] ||= []).push(r.person_2);
    } else if (r.type === "marriage" && ((r.person_1 === a && r.person_2 === b) || (r.person_1 === b && r.person_2 === a))) {
      areMarried = true;
    }
  }
  const maxDepth = 10;
  function ancDist(start) {
    const dist = { [start]: 0 };
    const q = [[start, 0]];
    let head = 0;
    while (head < q.length) {
      const [u, d] = q[head++];
      const nd = d + 1;
      for (const p of (parents[u] || [])) {
        if (!(p in dist)) {
          dist[p] = nd;
          if (nd < maxDepth) q.push([p, nd]);
        }
      }
    }
    return dist;
  }
  const da = ancDist(a);
  const db = ancDist(b);
  const common = new Set();
  for (const k of Object.keys(da)) {
    if (k in db) common.add(k);
  }
  const paths = [];
  if (common.size) {
    function prunedPaths(start, distFromStart) {
      const distToTarget = {};
      const dq = [];
      for (const t of common) { distToTarget[t] = 0; dq.push(t); }
      let dHead = 0;
      while (dHead < dq.length) {
        const u = dq[dHead++];
        const nd = distToTarget[u] + 1;
        for (const c of (children[u] || [])) {
          if ((c in distFromStart) && !(c in distToTarget) && distFromStart[c] + nd <= maxDepth) {
            distToTarget[c] = nd;
            dq.push(c);
          }
        }
      }
      if (!(start in distToTarget)) return {};
      const out = {};
      const pq = [[start]];
      let pHead = 0;
      while (pHead < pq.length) {
        const path = pq[pHead++];
        const cur = path[path.length - 1];
        if (common.has(cur)) (out[cur] ||= []).push(path);
        const plen = path.length - 1;
        if (plen >= maxDepth) continue;
        for (const p of (parents[cur] || [])) {
          const dt = distToTarget[p];
          if (dt !== undefined && plen + 1 + dt <= maxDepth && !path.includes(p)) {
            pq.push([...path, p]);
          }
        }
      }
      return out;
    }
    const pa = prunedPaths(a, da);
    const pb = prunedPaths(b, db);
    for (const anc of common) {
      const listA = pa[anc];
      const listB = pb[anc];
      if (!listA || !listB) continue;
      const pbInt = listB.map(y => [y, new Set(y.slice(0, -1))]);
      const ancName = data.individuals[anc]?.primary_name || anc;
      for (const x of listA) {
        const ix = new Set(x.slice(0, -1));
        const m = x.length - 1;
        for (const [y, iy] of pbInt) {
          let overlap = false;
          for (const node of ix) if (iy.has(node)) { overlap = true; break; }
          if (overlap) continue;
          const n = y.length - 1;
          paths.push({
            common_ancestor: anc,
            common_ancestor_name: ancName,
            steps_a: m,
            steps_b: n,
            label: `${m}↑ / ${n}↑ via ${ancName}`,
            path_a: x,
            path_b: y,
            path_contribution: Math.pow(0.5, m + n),
          });
        }
      }
    }
    paths.sort((u, v) => (u.steps_a + u.steps_b) - (v.steps_a + v.steps_b));
  }
  const estR = paths.reduce((acc, p) => acc + p.path_contribution, 0);
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

let _consangMapCache = { data: null, map: null };
function getConsangMap() {
  if (_consangMapCache.data === state.data && _consangMapCache.map) return _consangMapCache.map;
  const map = new Map();
  for (const rep of (state.data?.consanguineous_marriages || [])) {
    const key = rep.person_a <= rep.person_b ? `${rep.person_a}|${rep.person_b}` : `${rep.person_b}|${rep.person_a}`;
    map.set(key, rep);
  }
  _consangMapCache = { data: state.data, map };
  return map;
}

function getHubKinship(hub) {
  if (!hub) return null;
  if (hub.kinship) return hub.kinship;
  if (!hub.consanguineous || !hub.parents || hub.parents.length !== 2) return null;
  const [a, b] = hub.parents;
  const key = a <= b ? `${a}|${b}` : `${b}|${a}`;
  return getConsangMap().get(key) || null;
}

let _familyIndexCache = { data: null, idx: null };

function getFamilyIndex() {
  if (_familyIndexCache.data === state.data && _familyIndexCache.idx) return _familyIndexCache.idx;
  const parentsOf = {};
  const childrenOf = {};
  const spousesOf = {};
  const partnersOf = {};
  const siblingsOf = {};
  const marriageDates = {};
  const inds = state.data.individuals || {};

  const pushUnique = (map, k, v) => {
    const arr = (map[k] ||= []);
    if (!arr.includes(v)) arr.push(v);
  };

  for (const r of (state.data.relationships || [])) {
    if (!inds[r.person_1] || !inds[r.person_2]) continue;
    if (r.type === "parent_child") {
      pushUnique(parentsOf, r.person_2, r.person_1);
      pushUnique(childrenOf, r.person_1, r.person_2);
    } else if (r.type === "marriage") {
      pushUnique(spousesOf, r.person_1, r.person_2);
      pushUnique(spousesOf, r.person_2, r.person_1);
      pushUnique(partnersOf, r.person_1, r.person_2);
      pushUnique(partnersOf, r.person_2, r.person_1);
      if (r.date) {
        const key = r.person_1 <= r.person_2 ? `${r.person_1}|${r.person_2}` : `${r.person_2}|${r.person_1}`;
        marriageDates[key] = r.date;
      }
    } else if (r.type === "sibling") {
      pushUnique(siblingsOf, r.person_1, r.person_2);
      pushUnique(siblingsOf, r.person_2, r.person_1);
    }
  }
  for (const ps of Object.values(parentsOf)) {
    if (ps.length >= 2) {
      for (let i = 0; i < ps.length; i++) {
        for (let j = i + 1; j < ps.length; j++) {
          pushUnique(partnersOf, ps[i], ps[j]);
          pushUnique(partnersOf, ps[j], ps[i]);
        }
      }
    }
  }
  const idx = { parentsOf, childrenOf, spousesOf, partnersOf, siblingsOf, marriageDates };
  _familyIndexCache = { data: state.data, idx };
  return idx;
}

function countAncestorsIn(iid, allowedSet, idx) {
  const seen = new Set();
  const q = [];
  for (const p of (idx.parentsOf[iid] || [])) {
    if (allowedSet.has(p) && !seen.has(p)) { seen.add(p); q.push(p); }
  }
  let head = 0;
  while (head < q.length) {
    const cur = q[head++];
    for (const p of (idx.parentsOf[cur] || [])) {
      if (allowedSet.has(p) && !seen.has(p)) {
        seen.add(p);
        q.push(p);
      }
    }
  }
  return seen.size;
}

function getBaseVisibleIds() {
  const inds = state.data.individuals;
  let visible = new Set(Object.keys(inds).filter(id => state.showExcluded || inds[id].status !== "excluded"));

  if (state.selectedId && state.focusMode !== "all") {
    const idx = getFamilyIndex();
    const adj = state.focusMode === "ancestors" ? idx.parentsOf : idx.childrenOf;
    const keep = new Set([state.selectedId]);
    const queue = [state.selectedId];
    let head = 0;
    while (head < queue.length) {
      const cur = queue[head++];
      for (const nxt of (adj[cur] || [])) {
        if (visible.has(nxt) && !keep.has(nxt)) {
          keep.add(nxt);
          queue.push(nxt);
        }
      }
    }
    visible = keep;
  }
  return visible;
}

function hubKeyOf(hub) {
  return [...(hub.parents || [])].sort().join("|");
}

function getDirectAncestors(baseVisible) {
  const idx = getFamilyIndex();
  const data = state.data;
  const serverNodes = data.layout?.nodes || {};
  const indsMap = data.individuals || {};
  let focalForPedigree = data.meta?.focal_person_id;
  if (!focalForPedigree || !indsMap[focalForPedigree] || (baseVisible && !baseVisible.has(focalForPedigree))) {
    let bestId = null, bestGen = -Infinity, bestX = Infinity;
    const pool = baseVisible ? baseVisible : Object.keys(indsMap);
    for (const iid of pool) {
      const p = serverNodes[iid];
      if (!p) continue;
      if (p.generation > bestGen || (p.generation === bestGen && p.x < bestX)) {
        bestGen = p.generation;
        bestX = p.x;
        bestId = iid;
      }
    }
    focalForPedigree = bestId;
  }
  const direct = new Set();
  if (focalForPedigree) {
    direct.add(focalForPedigree);
    const dq = [focalForPedigree];
    let head = 0;
    while (head < dq.length) {
      const cur = dq[head++];
      for (const p of (idx.parentsOf[cur] || [])) {
        if (!direct.has(p) && (!baseVisible || baseVisible.has(p))) {
          direct.add(p);
          dq.push(p);
        }
      }
    }
  }
  return direct;
}

function isChildBlockedByCollateralCollapse(parentId, childId, directSet, idx) {
  if (!state.collapsedCollaterals.size || directSet.has(childId)) return false;
  // Check if the child's parent-set hub (containing parentId) is collapsed
  const cParents = (idx.parentsOf[childId] || []).slice().sort();
  if (!cParents.includes(parentId)) return false;
  if (state.collapsedCollaterals.has(cParents.join("|"))) return true;
  if (state.collapsedCollaterals.has(parentId)) return true;
  return false;
}

function getVisibleIds(baseVisibleOpt, directSetOpt) {
  const baseVisible = baseVisibleOpt || getBaseVisibleIds();
  if (!state.collapsedAncestors.size && !state.collapsedCollaterals.size) return baseVisible;

  const idx = getFamilyIndex();
  const directSet = directSetOpt || getDirectAncestors(baseVisible);
  const focalId = state.data.meta?.focal_person_id;

  // Partition baseVisible into connected components over family edges
  const visitedComp = new Set();
  const components = [];
  for (const id of [...baseVisible].sort()) {
    if (visitedComp.has(id)) continue;
    const comp = [];
    const q = [id];
    let head = 0;
    visitedComp.add(id);
    while (head < q.length) {
      const u = q[head++];
      comp.push(u);
      for (const adj of [idx.parentsOf[u], idx.childrenOf[u], idx.partnersOf[u], idx.siblingsOf[u]]) {
        if (!adj) continue;
        for (const nb of adj) {
          if (baseVisible.has(nb) && !visitedComp.has(nb)) {
            visitedComp.add(nb);
            q.push(nb);
          }
        }
      }
    }
    components.push(comp);
  }

  // Walk each component from its primary anchor respecting collapsedAncestors (up) and collapsedCollaterals (down)
  const kept = new Set();
  for (const comp of components) {
    const compSet = new Set(comp);
    let anchor = null;
    if (state.selectedId && state.focusMode !== "all" && compSet.has(state.selectedId)) {
      anchor = state.selectedId;
    } else if (focalId && compSet.has(focalId)) {
      anchor = focalId;
    } else {
      const sinks = comp.filter(u => !(idx.childrenOf[u] || []).some(c => compSet.has(c)));
      const cands = sinks.length ? sinks : comp;
      cands.sort((a, b) => {
        const ga = state.data.layout?.nodes?.[a]?.generation ?? 0;
        const gb = state.data.layout?.nodes?.[b]?.generation ?? 0;
        if (gb !== ga) return gb - ga;
        return a.localeCompare(b);
      });
      anchor = cands[0];
    }

    const q = [anchor];
    let head = 0;
    kept.add(anchor);
    while (head < q.length) {
      const u = q[head++];
      // Always traverse to partners (spouses / co-parents)
      for (const s of (idx.partnersOf[u] || [])) {
        if (compSet.has(s) && !kept.has(s)) { kept.add(s); q.push(s); }
      }
      // Traverse downward to children unless blocked by collapsedCollaterals (or child collapsed upward)
      for (const c of (idx.childrenOf[u] || [])) {
        if (
          compSet.has(c) &&
          !kept.has(c) &&
          !state.collapsedAncestors.has(c) &&
          !isChildBlockedByCollateralCollapse(u, c, directSet, idx)
        ) {
          kept.add(c);
          q.push(c);
        }
      }
      // Traverse upward to parents ONLY if u's ancestors are not collapsed
      if (!state.collapsedAncestors.has(u)) {
        for (const p of (idx.parentsOf[u] || [])) {
          if (compSet.has(p) && !kept.has(p)) { kept.add(p); q.push(p); }
        }
      }
    }
  }
  return kept;
}

function pavaPlaceRow(widths, targets, gap) {
  const n = widths.length;
  if (!n) return [];
  const offsets = new Array(n).fill(0);
  for (let i = 1; i < n; i++) {
    offsets[i] = offsets[i - 1] + (widths[i - 1] + widths[i]) / 2 + gap;
  }
  const blocks = [];
  for (let i = 0; i < n; i++) {
    blocks.push([targets[i] - offsets[i], 1, i, i]);
    while (blocks.length >= 2) {
      const prev = blocks[blocks.length - 2];
      const curr = blocks[blocks.length - 1];
      if (prev[0] / prev[1] > curr[0] / curr[1]) {
        prev[0] += curr[0];
        prev[1] += curr[1];
        prev[3] = curr[3];
        blocks.pop();
      } else break;
    }
  }
  const centers = new Array(n).fill(0);
  for (const [sumZ, count, s, e] of blocks) {
    const val = sumZ / count;
    for (let i = s; i <= e; i++) centers[i] = val + offsets[i];
  }
  return centers;
}

function generationLabelClient(delta) {
  if (delta === 0) return "Focal / Self";
  if (delta === 1) return "Parents (+1)";
  if (delta === 2) return "Grandparents (+2)";
  if (delta === 3) return "Great-Grandparents (+3)";
  if (delta > 3) return `${delta - 2}× Great-Grandparents (+${delta})`;
  if (delta === -1) return "Children (-1)";
  if (delta === -2) return "Grandchildren (-2)";
  return `${-delta - 2}× Great-Grandchildren (${delta})`;
}

function pairCrossDiffClient(a, b) {
  const na = a.length, nb = b.length;
  if (!na || !nb) return 0;
  if (a[na - 1] < b[0]) return na * nb;
  if (a[0] > b[nb - 1]) return -na * nb;
  let lo = 0, hi = 0, diff = 0;
  for (let idx = 0; idx < na; idx++) {
    const x = a[idx];
    while (lo < nb && b[lo] < x) lo++;
    if (hi < lo) hi = lo;
    while (hi < nb && b[hi] <= x) hi++;
    diff += (nb - hi) - lo;
  }
  return diff;
}

function fastPairCostIntClient(groups, lxs2) {
  let span2 = 0, maxR = 0, edgeCount = 0;
  for (let i = 0; i < groups.length; i++) {
    const us = groups[i], lx2 = lxs2[i];
    for (let j = 0; j < us.length; j++) {
      const u2 = us[j];
      const d = u2 - lx2;
      span2 += d * d;
      if (u2 + 1 > maxR) maxR = u2 + 1;
      edgeCount++;
    }
  }
  if (edgeCount <= 1 || maxR <= 1) return span2 * 0.0125;
  const bit = new Int32Array(maxR + 1);
  let crossings = 0, inserted = 0;
  for (let i = 0; i < groups.length; i++) {
    const us = groups[i];
    if (!us.length) continue;
    for (let j = 0; j < us.length; j++) {
      let idx = us[j] + 1, s = 0;
      while (idx > 0) { s += bit[idx]; idx -= idx & -idx; }
      crossings += inserted - s;
    }
    for (let j = 0; j < us.length; j++) {
      let idx = us[j] + 1;
      while (idx <= maxR) { bit[idx]++; idx += idx & -idx; }
      inserted++;
    }
  }
  return crossings * 100 + span2 * 0.0125;
}

let _clientLayoutCache = { data: null, key: null, layout: null };

function computeClientLayout(visible) {
  const serverLayout = state.data.layout;
  const allCount = Object.keys(state.data.individuals || {}).length;
  if (!state.collapsedAncestors.size && !state.collapsedCollaterals.size && state.focusMode === "all" && visible.size === allCount) {
    return serverLayout;
  }

  const cacheKey = [
    state.focusMode,
    state.focusMode === "all" ? "" : (state.selectedId || ""),
    state.showExcluded ? "1" : "0",
    [...state.collapsedAncestors].sort().join(","),
    [...state.collapsedCollaterals].sort().join(","),
  ].join("::");
  if (_clientLayoutCache.data === state.data && _clientLayoutCache.key === cacheKey && _clientLayoutCache.layout) {
    return _clientLayoutCache.layout;
  }

  const cw = serverLayout.card_w || 216;
  const ch = serverLayout.card_h || 88;
  const LAYER_GAP_Y = 192;
  const COUPLE_GAP_X = 28;
  const UNIT_GAP_X = 56;
  const MARGIN_X = 140;
  const MARGIN_Y = 92;

  const visIds = [...visible].sort();
  if (!visIds.length) {
    const emptyLayout = { nodes: {}, hubs: [], edges: [], generations: [], width: 800, height: 500, card_w: cw, card_h: ch };
    _clientLayoutCache = { data: state.data, key: cacheKey, layout: emptyLayout };
    return emptyLayout;
  }

  const idx = getFamilyIndex();
  const parentsOf = {};
  const childrenOf = {};
  const spousesOf = {};
  const partnersOf = {};
  for (const u of visIds) {
    parentsOf[u] = state.collapsedAncestors.has(u)
      ? []
      : (idx.parentsOf[u] || []).filter(p => visible.has(p));
    childrenOf[u] = (idx.childrenOf[u] || []).filter(c => visible.has(c) && !state.collapsedAncestors.has(c));
    spousesOf[u] = (idx.spousesOf[u] || []).filter(s => visible.has(s));
    partnersOf[u] = (idx.partnersOf[u] || []).filter(s => visible.has(s));
  }

  const serverX = (id) => (typeof serverLayout.nodes?.[id]?.x === "number" ? serverLayout.nodes[id].x : 0);

  // Reuse server generation ranks (already globally consistent!) and re-normalize to 0..k
  const rawRanks = {};
  let minR = Infinity;
  for (const u of visIds) {
    const r = serverLayout.nodes?.[u]?.generation ?? 0;
    rawRanks[u] = r;
    if (r < minR) minR = r;
  }
  const rank = {};
  const layers = {};
  for (const u of visIds) {
    const r = rawRanks[u] - minR;
    rank[u] = r;
    (layers[r] ||= []).push(u);
  }
  const sortedRanks = Object.keys(layers).map(Number).sort((a, b) => a - b);

  // Form atomic couple units per layer (using redaction-invariant serverX ordering)
  const rowUnits = {};
  for (const r of sortedRanks) {
    const rowSet = new Set(layers[r]);
    const sameRowPartners = {};
    for (const u of layers[r]) {
      const married = spousesOf[u].filter(s => rowSet.has(s));
      const marriedSet = new Set(married);
      const other = partnersOf[u].filter(s => rowSet.has(s) && !marriedSet.has(s));
      married.sort((a, b) => {
        const ka = u <= a ? `${u}|${a}` : `${a}|${u}`;
        const kb = u <= b ? `${u}|${b}` : `${b}|${u}`;
        return (idx.marriageDates[ka] || "9999").localeCompare(idx.marriageDates[kb] || "9999") || serverX(a) - serverX(b);
      });
      sameRowPartners[u] = [...married, ...other];
    }
    const used = new Set();
    const units = [];
    const ordered = [...layers[r]].sort((a, b) =>
      (sameRowPartners[b].length - sameRowPartners[a].length) || (serverX(a) - serverX(b)) || a.localeCompare(b)
    );
    for (const u of ordered) {
      if (used.has(u)) continue;
      const ps = sameRowPartners[u].filter(p => !used.has(p));
      if (!ps.length) {
        used.add(u);
        units.push([u]);
      } else if (ps.length === 1) {
        const v = ps[0];
        used.add(u); used.add(v);
        const pair = [u, v].sort((a, b) => serverX(a) - serverX(b) || a.localeCompare(b));
        units.push(pair);
      } else {
        used.add(u); used.add(ps[0]); used.add(ps[1]);
        units.push([ps[0], u, ps[1]]);
      }
    }
    units.sort((a, b) => Math.min(...a.map(serverX)) - Math.min(...b.map(serverX)) || a[0].localeCompare(b[0]));
    rowUnits[r] = units;
  }

  // Barycenter sweeps using exact doubled integer positions (`pos2`)
  const pos2 = {};
  const updateRowPos2 = (r) => {
    let cur2 = 0;
    for (const unit of rowUnits[r]) {
      for (const id of unit) { pos2[id] = cur2; cur2 += 2; }
      cur2 += 1;
    }
  };
  for (const r of sortedRanks) updateRowPos2(r);

  for (let pass = 0; pass < 6; pass++) {
    let snap = { ...pos2 };
    for (let i = 1; i < sortedRanks.length; i++) {
      const r = sortedRanks[i];
      const scored = rowUnits[r].map(unit => {
        if (unit.length === 2) {
          const [a, b] = unit;
          const pa = parentsOf[a].filter(p => p in snap).map(p => snap[p]);
          const pb = parentsOf[b].filter(p => p in snap).map(p => snap[p]);
          if (pa.length && pb.length && (pa.reduce((s, x) => s + x, 0) / pa.length) > (pb.reduce((s, x) => s + x, 0) / pb.length)) {
            unit = [b, a];
          }
        }
        const allP = unit.flatMap(m => parentsOf[m].filter(p => p in snap).map(p => snap[p]));
        const bary = allP.length ? allP.reduce((s, x) => s + x, 0) / allP.length
                                 : unit.reduce((s, m) => s + snap[m], 0) / unit.length;
        return { bary, sx: Math.min(...unit.map(serverX)), id: unit[0], unit };
      });
      scored.sort((a, b) => a.bary - b.bary || a.sx - b.sx || a.id.localeCompare(b.id));
      rowUnits[r] = scored.map(s => s.unit);
      updateRowPos2(r);
    }
    snap = { ...pos2 };
    for (let i = sortedRanks.length - 2; i >= 0; i--) {
      const r = sortedRanks[i];
      const scored = rowUnits[r].map(unit => {
        const allC = unit.flatMap(m => childrenOf[m].filter(c => c in snap).map(c => snap[c]));
        const bary = allC.length ? allC.reduce((s, x) => s + x, 0) / allC.length
                                 : unit.reduce((s, m) => s + snap[m], 0) / unit.length;
        return { bary, sx: Math.min(...unit.map(serverX)), id: unit[0], unit };
      });
      scored.sort((a, b) => a.bary - b.bary || a.sx - b.sx || a.id.localeCompare(b.id));
      rowUnits[r] = scored.map(s => s.unit);
      updateRowPos2(r);
    }
  }

  // Exact crossing-count + span 2-opt optimization (matches server layout)
  const rowSets = {};
  const hasIntraRow = {};
  for (const r of sortedRanks) {
    const rs = new Set(layers[r]);
    rowSets[r] = rs;
    let intra = false;
    for (const u of rs) {
      if ((parentsOf[u] || []).some(p => rs.has(p)) || (childrenOf[u] || []).some(c => rs.has(c))) {
        intra = true;
        break;
      }
    }
    hasIntraRow[r] = intra;
  }
  const simpleBetween = {};
  for (let ir = 1; ir < sortedRanks.length; ir++) {
    const ra = sortedRanks[ir - 1], rb = sortedRanks[ir];
    const sa = rowSets[ra], sb = rowSets[rb];
    let ok = true;
    for (const u of sa) if ((childrenOf[u] || []).some(c => !sb.has(c))) { ok = false; break; }
    if (ok) for (const u of sb) if ((parentsOf[u] || []).some(p => !sa.has(p))) { ok = false; break; }
    simpleBetween[`${ra}|${rb}`] = ok;
  }

  const rowCostUp = (r, pm2) => {
    const groupsUp = [], lxs2 = [];
    let cur2 = 0;
    for (const unit of rowUnits[r]) {
      for (const id of unit) {
        lxs2.push(cur2);
        groupsUp.push((parentsOf[id] || []).filter(p => p in pm2).map(p => pm2[p]));
        cur2 += 2;
      }
      cur2 += 1;
    }
    return fastPairCostIntClient(groupsUp, lxs2);
  };
  const rowCostDown = (r, pm2) => {
    const groupsDn = [], lxs2 = [];
    let cur2 = 0;
    for (const unit of rowUnits[r]) {
      for (const id of unit) {
        lxs2.push(cur2);
        groupsDn.push((childrenOf[id] || []).filter(c => c in pm2).map(c => pm2[c]));
        cur2 += 2;
      }
      cur2 += 1;
    }
    return fastPairCostIntClient(groupsDn, lxs2);
  };
  const rowCost = (r, pm2) => rowCostUp(r, pm2) + rowCostDown(r, pm2);

  const sweepOrder = [...sortedRanks, ...[...sortedRanks].reverse()];
  for (let round = 0; round < 5; round++) {
    let improved = false;
    for (const r of sweepOrder) {
      const units = rowUnits[r];
      const k = units.length;
      if (!k) continue;

      if (hasIntraRow[r]) {
        const snapPm = { ...pos2 };
        let best = rowCost(r, snapPm);
        for (let i = 0; i < k; i++) {
          if (units[i].length === 2) {
            units[i] = [units[i][1], units[i][0]];
            const c = rowCost(r, snapPm);
            if (c + 1e-6 < best) { best = c; improved = true; }
            else units[i] = [units[i][1], units[i][0]];
          }
        }
        const maxD = k <= 30 ? k : 14;
        for (let i = 0; i < k; i++) {
          for (let j = i + 1; j < Math.min(k, i + maxD + 1); j++) {
            [units[i], units[j]] = [units[j], units[i]];
            const c = rowCost(r, snapPm);
            if (c + 1e-6 < best) { best = c; improved = true; }
            else [units[i], units[j]] = [units[j], units[i]];
          }
        }
        updateRowPos2(r);
        continue;
      }

      const descs = [];
      const x0_2 = new Int32Array(k);
      let cur2 = 0;
      for (let uid = 0; uid < k; uid++) {
        const u = units[uid];
        x0_2[uid] = cur2;
        const upXs2 = [], dnXs2 = [];
        let sumUd2 = 0;
        for (let d = 0; d < u.length; d++) {
          const m = u[d], d2 = 2 * d;
          for (const p of (parentsOf[m] || [])) if (p in pos2) { upXs2.push(pos2[p]); sumUd2 += pos2[p] - d2; }
          for (const c of (childrenOf[m] || [])) if (c in pos2) { dnXs2.push(pos2[c]); sumUd2 += pos2[c] - d2; }
        }
        upXs2.sort((a, b) => a - b);
        dnXs2.sort((a, b) => a - b);
        descs.push({ uid, upXs2, dnXs2, deg: upXs2.length + dnXs2.length, sumUd2, w: u.length });
        cur2 += 2 * u.length + 1;
      }

      for (let i = 0; i < k; i++) {
        const u = units[i];
        if (u.length === 2) {
          const [a, b] = u;
          const upA = (parentsOf[a] || []).filter(p => p in pos2).map(p => pos2[p]).sort((x, y) => x - y);
          const upB = (parentsOf[b] || []).filter(p => p in pos2).map(p => pos2[p]).sort((x, y) => x - y);
          const dnA = (childrenOf[a] || []).filter(c => c in pos2).map(c => pos2[c]).sort((x, y) => x - y);
          const dnB = (childrenOf[b] || []).filter(c => c in pos2).map(c => pos2[c]).sort((x, y) => x - y);
          const degA = upA.length + dnA.length, degB = upB.length + dnB.length;
          let sumA = 0, sumB = 0;
          for (const v of upA) sumA += v;
          for (const v of dnA) sumA += v;
          for (const v of upB) sumB += v;
          for (const v of dnB) sumB += v;
          const dcross = pairCrossDiffClient(upA, upB) + pairCrossDiffClient(dnA, dnB);
          const dspan2 = 4 * (x0_2[i] + 1) * (degA - degB) - 4 * (sumA - sumB);
          if (dcross * 100 + dspan2 * 0.0125 < -1e-6) {
            units[i] = [b, a];
            descs[i].sumUd2 = sumB + sumA - 2 * degA;
            improved = true;
          }
        }
      }

      const dCache = Array.from({ length: k }, () => new Map());
      const getCdiff = (da, db) => {
        const rowMap = dCache[da.uid];
        let val = rowMap.get(db.uid);
        if (val === undefined) {
          val = pairCrossDiffClient(da.upXs2, db.upXs2) + pairCrossDiffClient(da.dnXs2, db.dnXs2);
          rowMap.set(db.uid, val);
          dCache[db.uid].set(da.uid, -val);
        }
        return val;
      };

      const maxD = k <= 30 ? k : 14;
      for (let i = 0; i < k; i++) {
        let di = descs[i];
        let sumIt = 0;
        for (let j = i + 1; j < Math.min(k, i + maxD + 1); j++) {
          const dj = descs[j];
          if (j > i + 1) sumIt += getCdiff(di, descs[j - 1]);
          let dcross = getCdiff(di, dj) + sumIt;
          const shift2 = 2 * (dj.w - di.w);
          let dspan2 = 0;
          if (shift2 === 0) {
            for (let t = i + 1; t < j; t++) dcross += getCdiff(descs[t], dj);
          } else {
            for (let t = i + 1; t < j; t++) {
              const dt = descs[t];
              dcross += getCdiff(dt, dj);
              dspan2 += shift2 * ((2 * x0_2[t] + shift2) * dt.deg - 2 * dt.sumUd2);
            }
          }
          const xi = x0_2[i], xj = x0_2[j], xiNew = xj + shift2;
          dspan2 += (xiNew - xi) * ((xiNew + xi) * di.deg - 2 * di.sumUd2);
          dspan2 += (xi - xj) * ((xi + xj) * dj.deg - 2 * dj.sumUd2);
          if (dcross * 100 + dspan2 * 0.0125 < -1e-6) {
            [units[i], units[j]] = [units[j], units[i]];
            [descs[i], descs[j]] = [descs[j], descs[i]];
            if (shift2 !== 0) for (let t = i + 1; t <= j; t++) x0_2[t] += shift2;
            di = descs[i];
            sumIt = 0;
            for (let t = i + 1; t < j; t++) sumIt += getCdiff(di, descs[t]);
            improved = true;
          }
        }
      }
      updateRowPos2(r);
    }

    // Coupled 2-layer move
    for (let ir = 1; ir < sortedRanks.length; ir++) {
      const r = sortedRanks[ir], rPrev = sortedRanks[ir - 1];
      const rPrev2 = ir >= 2 ? sortedRanks[ir - 2] : null;
      const sPrevR = !!simpleBetween[`${rPrev}|${r}`];
      const sP2Prev = rPrev2 !== null ? !!simpleBetween[`${rPrev2}|${rPrev}`] : false;
      const buildNodeToPrevU = () => {
        const m = {};
        rowUnits[rPrev].forEach((u, ui) => { for (const x of u) m[x] = ui; });
        return m;
      };
      let nodeToPrevU = buildNodeToPrevU();
      let cachedBefore = null;

      for (let i = 0; i < rowUnits[r].length; i++) {
        const unit = rowUnits[r][i];
        if (unit.length !== 2) continue;
        const [a, b] = unit;
        const uaSet = new Set((parentsOf[a] || []).filter(p => p in nodeToPrevU).map(p => nodeToPrevU[p]));
        const ubSet = new Set((parentsOf[b] || []).filter(p => p in nodeToPrevU).map(p => nodeToPrevU[p]));
        if (uaSet.size !== 1 || ubSet.size !== 1) continue;
        const ua = [...uaSet][0], ub = [...ubSet][0];
        if (ua === ub) continue;

        if (!cachedBefore) {
          const cPU = rowCostUp(rPrev, pos2);
          const cPD = rowCostDown(rPrev, pos2);
          const cRU = sPrevR ? cPD : rowCostUp(r, pos2);
          const cRD = rowCostDown(r, pos2);
          let c2U = 0, c2D = 0;
          if (rPrev2 !== null) {
            c2U = rowCostUp(rPrev2, pos2);
            c2D = sP2Prev ? cPU : rowCostDown(rPrev2, pos2);
          }
          cachedBefore = [cPU, cPD, cRU, cRD, c2U, c2D];
        }
        const costBefore = cachedBefore[0] + cachedBefore[1] + cachedBefore[2] + cachedBefore[3] + cachedBefore[4] + cachedBefore[5];

        let cRD_a = null;
        if (!hasIntraRow[r]) {
          const dnA = (childrenOf[a] || []).filter(c => c in pos2).map(c => pos2[c]).sort((x, y) => x - y);
          const dnB = (childrenOf[b] || []).filter(c => c in pos2).map(c => pos2[c]).sort((x, y) => x - y);
          const sumA = dnA.reduce((s, v) => s + v, 0), sumB = dnB.reduce((s, v) => s + v, 0);
          const dRD = pairCrossDiffClient(dnA, dnB) * 100 + (4 * (pos2[a] + 1) * (dnA.length - dnB.length) - 4 * (sumA - sumB)) * 0.0125;
          cRD_a = cachedBefore[3] + dRD;
        }

        rowUnits[r][i] = [b, a];
        [rowUnits[rPrev][ua], rowUnits[rPrev][ub]] = [rowUnits[rPrev][ub], rowUnits[rPrev][ua]];
        updateRowPos2(r);
        updateRowPos2(rPrev);

        let prev2Changed = false, savedPrev2 = null;
        if (rPrev2 !== null) {
          const scoredP2 = rowUnits[rPrev2].map(u2 => {
            const cs = u2.flatMap(m => (childrenOf[m] || []).filter(c => c in pos2).map(c => pos2[c]));
            const bary = cs.length ? cs.reduce((s, x) => s + x, 0) / cs.length
                                   : u2.reduce((s, m) => s + pos2[m], 0) / u2.length;
            return { bary, sx: Math.min(...u2.map(serverX)), id: u2[0], unit: u2 };
          });
          scoredP2.sort((x, y) => x.bary - y.bary || x.sx - y.sx || x.id.localeCompare(y.id));
          const newP2 = scoredP2.map(s => s.unit);
          for (let q = 0; q < newP2.length; q++) {
            if (newP2[q] !== rowUnits[rPrev2][q]) { prev2Changed = true; break; }
          }
          if (prev2Changed) {
            savedPrev2 = rowUnits[rPrev2];
            rowUnits[rPrev2] = newP2;
            updateRowPos2(rPrev2);
          }
        }

        const cPU_a = rowCostUp(rPrev, pos2);
        const cPD_a = rowCostDown(rPrev, pos2);
        const cRU_a = sPrevR ? cPD_a : rowCostUp(r, pos2);
        if (cRD_a === null) cRD_a = rowCostDown(r, pos2);
        let c2U_a = 0, c2D_a = 0;
        if (rPrev2 !== null) {
          c2U_a = prev2Changed ? rowCostUp(rPrev2, pos2) : cachedBefore[4];
          c2D_a = sP2Prev ? cPU_a : rowCostDown(rPrev2, pos2);
        }
        const costAfter = cPU_a + cPD_a + cRU_a + cRD_a + c2U_a + c2D_a;

        if (costAfter + 1e-6 < costBefore) {
          improved = true;
          cachedBefore = [cPU_a, cPD_a, cRU_a, cRD_a, c2U_a, c2D_a];
          nodeToPrevU = buildNodeToPrevU();
        } else {
          rowUnits[r][i] = [a, b];
          [rowUnits[rPrev][ua], rowUnits[rPrev][ub]] = [rowUnits[rPrev][ub], rowUnits[rPrev][ua]];
          updateRowPos2(r);
          updateRowPos2(rPrev);
          if (prev2Changed && savedPrev2 !== null) {
            rowUnits[rPrev2] = savedPrev2;
            updateRowPos2(rPrev2);
          }
        }
      }
    }
    if (!improved) break;
  }

  // PAVA X-alignment
  const unitWidth = (u) => u.length * cw + (u.length - 1) * COUPLE_GAP_X;
  const unitCenters = {};
  const nodeX = {};
  const syncRow = (r, centers) => {
    unitCenters[r] = centers;
    rowUnits[r].forEach((unit, ui) => {
      const uw = unitWidth(unit);
      const left = centers[ui] - uw / 2 + cw / 2;
      unit.forEach((id, mi) => { nodeX[id] = left + mi * (cw + COUPLE_GAP_X); });
    });
  };
  for (const r of sortedRanks) {
    const widths = rowUnits[r].map(unitWidth);
    const totalW = widths.reduce((a, b) => a + b, 0) + Math.max(0, widths.length - 1) * UNIT_GAP_X;
    let cur = -totalW / 2;
    const centers = widths.map(w => { const c = cur + w / 2; cur += w + UNIT_GAP_X; return c; });
    syncRow(r, centers);
  }
  for (let pass = 0; pass < 8; pass++) {
    for (const r of sortedRanks) {
      const units = rowUnits[r];
      const widths = units.map(unitWidth);
      const targets = units.map((unit, ui) => {
        const pXs = unit.flatMap(m => parentsOf[m].filter(p => p in nodeX).map(p => nodeX[p]));
        const cXs = unit.flatMap(m => childrenOf[m].filter(c => c in nodeX).map(c => nodeX[c]));
        if (pXs.length && cXs.length) return 0.55 * (pXs.reduce((a, b) => a + b, 0) / pXs.length) + 0.45 * (cXs.reduce((a, b) => a + b, 0) / cXs.length);
        if (pXs.length) return pXs.reduce((a, b) => a + b, 0) / pXs.length;
        if (cXs.length) return cXs.reduce((a, b) => a + b, 0) / cXs.length;
        return unitCenters[r][ui];
      });
      syncRow(r, pavaPlaceRow(widths, targets, UNIT_GAP_X));
    }
    for (let i = sortedRanks.length - 1; i >= 0; i--) {
      const r = sortedRanks[i];
      const units = rowUnits[r];
      const widths = units.map(unitWidth);
      const targets = units.map((unit, ui) => {
        const pXs = unit.flatMap(m => parentsOf[m].filter(p => p in nodeX).map(p => nodeX[p]));
        const cXs = unit.flatMap(m => childrenOf[m].filter(c => c in nodeX).map(c => nodeX[c]));
        if (pXs.length && cXs.length) return 0.45 * (pXs.reduce((a, b) => a + b, 0) / pXs.length) + 0.55 * (cXs.reduce((a, b) => a + b, 0) / cXs.length);
        if (cXs.length) return cXs.reduce((a, b) => a + b, 0) / cXs.length;
        if (pXs.length) return pXs.reduce((a, b) => a + b, 0) / pXs.length;
        return unitCenters[r][ui];
      });
      syncRow(r, pavaPlaceRow(widths, targets, UNIT_GAP_X));
    }
  }

  let minLeft = Infinity;
  for (const id of visIds) minLeft = Math.min(minLeft, nodeX[id] - cw / 2);
  const shiftX = MARGIN_X - minLeft;
  let maxRight = 0;

  const nodesPos = {};
  const generations = [];
  const focalId = state.data.meta?.focal_person_id;
  const focalRank = (focalId && focalId in rank)
    ? rank[focalId]
    : (sortedRanks.length ? sortedRanks[sortedRanks.length - 1] : null);
  sortedRanks.forEach((r, rowIdx) => {
    const y = Math.round((MARGIN_Y + rowIdx * LAYER_GAP_Y) * 10) / 10;
    const label = focalRank !== null ? generationLabelClient(focalRank - r) : `Gen ${rowIdx + 1}`;
    generations.push({ generation: r, y, label });
    for (const unit of rowUnits[r]) {
      for (const id of unit) {
        const x = Math.round((nodeX[id] + shiftX) * 10) / 10;
        maxRight = Math.max(maxRight, x + cw / 2);
        nodesPos[id] = { x, y, generation: r };
      }
    }
  });

  // Rebuild visible hubs with inline placement & bus_y lanes
  const hubs = [];
  const hubById = new Map();
  const placedBelow = [];
  for (const sh of (serverLayout.hubs || [])) {
    const hKey = hubKeyOf(sh);
    const visParents = sh.parents.filter(p => visible.has(p)).sort((a, b) => nodesPos[a].x - nodesPos[b].x);
    const visChildren = sh.children.filter(c => visible.has(c) && !state.collapsedAncestors.has(c)).sort((a, b) => nodesPos[a].x - nodesPos[b].x);
    if (!visParents.length || (!sh.marriage && !visChildren.length && !state.collapsedCollaterals.has(hKey))) continue;
    const xs = visParents.map(p => nodesPos[p].x);
    const ys = visParents.map(p => nodesPos[p].y);
    const isInline = visParents.length === 2 && Math.abs(ys[0] - ys[1]) < 1 && Math.abs(xs[1] - xs[0]) <= cw + COUPLE_GAP_X + 8;
    let hx, hy;
    if (isInline) {
      hx = Math.round(((xs[0] + xs[1]) / 2) * 10) / 10;
      hy = ys[0];
    } else if (visParents.length === 1) {
      hx = xs[0];
      hy = Math.round((ys[0] + ch / 2 + 18) * 10) / 10;
    } else {
      hx = Math.round((xs.reduce((a, b) => a + b, 0) / xs.length) * 10) / 10;
      hy = Math.round((Math.max(...ys) + ch / 2 + 24) * 10) / 10;
      while (placedBelow.some(([px, py]) => Math.abs(hx - px) < 42 && Math.abs(hy - py) < 20)) hy += 18;
      placedBelow.push([hx, hy]);
    }
    const hObj = { ...sh, x: hx, y: hy, inline: isInline, bus_y: null, parents: visParents, children: visChildren };
    hubs.push(hObj);
    hubById.set(hObj.id, hObj);
  }

  // Assign non-overlapping bus_y per corridor
  const corrMap = {};
  for (const h of hubs) {
    if (!h.children.length) continue;
    const minGen = Math.min(...h.children.map(c => nodesPos[c].generation));
    (corrMap[minGen] ||= []).push(h);
  }
  for (const [cgStr, chList] of Object.entries(corrMap)) {
    const cg = Number(cgStr);
    const childY = Math.min(...chList.flatMap(h => h.children.filter(c => nodesPos[c].generation === cg).map(c => nodesPos[c].y)));
    const corrTop = childY - LAYER_GAP_Y + ch / 2 + 44;
    const corrBot = childY - ch / 2 - 22;
    const corrMid = (corrTop + corrBot) / 2;
    const spans = chList.map(h => {
      const xs = [h.x, ...h.children.map(c => nodesPos[c].x)];
      return { minX: Math.min(...xs), maxX: Math.max(...xs), h };
    }).sort((a, b) => a.minX - b.minX || a.maxX - b.maxX);
    const laneEnds = [];
    const assigned = [];
    for (const sp of spans) {
      let li = laneEnds.findIndex(endX => sp.minX > endX + 12);
      if (li === -1) { li = laneEnds.length; laneEnds.push(sp.maxX); }
      else laneEnds[li] = sp.maxX;
      assigned.push([sp.h, li]);
    }
    const numLanes = Math.max(1, laneEnds.length);
    const step = Math.min(14, Math.max(6, (corrBot - corrTop) / numLanes));
    for (const [h, li] of assigned) {
      const by = corrMid + (li - (numLanes - 1) / 2) * step;
      h.bus_y = Math.round(Math.max(corrTop, Math.min(corrBot, by)) * 10) / 10;
    }
  }

  // Compute clear vertical gap X for multi-generation edges in client layout
  const rowIntervals = {};
  for (const pos of Object.values(nodesPos)) {
    (rowIntervals[pos.generation] ||= []).push([pos.x - cw / 2 - 10, pos.x + cw / 2 + 10]);
  }
  const clearCandsCache = new Map();
  const findClearRouteX = (gStart, gEnd, prefX) => {
    if (gEnd - gStart <= 1) return null;
    const cKey = `${gStart}|${gEnd}`;
    let cands = clearCandsCache.get(cKey);
    if (!cands) {
      const blocked = [];
      for (let g = gStart + 1; g < gEnd; g++) {
        const ivs = rowIntervals[g];
        if (ivs) for (const iv of ivs) blocked.push(iv);
      }
      if (!blocked.length) {
        clearCandsCache.set(cKey, []);
        return null;
      }
      blocked.sort((a, b) => a[0] - b[0]);
      const merged = [blocked[0].slice()];
      for (let i = 1; i < blocked.length; i++) {
        const prev = merged[merged.length - 1];
        if (blocked[i][0] <= prev[1]) prev[1] = Math.max(prev[1], blocked[i][1]);
        else merged.push(blocked[i].slice());
      }
      cands = [merged[0][0] - 18];
      for (let i = 0; i < merged.length - 1; i++) {
        if (merged[i + 1][0] - merged[i][1] >= 12) cands.push((merged[i][1] + merged[i + 1][0]) / 2);
      }
      cands.push(merged[merged.length - 1][1] + 18);
      clearCandsCache.set(cKey, cands);
    }
    if (!cands.length) return null;
    let lo = 0, hi = cands.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cands[mid] < prefX) lo = mid + 1;
      else hi = mid;
    }
    let best;
    if (lo === 0) best = cands[0];
    else if (lo === cands.length) best = cands[cands.length - 1];
    else {
      const left = cands[lo - 1], right = cands[lo];
      best = Math.abs(left - prefX) <= Math.abs(right - prefX) ? left : right;
    }
    return Math.round(best * 10) / 10;
  };

  // Filter edges to visible hubs/nodes (and respect collapsed upward edges)
  const edges = (serverLayout.edges || []).filter(e => {
    if (e.kind === "spouse_to_hub") return visible.has(e.from) && hubById.has(e.to);
    if (e.kind === "hub_to_child") return hubById.has(e.from) && visible.has(e.to) && !state.collapsedAncestors.has(e.to);
    return visible.has(e.from) && visible.has(e.to);
  }).map(e => {
    if (e.kind !== "hub_to_child") return e;
    const h = hubById.get(e.from);
    const cPos = nodesPos[e.to];
    if (!h || !cPos || !h.parents.length) return e;
    const parentGen = Math.max(...h.parents.map(p => nodesPos[p].generation));
    const rx = findClearRouteX(parentGen, cPos.generation, (h.x + cPos.x) / 2);
    if (rx === null) {
      const copy = { ...e };
      delete copy.route_x;
      delete copy.bot_bus_y;
      return copy;
    }
    return { ...e, route_x: rx, bot_bus_y: Math.round((cPos.y - ch / 2 - 24) * 10) / 10 };
  });

  const computedLayout = {
    nodes: nodesPos,
    hubs,
    edges,
    generations,
    width: Math.round(Math.max(960, maxRight + MARGIN_X) * 10) / 10,
    height: Math.round(Math.max(560, MARGIN_Y + Math.max(0, sortedRanks.length - 1) * LAYER_GAP_Y + 120) * 10) / 10,
    card_w: cw,
    card_h: ch,
  };
  _clientLayoutCache = { data: state.data, key: cacheKey, layout: computedLayout };
  return computedLayout;
}

function roundedOrthogonalPath(x1, y1, x2, y2, busY) {
  if (Math.abs(x1 - x2) < 2) {
    return `M ${x1} ${y1} L ${x2} ${y2}`;
  }
  const midY = (typeof busY === "number" && busY > y1 + 6 && busY < y2 - 6)
    ? busY
    : (y1 + y2) / 2;
  const r = Math.min(9, Math.abs(x2 - x1) / 2, Math.abs(midY - y1) / 2, Math.abs(y2 - midY) / 2);
  if (r < 2) {
    return `M ${x1} ${y1} L ${x1} ${midY} L ${x2} ${midY} L ${x2} ${y2}`;
  }
  const dir = x2 > x1 ? 1 : -1;
  return [
    `M ${x1} ${y1}`,
    `L ${x1} ${midY - r}`,
    `Q ${x1} ${midY}, ${x1 + dir * r} ${midY}`,
    `L ${x2 - dir * r} ${midY}`,
    `Q ${x2} ${midY}, ${x2} ${midY + r}`,
    `L ${x2} ${y2}`,
  ].join(" ");
}

// Smooth hover-focus spotlight & per-row parent alignment animation
let _hoverLeaveTimer = null;
let _hoverAlignTimer = null;
let _rowAnimFrame = null;
const HOVER_ALIGN_DELAY_MS = 1800;
const _renderedRefs = {
  nodes: new Map(),
  hubs: new Map(),
  edges: [],
  visibleHubs: new Map(),
  genLabels: [],
  hubsByNode: new Map(),
  edgesByNode: new Map(),
  edgesByHub: new Map(),
  activeHover: [],
};

function getRowOffset(gen) {
  return state.rowOffsets[gen] || 0;
}

function getVisualNodePos(layout, iid) {
  const p = layout.nodes[iid];
  if (!p) return null;
  return { x: p.x + getRowOffset(p.generation), y: p.y, generation: p.generation };
}

function getVisualHubPos(layout, h) {
  const ps = (h.parents || []).filter(p => layout.nodes[p]);
  if (!ps.length) return { x: h.x, y: h.y };
  const gens = ps.map(p => layout.nodes[p].generation);
  if (gens.every(g => g === gens[0])) {
    return { x: h.x + getRowOffset(gens[0]), y: h.y };
  }
  const avgX = ps.reduce((sum, p) => sum + getVisualNodePos(layout, p).x, 0) / ps.length;
  return { x: avgX, y: h.y };
}

function roundedMultiGenPath(x1, y1, x2, y2, topBusY, routeX, botBusY) {
  const seg1 = (ax, ay, bx, by, cy) => {
    if (Math.abs(ax - bx) < 2) return [`L ${bx} ${by}`];
    const r = Math.min(8, Math.abs(bx - ax) / 2, Math.abs(cy - ay) / 2, Math.abs(by - cy) / 2);
    const dir = bx > ax ? 1 : -1;
    if (r < 2) return [`L ${ax} ${cy}`, `L ${bx} ${cy}`, `L ${bx} ${by}`];
    return [
      `L ${ax} ${cy - r}`,
      `Q ${ax} ${cy}, ${ax + dir * r} ${cy}`,
      `L ${bx - dir * r} ${cy}`,
      `Q ${bx} ${cy}, ${bx} ${cy + r}`,
      `L ${bx} ${by}`,
    ];
  };
  const midY = (topBusY + botBusY) / 2;
  return [
    `M ${x1} ${y1}`,
    ...seg1(x1, y1, routeX, midY, topBusY),
    ...seg1(routeX, midY, x2, y2, botBusY),
  ].join(" ");
}

function computeEdgePath(layout, edge, visibleHubs, hubPosMap = null) {
  const cw = layout.card_w;
  const ch = layout.card_h;
  if (edge.kind === "spouse_to_hub") {
    const p = getVisualNodePos(layout, edge.from);
    const h = visibleHubs.get(edge.to);
    if (!p || !h) return null;
    const hp = (hubPosMap && hubPosMap.get(h.id)) || getVisualHubPos(layout, h);
    if (h.inline) {
      const x1 = p.x < hp.x ? p.x + cw / 2 : p.x - cw / 2;
      return `M ${x1} ${p.y} L ${hp.x} ${hp.y}`;
    }
    const x1 = p.x, y1 = p.y + ch / 2;
    const midY = (y1 + hp.y) / 2;
    return `M ${x1} ${y1} C ${x1} ${midY}, ${hp.x} ${midY}, ${hp.x} ${hp.y}`;
  }
  if (edge.kind === "hub_to_child") {
    const h = visibleHubs.get(edge.from);
    const c = getVisualNodePos(layout, edge.to);
    if (!h || !c) return null;
    const hp = (hubPosMap && hubPosMap.get(h.id)) || getVisualHubPos(layout, h);
    if (typeof edge.route_x === "number" && typeof edge.bot_bus_y === "number") {
      const parentGen = h.parents?.length ? Math.max(...h.parents.map(p => layout.nodes[p]?.generation ?? 0)) : 0;
      const topBusY = typeof h.bus_y === "number" ? h.bus_y : hp.y + ch / 2 + 44;
      const routeX = edge.route_x + getRowOffset(parentGen + 1);
      if (topBusY > hp.y + 6 && edge.bot_bus_y > topBusY + 12 && c.y - ch / 2 > edge.bot_bus_y + 6) {
        return roundedMultiGenPath(hp.x, hp.y, c.x, c.y - ch / 2, topBusY, routeX, edge.bot_bus_y);
      }
    }
    return roundedOrthogonalPath(hp.x, hp.y, c.x, c.y - ch / 2, h.bus_y);
  }
  const a = getVisualNodePos(layout, edge.from);
  const b = getVisualNodePos(layout, edge.to);
  if (!a || !b) return null;
  const mx = (a.x + b.x) / 2;
  const my = Math.min(a.y, b.y) - 44;
  return `M ${a.x} ${a.y} Q ${mx} ${my}, ${b.x} ${b.y}`;
}

function applyRowOffsetsToDom() {
  const layout = state.activeLayout || state.data.layout;
  if (!layout) return;
  const cw = layout.card_w;
  const ch = layout.card_h;
  for (const [iid, elNode] of _renderedRefs.nodes.entries()) {
    const vp = getVisualNodePos(layout, iid);
    if (vp) elNode.setAttribute("transform", `translate(${vp.x - cw / 2}, ${vp.y - ch / 2})`);
  }
  const hubPosMap = new Map();
  for (const [hid, elHub] of _renderedRefs.hubs.entries()) {
    const h = _renderedRefs.visibleHubs.get(hid);
    if (!h) continue;
    const hp = getVisualHubPos(layout, h);
    hubPosMap.set(hid, hp);
    elHub.setAttribute("transform", `translate(${hp.x},${hp.y})`);
  }
  for (const { edge, el: elEdge } of _renderedRefs.edges) {
    const dStr = computeEdgePath(layout, edge, _renderedRefs.visibleHubs, hubPosMap);
    if (dStr) elEdge.setAttribute("d", dStr);
  }
}

function cancelRowOffsetAnimation() {
  if (_rowAnimFrame !== null) {
    cancelAnimationFrame(_rowAnimFrame);
    _rowAnimFrame = null;
  }
}

function cancelHoverAlignTimer() {
  if (_hoverAlignTimer !== null) {
    clearTimeout(_hoverAlignTimer);
    _hoverAlignTimer = null;
  }
}

function animateRowOffsetsTo(targetOffsets, durationMs = 420) {
  cancelRowOffsetAnimation();
  const keys = new Set([...Object.keys(state.rowOffsets), ...Object.keys(targetOffsets)].map(Number));
  let maxDelta = 0;
  for (const g of keys) {
    maxDelta = Math.max(maxDelta, Math.abs((targetOffsets[g] || 0) - (state.rowOffsets[g] || 0)));
  }
  if (maxDelta < 1) return;

  const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || durationMs <= 0) {
    state.rowOffsets = { ...targetOffsets };
    applyRowOffsetsToDom();
    return;
  }

  const startOffsets = {};
  for (const g of keys) startOffsets[g] = state.rowOffsets[g] || 0;
  const t0 = performance.now();

  const step = (now) => {
    const raw = Math.min(1, (now - t0) / durationMs);
    // Cubic ease-in-out
    const ease = raw < 0.5 ? 4 * raw * raw * raw : 1 - Math.pow(-2 * raw + 2, 3) / 2;
    for (const g of keys) {
      const s = startOffsets[g] || 0;
      const e = targetOffsets[g] || 0;
      state.rowOffsets[g] = s + (e - s) * ease;
    }
    applyRowOffsetsToDom();
    if (raw < 1) {
      _rowAnimFrame = requestAnimationFrame(step);
    } else {
      state.rowOffsets = { ...targetOffsets };
      applyRowOffsetsToDom();
      _rowAnimFrame = null;
    }
  };
  _rowAnimFrame = requestAnimationFrame(step);
}

function computeOffsetsFromAlignments(layout, visibleHubs) {
  const offsets = {};
  if (!layout || !Object.keys(state.rowAlignments).length) return offsets;
  const idx = getFamilyIndex();

  // Map child -> visible hub (if any)
  const childToHub = new Map();
  for (const h of visibleHubs.values()) {
    for (const c of (h.children || [])) childToHub.set(c, h);
  }

  // Collect active alignments and replay bottom-up (highest active generation -> 0)
  const entries = [];
  for (const childId of Object.values(state.rowAlignments)) {
    const pos = layout.nodes?.[childId];
    if (!pos) continue;
    if (state.collapsedAncestors.has(childId)) continue;
    const visParents = (idx.parentsOf[childId] || []).filter(p => layout.nodes?.[p]);
    if (!visParents.length) continue;
    entries.push({ gen: pos.generation, childId, visParents });
  }
  entries.sort((a, b) => b.gen - a.gen);

  const hubXWithOffsets = (h) => {
    const ps = (h.parents || []).filter(p => layout.nodes?.[p]);
    if (!ps.length) return h.x;
    const gens = ps.map(p => layout.nodes[p].generation);
    if (gens.every(g => g === gens[0])) return h.x + (offsets[gens[0]] || 0);
    return ps.reduce((s, p) => s + layout.nodes[p].x + (offsets[layout.nodes[p].generation] || 0), 0) / ps.length;
  };

  for (const { gen, childId, visParents } of entries) {
    const childVisualX = layout.nodes[childId].x + (offsets[gen] || 0);
    const hub = childToHub.get(childId);
    const parentVisualX = hub
      ? hubXWithOffsets(hub)
      : visParents.reduce((s, p) => s + layout.nodes[p].x + (offsets[layout.nodes[p].generation] || 0), 0) / visParents.length;
    const dx = childVisualX - parentVisualX;
    if (Math.abs(dx) < 0.5) continue;
    for (const gMeta of (layout.generations || [])) {
      if (gMeta.generation < gen) {
        offsets[gMeta.generation] = (offsets[gMeta.generation] || 0) + dx;
      }
    }
  }
  return offsets;
}

function alignParentsOverNode(iid) {
  const layout = state.activeLayout || state.data.layout;
  if (!layout || !layout.nodes[iid]) return;
  const idx = getFamilyIndex();
  const visParents = (idx.parentsOf[iid] || []).filter(p => layout.nodes[p] && !state.collapsedAncestors.has(iid));
  if (!visParents.length) return;

  // Record this generation's alignment using canonical server generation (invariant across collapses)
  const serverGen = state.data.layout?.nodes?.[iid]?.generation ?? layout.nodes[iid].generation;
  state.rowAlignments[serverGen] = iid;

  const targetOffsets = computeOffsetsFromAlignments(layout, _renderedRefs.visibleHubs);
  animateRowOffsetsTo(targetOffsets, 420);
  scheduleSaveViewerState();
}

function _resetActiveHover() {
  for (const elem of _renderedRefs.activeHover) elem.classList.remove("hover-focus");
  _renderedRefs.activeHover.length = 0;
}

function clearHoverFocusImmediate() {
  cancelHoverAlignTimer();
  const svg = document.getElementById("graph");
  if (!svg) return;
  svg.classList.remove("hover-focus-active");
  _resetActiveHover();
}

function scheduleClearHoverFocus() {
  cancelHoverAlignTimer();
  clearTimeout(_hoverLeaveTimer);
  _hoverLeaveTimer = setTimeout(clearHoverFocusImmediate, 65);
}

function applyHoverFocusForNode(iid, scheduleAlign = false) {
  clearTimeout(_hoverLeaveTimer);
  cancelHoverAlignTimer();
  _resetActiveHover();
  const idx = getFamilyIndex();
  const focusNodes = new Set([
    iid,
    ...(idx.parentsOf[iid] || []),
    ...(idx.childrenOf[iid] || []),
    ...(idx.partnersOf[iid] || []),
  ]);
  const focusHubs = _renderedRefs.hubsByNode.get(iid) || new Set();
  const svg = document.getElementById("graph");
  svg.classList.add("hover-focus-active");
  for (const id of focusNodes) {
    const elNode = _renderedRefs.nodes.get(id);
    if (elNode) {
      elNode.classList.add("hover-focus");
      _renderedRefs.activeHover.push(elNode);
    }
  }
  for (const hid of focusHubs) {
    const elHub = _renderedRefs.hubs.get(hid);
    if (elHub) {
      elHub.classList.add("hover-focus");
      _renderedRefs.activeHover.push(elHub);
    }
    for (const item of (_renderedRefs.edgesByHub.get(hid) || [])) {
      const e = item.edge;
      const hit = (e.kind === "spouse_to_hub" && focusNodes.has(e.from))
               || (e.kind === "hub_to_child" && (e.to === iid || (e.parents || []).includes(iid)));
      if (hit) {
        item.el.classList.add("hover-focus");
        _renderedRefs.activeHover.push(item.el);
      }
    }
  }
  for (const item of (_renderedRefs.edgesByNode.get(iid) || [])) {
    if (item.edge.kind !== "spouse_to_hub" && item.edge.kind !== "hub_to_child") {
      item.el.classList.add("hover-focus");
      _renderedRefs.activeHover.push(item.el);
    }
  }
  if (scheduleAlign) {
    _hoverAlignTimer = setTimeout(() => {
      _hoverAlignTimer = null;
      alignParentsOverNode(iid);
    }, HOVER_ALIGN_DELAY_MS);
  }
}

function applyHoverFocusForHub(hub) {
  clearTimeout(_hoverLeaveTimer);
  cancelHoverAlignTimer();
  _resetActiveHover();
  const focusNodes = new Set([...hub.parents, ...hub.children]);
  const svg = document.getElementById("graph");
  svg.classList.add("hover-focus-active");
  for (const id of focusNodes) {
    const elNode = _renderedRefs.nodes.get(id);
    if (elNode) {
      elNode.classList.add("hover-focus");
      _renderedRefs.activeHover.push(elNode);
    }
  }
  const elHub = _renderedRefs.hubs.get(hub.id);
  if (elHub) {
    elHub.classList.add("hover-focus");
    _renderedRefs.activeHover.push(elHub);
  }
  for (const item of (_renderedRefs.edgesByHub.get(hub.id) || [])) {
    item.el.classList.add("hover-focus");
    _renderedRefs.activeHover.push(item.el);
  }
}

function toggleBranchCollapse(iid) {
  cancelRowOffsetAnimation();
  cancelHoverAlignTimer();
  const prevLayout = state.activeLayout || state.data.layout;
  const prevPos = prevLayout ? getVisualNodePos(prevLayout, iid) : null;
  const oldScreenX = prevPos ? prevPos.x * state.scale + state.tx : null;
  const oldScreenY = prevPos ? prevPos.y * state.scale + state.ty : null;

  if (state.collapsedAncestors.has(iid)) state.collapsedAncestors.delete(iid);
  else state.collapsedAncestors.add(iid);

  renderGraph({ recomputeAlignments: true });

  // Keep the clicked card anchored at its exact screen position so the tree doesn't jump
  const nextLayout = state.activeLayout || state.data.layout;
  const nextPos = nextLayout ? getVisualNodePos(nextLayout, iid) : null;
  if (oldScreenX !== null && nextPos) {
    state.tx = oldScreenX - nextPos.x * state.scale;
    state.ty = oldScreenY - nextPos.y * state.scale;
    applyTransform();
  }
  scheduleSaveViewerState();
}

function toggleHubCollaterals(hubKey, hubId) {
  cancelRowOffsetAnimation();
  cancelHoverAlignTimer();
  const prevLayout = state.activeLayout || state.data.layout;
  const prevHub = _renderedRefs.visibleHubs.get(hubId);
  const prevPos = prevLayout && prevHub ? getVisualHubPos(prevLayout, prevHub) : null;
  const oldScreenX = prevPos ? prevPos.x * state.scale + state.tx : null;
  const oldScreenY = prevPos ? prevPos.y * state.scale + state.ty : null;

  if (state.collapsedCollaterals.has(hubKey)) state.collapsedCollaterals.delete(hubKey);
  else state.collapsedCollaterals.add(hubKey);

  renderGraph({ recomputeAlignments: true });

  // Keep the clicked marriage hub pinned to its exact screen position
  const nextHub = _renderedRefs.visibleHubs.get(hubId);
  const nextLayout = state.activeLayout || state.data.layout;
  const nextPos = nextLayout && nextHub ? getVisualHubPos(nextLayout, nextHub) : null;
  if (oldScreenX !== null && nextPos) {
    state.tx = oldScreenX - nextPos.x * state.scale;
    state.ty = oldScreenY - nextPos.y * state.scale;
    applyTransform();
  }
  scheduleSaveViewerState();
}

function collapseAllCollaterals() {
  cancelRowOffsetAnimation();
  cancelHoverAlignTimer();
  const baseVisible = getBaseVisibleIds();
  const directSet = getDirectAncestors(baseVisible);
  for (const sh of (state.data.layout?.hubs || [])) {
    const hasCollateral = (sh.children || []).some(c => baseVisible.has(c) && !directSet.has(c));
    if (hasCollateral) state.collapsedCollaterals.add(hubKeyOf(sh));
  }
  renderGraph({ recomputeAlignments: true });
  fitToView({ preserveAlignments: true });
  scheduleSaveViewerState();
}

let _searchIndexCache = { data: null, indEntries: null, srcEntries: null };

function getSearchIndex() {
  if (_searchIndexCache.data === state.data && _searchIndexCache.indEntries) return _searchIndexCache;
  const indEntries = [];
  for (const [id, ind] of Object.entries(state.data.individuals || {})) {
    const parts = [
      id,
      ind.primary_name || "",
      ...(ind.name_variants || []),
      ind.birth?.place || "",
      ind.death?.place || "",
    ];
    for (const e of (ind.events || [])) {
      parts.push(e.place || "", e.tract || "", e.description || "", e.suggested_label || "");
    }
    parts.push(ind.body_markdown || "");
    indEntries.push([id, parts.join(" ").toLowerCase()]);
  }
  const srcEntries = [];
  for (const src of Object.values(state.data.sources || {})) {
    const cited = src.individuals_cited || [];
    if (!cited.length) continue;
    const hay = `${src.id} ${src.title} ${src.jurisdiction || ""} ${src.body_markdown || ""}`.toLowerCase();
    srcEntries.push([hay, cited]);
  }
  _searchIndexCache = { data: state.data, indEntries, srcEntries };
  return _searchIndexCache;
}

function getSearchMatches() {
  const q = state.searchQuery.trim().toLowerCase();
  if (!q) return null;
  const { indEntries, srcEntries } = getSearchIndex();
  const matches = new Set();
  for (const [id, hay] of indEntries) {
    if (hay.includes(q)) matches.add(id);
  }
  for (const [hay, cited] of srcEntries) {
    if (hay.includes(q)) {
      for (const iid of cited) matches.add(iid);
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

function getPlaceMatchSet() {
  if (!state.selectedPlace) return null;
  const pl = state.placeProfile && state.placeProfile.id === state.selectedPlace
    ? state.placeProfile
    : (state.data.places || []).find(p => p.id === state.selectedPlace);
  return pl ? new Set((pl.individuals || []).map(i => i.id)) : new Set();
}

function renderGraph({ recomputeAlignments = false } = {}) {
  const data = state.data;
  const idx = getFamilyIndex();
  const baseVisible = getBaseVisibleIds();
  const directAncestors = getDirectAncestors(baseVisible);
  const visible = getVisibleIds(baseVisible, directAncestors);
  const layout = computeClientLayout(visible);
  state.activeLayout = layout;

  const searchMatches = getSearchMatches();
  const tractMatches = getTractMatchSet();
  const placeMatches = getPlaceMatchSet();
  const eraActive = state.eraEnabled;
  const eraYear = state.eraYear;
  const { pathNodes, pathPairs, multiPathBadges } = getHighlightedSets();
  const hasHighlight = pathNodes.size > 0;

  clearHoverFocusImmediate();
  _renderedRefs.nodes.clear();
  _renderedRefs.hubs.clear();
  _renderedRefs.edges = [];
  _renderedRefs.genLabels = [];
  _renderedRefs.hubsByNode.clear();
  _renderedRefs.edgesByNode.clear();
  _renderedRefs.edgesByHub.clear();
  _renderedRefs.activeHover = [];

  const layerBands = document.getElementById("layer-bands");
  const layerGenLabels = document.getElementById("layer-gen-labels");
  const layerEdges = document.getElementById("layer-edges");
  const layerHubs = document.getElementById("layer-hubs");
  const layerNodes = document.getElementById("layer-nodes");
  layerBands.replaceChildren();
  if (layerGenLabels) layerGenLabels.replaceChildren();
  layerEdges.replaceChildren();
  layerHubs.replaceChildren();
  layerNodes.replaceChildren();

  const fragBands = document.createDocumentFragment();
  const fragGenLabels = document.createDocumentFragment();
  const fragEdges = document.createDocumentFragment();
  const fragHubs = document.createDocumentFragment();
  const fragNodes = document.createDocumentFragment();

  const cw = layout.card_w;
  const ch = layout.card_h;

  // Subtle generation row guide lines (pan/zoom with graph) + sticky left-edge generation label pills
  for (const gMeta of (layout.generations || [])) {
    fragBands.appendChild(svgEl("line", {
      class: "gen-band-line",
      x1: -2000,
      y1: gMeta.y,
      x2: Math.max(layout.width + 2000, 4000),
      y2: gMeta.y,
    }));
    if (layerGenLabels) {
      const pillW = Math.max(66, gMeta.label.length * 6.5 + 16);
      const pillH = 22;
      const gLbl = svgEl("g", { class: "gen-band-pill", "data-gen-y": String(gMeta.y) });
      gLbl.appendChild(svgEl("rect", { x: 0, y: -pillH / 2, width: pillW, height: pillH }));
      const lbl = svgEl("text", { class: "gen-band-label", x: 8, y: 4 });
      lbl.textContent = gMeta.label;
      gLbl.appendChild(lbl);
      fragGenLabels.appendChild(gLbl);
      _renderedRefs.genLabels.push({ y: gMeta.y, el: gLbl });
    }
  }
  layerBands.appendChild(fragBands);
  if (layerGenLabels) layerGenLabels.appendChild(fragGenLabels);
  updateStickyGenLabels();

  // Map hubKey -> full baseVisible children list from server layout (so collapsed hubs still know their collateral count)
  const serverHubChildren = new Map();
  for (const sh of (data.layout?.hubs || [])) {
    serverHubChildren.set(hubKeyOf(sh), (sh.children || []).filter(c => baseVisible.has(c)));
  }

  const visibleHubs = new Map();
  for (const h of layout.hubs) {
    const anyParent = h.parents.some(p => visible.has(p));
    const anyChild = h.children.some(c => visible.has(c));
    if (anyParent && (h.marriage || anyChild || state.collapsedCollaterals.has(hubKeyOf(h)))) {
      visibleHubs.set(h.id, h);
    }
  }
  _renderedRefs.visibleHubs = visibleHubs;
  if (recomputeAlignments) {
    state.rowOffsets = computeOffsetsFromAlignments(layout, visibleHubs);
  }

  const hubPosMap = new Map();
  for (const h of visibleHubs.values()) {
    hubPosMap.set(h.id, getVisualHubPos(layout, h));
    for (const p of h.parents) {
      let s = _renderedRefs.hubsByNode.get(p);
      if (!s) { s = new Set(); _renderedRefs.hubsByNode.set(p, s); }
      s.add(h.id);
    }
    for (const c of h.children) {
      let s = _renderedRefs.hubsByNode.get(c);
      if (!s) { s = new Set(); _renderedRefs.hubsByNode.set(c, s); }
      s.add(h.id);
    }
  }

  // Draw edges
  for (const edge of layout.edges) {
    if (edge.kind === "fan_associate" && !state.showFan) continue;
    let isHighlighted = false;

    if (edge.kind === "spouse_to_hub") {
      if (!visible.has(edge.from) || !visibleHubs.has(edge.to)) continue;
      const h = visibleHubs.get(edge.to);
      for (const c of h.children) {
        if (pathPairs.has(`${edge.from}|${c}`)) { isHighlighted = true; break; }
      }
    } else if (edge.kind === "hub_to_child") {
      if (!visibleHubs.has(edge.from) || !visible.has(edge.to)) continue;
      for (const p of (edge.parents || [])) {
        if (pathPairs.has(`${p}|${edge.to}`)) { isHighlighted = true; break; }
      }
    } else {
      if (!visible.has(edge.from) || !visible.has(edge.to)) continue;
    }

    const dStr = computeEdgePath(layout, edge, visibleHubs, hubPosMap);
    if (!dStr) continue;

    const cls = ["edge-path", edge.confidence || ""];
    if (edge.kind === "fan_associate") cls.push("fan");
    if (state.showConsang && edge.consanguineous) cls.push("consang");
    if (isHighlighted) cls.push("highlighted");
    else if (hasHighlight) cls.push("dimmed");

    const pathEl = svgEl("path", { d: dStr, class: cls.join(" ") });
    fragEdges.appendChild(pathEl);
    const edgeItem = { edge, el: pathEl };
    _renderedRefs.edges.push(edgeItem);
    if (edge.kind === "spouse_to_hub") {
      let list = _renderedRefs.edgesByHub.get(edge.to);
      if (!list) { list = []; _renderedRefs.edgesByHub.set(edge.to, list); }
      list.push(edgeItem);
    } else if (edge.kind === "hub_to_child") {
      let list = _renderedRefs.edgesByHub.get(edge.from);
      if (!list) { list = []; _renderedRefs.edgesByHub.set(edge.from, list); }
      list.push(edgeItem);
    } else {
      for (const nodeId of [edge.from, edge.to]) {
        let list = _renderedRefs.edgesByNode.get(nodeId);
        if (!list) { list = []; _renderedRefs.edgesByNode.set(nodeId, list); }
        list.push(edgeItem);
      }
    }
  }
  layerEdges.appendChild(fragEdges);

  // Draw Marriage / Family Hubs
  for (const h of visibleHubs.values()) {
    const gCls = ["hub-group"];
    if (state.showConsang && h.consanguineous) gCls.push("consang");
    if (state.selectedHubId === h.id) gCls.push("selected");
    const hp = hubPosMap.get(h.id);
    const g = svgEl("g", { class: gCls.join(" "), transform: `translate(${hp.x},${hp.y})` });
    const s = h.inline ? 6.5 : 8;
    g.appendChild(svgEl("polygon", {
      class: "hub-diamond",
      points: `0,${-s} ${s},0 0,${s} ${-s},0`,
    }));
    const hasLabel = !!(h.marriage?.date || (state.showConsang && h.consanguineous));
    if (hasLabel) {
      const lblText = (state.showConsang && h.consanguineous ? "⚠ " : "") + (h.marriage?.date ? `m. ${h.marriage.date}` : "related");
      const lbl = svgEl("text", { class: "hub-label", y: h.inline ? ch / 2 + 14 : 20 });
      lbl.textContent = lblText;
      g.appendChild(lbl);
    }

    // Collateral children expand/collapse pill on the downward stem below the hub
    const hKey = hubKeyOf(h);
    const allHubChildren = serverHubChildren.get(hKey) || h.children || [];
    const directKids = allHubChildren.filter(c => directAncestors.has(c));
    const collateralKids = allHubChildren.filter(c => !directAncestors.has(c));
    if (collateralKids.length > 0) {
      const isColCollapsed = state.collapsedCollaterals.has(hKey);
      const colCount = collateralKids.length;
      const noun = directKids.length > 0 ? (colCount === 1 ? "sib" : "sibs") : "ch.";
      const pillText = isColCollapsed ? `+${colCount} ${noun}` : `−${colCount} ${noun}`;
      const pillW = Math.max(46, pillText.length * 6.2 + 12);
      const pillH = 17;
      const pillY = h.inline ? (hasLabel ? ch / 2 + 23 : ch / 2 + 8) : (hasLabel ? 28 : 13);
      const parentNames = h.parents.map(p => data.individuals[p]?.primary_name || p).join(" & ");
      const colG = svgEl("g", {
        class: isColCollapsed ? "collateral-toggle is-collapsed" : "collateral-toggle",
        transform: `translate(${-pillW / 2}, ${pillY})`,
        role: "button",
        tabindex: "0",
        "aria-expanded": String(!isColCollapsed),
        "aria-label": isColCollapsed
          ? `Show ${colCount} collateral children of ${parentNames}`
          : `Hide ${colCount} collateral children of ${parentNames}`,
      });
      const tEl = svgEl("title");
      tEl.textContent = isColCollapsed
        ? `Show ${colCount} collateral ${directKids.length > 0 ? "sibling(s) & descendants" : "child(ren) & descendants"} of ${parentNames}`
        : `Hide ${colCount} collateral ${directKids.length > 0 ? "sibling(s) (keep direct line)" : "child(ren)"} of ${parentNames}`;
      colG.appendChild(tEl);
      colG.appendChild(svgEl("rect", { width: pillW, height: pillH }));
      const ct = svgEl("text", { x: pillW / 2, y: 11.8 });
      ct.textContent = pillText;
      colG.appendChild(ct);
      const onColToggle = (ev) => {
        ev.stopPropagation();
        toggleHubCollaterals(hKey, h.id);
      };
      colG.addEventListener("click", onColToggle);
      colG.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onColToggle(ev);
        }
      });
      g.appendChild(colG);
    }

    g.addEventListener("pointerenter", () => applyHoverFocusForHub(h));
    g.addEventListener("pointerleave", scheduleClearHoverFocus);
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectHub(h);
    });
    fragHubs.appendChild(g);
    _renderedRefs.hubs.set(h.id, g);
  }
  layerHubs.appendChild(fragHubs);

  const svgRoot = document.getElementById("graph");
  if (svgRoot) svgRoot.classList.toggle("compact-collateral", !!state.compactCollateral);

  // Draw Individual Cards
  for (const iid of visible) {
    const pos = getVisualNodePos(layout, iid);
    const ind = data.individuals[iid];
    if (!pos || !ind) continue;

    const cls = ["node-group", `status-${ind.status}`];
    if (directAncestors.size && !directAncestors.has(iid)) cls.push("non-direct");
    if (state.selectedId === iid) cls.push("selected");
    if (state.compareId === iid) cls.push("compare-target");
    if (pathNodes.has(iid)) cls.push("in-path");
    if (tractMatches && tractMatches.has(iid)) cls.push("in-tract");
    const aliveNow = eraActive ? isAliveInYear(ind, eraYear) : true;
    if (
      (searchMatches && !searchMatches.has(iid)) ||
      (tractMatches && !tractMatches.has(iid)) ||
      (placeMatches && !placeMatches.has(iid)) ||
      (eraActive && !aliveNow) ||
      (hasHighlight && !pathNodes.has(iid) && state.selectedId !== iid && state.compareId !== iid)
    ) {
      cls.push("dimmed");
    }

    const g = svgEl("g", {
      class: cls.join(" "),
      transform: `translate(${pos.x - cw / 2}, ${pos.y - ch / 2})`,
      tabindex: "0",
      role: "button",
      "aria-label": `${ind.primary_name} (${iid})`,
    });

    const inner = svgEl("g", { class: "node-inner" });
    g.appendChild(inner);

    inner.appendChild(svgEl("rect", { class: "node-card", width: cw, height: ch }));

    // Wrap long names cleanly across up to 2 lines
    const rawName = ind.primary_name || "";
    const maxLine = 25;
    let nameLines;
    if (rawName.length <= maxLine) {
      nameLines = [rawName];
    } else {
      let splitAt = rawName.lastIndexOf(" ", maxLine);
      if (splitAt < 8) splitAt = maxLine;
      const l1 = rawName.slice(0, splitAt).trim();
      const rest = rawName.slice(splitAt).trim();
      const l2 = rest.length > maxLine ? rest.slice(0, maxLine - 1) + "…" : rest;
      nameLines = l2 ? [l1, l2] : [l1];
    }
    const twoLine = nameLines.length > 1;
    const nameEl = svgEl("text", { class: "node-name", x: 12, y: twoLine ? 22 : 27 });
    if (!twoLine) {
      nameEl.textContent = nameLines[0];
    } else {
      const ts1 = svgEl("tspan", { x: 12, dy: 0 });
      ts1.textContent = nameLines[0];
      const ts2 = svgEl("tspan", { x: 12, dy: 14.5 });
      ts2.textContent = nameLines[1];
      nameEl.appendChild(ts1);
      nameEl.appendChild(ts2);
    }
    inner.appendChild(nameEl);

    const b = ind.birth?.date || "?";
    const d = ind.living ? "Living" : (ind.death?.date || "?");
    const datesEl = svgEl("text", { class: "node-dates", x: 12, y: twoLine ? 54 : 48 });
    datesEl.textContent = `${b} – ${d} · ${ind.sex}`;
    inner.appendChild(datesEl);

    const idEl = svgEl("text", { class: "node-id", x: 12, y: twoLine ? 73 : 70 });
    const extra = ind.inbreeding_f > 0 && state.showConsang ? ` · F=${ind.inbreeding_f.toFixed(4)}` : "";
    idEl.textContent = `${iid}${extra}`;
    inner.appendChild(idEl);

    // Ancestry-style Expand / Collapse branch pill at top-center for anyone with parents
    const hasParents = (idx.parentsOf[iid] || []).some(p => baseVisible.has(p));
    if (hasParents) {
      const isCollapsed = state.collapsedAncestors.has(iid);
      const ancCount = isCollapsed ? countAncestorsIn(iid, baseVisible, idx) : 0;
      const pillW = isCollapsed ? 46 : 22;
      const pillH = 18;
      const toggleG = svgEl("g", {
        class: isCollapsed ? "branch-toggle is-collapsed" : "branch-toggle",
        transform: `translate(${(cw - pillW) / 2}, ${-pillH / 2})`,
        role: "button",
        tabindex: "0",
        "aria-expanded": String(!isCollapsed),
        "aria-label": isCollapsed
          ? `Expand ${ancCount} ancestor(s) of ${ind.primary_name}`
          : `Collapse ancestors of ${ind.primary_name}`,
      });
      const titleEl = svgEl("title");
      titleEl.textContent = isCollapsed ? `Expand ${ancCount} ancestor(s)` : "Collapse ancestors";
      toggleG.appendChild(titleEl);
      toggleG.appendChild(svgEl("rect", { width: pillW, height: pillH }));
      const txt = svgEl("text", { x: pillW / 2, y: 12.5 });
      txt.textContent = isCollapsed ? `⌄ +${ancCount}` : "⌃";
      toggleG.appendChild(txt);
      const onToggle = (ev) => {
        ev.stopPropagation();
        toggleBranchCollapse(iid);
      };
      toggleG.addEventListener("click", onToggle);
      toggleG.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onToggle(ev);
        }
      });
      inner.appendChild(toggleG);
    }

    // Multi-path ancestor badge (×2, ×3...)
    const mCount = multiPathBadges[iid];
    if (mCount && mCount >= 2) {
      const bg = svgEl("g", { class: "badge-collapse", transform: `translate(${cw - 36}, -9)` });
      bg.appendChild(svgEl("rect", { width: 32, height: 18 }));
      const bt = svgEl("text", { x: 16, y: 12.5, "text-anchor": "middle" });
      bt.textContent = `×${mCount}`;
      bg.appendChild(bt);
      inner.appendChild(bg);
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
      inner.appendChild(cBadge);
    }

    g.addEventListener("pointerenter", () => applyHoverFocusForNode(iid, true));
    g.addEventListener("pointerleave", scheduleClearHoverFocus);
    g.addEventListener("focusin", () => applyHoverFocusForNode(iid, false));
    g.addEventListener("focusout", (ev) => {
      if (!g.contains(ev.relatedTarget)) scheduleClearHoverFocus();
    });
    g.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onNodeClick(iid, ev.shiftKey);
    });
    g.addEventListener("keydown", (ev) => {
      if (ev.target === g && (ev.key === "Enter" || ev.key === " ")) {
        ev.preventDefault();
        onNodeClick(iid, ev.shiftKey);
      }
    });
    fragNodes.appendChild(g);
    _renderedRefs.nodes.set(iid, g);
  }
  layerNodes.appendChild(fragNodes);

  const totalCollapsed = state.collapsedAncestors.size + state.collapsedCollaterals.size;
  const btnExpandAll = document.getElementById("btn-expand-all");
  if (btnExpandAll) {
    btnExpandAll.hidden = totalCollapsed === 0;
    if (totalCollapsed > 0) {
      btnExpandAll.textContent = `Expand All (${totalCollapsed})`;
    }
  }
  const btnHideCol = document.getElementById("btn-collapse-collaterals");
  if (btnHideCol) {
    const anyUncollapsedCollateral = (data.layout?.hubs || []).some(sh => {
      const hasCol = (sh.children || []).some(c => baseVisible.has(c) && !directAncestors.has(c));
      return hasCol && !state.collapsedCollaterals.has(hubKeyOf(sh));
    });
    btnHideCol.hidden = !anyUncollapsedCollateral;
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
  cancelHoverAlignTimer();
  if ((state.compareMode || shiftKey) && state.selectedId && state.selectedId !== iid) {
    state.compareId = iid;
    state.compareMode = false;
    state.selectedHubId = null;
    state.kinshipResult = await fetchKinship(state.selectedId, iid);
    renderGraph();
    renderDrawer();
    return;
  }
  // Clicking the already-selected individual unselects them without moving cards
  if (!state.compareMode && !shiftKey && state.selectedId === iid) {
    state.selectedId = null;
    state.selectedHubId = null;
    state.compareId = null;
    state.kinshipResult = null;
    renderGraph();
    renderDrawer();
    scheduleSaveViewerState();
    return;
  }
  state.selectedId = iid;
  state.selectedHubId = null;
  state.compareId = null;
  state.compareMode = false;
  state.kinshipResult = null;
  renderGraph();
  renderDrawer();
  alignParentsOverNode(iid);
  scheduleSaveViewerState();
}

function selectHub(hub) {
  state.selectedHubId = hub.id;
  state.selectedId = null;
  state.compareId = null;
  state.compareMode = false;
  state.kinshipResult = getHubKinship(hub);
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
    const activeHubs = (state.activeLayout || state.data.layout).hubs || [];
    const hub = activeHubs.find(h => h.id === state.selectedHubId)
      || (state.data.layout.hubs || []).find(h => h.id === state.selectedHubId);
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
    const hubKinship = getHubKinship(hub);
    if (hubKinship) drawer.appendChild(renderKinshipSection(hubKinship));

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

    if (state.selectedPlace) {
      const pl = (state.placeProfile && state.placeProfile.id === state.selectedPlace)
        ? state.placeProfile
        : (state.data.places || []).find(p => p.id === state.selectedPlace);
      if (pl) {
        drawer.appendChild(el("h2", {}, `${pl.name}`));
        const aliasStr = pl.aliases?.length ? ` · Aliases: ${pl.aliases.join(", ")}` : "";
        drawer.appendChild(el("div", { className: "source-meta" }, `${pl.place_type} · ${pl.status}${aliasStr}`));

        // Focal / Root Person selector for kinship overlay
        const focalWrap = el("div", { style: "margin: 0.6rem 0; display: flex; gap: 0.5rem; align-items: center;" });
        focalWrap.appendChild(el("label", { for: "place-focal-select", style: "font-size:0.82rem; font-weight:600;" }, "Focal Person:"));
        const focalSel = el("select", { id: "place-focal-select", style: "flex:1; font-size:0.82rem;" });
        focalSel.appendChild(el("option", { value: "" }, "(None — show all residents)"));
        for (const iid of Object.keys(state.data.individuals || {}).sort()) {
          const ind = state.data.individuals[iid];
          focalSel.appendChild(el("option", { value: iid }, `${ind.primary_name} (${iid})`));
        }
        focalSel.value = state.placeFocalId || "";
        focalSel.addEventListener("change", async (e) => {
          state.placeFocalId = e.target.value;
          await loadPlaceProfile(state.selectedPlace, state.placeFocalId);
          renderDrawer();
        });
        focalWrap.appendChild(focalSel);
        drawer.appendChild(focalWrap);

        if (pl.jurisdiction_history?.length) {
          drawer.appendChild(el("h3", {}, "Jurisdiction History"));
          for (const j of pl.jurisdiction_history) {
            const parts = [j.period, j.civil, j.ecclesiastical, j.note].filter(Boolean);
            drawer.appendChild(el("div", { className: "source-meta" }, `• ${parts.join(" | ")}`));
          }
        }

        if (pl.people?.length) {
          drawer.appendChild(el("h3", {}, "Associated People (Place Profile)"));
          for (const person of pl.people) {
            const meta = [person.period, person.role].filter(Boolean).join(" · ");
            const row = el("div", { className: "source-meta" });
            if (person.individual_id && state.data.individuals[person.individual_id]) {
              row.appendChild(el("button", { className: "jump-link", onClick: () => onNodeClick(person.individual_id, false) }, person.name));
              if (meta) row.appendChild(document.createTextNode(` (${meta})`));
            } else {
              row.textContent = meta ? `${person.name} (${meta})` : person.name;
            }
            drawer.appendChild(row);
          }
        }

        drawer.appendChild(el("h3", {}, `Associated Individuals (${(pl.individuals || []).length})`));
        const catLabels = {
          self: "Focal Person",
          direct_ancestor: "Direct Ancestor",
          direct_descendant: "Direct Descendant",
          collateral: "Collateral Relative",
          spouse: "Spouse",
          fan_or_unrelated: "FAN / Unrelated Neighbor",
        };
        const tbl = el("table", { className: "tl" },
          el("thead", {}, el("tr", {}, el("th", {}, "Person"), el("th", {}, "Roles"), el("th", {}, "Kinship")))
        );
        const tb = el("tbody");
        for (const ind of (pl.individuals || [])) {
          let kinText = "—";
          if (ind.kinship) {
            kinText = `${catLabels[ind.kinship.category] || ind.kinship.category}: ${ind.kinship.label}`;
          } else if (state.placeFocalId && state.data.individuals[state.placeFocalId]) {
            const k = ind.id === state.placeFocalId ? { label: "focal person (self)" } : computeKinshipClient(state.data, state.placeFocalId, ind.id);
            kinText = k?.paths?.length ? k.paths[0].label : (k?.are_married ? "spouse" : (k?.label || "FAN / unrelated"));
          }
          tb.appendChild(el("tr", {},
            el("td", {}, el("button", { className: "jump-link", onClick: () => onNodeClick(ind.id, false) }, ind.primary_name)),
            el("td", {}, (ind.roles || []).join(", ")),
            el("td", {}, kinText)
          ));
        }
        tbl.appendChild(tb);
        drawer.appendChild(tbl);

        if (pl.couples?.length) {
          drawer.appendChild(el("h3", {}, `Couples / Marriages (${pl.couples.length})`));
          for (const c of pl.couples) {
            drawer.appendChild(el("div", { className: "source-meta" }, `• ${c.person_1_name} + ${c.person_2_name} (${c.date || "undated"})`));
          }
        }

        if (pl.sources?.length) {
          drawer.appendChild(el("h3", {}, `Sources (${pl.sources.length})`));
          for (const s of pl.sources) {
            if (state.data.sources[s.id]) drawer.appendChild(renderSourceCard(state.data.sources[s.id]));
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
      const evType = ev.type === "other" && ev.suggested_label ? `other (${ev.suggested_label})` : ev.type.replace(/_/g, " ");
      tb.appendChild(el("tr", {}, el("td", {}, ev.date || "—"), el("td", {}, evType), el("td", {}, detail)));
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
function updateStickyGenLabels() {
  for (const { y, el: gLbl } of _renderedRefs.genLabels) {
    const screenY = Math.round((y * state.scale + state.ty) * 10) / 10;
    gLbl.setAttribute("transform", `translate(12, ${screenY})`);
  }
}

function applyTransform() {
  document.getElementById("viewport").setAttribute("transform", `translate(${state.tx},${state.ty}) scale(${state.scale})`);
  updateStickyGenLabels();
}

function fitToView({ preserveAlignments = false } = {}) {
  cancelRowOffsetAnimation();
  cancelHoverAlignTimer();
  if (!preserveAlignments) {
    state.rowAlignments = {};
    if (Object.keys(state.rowOffsets).length) {
      state.rowOffsets = {};
      applyRowOffsetsToDom();
    }
  }
  const wrap = document.getElementById("canvas-wrap");
  const rect = wrap.getBoundingClientRect();
  const active = state.activeLayout || state.data.layout;
  const cw = active.card_w || 216;
  const ch = active.card_h || 82;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const iid of Object.keys(active.nodes || {})) {
    const vp = getVisualNodePos(active, iid);
    if (!vp) continue;
    minX = Math.min(minX, vp.x - cw / 2);
    maxX = Math.max(maxX, vp.x + cw / 2);
    minY = Math.min(minY, vp.y - ch / 2);
    maxY = Math.max(maxY, vp.y + ch / 2);
  }
  if (!Number.isFinite(minX)) {
    minX = 0; maxX = active.width || 960; minY = 0; maxY = active.height || 600;
  }
  const lw = Math.max(300, maxX - minX + 96);
  const lh = Math.max(240, maxY - minY + 96);
  const pad = 48;
  const s = Math.min((rect.width - pad) / lw, (rect.height - pad) / lh, 1.15);
  state.scale = Math.max(0.04, s);
  state.tx = (rect.width - (minX + maxX) * state.scale) / 2;
  state.ty = (rect.height - (minY + maxY) * state.scale) / 2;
  applyTransform();
}

function centerOnNode(iid) {
  const active = state.activeLayout || state.data.layout;
  const pos = getVisualNodePos(active, iid);
  if (!pos) return;
  const wrap = document.getElementById("canvas-wrap");
  const rect = wrap.getBoundingClientRect();
  state.scale = Math.max(state.scale, 0.85);
  state.tx = rect.width / 2 - pos.x * state.scale;
  state.ty = rect.height / 2 - pos.y * state.scale;
  applyTransform();
}

function initPanZoom() {
  const wrap = document.getElementById("canvas-wrap");
  let dragging = false, sx = 0, sy = 0;

  wrap.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".node-group, .hub-group, .branch-toggle, .collateral-toggle, .canvas-controls")) return;
    cancelHoverAlignTimer();
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
  wrap.addEventListener("pointerup", () => {
    if (dragging) {
      dragging = false;
      scheduleSaveViewerState();
    }
  });

  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    const nextScale = Math.min(2.5, Math.max(0.04, state.scale * factor));
    state.tx = mx - (mx - state.tx) * (nextScale / state.scale);
    state.ty = my - (my - state.ty) * (nextScale / state.scale);
    state.scale = nextScale;
    applyTransform();
    scheduleSaveViewerState();
  }, { passive: false });

  document.getElementById("btn-zoom-in").addEventListener("click", () => { state.scale = Math.min(2.5, state.scale * 1.25); applyTransform(); scheduleSaveViewerState(); });
  document.getElementById("btn-zoom-out").addEventListener("click", () => { state.scale = Math.max(0.04, state.scale / 1.25); applyTransform(); scheduleSaveViewerState(); });
  document.getElementById("btn-fit").addEventListener("click", () => { fitToView(); scheduleSaveViewerState(); });
  document.getElementById("btn-collapse-collaterals").addEventListener("click", collapseAllCollaterals);
  document.getElementById("btn-expand-all").addEventListener("click", () => {
    state.collapsedAncestors.clear();
    state.collapsedCollaterals.clear();
    state.rowAlignments = {};
    state.rowOffsets = {};
    renderGraph();
    fitToView();
    scheduleSaveViewerState();
  });
  document.getElementById("btn-clear-sel").addEventListener("click", () => {
    state.selectedId = null; state.selectedHubId = null; state.compareId = null; state.kinshipResult = null;
    renderGraph(); renderDrawer();
    scheduleSaveViewerState();
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

function populatePlaceFilter() {
  const sel = document.getElementById("place-filter");
  if (!sel) return;
  const prev = state.selectedPlace;
  sel.replaceChildren(el("option", { value: "" }, "All Places / Communities"));
  for (const pl of (state.data.places || [])) {
    sel.appendChild(el("option", { value: pl.id }, `Place: ${pl.name} (${(pl.individuals || []).length})`));
  }
  sel.value = prev;
}

async function loadPlaceProfile(placeId, focalId) {
  if (!placeId) {
    state.placeProfile = null;
    return;
  }
  if (STANDALONE_MODE) {
    state.placeProfile = (state.data.places || []).find(p => p.id === placeId) || null;
    return;
  }
  const r = state.redactLiving ? "1" : "0";
  const params = new URLSearchParams({ id: placeId, redact_living: r });
  if (focalId) params.set("root", focalId);
  const resp = await fetch(`/api/place?${params.toString()}`);
  if (resp.ok) {
    state.placeProfile = await resp.json();
  }
}

function exportSvg() {
  const orig = document.getElementById("graph");
  const clone = orig.cloneNode(true);
  clone.classList.remove("hover-focus-active");
  const active = state.activeLayout || state.data.layout;
  const lw = active.width || 960;
  const lh = active.height || 600;
  clone.setAttribute("viewBox", `0 0 ${lw} ${lh}`);
  clone.setAttribute("width", String(lw));
  clone.setAttribute("height", String(lh));
  const vp = clone.querySelector("#viewport");
  if (vp) vp.setAttribute("transform", "translate(0,0) scale(1)");
  for (const gLbl of clone.querySelectorAll("#layer-gen-labels .gen-band-pill")) {
    const gy = parseFloat(gLbl.getAttribute("data-gen-y") || "0");
    gLbl.setAttribute("transform", `translate(12, ${gy})`);
  }

  // Resolve CSS custom properties (including light-dark()) to concrete rgb() colors for standalone SVG compatibility
  const probe = document.createElement("span");
  probe.style.display = "none";
  document.body.appendChild(probe);
  const v = (name, fb) => {
    probe.style.color = "";
    probe.style.color = `var(${name})`;
    const resolved = getComputedStyle(probe).color;
    return resolved && !resolved.includes("light-dark") ? resolved : fb;
  };
  const inlineCss = `
    .gen-band-line { stroke: ${v("--border", "#ccc")}; stroke-width: 1; stroke-dasharray: 4 6; opacity: 0.45; }
    .gen-band-pill rect { fill: ${v("--surface", "#fff")}; stroke: ${v("--border", "#ccc")}; stroke-width: 1; rx: 6; opacity: 0.9; }
    .gen-band-label { font: 600 11px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; }
    .node-inner { transform-origin: 108px 44px; transform: scale(1); }
    svg.compact-collateral .node-group.non-direct .node-inner { transform: scale(0.82); }
    svg.compact-collateral .node-group.non-direct.selected .node-inner { transform: scale(1); }
    .node-card { fill: ${v("--surface", "#fff")}; stroke: ${v("--border", "#ccc")}; stroke-width: 1.6; rx: 10; }
    .node-group.status-confirmed .node-card { stroke: ${v("--confirmed", "#047857")}; }
    .node-group.status-tentative .node-card { stroke: ${v("--tentative", "#b45309")}; stroke-dasharray: 5 3; }
    .node-group.in-path .node-card { stroke: ${v("--collapse", "#7c3aed")}; stroke-width: 2.8; }
    .node-group.in-tract .node-card { stroke: ${v("--confirmed", "#047857")}; stroke-width: 2.8; }
    .node-group.dimmed { opacity: 0.36; }
    .node-name { font: 700 13px system-ui, sans-serif; fill: ${v("--text", "#161d26")}; }
    .node-dates { font: 11px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; }
    .node-id { font: 9.5px monospace; fill: ${v("--muted", "#5a6678")}; }
    .branch-toggle rect { fill: ${v("--surface", "#fff")}; stroke: ${v("--border", "#ccc")}; stroke-width: 1.5; rx: 10; }
    .branch-toggle.is-collapsed rect { fill: ${v("--accent", "#1d4ed8")}; stroke: ${v("--accent", "#1d4ed8")}; }
    .branch-toggle text { font: 700 10px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; text-anchor: middle; }
    .branch-toggle.is-collapsed text { fill: #fff; }
    .collateral-toggle rect { fill: ${v("--surface", "#fff")}; stroke: ${v("--border", "#ccc")}; stroke-width: 1.4; rx: 9; }
    .collateral-toggle.is-collapsed rect { fill: ${v("--collapse", "#7c3aed")}; stroke: ${v("--collapse", "#7c3aed")}; }
    .collateral-toggle text { font: 700 9.5px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; text-anchor: middle; }
    .collateral-toggle.is-collapsed text { fill: #fff; }
    .badge-collapse rect { fill: ${v("--collapse", "#7c3aed")}; rx: 8; }
    .badge-collapse text { fill: #fff; font: 700 10px system-ui, sans-serif; }
    .badge-census-ok rect { fill: ${v("--confirmed", "#047857")}; rx: 8; }
    .badge-census-miss rect { fill: ${v("--tentative", "#b45309")}; rx: 8; }
    .badge-census-ok text, .badge-census-miss text { fill: #fff; font: 700 10px system-ui, sans-serif; }
    .hub-diamond { fill: ${v("--surface", "#fff")}; stroke: ${v("--muted", "#5a6678")}; stroke-width: 1.8; }
    .hub-group.consang .hub-diamond { fill: ${v("--consang", "#be123c")}; stroke: ${v("--consang", "#be123c")}; }
    .hub-label { font: 10px system-ui, sans-serif; fill: ${v("--muted", "#5a6678")}; text-anchor: middle; paint-order: stroke; stroke: ${v("--bg", "#f6f7f9")}; stroke-width: 7px; stroke-linejoin: round; }
    .hub-group.consang .hub-label { fill: ${v("--consang", "#be123c")}; font-weight: 700; }
    .edge-path { fill: none; stroke: ${v("--muted", "#5a6678")}; stroke-width: 1.7; opacity: 0.72; }
    .edge-path.proven { stroke: ${v("--confirmed", "#047857")}; opacity: 0.85; }
    .edge-path.probable { stroke-dasharray: 6 3; }
    .edge-path.speculative { stroke-dasharray: 2 3; }
    .edge-path.fan { stroke: ${v("--fan", "#4338ca")}; stroke-dasharray: 4 4; }
    .edge-path.consang { stroke: ${v("--consang", "#be123c")}; stroke-width: 2.2; }
    .edge-path.highlighted { stroke: ${v("--collapse", "#7c3aed")}; stroke-width: 3.2; stroke-dasharray: none; opacity: 1; }
    .edge-path.dimmed { opacity: 0.18; }
  `;
  const bgFill = v("--bg", "#f6f7f9");
  probe.remove();
  const styleEl = svgEl("style");
  styleEl.textContent = inlineCss;
  const bgRect = svgEl("rect", { width: lw, height: lh, fill: bgFill });
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

async function reloadFromServer({ preserveOffsets = false } = {}) {
  if (STANDALONE_MODE) return;
  cancelRowOffsetAnimation();
  cancelHoverAlignTimer();
  const r = state.redactLiving ? "1" : "0";
  const resp = await fetch(`/api/graph.json?redact_living=${r}`);
  if (!resp.ok) return;
  state.data = await resp.json();
  if (!preserveOffsets) {
    state.rowAlignments = {};
    state.rowOffsets = {};
  }
  populateTractFilter();
  populatePlaceFilter();
  if (state.selectedPlace) await loadPlaceProfile(state.selectedPlace, state.placeFocalId);
  renderGraph();
  renderDrawer();
}

function initToolbar() {
  populateTractFilter();
  populatePlaceFilter();

  const searchInput = document.getElementById("search-input");
  searchInput.addEventListener("input", (e) => {
    state.searchQuery = e.target.value;
    renderGraph();
  });
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const matches = getSearchMatches();
      if (matches && matches.size > 0) {
        const firstId = matches.values().next().value;
        onNodeClick(firstId, false);
        centerOnNode(firstId);
      }
    }
  });
  document.getElementById("focus-mode").addEventListener("change", (e) => {
    cancelRowOffsetAnimation();
    state.rowAlignments = {};
    state.rowOffsets = {};
    state.focusMode = e.target.value;
    renderGraph();
    scheduleSaveViewerState();
  });
  document.getElementById("tract-filter").addEventListener("change", (e) => {
    state.selectedTract = e.target.value;
    if (state.selectedTract) { state.selectedId = null; state.selectedHubId = null; state.selectedPlace = ""; document.getElementById("place-filter").value = ""; }
    renderGraph();
    renderDrawer();
  });
  document.getElementById("place-filter").addEventListener("change", async (e) => {
    state.selectedPlace = e.target.value;
    if (state.selectedPlace) {
      state.selectedId = null;
      state.selectedHubId = null;
      state.selectedTract = "";
      document.getElementById("tract-filter").value = "";
      await loadPlaceProfile(state.selectedPlace, state.placeFocalId);
    } else {
      state.placeProfile = null;
    }
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
    scheduleSaveViewerState();
  };
  eraCheck.addEventListener("change", syncEra);
  eraSlider.addEventListener("input", syncEra);

  document.getElementById("toggle-fan").addEventListener("change", (e) => { state.showFan = e.target.checked; renderGraph(); scheduleSaveViewerState(); });
  document.getElementById("toggle-consang").addEventListener("change", (e) => { state.showConsang = e.target.checked; renderGraph(); scheduleSaveViewerState(); });
  document.getElementById("toggle-compact-collateral").addEventListener("change", (e) => {
    state.compactCollateral = e.target.checked;
    const svg = document.getElementById("graph");
    if (svg) svg.classList.toggle("compact-collateral", state.compactCollateral);
    scheduleSaveViewerState();
  });
  document.getElementById("toggle-excluded").addEventListener("change", (e) => { state.showExcluded = e.target.checked; renderGraph(); scheduleSaveViewerState(); });
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
      await reloadFromServer({ preserveOffsets: true });
    });
    document.getElementById("btn-reload").addEventListener("click", () => reloadFromServer());
  }
}

let _saveStateTimer = null;
let _restoringState = false;

function viewerStorageKey() {
  return `genealogy-viewer-state:${state.data?.meta?.workspace_name || "default"}`;
}

function serializeViewerState() {
  return {
    version: 1,
    showFan: !!state.showFan,
    showConsang: !!state.showConsang,
    compactCollateral: !!state.compactCollateral,
    showExcluded: !!state.showExcluded,
    focusMode: state.focusMode || "all",
    eraEnabled: !!state.eraEnabled,
    eraYear: state.eraYear,
    collapsedAncestors: [...state.collapsedAncestors],
    collapsedCollaterals: [...state.collapsedCollaterals],
    rowAlignments: { ...state.rowAlignments },
    selectedId: state.selectedId || null,
    viewport: {
      tx: Math.round(state.tx * 10) / 10,
      ty: Math.round(state.ty * 10) / 10,
      scale: Math.round(state.scale * 10000) / 10000,
    },
  };
}

function scheduleSaveViewerState() {
  if (_restoringState) return;
  clearTimeout(_saveStateTimer);
  _saveStateTimer = setTimeout(() => {
    const payload = serializeViewerState();
    if (STANDALONE_MODE) {
      try { localStorage.setItem(viewerStorageKey(), JSON.stringify(payload)); } catch { /* ignore */ }
      return;
    }
    fetch("/api/viewer-state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => { /* ignore transient network errors */ });
  }, 280);
}

function loadInitialViewerState() {
  if (!STANDALONE_MODE && INITIAL_VIEWER_STATE && typeof INITIAL_VIEWER_STATE === "object") {
    return INITIAL_VIEWER_STATE;
  }
  if (STANDALONE_MODE) {
    try {
      const raw = localStorage.getItem(viewerStorageKey());
      if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
  }
  return {};
}

function applySavedViewerState(saved) {
  if (!saved || typeof saved !== "object") return false;
  _restoringState = true;
  let restoredViewport = false;
  try {
    const inds = state.data?.individuals || {};
    const validHubKeys = new Set((state.data?.layout?.hubs || []).map(hubKeyOf));

    if (typeof saved.showFan === "boolean") {
      state.showFan = saved.showFan;
      const elBox = document.getElementById("toggle-fan");
      if (elBox) elBox.checked = state.showFan;
    }
    if (typeof saved.showConsang === "boolean") {
      state.showConsang = saved.showConsang;
      const elBox = document.getElementById("toggle-consang");
      if (elBox) elBox.checked = state.showConsang;
    }
    if (typeof saved.compactCollateral === "boolean") {
      state.compactCollateral = saved.compactCollateral;
      const elBox = document.getElementById("toggle-compact-collateral");
      if (elBox) elBox.checked = state.compactCollateral;
    }
    if (typeof saved.showExcluded === "boolean") {
      state.showExcluded = saved.showExcluded;
      const elBox = document.getElementById("toggle-excluded");
      if (elBox) elBox.checked = state.showExcluded;
    }
    if (["all", "ancestors", "descendants"].includes(saved.focusMode)) {
      state.focusMode = saved.focusMode;
      const elSel = document.getElementById("focus-mode");
      if (elSel) elSel.value = state.focusMode;
    }
    if (typeof saved.eraYear === "number" && Number.isFinite(saved.eraYear)) {
      const eraSlider = document.getElementById("era-slider");
      const eMin = parseInt(eraSlider?.min || "1500", 10);
      const eMax = parseInt(eraSlider?.max || "2020", 10);
      state.eraYear = Math.min(eMax, Math.max(eMin, Math.round(saved.eraYear)));
      if (eraSlider) eraSlider.value = String(state.eraYear);
    }
    if (typeof saved.eraEnabled === "boolean") {
      state.eraEnabled = saved.eraEnabled;
      const eraCheck = document.getElementById("era-enabled");
      const eraSlider = document.getElementById("era-slider");
      const eraLabel = document.getElementById("era-year-label");
      if (eraCheck) eraCheck.checked = state.eraEnabled;
      if (eraSlider) eraSlider.disabled = !state.eraEnabled;
      if (eraLabel) eraLabel.textContent = state.eraEnabled ? String(state.eraYear) : "Off";
    }
    if (Array.isArray(saved.collapsedAncestors)) {
      state.collapsedAncestors = new Set(saved.collapsedAncestors.filter(id => typeof id === "string" && id in inds));
    }
    if (Array.isArray(saved.collapsedCollaterals)) {
      state.collapsedCollaterals = new Set(saved.collapsedCollaterals.filter(k => typeof k === "string" && validHubKeys.has(k)));
    }
    if (saved.rowAlignments && typeof saved.rowAlignments === "object") {
      const cleanAlign = {};
      for (const [k, cid] of Object.entries(saved.rowAlignments)) {
        if (typeof cid === "string" && cid in inds) {
          const gen = state.data?.layout?.nodes?.[cid]?.generation;
          cleanAlign[gen !== undefined ? gen : k] = cid;
        }
      }
      state.rowAlignments = cleanAlign;
    }
    if (typeof saved.selectedId === "string" && saved.selectedId in inds) {
      state.selectedId = saved.selectedId;
    }
    const vp = saved.viewport;
    if (
      vp &&
      typeof vp.tx === "number" && Number.isFinite(vp.tx) &&
      typeof vp.ty === "number" && Number.isFinite(vp.ty) &&
      typeof vp.scale === "number" && Number.isFinite(vp.scale) &&
      vp.scale >= 0.04 && vp.scale <= 2.5
    ) {
      state.tx = vp.tx;
      state.ty = vp.ty;
      state.scale = vp.scale;
      restoredViewport = true;
    }
  } finally {
    _restoringState = false;
  }
  return restoredViewport;
}

window.addEventListener("DOMContentLoaded", () => {
  initDialogLightDismissFallback();
  initCopyHandlers();
  initPanZoom();
  initToolbar();
  const hadSavedViewport = applySavedViewerState(loadInitialViewerState());
  renderGraph({ recomputeAlignments: true });
  renderDrawer();
  if (hadSavedViewport) applyTransform();
  else fitToView();
});
</script>
</body>
</html>
"""


_VIEWER_STATE_RELPATH = Path("build") / "viewer-state.json"
_ALLOWED_VIEWER_STATE_KEYS = {
    "version",
    "showFan",
    "showConsang",
    "compactCollateral",
    "showExcluded",
    "focusMode",
    "eraEnabled",
    "eraYear",
    "collapsedAncestors",
    "collapsedCollaterals",
    "rowAlignments",
    "selectedId",
    "viewport",
}


def _load_viewer_state(root: Path | str | Dataset) -> dict[str, Any]:
    """Load persisted viewer UI state from <root>/build/viewer-state.json if present."""
    root_path = root.root if isinstance(root, Dataset) else Path(root).resolve()
    state_path = root_path / _VIEWER_STATE_RELPATH
    if not state_path.is_file():
        return {}
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in _ALLOWED_VIEWER_STATE_KEYS}


def _save_viewer_state(root: Path | str, state_obj: dict[str, Any]) -> dict[str, Any]:
    """Atomically persist whitelisted viewer UI state to <root>/build/viewer-state.json."""
    root_path = Path(root).resolve()
    state_path = root_path / _VIEWER_STATE_RELPATH
    clean = {k: v for k, v in state_obj.items() if k in _ALLOWED_VIEWER_STATE_KEYS}
    clean.setdefault("version", 1)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)
    return clean


def _workspace_signature(root_path: Path) -> tuple[Any, ...]:
    """Build a lightweight stat signature over workspace YAML and Markdown inputs."""
    sig: list[Any] = []
    for rel in (".genealogy.yaml", Path("data") / "relationships.yaml"):
        p = root_path / rel
        try:
            st = p.stat()
            sig.append((str(rel), st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino))
        except OSError:
            sig.append((str(rel), None))
    data_dir = root_path / "data"
    for sub in ("individuals", "sources", "places"):
        sub_dir = data_dir / sub
        entries: list[tuple[str, int, int, int, int]] = []
        try:
            with os.scandir(sub_dir) as it:
                for entry in it:
                    if entry.name.endswith(".md"):
                        st = entry.stat()
                        entries.append((entry.name, st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino))
        except OSError:
            pass
        entries.sort()
        sig.append((sub, tuple(entries)))
    return tuple(sig)


def render_viewer_html(
    root: Path | str | Dataset,
    *,
    standalone: bool = False,
    redact_living: bool = True,
) -> str:
    """Render the self-contained interactive HTML viewer."""
    payload = build_viewer_payload(root, redact_living=redact_living)
    saved_state = {} if standalone else _load_viewer_state(root)
    # Escape closing script tags defensively inside inline JSON
    json_blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    state_blob = json.dumps(saved_state, ensure_ascii=False).replace("</", "<\\/")
    return (
        _VIEWER_HTML_TEMPLATE.replace("__STANDALONE_FLAG__", "true" if standalone else "false")
        .replace("__INITIAL_JSON__", json_blob)
        .replace("__INITIAL_VIEWER_STATE__", state_blob)
    )


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
    artifacts_root = Dataset(root=root_path, config=load_workspace_config(root_path)).artifacts_dir

    cache_lock = threading.Lock()
    server_cache: dict[str, Any] = {
        "sig": None,
        "ds": None,
        "ctx": None,
        "payloads": {},
    }

    def _get_dataset_and_ctx() -> tuple[Dataset, KinshipContext]:
        sig = _workspace_signature(root_path)
        with cache_lock:
            if server_cache["sig"] == sig and server_cache["ds"] is not None:
                return server_cache["ds"], server_cache["ctx"]
            ds_loaded = load_dataset(root_path)
            ctx_loaded = KinshipContext(ds_loaded)
            server_cache["sig"] = sig
            server_cache["ds"] = ds_loaded
            server_cache["ctx"] = ctx_loaded
            server_cache["payloads"].clear()
            return ds_loaded, ctx_loaded

    def _get_payload_bundle(redact_val: bool) -> tuple[dict[str, Any], bytes, str]:
        sig = _workspace_signature(root_path)
        with cache_lock:
            if server_cache["sig"] == sig and redact_val in server_cache["payloads"]:
                return server_cache["payloads"][redact_val]
            if server_cache["sig"] != sig or server_cache["ds"] is None:
                ds_loaded = load_dataset(root_path)
                ctx_loaded = KinshipContext(ds_loaded)
                server_cache["sig"] = sig
                server_cache["ds"] = ds_loaded
                server_cache["ctx"] = ctx_loaded
                server_cache["payloads"].clear()
            else:
                ds_loaded = server_cache["ds"]
                ctx_loaded = server_cache["ctx"]
            payload_obj = build_viewer_payload(ds_loaded, redact_living=redact_val, _ctx=ctx_loaded)
            raw_json = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))
            bundle = (
                payload_obj,
                raw_json.encode("utf-8"),
                raw_json.replace("</", "<\\/"),
            )
            server_cache["payloads"][redact_val] = bundle
            return bundle

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
                _, _, json_blob = _get_payload_bundle(redact_living)
                saved_state = _load_viewer_state(root_path)
                state_blob = json.dumps(saved_state, ensure_ascii=False).replace("</", "<\\/")
                html = (
                    _VIEWER_HTML_TEMPLATE.replace("__STANDALONE_FLAG__", "false")
                    .replace("__INITIAL_JSON__", json_blob)
                    .replace("__INITIAL_VIEWER_STATE__", state_blob)
                )
                self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/graph.json":
                redact_q = qs.get("redact_living", [None])[0]
                redact_val = redact_living if redact_q is None else (redact_q != "0")
                _, json_bytes, _ = _get_payload_bundle(redact_val)
                self._send_bytes(json_bytes, "application/json; charset=utf-8")
                return

            if path == "/api/viewer-state":
                self._send_json(_load_viewer_state(root_path))
                return

            if path == "/api/kinship":
                a = qs.get("a", [None])[0]
                b = qs.get("b", [None])[0]
                if not a or not b:
                    self._send_json({"error": "Parameters 'a' and 'b' are required"}, status=400)
                    return
                try:
                    ds_live, ctx_live = _get_dataset_and_ctx()
                    rep = compute_kinship(ds_live, a, b, _ctx=ctx_live)
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
                    ds_live, ctx_live = _get_dataset_and_ctx()
                    rep = compute_pedigree_collapse(ds_live, iid, _ctx=ctx_live)
                    self._send_json(rep.to_dict())
                except KeyError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                return

            if path == "/api/places":
                ds_live, _ = _get_dataset_and_ctx()
                self._send_json(list_places_summary(ds_live))
                return

            if path == "/api/place":
                pid = qs.get("id", [None])[0]
                if not pid:
                    self._send_json({"error": "Parameter 'id' is required"}, status=400)
                    return
                root_ind = qs.get("root", [None])[0]
                redact_q = qs.get("redact_living", [None])[0]
                redact_val = redact_living if redact_q is None else (redact_q != "0")
                try:
                    ds_live, ctx_live = _get_dataset_and_ctx()
                    profile = compute_place_profile(ds_live, pid, root_id=root_ind, _ctx=ctx_live)
                    scrub_fn = _build_living_redactor(ds_live) if redact_val else (lambda t: t)
                    self._send_json(_redact_place_profile(profile, ds_live, scrub_fn, redact_val))
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

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/viewer-state":
                self._send_bytes(b"Not Found", "text/plain", status=HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json({"error": "Invalid Content-Length"}, status=400)
                return
            if length < 0 or length > 524_288:
                self._send_json({"error": "Payload too large"}, status=413)
                return
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "Invalid JSON body"}, status=400)
                return
            if not isinstance(body, dict):
                self._send_json({"error": "Expected JSON object"}, status=400)
                return
            saved = _save_viewer_state(root_path, body)
            self._send_json(saved)

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
