"""SQLite + FTS5 compiler and query helper for a genealogy dataset."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from genealogy.loader import Dataset, load_dataset

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE individuals (
    id TEXT PRIMARY KEY,
    primary_name TEXT NOT NULL,
    name_variants_json TEXT NOT NULL,
    sex TEXT NOT NULL,
    living INTEGER NOT NULL,
    birth_date TEXT,
    birth_place TEXT,
    death_date TEXT,
    death_place TEXT,
    status TEXT NOT NULL,
    external_ids_json TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE individual_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    individual_id TEXT NOT NULL REFERENCES individuals(id),
    event_type TEXT NOT NULL,
    event_date TEXT,
    event_place TEXT,
    tract TEXT,
    description TEXT,
    sources_json TEXT NOT NULL
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_date TEXT,
    jurisdiction TEXT,
    repository TEXT,
    url TEXT,
    links_json TEXT NOT NULL,
    artifact_path TEXT,
    artifact_sha256 TEXT,
    source_type TEXT,
    information_type TEXT,
    relevance TEXT NOT NULL,
    excluded_for_json TEXT NOT NULL,
    exclusion_reason TEXT,
    body TEXT NOT NULL
);

CREATE TABLE source_citations (
    source_id TEXT NOT NULL,
    individual_id TEXT NOT NULL,
    PRIMARY KEY (source_id, individual_id)
);

CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_type TEXT NOT NULL,
    person_1 TEXT NOT NULL,
    person_2 TEXT NOT NULL,
    subtype TEXT,
    rel_date TEXT,
    end_date TEXT,
    place TEXT,
    confidence TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE relationship_constraints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    constraint_id TEXT,
    constraint_type TEXT NOT NULL,
    person_1 TEXT NOT NULL,
    person_2 TEXT NOT NULL,
    steps_1 INTEGER NOT NULL,
    steps_2 INTEGER NOT NULL,
    symmetric INTEGER NOT NULL,
    common_ancestor_sex TEXT,
    common_ancestor_surname TEXT,
    confidence TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE places (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    place_type TEXT NOT NULL,
    parent_place TEXT,
    jurisdiction_history_json TEXT NOT NULL,
    people_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE VIRTUAL TABLE fts_records USING fts5(
    entity_type,
    entity_id,
    title,
    content
);
"""


def build_sqlite(root: Path | str, out_path: Path | str | None = None) -> Path:
    """Compile `<root>/data/` into a SQLite database with FTS5 full-text search."""
    ds = load_dataset(root)
    if out_path is None:
        db_path = ds.root / "build" / "genealogy.sqlite"
    else:
        db_path = Path(out_path).resolve()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        _populate_connection(conn, ds)
    finally:
        conn.close()
    return db_path


def _populate_connection(conn: sqlite3.Connection, ds: Dataset) -> None:
    conn.executescript(SCHEMA_SQL)

    for iid, ind in ds.individuals.items():
        d = ind.data
        conn.execute(
            """
            INSERT INTO individuals (
                id, primary_name, name_variants_json, sex, living,
                birth_date, birth_place, death_date, death_place,
                status, external_ids_json, body
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                iid,
                d.primary_name,
                json.dumps(d.name_variants),
                d.sex,
                int(d.living),
                d.birth.date if d.birth else None,
                d.birth.place if d.birth else None,
                d.death.date if d.death else None,
                d.death.place if d.death else None,
                d.status,
                json.dumps(d.external_ids),
                ind.body,
            ),
        )
        if d.birth:
            conn.execute(
                """
                INSERT INTO individual_events (
                    individual_id, event_type, event_date, event_place, description, sources_json
                ) VALUES (?, 'birth', ?, ?, NULL, ?)
                """,
                (iid, d.birth.date, d.birth.place, json.dumps(d.birth.sources)),
            )
        if d.death:
            conn.execute(
                """
                INSERT INTO individual_events (
                    individual_id, event_type, event_date, event_place, description, sources_json
                ) VALUES (?, 'death', ?, ?, NULL, ?)
                """,
                (iid, d.death.date, d.death.place, json.dumps(d.death.sources)),
            )
        for ev in d.events:
            conn.execute(
                """
                INSERT INTO individual_events (
                    individual_id, event_type, event_date, event_place, tract, description, sources_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (iid, ev.type, ev.date, ev.place, ev.tract, ev.description, json.dumps(ev.sources)),
            )

        searchable = " ".join(
            part
            for part in [
                d.primary_name,
                " ".join(d.name_variants),
                d.birth.place if d.birth and d.birth.place else "",
                d.death.place if d.death and d.death.place else "",
                " ".join(ev.tract for ev in d.events if ev.tract),
                ind.body,
            ]
            if part
        )
        fts_title = f"[EXCLUDED] {d.primary_name}" if d.status == "excluded" else d.primary_name
        conn.execute(
            "INSERT INTO fts_records (entity_type, entity_id, title, content) VALUES (?, ?, ?, ?)",
            ("individual", iid, fts_title, searchable),
        )

    for sid, src in ds.sources.items():
        s = src.data
        links_data = [lnk.model_dump() for lnk in s.links]
        conn.execute(
            """
            INSERT INTO sources (
                id, title, record_type, record_date, jurisdiction, repository,
                url, links_json, artifact_path, artifact_sha256, source_type,
                information_type, relevance, excluded_for_json, exclusion_reason, body
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                s.title,
                s.record_type,
                s.date,
                s.jurisdiction,
                s.repository,
                s.url,
                json.dumps(links_data),
                s.artifact_path,
                s.artifact_sha256,
                s.source_type,
                s.information_type,
                s.relevance,
                json.dumps(s.excluded_for),
                s.exclusion_reason,
                src.body,
            ),
        )
        for iid in s.individuals_cited:
            conn.execute(
                "INSERT OR IGNORE INTO source_citations (source_id, individual_id) VALUES (?, ?)",
                (sid, iid),
            )
        searchable = " ".join(
            part
            for part in [
                s.title,
                s.jurisdiction or "",
                s.repository or "",
                s.exclusion_reason or "",
                src.body,
            ]
            if part
        )
        fts_src_title = f"[EXCLUDED] {s.title}" if s.relevance == "excluded" else s.title
        conn.execute(
            "INSERT INTO fts_records (entity_type, entity_id, title, content) VALUES (?, ?, ?, ?)",
            ("source", sid, fts_src_title, searchable),
        )

    for rel in ds.relationships:
        conn.execute(
            """
            INSERT INTO relationships (
                rel_type, person_1, person_2, subtype, rel_date, end_date,
                place, confidence, sources_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rel.type,
                rel.person_1,
                rel.person_2,
                rel.subtype,
                rel.date,
                rel.end_date,
                rel.place,
                rel.confidence,
                json.dumps(rel.sources),
                rel.notes,
            ),
        )

    for idx, con in enumerate(ds.constraints, start=1):
        conn.execute(
            """
            INSERT INTO relationship_constraints (
                constraint_id, constraint_type, person_1, person_2, steps_1, steps_2,
                symmetric, common_ancestor_sex, common_ancestor_surname,
                confidence, sources_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                con.id or f"C-{idx:03d}",
                con.type,
                con.person_1,
                con.person_2,
                con.steps_1,
                con.steps_2,
                1 if con.symmetric else 0,
                con.common_ancestor_sex,
                con.common_ancestor_surname,
                con.confidence,
                json.dumps(con.sources),
                con.notes,
            ),
        )

    for pid, place in ds.places.items():
        p = place.data
        conn.execute(
            """
            INSERT INTO places (
                id, name, aliases_json, place_type, parent_place,
                jurisdiction_history_json, people_json, sources_json, status, body
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p.id,
                p.name,
                json.dumps(p.aliases),
                p.place_type,
                p.parent_place,
                json.dumps([j.model_dump() for j in p.jurisdiction_history]),
                json.dumps([person.model_dump() for person in p.people]),
                json.dumps(p.sources),
                p.status,
                place.body,
            ),
        )
        searchable = " ".join(
            part
            for part in [
                p.name,
                *p.aliases,
                p.place_type,
                p.parent_place or "",
                *(j.civil or "" for j in p.jurisdiction_history),
                *(j.ecclesiastical or "" for j in p.jurisdiction_history),
                *(person.name for person in p.people),
                place.body,
            ]
            if part
        )
        conn.execute(
            "INSERT INTO fts_records (entity_type, entity_id, title, content) VALUES (?, ?, ?, ?)",
            ("place", pid, p.name, searchable),
        )

    conn.commit()


def _open_in_memory_db(root: Path | str) -> sqlite3.Connection:
    ds = load_dataset(root)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _populate_connection(conn, ds)
    return conn


def search_fts(root: Path | str, query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Run an FTS5 full-text search across individuals and source transcriptions."""
    conn = _open_in_memory_db(root)
    try:
        rows = conn.execute(
            """
            SELECT
                entity_type,
                entity_id,
                title,
                snippet(fts_records, 3, '[', ']', '...', 18) AS snippet
            FROM fts_records
            WHERE fts_records MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def run_sql_query(root: Path | str, sql: str) -> list[dict[str, Any]]:
    """Execute a read-only SELECT query against the compiled in-memory dataset."""
    stripped = sql.strip().lstrip("(").upper()
    if not (stripped.startswith("SELECT") or stripped.startswith("WITH")):
        raise ValueError("Only read-only SELECT / WITH queries are permitted.")
    conn = _open_in_memory_db(root)
    try:
        conn.execute("PRAGMA query_only = ON;")
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
