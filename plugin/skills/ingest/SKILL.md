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
- Run `genealogy intake` to list unlinked files in `artifacts/`, check SHA-256 checksums against previously excluded leads (`relevance: excluded`), and automatically detect recent Screenshots/Downloads by timestamp (including any files newer than the latest file in `artifacts/`).
  - If `genealogy intake` lists suggested recent files from Screenshots or Downloads (either because `artifacts/` has no unlinked files, or because newer files were found during the current session), show the numbered candidates to the user and ask which file(s) to pull into `artifacts/` (e.g., "Would you like me to copy #1 and #2 into `artifacts/`?"). Once confirmed (or if the user already asked to grab their latest screenshot/download), run `genealogy intake --pick <numbers>` (or `genealogy grab-screenshot` / `genealogy grab-download`), then re-run `genealogy intake` to inspect the copied files.
  - If any file matches an excluded lead's SHA-256, alert the user immediately and skip re-transcribing it unless explicitly told otherwise.
- Check `notes/` for new user context and `RESEARCH_HANDOFF.md` if present.

### 2. Inspect Generic Scans & Prompt for URL, Context, and Rename
If new unlinked artifact(s) are found:
- If an unlinked artifact has a generic or timestamp-based filename (e.g., `Screenshot*.png`, `Screen Shot*.png`, `Capture*.png`, `IMG_*.jpg`), inspect the image first so you know what record it is, and propose a descriptive filename (e.g., `1920_census_west_cocke_tn.png`).
- Before transcribing or creating `data/sources/<SOURCE_ID>.md` (and before committing), ask the user concisely for any missing details:
  - **Rename suggestion (if generic filename):** Ask if they would like to rename `artifacts/<current_name>` to the suggested descriptive name (or a name they prefer) using `genealogy rename-artifact <current_path> <new_name>`.
  - **Provenance URL:** Do you have an Ancestry, FamilySearch, Newspapers.com, Find a Grave, or archival URL to attach to any of these scans? (If so, I'll record it in `links:` via `genealogy add-link` / `genealogy intake --url`.)
  - **Context:** Which person or family branch does it relate to (or is it a suspected mistaken lead)?

If the user approves the rename, run `genealogy rename-artifact` first so the canonical filename and `artifact_path` are clean before scaffolding or writing the source record. If the user already provided URLs/context or says to skip URLs/renaming, proceed immediately.

### 3. Transcribe Verbatim (`artifact-transcription`)
- Follow the `artifact-transcription` skill for each new scan:
  - Inspect the image or PDF directly from `artifacts/` (never modify file contents, and only rename generic/screenshot filenames with user confirmation via `genealogy rename-artifact` before linking/committing).
  - Transcribe verbatim (original language first + English translation if Spanish/Latin, including *abuelos*, *padrinos*, witnesses, and neighboring FAN Club entries).
  - Create `data/sources/<SOURCE_ID>.md` with `artifact_path` (using the renamed path if renamed), `artifact_sha256`, `source_type`, `information_type`, and any `links:`.

### 4. Cross-Reference & Update `data/`
- Match individuals, parents, grandparents, spouses, and witnesses against `data/individuals/` and `data/relationships.yaml`.
- Create or update profiles and relationships with inline source IDs (`[S-...]`).
- Never silently overwrite conflicting dates, ages, places, or spellings—preserve variants and record conflicts under `## Recorded Discrepancies`.
- Log unresolved questions or conflicting leads in `workspace/HYPOTHESES.md`.

### 5. Validate
- Run `genealogy validate` and fix any schema, hash, biological, or referential errors before finishing.
- **Session hygiene check:** If this is the 3rd+ `/ingest` in the current chat (or the conversation is getting long), append a brief one-line tip reminding the user that everything is saved in `data/` and suggesting `/wrap-up` followed by a **new chat** (with `/status`) before the next batch to keep transcription accuracy high and save quota.
