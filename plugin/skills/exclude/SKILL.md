---
name: exclude
description: >-
  Marks a scan or source record as a mistaken lead (unrelated same-name person)
  without deleting the file from artifacts/. Sets relevance to excluded, records
  excluded_for and exclusion_reason, removes accidental tree links, and validates.
---

# Exclude Mistaken Lead (`/exclude`)

Use this workflow when a scan or record turns out to belong to a different same-name person rather than the target individual.

## Hard Rule
- **Never delete or move the file from `artifacts/`.** Keeping it lets `genealogy intake` warn automatically via SHA-256 if the same false hint is downloaded again later.

## Procedure

### 1. Confirm Target & Rationale
If not already clear from the user's message, briefly confirm:
- Which source ID (`data/sources/<SOURCE_ID>.md`) or artifact path is being excluded.
- Which individual ID(s) (`I-...`) it was ruled out for, and why.

### 2. Update the Source Record
Edit `data/sources/<SOURCE_ID>.md`:
- Set `relevance: excluded`.
- Add the ruled-out individual ID(s) to `excluded_for: [<Individual ID>]`.
- Write a clear `exclusion_reason: "..."` explaining the disambiguating evidence (see `identity-disambiguation` skill if needed).
- Remove the ruled-out individual(s) from `individuals_cited:` if they were mistakenly linked.

### 3. Clean Accidental Links
- Remove any accidental citations to `<SOURCE_ID>` from `data/individuals/` and `data/relationships.yaml` (and remove or revert any tentative profile/relationship created solely from this mistaken lead).
- Optionally note the ruled-out record in `workspace/NEGATIVE_SEARCHES.md` or `workspace/HYPOTHESES.md`.

### 4. Validate
- Run `genealogy validate` to confirm referential integrity and excluded-lead guardrails pass.
