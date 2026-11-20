---
name: identity-disambiguation
description: >-
  Separates or merges homonymous (same-name) individuals in the same time and
  jurisdiction using FAN Club (Friends, Associates, Neighbors) analysis,
  timeline overlap matrices, and signature/witness comparison. Activate when two
  or more records share an identical or similar name and it is unclear whether
  they represent one person or multiple distinct individuals.
---

# Identity Disambiguation & FAN Club Analysis

Use this workflow whenever two or more records mention the same (or very similar) name in the same county or era and you must prove whether they refer to **one individual (merge)** or **multiple distinct individuals (split)**.

> **Golden Rule:** Never assume two same-name records belong to the same person—and never assume a middle initial or 2-year age drift proves two people are different—without correlating timelines, locations, kin, and associates.

## Step-by-Step Procedure

### 1. Gather Every Record Touching the Name
Build/refresh the local search index and pull every existing record, timeline, and family link:

```bash
uv run genealogy build-db
uv run genealogy query "<SURNAME>" --fts
uv run genealogy timeline <INDIVIDUAL_ID> --include-family
```

Also inspect `notes/` and `workspace/HYPOTHESES.md` for informal leads or known cousins/uncles sharing the name.

### 2. Build a Side-by-Side Comparison & Timeline Overlap Matrix
In `workspace/disambiguation_<surname>_<given>.md` (or a dedicated section of `workspace/HYPOTHESES.md`), list **every single record** chronologically and test for physical impossibilities and consistent clusters:

| Record / Source ID | Date | Jurisdiction / District | Age / Implied Birth | Spouse / Household | Occupation / Property | Signature vs. Mark | FAN Club (Neighbors, Witnesses, Bondsmen, Executors) | Candidate Cluster |
|---|---|---|---|---|---|---|---|---|
| `S-1880-CENSUS-...` | `1880-06-10` | Civil Dist. 4, Cocke Co., TN | 22 (b. ~1858) | Wife: Sarah; child: Mary | Farmer | — | Neighbors: Rainwater, Stokely | **A** |
| `S-1880-CENSUS-...` | `1880-06-14` | Civil Dist. 9, Cocke Co., TN | 24 (b. ~1856) | Wife: Martha | Blacksmith | — | Neighbors: Huff, Blazer | **B** |
| `S-1884-DEED-...` | `1884-03-12` | Civil Dist. 4, Cocke Co., TN | — | Dower: Sarah | 80 acres, French Broad | `[signed]` | Witness: J. Rainwater | **A** |

### 3. Apply High-Signal Disambiguation Tests

Evaluate these five discriminator categories before deciding to merge or split:

1. **Simultaneous Appearance (Hard Split Proof):** Two households enumerated in the same census year (check dwelling/ED/date to rule out double enumeration after a mid-summer move), or two deeds/vital events occurring in incompatible locations on the same day.
2. **Spouses, Children & Kin Networks:** Compare spouse given names, children's birth spacing and names, and parents' birthplaces (1880+ censuses). Watch for remarriages—check 1910 `M1`/`M2` and 1930 age-at-first-marriage columns before splitting!
3. **Signatures vs. Marks:** Compare handwriting on original scans (wills, deeds, marriage bonds, draft cards, pension affidavits). A literate man who signs fluently (`[signed]`) in 1884 is unlikely to be the contemporary namesake making `his [X] mark` in 1885 (barring documented illness/injury in old age).
4. **Land & Tax Continuity:** Track exact tract descriptions (metes and bounds, watercourse, acreage, adjoining landowners) and poll/property tax lists year by year. Land rarely moves even when county lines do.
5. **FAN Club (Friends, Associates, Neighbors):** Trace the recurring orbit of non-immediate associates across records:
   - Marriage bondsmen and officiating ministers/justices
   - Deed witnesses and adjoining landowners
   - Estate administrators, executors, appraisers, and estate-sale buyers
   - Baptismal godparents/sponsors and church pews
   - Military unit comrades and pension affidavit witnesses
   - Record persistent associates in `data/relationships.yaml` using `type: fan_associate` with clear `notes` explaining the shared record(s).

### 4. Decide: Merge, Split, or Hold Pending Evidence

- **Hold (`tentative` / `hypothetical`):** If evidence is still ambiguous, do **not** prematurely merge records into one `data/individuals/` file or proliferate speculative duplicates. Keep the open conflict in `workspace/HYPOTHESES.md` and plan targeted tie-breaker searches (see `research-planning` skill).
- **Flag Ruled-Out / Mistaken-Lead Artifacts (`relevance: excluded`):** When an ingested scan turns out to belong to an unrelated same-name individual, **never delete the file from `artifacts/`**. Keep its source record in `data/sources/` with `relevance: excluded`, `excluded_for: [<TARGET_ID>]`, `exclusion_reason: "<concise disambiguation proof>"`, and `individuals_cited: []`. That preserves the SHA-256 fingerprint so `uv run genealogy intake` immediately warns if the same false lead is downloaded again. If you choose to track an unrelated namesake household separately, mark those profiles `status: excluded` so they stay out of canonical trees and GEDCOM exports.
- **Merge (Same Person):** Consolidate all sources into one `data/individuals/<ID>.md`, preserve all spelling/middle-initial/age variations in `name_variants` and `## Recorded Discrepancies`, and remove any obsolete duplicate ID references.
- **Split (Proven Distinct Individuals):**
  - If their birth years differ, their standard deterministic IDs already distinguish them (`I-WEST-JOHN-1856` vs. `I-WEST-JOHN-1858`).
  - **Only when two proven distinct individuals share the exact same surname, given name, AND birth year (or both `UNKNOWN`)**, assign `-01` and `-02` suffixes per `AGENTS.md`:
    - `I-WEST-JOHN-1858-01`
    - `I-WEST-JOHN-1858-02`
  - Document the disambiguation proof explicitly in both profiles' `## Biographical Summary` / `## Recorded Discrepancies` sections and in `workspace/`.
  - Re-verify every `individuals_cited` list in `data/sources/` and every edge in `data/relationships.yaml` so no record remains attached to the wrong namesake.

### 5. Verify Graph & Integrity
After any merge, split, or `fan_associate` update in `data/`, run:

```bash
uv run genealogy validate
uv run genealogy tree <INDIVIDUAL_ID> --format mermaid
```

Confirm there are no dangling IDs, biological impossibilities (`CHILD_BEFORE_PARENT`, `EVENT_AFTER_DEATH`), or one-way source links.
