---
name: artifact-transcription
description: >-
  Transcribes raw genealogical artifacts (census scans, vital certificates,
  military cards, obituaries, land and probate images/PDFs) into structured
  source records in data/sources/. Activate when new files appear in artifacts/,
  when the user asks to transcribe or ingest a document, or when extracting
  verbatim evidence from primary or secondary scans.
---

# Artifact Transcription Workflow

Use this runbook whenever ingesting or transcribing scans from `artifacts/` into canonical source files under `data/sources/`.

## Hard Boundaries
- **Never modify, rename, move, or delete** any file in `artifacts/` (read-only).
- **Never silently correct** original spelling, phonetic variants, crossed-out text, or conflicting ages/dates. Record everything verbatim and log discrepancies separately.
- **Always capture the whole context:** full household, adjoining dwellings/families (FAN Club), marginalia, stamps, signatures, and informants.

## Step-by-Step Procedure

### 1. Audit Unlinked Artifacts & Capture SHA-256
Run intake first to identify unlinked files in `artifacts/` and obtain their exact 64-character lowercase SHA-256 digests:

```bash
genealogy intake
```

Optionally use `genealogy intake --scaffold` to generate starter source stubs, then complete them manually. Never guess or fabricate `artifact_sha256`.

### 2. Inspect the Artifact & Identify Record Structure
Read the image or PDF directly from `artifacts/` (via vision/file reading) and check `notes/` for any existing user context about the scan.

- If the artifact is a U.S. Federal Census page (1790–1950), consult [U.S. Federal Census Columns Reference](./references/us-census-columns.md) to verify official Census Day, column meanings, and special notations (e.g., 1900 children born/living, 1910 `M1`/`M2`, 1940 circled-`x` informant, 1950 sample lines).
- If the artifact is a Mexican or Hispanic church, civil, or colonial document (*Bautismo*, *Matrimonio*, *Defunción*, *Información/Dispensa Matrimonial*, *Registro Civil*, 1930 Mexican Census), consult [Mexican & Hispanic Records Reference](./references/mexican-hispanic-records.md) for double surnames, grandparents (*abuelos*), godparents (*padrinos*), Spanish/Latin abbreviations, and bilingual transcription rules.
- For non-English records, always record the **verbatim original-language transcription first** followed by a faithful English translation.
- Record sheet/page/folio/libro/acta number, parish or civil registry office, enumeration district (ED), dwelling/family/line numbers, record date, and clerk/priest/enumerator name when visible.

### 3. Transcribe Verbatim Using Standard Editorial Conventions
Draft tricky or faded transcriptions in `workspace/` first if needed, then write the finalized transcription into `data/sources/<SOURCE_ID>.md`.

Use these editorial markers consistently:
- `[illegible]` — text cannot be read due to damage, fade, or ink bleed.
- `[word?]` — tentative reading of unclear handwriting (e.g., `[Rainwater?]`).
- `[sic]` — unusual spelling or obvious clerk error preserved verbatim (e.g., `Claud [sic] West`).
- `~~struck~~` — crossed-out text still legible; follow with any interlined replacement in brackets `[inserted: ...]`.
- `[blank]` — column explicitly left empty (distinct from `do.` / `"` ditto marks, which should be transcribed as `do. [Tennessee]`).
- `[signed]` vs. `his [X] mark` — always distinguish an actual signature from a mark.

**FAN Club Capture:** On censuses, deeds, wills, and vital records, transcribe not only the target individual, but every household member, boarder, servant, witness, bondsman, executor, informant, officiating minister/justice, and at least 2–3 neighboring dwellings before and after the target household.

### 4. Classify Source & Information Quality
Assign `source_type` and `information_type` in YAML frontmatter using *Evidence Explained* standards (see `gps-proof-argument` skill for the full 3×3 matrix):

| Field | Allowed Values | How to Assign |
|---|---|---|
| `source_type` | `original` \| `derivative` \| `authored` | `original`: first recording of an event (or faithful digital/microfilm image of it). `derivative`: abstract, index, transcript, database entry, or later copy. `authored`: compiled tree, family history, county mugbook, GEDCOM. |
| `information_type` | `primary` \| `secondary` \| `mixed` \| `undetermined` | `primary`: informant was an eyewitness/participant at or near the event. `secondary`: hearsay or reported by someone without firsthand knowledge (e.g., birth info on a death certificate). `mixed`: contains both primary and secondary items (common on death certs and censuses). `undetermined`: informant is unknown (e.g., pre-1940 U.S. census without an informant marker). |

### 5. Write the Source File (`data/sources/<ID>.md`)
Follow the deterministic ID format `S-<YEAR>-<TYPE>-<SLUG>` (or `S-UNDATED-<TYPE>-<SLUG>`). Filename must match `id` exactly.

```yaml
---
id: S-1910-CENSUS-WEST-COCKE-TN
title: "1910 U.S. Federal Census, Cocke County, Tennessee"
record_type: census # census | vital | church | military | newspaper | cemetery | probate | land | immigration | compiled | oral | other
date: "1910-04-22" # YYYY, YYYY-MM, YYYY-MM-DD, qualified date, or "undated"
jurisdiction: "Civil District 4, Cocke County, Tennessee, USA"
repository: "National Archives and Records Administration (NARA), T624, roll 1494"
url: ""
links:
  - provider: ancestry # ancestry | familysearch | newspapers | findagrave | fold3 | nara | loc | wikitree | state_archive | personal_collection | other
    url: "https://www.ancestry.com/imageviewer/collections/7884/images/..."
    accessed: "2026-11-19"
    requires_auth: true
    note: "Online scan provenance (even if login-gated)"
artifact_path: "artifacts/1910_census_cocke_tn_p4a.png"
artifact_sha256: "<64-char-lowercase-hex-from-intake>"
source_type: original
information_type: undetermined
relevance: relevant # relevant | pending | excluded
excluded_for: []
exclusion_reason: ""
individuals_cited:
  - I-WEST-CLAUDE-1886
  - I-RAINWATER-OCTA-1890
---
```

Include these Markdown body sections:
1. **`## Formal Citation`** — *Evidence Explained* layered format:
   - *Layer 1 (Original record):* Jurisdiction, record group/series, date, volume/page/ED/sheet/dwelling/family/certificate number, named principal.
   - *Layer 2 (Image/provider, if digital):* Database/website title, URL or ARK, image number, access date.
   - *Layer 3 (Repository/film):* Archive, microfilm series/roll, or local artifact reference.
2. **`## Verbatim Transcription & Extracted Data`** — Line-by-line transcription preserving exact spellings, abbreviations, and editorial brackets.
3. **`## FAN Club & Contextual Notes`** — Neighboring households, witnesses, informants, clerk notes, and any legibility cautions.

### 6. Cross-Reference & Handle Conflicts
- Link the new `S-...` ID in relevant `data/individuals/<ID>.md` files (under `birth.sources`, `death.sources`, or `events[].sources`) and in `data/relationships.yaml` so links are bidirectional (avoiding `ONE_WAY_SOURCE_LINK` warnings).
- Add newly observed spellings to `name_variants` on the individual profile.
- **Do not overwrite confirmed facts** when a new artifact disagrees with existing data. Instead:
  1. Record the conflict verbatim under `## Recorded Discrepancies` in the individual's file.
  2. Log an investigation item in `workspace/HYPOTHESES.md` for resolution via the `gps-proof-argument` skill.

### 7. Validate
Run schema, referential integrity, SHA-256, and GPS checks after every edit to `data/`:

```bash
genealogy validate
```

Resolve all errors (and inspect any `ARTIFACT_HASH_MISMATCH`, `UNLINKED_ARTIFACT`, or `ONE_WAY_SOURCE_LINK` warnings) before finishing.
