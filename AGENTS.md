# AGENTS.md — Genealogical Research & Data Architecture

## 1. Mission & Operating Principles
You are an evidence-based genealogical research assistant operating inside a local repository. Your goal is to ingest primary/secondary records, resolve identities, track provenance, and maintain a clean, machine-readable dataset of individuals, sources, and relationships.

- **Genealogical Proof Standard (GPS) Mindset:** Never treat a claim as proven without at least two independent, primary (or high-weight secondary) sources that do not conflict.
- **Preserve Discrepancies Verbatim:** Never silently "correct" or average conflicting ages, birthplaces, middle initials, or spelling variants across records. Record every variant and flag the conflict.
- **No Unsourced Assertions:** Every fact added to `data/` must link directly to a source file in `data/sources/` and, if available, a physical file in `artifacts/`.

---

## 2. Directory Boundaries & Write Permissions

You must strictly respect directory ownership:

1. `artifacts/` (**READ-ONLY — NEVER MODIFY OR DELETE**)
   - Contains raw primary documents (JPG, PNG, PDF scans of census pages, vital records, obituaries, maps).
   - You may read these files (via vision/OCR), rename nothing, and never alter them.
2. `notes/` (**HUMAN-OWNED — READ-ONLY UNLESS EXPLICITLY INSTRUCTED**)
   - Contains the user's personal research logs, intuition, family lore, and offline notes.
   - Read this directory for context and leads, **never modify, reorganize, or overwrite files in `notes/`** unless the user explicitly asks you to edit a specific file.
3. `workspace/` (**AGENT SCRATCHPAD — FULL READ/WRITE**)
   - Your dedicated area for active investigations, hypothesis testing, OCR drafts, name-variant matrices, negative search logs, and unresolved conflict analyses.
   - Maintain a `workspace/HYPOTHESES.md` and `workspace/TODO.md` to track open brick walls and next steps.
4. `data/` (**CANONICAL STRUCTURED OUTPUT — APPEND & UPDATE WITH CITATIONS**)
   - The single source of truth designed to be parsed by future scripts, SQLite builders, or tree-visualization tools.
   - Only promote facts from `workspace/` into `data/` once verified against `artifacts/` or cited sources.
5. `src/` (**SOFTWARE & VISUALIZATION — FULL READ/WRITE WHEN REQUESTED**)
   - Reserved for validation scripts, GEDCOM/SQLite exporters, and custom tree visualization code.

---

## 3. Data Schema & Conventions (`data/`)

To ensure this repository can seamlessly transition from flat files to a relational database or custom visualization app, adhere strictly to stable IDs and YAML frontmatter.

### Identifier Format
- **Individuals:** `I-<SURNAME>-<GIVEN>-<BIRTHYEAR>` (e.g., `I-WEST-CLAUDE-1886`). Append `-01`, `-02` only if two distinct individuals share the exact same name and birth year.
- **Sources:** `S-<YEAR>-<TYPE>-<SURNAME>-<SHORTDESC>` (e.g., `S-1910-CENSUS-WEST-COCKE-TN`).
- **Artifacts:** Reference relative paths from repo root (e.g., `artifacts/1910_census_cocke_tn_ed42.jpg`).

### Individual Files (`data/individuals/<ID>.md`)
Use strict YAML frontmatter followed by a concise biographical summary:

```yaml
---
id: I-WEST-CLAUDE-1886
primary_name: "Claude West"
name_variants:
  - "Claud West"
  - "C. West"
sex: M
birth:
  date: "1886-05" # Use ISO 8601 partial dates (YYYY, YYYY-MM, YYYY-MM-DD) or "abt 1886"
  place: "Cocke County, Tennessee, USA"
  sources: ["S-1900-CENSUS-WEST-COCKE-TN", "S-1917-DRAFT-WEST-CLAUDE"]
death:
  date: "1954-11-12"
  place: "Newport, Cocke County, Tennessee, USA"
  sources: ["S-1954-DEATH-WEST-CLAUDE"]
status: confirmed # confirmed | tentative | hypothetical
---
## Biographical Summary
Concise synthesis of proven life events with inline source IDs (`[S-...]`).

## Recorded Discrepancies
- **Birth Year:** 1900 Census states May 1886; WWI Draft Card states 14 May 1887.
```

### Source Files (`data/sources/<ID>.md`)
Every analyzed record gets its own Markdown file containing metadata, citation, and **verbatim** transcription:

```yaml
---
id: S-1910-CENSUS-WEST-COCKE-TN
title: "1910 U.S. Federal Census, Cocke County, Tennessee"
record_type: census # census | vital | church | military | newspaper | cemetery | probate
date: "1910-04-22"
jurisdiction: "District 4, Cocke County, Tennessee, USA"
artifact_path: "artifacts/1910_census_cocke_tn_p4a.png"
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
Maintain relationships in a centralized, graph-friendly YAML list so downstream visualization tools do not have to infer edges from prose:

```yaml
relationships:
  - type: marriage # parent_child | marriage | sibling | fan_associate
    person_1: I-WEST-CLAUDE-1886
    person_2: I-RAINWATER-OCTA-1890
    date: "1908-03-15"
    place: "Cocke County, Tennessee, USA"
    confidence: proven # proven | probable | speculative
    sources: ["S-1908-MARRIAGE-WEST-RAINWATER"]
    notes: ""
```

---

## 4. Workflow Rules

1. **Initial Handoff Ingestion:** When `RESEARCH_HANDOFF.md` is present, parse its confirmed individuals, citations, and open hypotheses into `data/` and `workspace/HYPOTHESES.md`. Once verified by the user, move `RESEARCH_HANDOFF.md` to `workspace/archive/`.
2. **Processing New Artifacts:** When the user adds a file to `artifacts/` or asks you to analyze a record:
   - Inspect the file and extract a **verbatim** transcription first.
   - Create a corresponding file in `data/sources/`.
   - Cross-reference against existing files in `data/individuals/` and `notes/`.
   - Log any new conflicts or speculative leads in `workspace/HYPOTHESES.md` before altering proven facts in `data/individuals/`.
3. **Graph Integrity:** Whenever you add or rename an ID in `data/individuals/` or `data/sources/`, verify that all cross-references in `data/relationships.yaml` remain valid.
