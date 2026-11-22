---
name: feedback
description: >-
  Ingest a user's TOOLKIT_FEEDBACK.md file in this repository, analyze the reported
  gaps and workarounds against the genealogy toolkit and plugin, propose toolkit and
  prompt/skill updates for approval, implement approved changes with verification,
  and offer to delete TOOLKIT_FEEDBACK.md once incorporated. Use when the user runs
  /feedback or asks to process TOOLKIT_FEEDBACK.md.
---

# Toolkit Feedback Ingestion

Process a `TOOLKIT_FEEDBACK.md` file dropped into this repository, turn actionable
items into concrete toolkit/plugin improvements, and clean up the feedback file
once handled.

## Steps

### 1. Locate and Read the Feedback File
1. Check for `TOOLKIT_FEEDBACK.md` at the repository root (`/home/rrhett/genealogy/TOOLKIT_FEEDBACK.md`) or `workspace/TOOLKIT_FEEDBACK.md` (or a path explicitly provided by the user).
   - If none is found, tell the user and ask where the feedback file is located or to paste its contents.
2. Read the file and perform a quick **privacy sanity check**: if any personal names, dates, places, or private citations appear to have leaked in, flag them immediately and avoid quoting them back in commits or code comments.

### 2. Analyze Against the Codebase
For each feedback item in `TOOLKIT_FEEDBACK.md`:
1. **Cross-reference the repo** (`src/genealogy/`, `plugin/rules/AGENTS.md`, `plugin/skills/`, `plugin/hooks/`, `examples/west-family/`, `tests/`):
   - Is it already supported (and just undocumented or missed by the researcher agent)?
   - Does it require a schema/validator update, a new or updated CLI command, or an import/export change?
   - Does it require updating `plugin/rules/AGENTS.md` or a skill in `plugin/skills/` (including new or refined agent prompts/instructions)?
2. **Group related items** that point to the same underlying gap.

### 3. Present Analysis & Proposed Updates (Wait for Approval)
Present a concise, structured summary to the user before editing any files:
- **Item-by-item breakdown**:
  - *Reported gap / workaround*
  - *Assessment* (new capability needed, bug/edge case, already supported, or out of scope/defer)
  - *Proposed toolkit changes* (files, schema/CLI/tests)
  - *Suggested prompts / plugin guidance* (any additions or wording updates for `plugin/rules/AGENTS.md` or `plugin/skills/` so researcher agents use the new feature or avoid the old workaround)
- Ask the user which items they approve for implementation (all, a subset, or with adjustments). **Do not implement changes until approved.**

### 4. Implement Approved Changes & Verify
1. Apply the approved changes across `src/genealogy/`, `plugin/`, `examples/` (if relevant), and `tests/`.
2. Run the repository's verification suite:
   ```bash
   uv run ruff check src tests plugin/hooks
   uv run pytest -v
   uv run genealogy validate --root examples/west-family --strict
   agy plugin validate plugin --strict
   ```
3. Report the exact changes made and verification results.

### 5. Clean Up `TOOLKIT_FEEDBACK.md`
Once the approved updates are implemented and verified:
- If **all** items were incorporated (or intentionally dismissed), **offer to delete `TOOLKIT_FEEDBACK.md`** (do not delete without confirming).
- If **some** items were deferred (e.g., to be filed as external issues), offer either to delete `TOOLKIT_FEEDBACK.md` now or to strip out only the incorporated items so the deferred ones remain for issue filing.
