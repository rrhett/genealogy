---
name: status
description: >-
  Quick session-start research briefing. Reads AGENTS.local.md and workspace/
  scratchpads, runs genealogy stats and genealogy gaps, and summarizes current
  progress, unlinked scans, and next steps.
---

# Research Status Briefing (`/status`)

Use this workflow at the start of a session (or anytime you need to orient yourself) to get a quick snapshot of the repository.

## Procedure

### 1. Read Local Context & Scratchpads
- Read `AGENTS.local.md` (if present) for focus surnames, regions, and privacy rules.
- Read `workspace/TODO.md` and `workspace/HYPOTHESES.md` (if present).

### 2. Run CLI Diagnostics
- Run `genealogy stats`.
- Run `genealogy gaps`.
- If `genealogy stats` reports unlinked artifacts, run `genealogy intake` to list them.

### 3. Present a Concise Dashboard
Summarize in a compact briefing:
- **Dataset Snapshot:** Counts of individuals (confirmed vs. tentative, living), sources, relationships, and any unlinked scans waiting in `artifacts/`.
- **Active Hypotheses & Brick Walls:** Top open items from `workspace/HYPOTHESES.md`.
- **Top Gaps & Next Steps:** Highest-priority missing records from `genealogy gaps` and `workspace/TODO.md`, plus a suggested next action (e.g., `/ingest` if unlinked scans are waiting).
