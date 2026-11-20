---
name: gps-proof-argument
description: >-
  Applies the Genealogical Proof Standard (GPS) and Elizabeth Shown Mills's
  Evidence Analysis Process Map to evaluate evidence, resolve conflicting
  records, and write proof arguments or summaries. Activate before promoting
  any individual from tentative to confirmed, promoting any relationship from
  probable to proven, or resolving contradictory dates, places, or parentage.
---

# Genealogical Proof Standard (GPS) & Proof Arguments

Use this workflow before upgrading any individual to `status: confirmed` or any relationship to `confidence: proven`, and whenever two or more records conflict.

## The Five Elements of the Genealogical Proof Standard

A conclusion meets the GPS only when **all five** elements are satisfied:

1. **Reasonably Exhaustive Search:** All relevant, surviving record groups (vital, census, probate, land, church, military, newspaper, cemetery, FAN Club) for the target time and jurisdiction have been searched—and negative results logged in `workspace/NEGATIVE_SEARCHES.md`.
2. **Complete and Accurate Source Citations:** Every item of information traces to a fully cited source file in `data/sources/` (and linked `artifacts/` file with `artifact_sha256` where available), never to an unsourced tree or memory.
3. **Thorough Analysis and Correlation:** Every source, information item, and piece of evidence is evaluated independently using the [Evidence Analysis Map Reference](./references/evidence-analysis-map.md) and correlated side by side.
4. **Resolution of Conflicting Evidence:** Every discrepancy (age drift, birthplace mismatch, middle initial, spelling, competing parent candidates) is explicitly explained and outweighed—never silently averaged or ignored.
5. **Soundly Reasoned, Coherently Written Conclusion:** Written as a Proof Statement (straightforward, uncontested direct evidence), Proof Summary (multiple converging items), or full Proof Argument (complex, indirect, or conflicting evidence).

## Step-by-Step Procedure

### 1. Gather & Classify All Relevant Sources
Pull every source touching the claim using the repository CLI:

```bash
uv run genealogy timeline <INDIVIDUAL_ID> --include-family
uv run genealogy query "<SURNAME>" --fts
```

Classify each source item using the 3×3 framework in [Evidence Analysis Map Reference](./references/evidence-analysis-map.md) (Source type → Information type → Evidence type). Verify that `source_type` and `information_type` in `data/sources/<ID>.md` reflect the actual record.

> **Hard Gate:** Compiled/authored sources (`record_type: compiled` or `source_type: authored`, such as Ancestry trees, FamilySearch profiles, WikiTree, or GEDCOM imports) **never** count toward proof. `uv run genealogy validate` flags `GPS_COMPILED_ONLY` and `GPS_PROVEN_REL_COMPILED_ONLY` if proof relies on them.

### 2. Build an Evidence Correlation Matrix in `workspace/`
Create or update a working section in `workspace/HYPOTHESES.md` (or a dedicated `workspace/proof_<slug>.md` file for complex cases) using this table structure:

| Source ID | Record Date | Source Type | Information Type | Evidence Type | Claimed Fact (Verbatim) | Informant / Reliability Notes |
|---|---|---|---|---|---|---|
| `S-1900-CENSUS-...` | `1900-06-08` | `original` | `undetermined` | `direct` | b. May 1886, TN | Recorded closest to birth; informant unknown |
| `S-1917-DRAFT-...` | `1917-06-05` | `original` | `primary` | `direct` | b. 14 May 1887, Cocke Co., TN | Self-reported & signed; year off by +1 |
| `S-1954-DEATH-...` | `1954-11-12` | `original` | `mixed` | `direct` | b. 14 May 1886, TN; parents John West & ... | Death info primary; birth/parent info secondary (informant: spouse) |

### 3. Resolve Conflicts Explicitly
For each conflicting data point:
- **Assess informant knowledge & proximity:** Who supplied the fact, how close in time was the record to the event, and what incentive existed to misstate it (e.g., underage marriage, WWI draft age brackets, pension eligibility, age rounding)?
- **Check independent corroboration:** Does a third independent record (e.g., earlier census, school roster, church baptism, estate settlement) break the tie?
- **Check jurisdictional shifts:** Before calling two county birthplaces a "conflict," run the `jurisdiction-history` skill to check whether county boundaries changed without the family moving.
- **Weigh negative evidence:** Absence of a person from a record where they *should* appear (e.g., omitted from a father's will listing all living heirs, or absent from a household between two censuses) can serve as strong indirect/negative evidence when documented rigorously.

### 4. Apply Promotion Gates

Do **not** promote status/confidence until the criteria below are met:

| Target Field | Permitted Value | Required Standard |
|---|---|---|
| Individual `status` | `hypothetical` | Working hypothesis only; unverified lead or compiled tree claim. |
| Individual `status` | `tentative` | At least 1 reliable source, OR unresolved conflicts remain, OR exhaustive search incomplete. |
| Individual `status` | `confirmed` | **>= 2 independent, non-compiled/non-authored sources** agreeing (or conflicts explicitly resolved in writing) + reasonably exhaustive search complete. |
| Relationship `confidence` | `speculative` | Hypothesis, FAN lead, or unsourced family lore. |
| Relationship `confidence` | `probable` | Strong single primary/original source or converging indirect evidence, but minor gap or single-source limitation remains. |
| Relationship `confidence` | `proven` | **>= 2 independent, non-compiled/non-authored sources** establishing the link, all conflicts resolved in writing, biological/chronological checks clean. |

### 5. Write the Proof Conclusion & Update `data/`
1. For complex or contested claims, keep the full multi-paragraph **Proof Argument** in `workspace/proof_<slug>.md` (or `workspace/HYPOTHESES.md`) with clear sections: *Research Question*, *Background & Methodology*, *Evidence Correlation*, *Conflict Resolution*, and *Conclusion*.
2. In `data/individuals/<ID>.md`:
   - Summarize the proven conclusion in `## Biographical Summary` with inline `[S-...]` citations.
   - Preserve every resolved and unresolved variant under `## Recorded Discrepancies`—**never erase discrepancies** after resolving them; instead append the resolution rationale.
   - Update `status` only if the promotion gate passes.
3. In `data/relationships.yaml`:
   - List all supporting `S-...` IDs in `sources`, update `confidence`, and record a concise proof summary in `notes`.

### 6. Validate Against Repository GPS Rules
Run validation to verify there are no schema, biological, or GPS warnings:

```bash
uv run genealogy validate
```

Confirm zero errors and zero `GPS_CONFIRMED_UNDER_SOURCED`, `GPS_COMPILED_ONLY`, `GPS_PROVEN_REL_UNDER_SOURCED`, or `GPS_PROVEN_REL_COMPILED_ONLY` warnings.
