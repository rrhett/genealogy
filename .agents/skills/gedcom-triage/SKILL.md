---
name: gedcom-triage
description: >-
  Safely imports GEDCOM 5.5.1 and 7.0 exports into workspace/staging/, audits
  staged individuals, sources, and relationships for unsourced compiled claims,
  chronological or biological impossibilities, duplicates, and living-person
  privacy, and selectively promotes verified records into data/. Activate when
  the user provides a .ged file or asks to import, vet, or merge a GEDCOM tree.
---

# GEDCOM Staging & Triage Workflow

Never import an external GEDCOM straight into canonical `data/`. Online trees (Ancestry, FamilySearch, WikiTree, MyHeritage) frequently blend solid research with unsourced conflations, impossible lifespans, and unredacted living people. Always stage, audit, and selectively promote.

## Step 1: Import Safely into `workspace/staging/`
Run `import-gedcom` with its default staging destination (do **not** pass `--dest data` unless explicitly instructed for an already-vetted dataset):

```bash
uv run genealogy import-gedcom <path/to/export.ged>
```

This creates an isolated staged dataset at:
`workspace/staging/gedcom_<filename_stem>/data/`

What the importer does automatically:
- Creates a single compiled source file `S-<YEAR>-COMPILED-GEDCOM-<SLUG>.md` (`record_type: compiled`, `source_type: authored`).
- Sets every staged individual to `status: tentative` and every staged relationship to `confidence: speculative`.
- Flags unparseable GEDCOM dates as warnings—inspect every warning printed by the command.

## Step 2: Run Automated Validation on the Staged Dataset
Because the staged folder mirrors the canonical `data/` layout, run the repository validator directly against it to surface chronological, biological, and structural red flags immediately:

```bash
uv run genealogy validate --root workspace/staging/gedcom_<slug>
uv run genealogy stats --root workspace/staging/gedcom_<slug>
```

Inspect every flagged issue in the staged dataset:
- `DEATH_BEFORE_BIRTH`, `IMPLAUSIBLE_LIFESPAN` (>115 years)
- `CHILD_BEFORE_PARENT`, `PARENT_TOO_YOUNG` (<12), `MOTHER_AGE_IMPLAUSIBLE` (>55), `CHILD_AFTER_PARENT_DEATH`
- `MARRIAGE_BEFORE_BIRTH`, `MARRIAGE_TOO_YOUNG`, `MARRIAGE_AFTER_DEATH`
- `EVENT_BEFORE_BIRTH`, `EVENT_AFTER_DEATH`, `ANCESTRY_CYCLE`

## Step 3: Triage Staged Records Against Existing `data/`
Build a triage worksheet in `workspace/staging/gedcom_<slug>/TRIAGE.md` (or `workspace/HYPOTHESES.md`) and compare staged entries against existing canonical records (`uv run genealogy build-db` + `uv run genealogy query "<surname>" --fts`).

Check every staged individual and relationship for five risk classes:

1. **Living-Person Privacy Audit (CRITICAL):**
   - `import-gedcom` defaults `living: false` unless otherwise updated.
   - Any person born within the last **~110 years** with **no death date** MUST be audited and marked `living: true` if potentially living before promotion to `data/`.
2. **Duplicate & Collision Check:**
   - Match staged individuals against existing `data/individuals/` by name variants, birth window, place, and relatives—not just ID string (a 1-year birth difference produces a different ID like `I-WEST-CLAUDE-1886` vs. `I-WEST-CLAUDE-1887`).
   - Run the `identity-disambiguation` skill whenever a match is uncertain.
3. **Source Depth Check (Compiled vs. Primary):**
   - Remember that a GEDCOM export itself is `record_type: compiled` / `source_type: authored`.
   - Separate records that merely cite the GEDCOM file from claims backed by real primary artifacts in `artifacts/` or verified original sources in `data/sources/`.
   - Never promote an individual to `status: confirmed` or a relationship to `confidence: proven` solely on GEDCOM authority.
4. **Conflation & Chronology Smell Tests:**
   - Watch for common GEDCOM errors: parents and children with identical names merged together, spouses attached to the wrong generation, birthplaces using counties that did not exist yet (check with `jurisdiction-history`), and children born in impossible geographic jumps.
5. **Preserve Discrepancies:**
   - Where a staged GEDCOM conflicts with an existing record in `data/`, do **not** overwrite canonical data; log the GEDCOM's differing assertion under `## Recorded Discrepancies` or in `workspace/HYPOTHESES.md` as an authored lead to test.

## Step 4: Categorize Each Staged Entity
Assign every staged record one of three dispositions in your triage log:

| Disposition | Criteria | Action |
|---|---|---|
| **Promote as Lead (`tentative` / `speculative`)** | Plausible chronology, no duplicates, living flag audited, useful structural scaffold or user-approved import. | Copy/merge into `data/individuals/`, `data/sources/` (including the `S-...-COMPILED-GEDCOM-...` source), and `data/relationships.yaml`, keeping `status: tentative` and `confidence: speculative`. |
| **Promote & Verify (`confirmed` / `proven`)** | Independently corroborated by >= 2 non-compiled/non-authored sources in `data/sources/` (meeting GPS). | Link the independent original sources, document conflict resolution per `gps-proof-argument`, then promote. |
| **Quarantine / Reject** | Biological impossibility, clear conflation, duplicate of existing person, or unverified myth. | Leave in `workspace/staging/` and document rejection rationale in `workspace/HYPOTHESES.md`. |

## Step 5: Validate Canonical `data/` After Promotion
After promoting any files or relationships into `data/`, always run full validation and rebuild the search index:

```bash
uv run genealogy validate
uv run genealogy build-db
uv run genealogy stats
```

Verify zero errors, zero unredacted living-with-death conflicts, and zero GPS warnings (`GPS_COMPILED_ONLY`, `GPS_PROVEN_REL_COMPILED_ONLY`).
