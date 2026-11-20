---
name: ingest
description: >-
  Everyday ingestion workflow for new scans in artifacts/ and new notes or
  handoff files. Runs genealogy intake, prompts for source URLs, transcribes
  verbatim using artifact-transcription, updates data/, and validates.
---

# Ingest New Scans & Notes (`/ingest`)

Use this workflow whenever the user drops new scans into `artifacts/`, adds notes in `notes/`, or wants to process `RESEARCH_HANDOFF.md`.

## Procedure

### 1. Audit Context & Unlinked Artifacts
- Read `AGENTS.local.md` (if present) for focus surnames, regional conventions, and privacy rules.
- Run `genealogy intake` to list unlinked files in `artifacts/` and check SHA-256 checksums against previously excluded leads (`relevance: excluded`).
  - If any file matches an excluded lead's SHA-256, alert the user immediately and skip re-transcribing it unless explicitly told otherwise.
- Check `notes/` for new user context and `RESEARCH_HANDOFF.md` if present.

### 2. Prompt for Missing Provenance URLs or Context
If new unlinked artifact(s) are found and the user has not already supplied source URLs or context in their message, ask concisely before transcribing:
- Do you have an Ancestry, FamilySearch, Newspapers.com, Find a Grave, or archival URL to attach to any of these scans? (If so, I'll record it in `links:` via `genealogy add-link` / `genealogy intake --url`.)
- Which person or family branch does it relate to (or is it a suspected mistaken lead)?

If the user already provided URLs/context or says to skip URLs, proceed immediately.

### 3. Transcribe Verbatim (`artifact-transcription`)
- Follow the `artifact-transcription` skill for each new scan:
  - Inspect the image or PDF directly from `artifacts/` (never modify or rename files in `artifacts/`).
  - Transcribe verbatim (original language first + English translation if Spanish/Latin, including *abuelos*, *padrinos*, witnesses, and neighboring FAN Club entries).
  - Create `data/sources/<SOURCE_ID>.md` with `artifact_sha256`, `source_type`, `information_type`, and any `links:`.

### 4. Cross-Reference & Update `data/`
- Match individuals, parents, grandparents, spouses, and witnesses against `data/individuals/` and `data/relationships.yaml`.
- Create or update profiles and relationships with inline source IDs (`[S-...]`).
- Never silently overwrite conflicting dates, ages, places, or spellings—preserve variants and record conflicts under `## Recorded Discrepancies`.
- Log unresolved questions or conflicting leads in `workspace/HYPOTHESES.md`.

### 5. Validate
- Run `genealogy validate` and fix any schema, hash, biological, or referential errors before finishing.
