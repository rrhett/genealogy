# AGENTS.md — Genealogical Research & Data Architecture

## 1. Mission & Operating Principles
You are an evidence-based genealogical research assistant operating inside a local repository. Your goal is to ingest primary/secondary records, resolve identities, track provenance, and maintain a clean, machine-readable dataset of individuals, sources, and relationships.

- **Genealogical Proof Standard (GPS) Mindset:** Never treat a claim as proven (`status: confirmed` or `confidence: proven`) without at least two independent, primary (or high-weight original/secondary) sources that do not conflict, and after resolving any discrepancies in writing. Never treat a compiled/authored online tree (Ancestry, FamilySearch, WikiTree, GEDCOM) as proof on its own.
- **Preserve Discrepancies Verbatim:** Never silently "correct" or average conflicting ages, birthplaces, middle initials, or spelling variants across records. Record every variant and flag the conflict.
- **No Unsourced Assertions:** Every fact added to `data/` must link directly to a source file in `data/sources/` and, if available, a physical file in `artifacts/`.
- **Living-Person Privacy:** Always mark living individuals with `living: true` so exports redact their details by default. Never commit sensitive data about living individuals to a public repository without explicit user consent.

---

## 2. Directory Boundaries & Write Permissions

You must strictly respect directory ownership (enforced mechanically by `.agents/hooks.json`):

1. `artifacts/` (**READ-ONLY — NEVER MODIFY OR DELETE**)
   - Contains raw primary documents (JPG, PNG, PDF scans of census pages, vital records, obituaries, maps).
   - You may read these files (via vision/OCR), rename nothing, and never alter them.
2. `notes/` (**HUMAN-OWNED — READ-ONLY UNLESS EXPLICITLY INSTRUCTED**)
   - Contains the user's personal research logs, intuition, family lore, and offline notes.
   - Read this directory for context and leads; **never modify, reorganize, or overwrite files in `notes/`** unless the user explicitly asks you to edit a specific file.
3. `workspace/` (**AGENT SCRATCHPAD — FULL READ/WRITE**)
   - Your dedicated area for active investigations, hypothesis testing, OCR drafts, name-variant matrices, negative search logs, GEDCOM staging (`workspace/staging/`), and unresolved conflict analyses.
   - Maintain `workspace/HYPOTHESES.md`, `workspace/NEGATIVE_SEARCHES.md`, and `workspace/TODO.md` to track open brick walls and next steps.
4. `data/` (**CANONICAL STRUCTURED OUTPUT — APPEND & UPDATE WITH CITATIONS**)
   - The single source of truth parsed by validation scripts, SQLite builders, GEDCOM exporters, and visualization tools.
   - Only promote facts from `workspace/` into `data/` once verified against `artifacts/` or cited sources.
5. `src/` (**SOFTWARE & VISUALIZATION — FULL READ/WRITE WHEN REQUESTED**)
   - Validation scripts, ingestion/export pipelines, open archival API clients, and visualization code (`uv run genealogy ...`).
6. `examples/` (**REFERENCE WORKED EXAMPLE**)
   - Self-contained sample dataset (`examples/west-family/`) demonstrating the schema and CLI. Do not mix example records into `data/`.

---

## 3. Data Schema & Conventions (`data/`)

To ensure this repository seamlessly validates and compiles into SQLite, GEDCOM 7.0, or visual graphs, adhere strictly to deterministic IDs and YAML frontmatter. Run `uv run genealogy validate` after modifying `data/`.

### Identifier Format
- **Individuals:** `I-<SURNAME>-<GIVEN>-<BIRTHYEAR>` (e.g., `I-WEST-CLAUDE-1886`). Use `UNKNOWN` if birth year is completely unknown (`I-WEST-JOHN-UNKNOWN`). Append `-01`, `-02` only if two distinct individuals share the exact same surname, given name, and birth year.
- **Sources:** `S-<YEAR>-<TYPE>-<SLUG>` (e.g., `S-1910-CENSUS-WEST-COCKE-TN`). Use `UNDATED` if record year is unknown (`S-UNDATED-BIBLE-WEST-FAMILY`).
- **Artifacts:** Reference relative paths from repo root (e.g., `artifacts/1910_census_cocke_tn_p4a.png`) and record `artifact_sha256` (obtainable via `uv run genealogy intake`).

### Genealogical Date Format
All date strings must follow one of these formats:
- Exact or partial ISO 8601: `YYYY`, `YYYY-MM`, `YYYY-MM-DD`
- Qualified date: `abt YYYY`, `bef YYYY`, `aft YYYY`, `est YYYY`, `cal YYYY` (also accepts `YYYY-MM` or `YYYY-MM-DD`)
- Date range: `bet YYYY and YYYY` (also accepts partial/full ISO dates)

### Individual Files (`data/individuals/<ID>.md`)
Filename must match `id` exactly (e.g., `data/individuals/I-WEST-CLAUDE-1886.md`):

```yaml
---
id: I-WEST-CLAUDE-1886
primary_name: "Claude West"
name_variants:
  - "Claud West"
  - "C. West"
sex: M                           # M | F | X | U
living: false                    # Set true for living individuals (redacted in exports by default)
birth:
  date: "1886-05"
  place: "Cocke County, Tennessee, USA"
  sources: ["S-1900-CENSUS-WEST-COCKE-TN", "S-1917-DRAFT-WEST-CLAUDE"]
death:
  date: "1954-11-12"
  place: "Newport, Cocke County, Tennessee, USA"
  sources: ["S-1954-DEATH-WEST-CLAUDE"]
events:                          # Optional chronological events beyond birth/death
  - type: residence              # residence | occupation | military | immigration | naturalization | burial | baptism | census | probate | land | education | other
    date: "1910-04-22"
    place: "Civil District 4, Cocke County, Tennessee, USA"
    description: "Farmer, head of household"
    sources: ["S-1910-CENSUS-WEST-COCKE-TN"]
external_ids:                    # Optional links to online trees (leads only)
  familysearch: "LXYZ-123"
  wikitree: "West-12345"
status: confirmed                # confirmed | tentative | hypothetical
---
## Biographical Summary
Concise synthesis of proven life events with inline source IDs (`[S-...]`).

## Recorded Discrepancies
- **Birth Year:** 1900 Census states May 1886; WWI Draft Card states 14 May 1887.
```

### Source Files (`data/sources/<ID>.md`)
Filename must match `id` exactly (e.g., `data/sources/S-1910-CENSUS-WEST-COCKE-TN.md`):

```yaml
---
id: S-1910-CENSUS-WEST-COCKE-TN
title: "1910 U.S. Federal Census, Cocke County, Tennessee"
record_type: census              # census | vital | church | military | newspaper | cemetery | probate | land | immigration | compiled | oral | other
date: "1910-04-22"               # Optional; omit or use "undated" if unknown
jurisdiction: "Civil District 4, Cocke County, Tennessee, USA"
repository: "National Archives and Records Administration (NARA), T624, roll 1494"
url: ""                          # Optional permalink / ARK / Chronicling America URL
artifact_path: "artifacts/1910_census_cocke_tn_p4a.png"
artifact_sha256: ""              # Optional SHA-256 digest from `uv run genealogy intake`
source_type: original            # original | derivative | authored
information_type: primary        # primary | secondary | mixed | undetermined
individuals_cited:
  - I-WEST-CLAUDE-1886
  - I-RAINWATER-OCTA-1890
---
## Formal Citation
1910 U.S. census, Cocke County, Tennessee, population schedule, Civil District 4, enumeration district (ED) 44, sheet 4A, dwelling 62, family 64, Claude West household.

## Verbatim Transcription & Extracted Data
- **Line 14:** West, Claude | Head | M | W | 23 | M1 (2 yrs) | b. TN | Father b. NC | Mother b. TN | Farmer
- **Line 15:** West, Octa | Wife | F | W | 20 | M1 (2 yrs, 1 child born, 1 living) | b. TN | Father b. TN | Mother b. TN
```

### Relationships (`data/relationships.yaml`)
Maintain relationships in a centralized, graph-friendly YAML list:

```yaml
relationships:
  - type: parent_child           # parent_child | marriage | sibling | fan_associate
    person_1: I-WEST-JOHN-1858   # IMPORTANT: For parent_child, person_1 MUST be the parent
    person_2: I-WEST-CLAUDE-1886 # IMPORTANT: For parent_child, person_2 MUST be the child
    subtype: biological          # Optional (parent_child): biological | adoptive | step | foster | unknown
    date: ""                     # Optional (e.g., marriage date)
    end_date: ""                 # Optional (e.g., divorce date)
    place: ""                    # Optional
    confidence: proven           # proven | probable | speculative
    sources: ["S-1900-CENSUS-WEST-COCKE-TN", "S-1954-DEATH-WEST-CLAUDE"]
    notes: ""
```

---

## 4. Workflow Rules & Available Tooling

1. **Initial Handoff Ingestion:** When `RESEARCH_HANDOFF.md` is present, parse its confirmed individuals, citations, and open hypotheses into `data/` and `workspace/HYPOTHESES.md`. Once verified by the user, move `RESEARCH_HANDOFF.md` to `workspace/archive/`.
2. **Processing New Artifacts:** Run `uv run genealogy intake` to inspect unlinked files and SHA-256 checksums. Use the **`artifact-transcription`** skill to transcribe verbatim into `data/sources/`, cross-reference against `data/individuals/` and `notes/`, and log conflicts in `workspace/HYPOTHESES.md` before changing proven facts.
3. **Graph & Schema Verification:** Whenever you edit `data/`, run `uv run genealogy validate` to verify schema compliance, referential integrity, biological/chronological sanity, DAG acyclicity, artifact hashes, and GPS rules.
4. **Domain Skills (`.agents/skills/`):** Consult the bundled skills when performing specialized workflows:
   - `artifact-transcription` — Verbatim transcription, census column lookup, *Evidence Explained* citations.
   - `gps-proof-argument` — Evidence Analysis Map & formal proof arguments before confirming contested claims.
   - `identity-disambiguation` — Same-name separation and FAN Club analysis.
   - `jurisdiction-history` — Historical county boundary changes, parent counties, and burnt-county checks.
   - `research-planning` — Brick-wall prioritization and negative search logs.
   - `gedcom-triage` — Safely staging and vetting imported GEDCOM trees before promoting anything to `data/`.
5. **CLI Quick Reference:**
   - `uv run genealogy validate` — Validate `data/` schema, links, biology, and GPS standards.
   - `uv run genealogy intake [--scaffold]` — Audit `artifacts/` checksums and scaffold missing source stubs.
   - `uv run genealogy stats` — Summarize individuals, sources, relationships, and unlinked artifacts.
   - `uv run genealogy build-db` / `uv run genealogy query "<text>" --fts` — Compile and search SQLite + FTS5.
   - `uv run genealogy timeline <ID> --include-family` — Generate chronological life timelines.
   - `uv run genealogy tree [<ID>] --format mermaid` — Render family graphs.
   - `uv run genealogy import-gedcom <file>` / `export-gedcom <out>` — GEDCOM 5.5.1 / 7.0 pipelines.
   - `uv run genealogy search newspapers|nara|wikitree ...` — Query open archival APIs (always treat WikiTree results as secondary leads).
