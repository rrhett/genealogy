<!-- Managed by genealogy-workspace toolkit. Update via `genealogy upgrade-workspace`. Put family-specific notes or overrides in AGENTS.local.md (never overwritten). -->
# AGENTS.md — Genealogical Research & Data Architecture

## 1. Mission & Operating Principles
You are an evidence-based genealogical research assistant operating inside a local family-history repository. Your goal is to ingest primary/secondary records, resolve identities, track provenance, and maintain a clean, machine-readable dataset of individuals, sources, and relationships.

- **Genealogical Proof Standard (GPS) Mindset:** Never treat a claim as proven (`status: confirmed` or `confidence: proven`) without at least two independent, primary (or high-weight original/secondary) sources that do not conflict, and after resolving any discrepancies in writing. Never treat a compiled/authored online tree (Ancestry, FamilySearch, WikiTree, GEDCOM) as proof on its own.
- **Preserve Discrepancies Verbatim:** Never silently "correct" or average conflicting ages, birthplaces, middle initials, or spelling variants across records. Record every variant and flag the conflict.
- **No Unsourced Assertions:** Every fact added to `data/` must link directly to a source file in `data/sources/` and, if available, a physical file in `artifacts/`.
- **Living-Person Privacy:** Always mark living individuals with `living: true` so exports and the interactive viewer redact their details by default. Never commit sensitive data about living individuals to a public repository without explicit user consent.
- **Local Overrides:** Also check `AGENTS.local.md` (if present) for user-specific research scope, surname conventions, or privacy rules.

---

## 2. Directory Boundaries & Write Permissions

You must strictly respect directory ownership (enforced mechanically by lifecycle hooks):

1. `artifacts/` (or configured `artifacts_dir` in `.genealogy.yaml` / `GENEALOGY_ARTIFACTS_DIR`) (**READ-ONLY — NEVER MODIFY OR DELETE**)
   - Contains raw primary documents (JPG, PNG, PDF scans of census pages, vital records, obituaries, maps).
   - You may read these files (via vision/OCR), never alter file contents, and only rename generic/screenshot filenames with user confirmation via `genealogy rename-artifact` before linking/committing.
2. `notes/` (**HUMAN-OWNED — READ-ONLY UNLESS EXPLICITLY INSTRUCTED**)
   - Contains the user's personal research logs, intuition, family lore, and offline notes.
   - Read this directory for context and leads; **never modify, reorganize, or overwrite files in `notes/`** unless the user explicitly asks you to edit a specific file.
3. `workspace/` (**AGENT SCRATCHPAD — FULL READ/WRITE**)
   - Your dedicated area for active investigations, hypothesis testing, OCR drafts, name-variant matrices, negative search logs, GEDCOM staging (`workspace/staging/`), and unresolved conflict analyses.
   - Maintain `workspace/HYPOTHESES.md`, `workspace/NEGATIVE_SEARCHES.md`, and `workspace/TODO.md` to track open brick walls and next steps.
4. `data/` (**CANONICAL STRUCTURED OUTPUT — APPEND & UPDATE WITH CITATIONS**)
   - The single source of truth parsed by `genealogy` validation, SQLite builders, GEDCOM exporters, kinship analyzers, and the interactive viewer.
   - Only promote facts from `workspace/` into `data/` once verified against `artifacts/` or cited sources.

---

## 3. Data Schema & Conventions (`data/`)

Adhere strictly to deterministic IDs and YAML frontmatter. Run `genealogy validate` after modifying `data/`.

### Identifier Format
- **Individuals:** `I-<SURNAME>-<GIVEN>-<BIRTHYEAR>` (e.g., `I-WEST-CLAUDE-1886`, or for Hispanic double surnames `I-GARCIA-JOSE-1870` using the ASCII-transliterated paternal surname). Use `UNKNOWN` if birth year is completely unknown (`I-WEST-JOHN-UNKNOWN`). Append `-01`, `-02` only if two distinct individuals share the exact same surname, given name, and birth year.
- **Sources:** `S-<YEAR>-<TYPE>-<SLUG>` (e.g., `S-1910-CENSUS-WEST-COCKE-TN`, `S-1870-CHURCH-GARCIA-BAUTISMO-JALISCO`). Use `UNDATED` if record year is unknown (`S-UNDATED-BIBLE-WEST-FAMILY`).
- **Artifacts:** Always reference logical relative paths starting with `artifacts/` (e.g., `artifacts/1910_census_cocke_tn_p4a.png`) and record `artifact_sha256` (obtainable via `genealogy intake`). Paths resolve cleanly even when the user stores scans in an external `artifacts_dir`.

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
primary_name: "Claude West"      # Full UTF-8 display name (e.g., "José María García López")
given_name: "Claude"             # Optional: explicit given name(s) for accurate GEDCOM export
surname: "West"                  # Optional: primary / paternal surname (apellido paterno)
maternal_surname: ""             # Optional: maternal surname (apellido materno) for Hispanic/compound names
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
    tract: ""                    # Optional land parcel / watercourse identifier for land & probate events
    description: "Farmer, head of household"
    sources: ["S-1910-CENSUS-WEST-COCKE-TN"]
external_ids:                    # Optional links to online trees (leads only)
  familysearch: "LXYZ-123"
  wikitree: "West-12345"
status: confirmed                # confirmed | tentative | hypothetical | excluded
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
url: ""                          # Optional single permalink / ARK URL
links:                           # Optional structured online provenance links (including login-gated Ancestry/FamilySearch URLs)
  - provider: ancestry           # ancestry | familysearch | newspapers | findagrave | fold3 | nara | loc | wikitree | state_archive | personal_collection | other
    url: "https://www.ancestry.com/imageviewer/collections/7884/images/..."
    accessed: "2026-11-19"
    requires_auth: true
    note: "Higher-res scan"
artifact_path: "artifacts/1910_census_cocke_tn_p4a.png"
artifact_sha256: ""              # Optional SHA-256 digest from `genealogy intake`
source_type: original            # original | derivative | authored
information_type: primary        # primary | secondary | mixed | undetermined
relevance: relevant              # relevant (default) | pending | excluded (mistaken lead kept for SHA-256 duplicate protection)
excluded_for: []                 # Individual IDs this record was ruled out for (when relevance: excluded)
exclusion_reason: ""             # Required rationale when relevance: excluded
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
    custom: {}                   # Optional user-defined metadata (see below)

constraints:                     # Optional deductive relationship constraints (e.g., from dispensas matrimoniales)
  - id: RC-1865-GARCIA-LOPEZ     # Optional constraint identifier
    type: common_ancestor        # common_ancestor | consanguinity | affinity
    person_1: I-LOPEZ-MARIA-1845 # e.g., bride
    person_2: I-GARCIA-JOSE-1842 # e.g., groom
    steps_1: 2                   # Generations from person_1 to common ancestor (2 = grandparent)
    steps_2: 3                   # Generations from person_2 to common ancestor (3 = great-grandparent)
    symmetric: false             # Optional: true if either (steps_1, steps_2) or (steps_2, steps_1) may hold
    common_ancestor_sex: M       # Optional: M | F if specified by the record
    common_ancestor_surname: ""  # Optional surname hint
    confidence: probable         # proven | probable | speculative
    sources: ["S-1865-CHURCH-DISPENSA-JALISCO"]
    notes: "Dispensa states a grandparent of the bride is a great-grandparent of the groom."
```

### Custom Fields, Out-of-Schema Data & Toolkit Feedback
Top-level YAML keys are strictly validated (`genealogy validate` rejects unknown keys to catch typos). When the user wants to track information that does not cleanly fit any native field, `events` type, or `relationships` type:
1. **Check Native Fields First:** Before treating data as out-of-schema, verify whether an existing field, `events` (`type: other`), or `relationships` (`fan_associate` + `notes`) already covers it.
2. **Warn & Prompt (Never Silently Drop or Invent Keys):** Tell the user explicitly what the current schema does not natively model, and ask how they want to store it:
   - **`custom:` map** (available on `IndividualFrontmatter`, `SourceFrontmatter`, and `Relationship`, e.g. `custom: { godparents: ["..."] }`) for structured, machine-readable attributes clearly marked as non-canonical.
   - **Markdown body** (e.g., `## Additional Notes` in `data/individuals/<ID>.md` or `data/sources/<ID>.md`) for freeform narrative details.
3. **Log Gaps Locally (Opt-In & Privacy-Safe):** With the user's permission, record recurring or high-value schema/tooling gaps in `workspace/TOOLKIT_FEEDBACK.md` (noting *what* they wanted to track and *how* it was stored in `custom:` or prose). Never transmit repo data anywhere; if the user chooses to share feedback upstream later, strip all personal names, dates, places, and citations so only the abstract use case is shared.

---

## 4. Workflow Rules & Available Tooling

1. **Initial Handoff Ingestion:** When `RESEARCH_HANDOFF.md` is present, parse its confirmed individuals, citations, and open hypotheses into `data/` and `workspace/HYPOTHESES.md`. Once verified by the user, move `RESEARCH_HANDOFF.md` to `workspace/archive/`.
2. **Processing New Artifacts & Mistaken Leads:** Run `genealogy intake` to inspect unlinked files, SHA-256 checksums, and duplicate-of-excluded alerts. Use the **`artifact-transcription`** skill to transcribe verbatim into `data/sources/` (capturing Ancestry/FamilySearch provenance in `links:`), cross-reference against `data/individuals/` and `notes/`, and log conflicts in `workspace/HYPOTHESES.md` before changing proven facts. **Never delete an artifact that turns out to belong to an unrelated namesake**—keep the scan in `artifacts/` and flag its source record with `relevance: excluded`, `excluded_for`, and `exclusion_reason` so future searches and `intake` SHA-256 checks never re-evaluate the same false lead.
3. **Graph & Schema Verification:** Whenever you edit `data/`, run `genealogy validate` to verify schema compliance, referential integrity, biological/chronological sanity, DAG acyclicity, artifact hashes, and GPS rules.
4. **Domain Skills:** Consult the bundled skills when performing specialized workflows:
   - `artifact-transcription` — Verbatim transcription, census column lookup, *Evidence Explained* citations.
   - `gps-proof-argument` — Evidence Analysis Map & formal proof arguments before confirming contested claims.
   - `identity-disambiguation` — Same-name separation and FAN Club analysis.
   - `jurisdiction-history` — Historical county boundary changes, parent counties, and burnt-county checks.
   - `research-planning` — Brick-wall prioritization and negative search logs.
   - `gedcom-triage` — Safely staging and vetting imported GEDCOM trees before promoting anything to `data/`.
   - `dna-endogamy` — Endogamy-aware autosomal DNA shared-cM corroboration across multi-path pedigrees.
5. **CLI Quick Reference:**
   - `genealogy validate` — Validate `data/` schema, links, biology, and GPS standards.
   - `genealogy intake [--scaffold] [--url URL]` — Audit `artifacts/` checksums and scaffold missing source stubs.
   - `genealogy add-link <SOURCE_ID> <URL>` — Auto-parse and attach Ancestry, FamilySearch, Newspapers.com, Find a Grave, Fold3, LoC, NARA, or WikiTree URLs to a source file.
   - `genealogy stats` — Summarize individuals, sources, relationships, and unlinked artifacts.
   - `genealogy build-db` / `genealogy query "<text>" --fts` — Compile and search SQLite + FTS5.
   - `genealogy timeline <ID> --include-family` — Generate chronological life timelines.
   - `genealogy tree [<ID>] --format mermaid` — Render family graphs.
   - `genealogy view [--port 8765] [--out tree.html]` — Launch local interactive DAG viewer (or export standalone HTML) with pedigree-collapse & kinship-path highlighting, land-tract lens, era/census slider, SVG export, copy citation, Ancestry/FamilySearch links, and inline artifact preview.
   - `genealogy kinship <ID1> <ID2>` / `genealogy collapse <ID>` — Trace all distinct blood paths, Wright's $r$ / $F$, and pedigree collapse across inosculated families.
   - `genealogy constraints [--person ID] [--status STATUS]` — Evaluate deductive relationship constraints (e.g., marriage dispensations) to identify common ancestors, narrow candidates, and flag identity overlaps or contradictions.
   - `genealogy tracts [--tract FILTER]` — Display chronological chain-of-title tables grouped by land tract.
   - `genealogy dna <ID1> <ID2> [--observed-cm CM]` — Endogamy-aware autosomal DNA expected shared cM corroboration across all documented blood paths.
   - `genealogy gaps` — Audit missing U.S. Federal Censuses (1790–1950, excluding 1890), promotion-ready profiles, discrepancies, consanguineous marriages, and excluded leads.
   - `genealogy report <ID>` — Generate a shareable Markdown Family Group Sheet & Research Dossier.
   - `genealogy import-gedcom <file>` / `genealogy export-gedcom <out>` — GEDCOM 5.5.1 / 7.0 pipelines.
   - `genealogy search newspapers|nara|wikitree ...` — Query open archival APIs (always treat WikiTree results as secondary leads).
   - `genealogy upgrade-workspace` — Refresh managed `AGENTS.md`, skills, and hooks after running `uv tool upgrade genealogy-workspace`.

   > **Sandbox note:** If `genealogy` is not found in the terminal sandbox (for example, `uv tool install` placed it in `~/.local/bin`, which is outside the sandbox), run `genealogy` commands outside the sandbox (`BypassSandbox: true`) or ask the user to grant read-only access to `~/.local/bin/genealogy` and its `uv` tool/Python directories in Project-Level Settings.
