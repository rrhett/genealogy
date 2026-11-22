---
name: wrap-up
description: >-
  End-of-session wrap-up workflow. Validates data/, updates
  workspace/HYPOTHESES.md, NEGATIVE_SEARCHES.md, and TODO.md, checks git
  status, and gives a concise session briefing with a suggested commit message.
---

# Session Wrap-Up (`/wrap-up`)

Use this workflow at the end of a research session to leave the repository clean, validated, and ready for the next session.

## Procedure

### 1. Validate Dataset
- Run `genealogy validate` (and `genealogy build-db` if `build/genealogy.sqlite` is present).
- Fix any schema, referential, biological, or hash errors before proceeding.

### 2. Update `workspace/` Scratchpads
Review what happened during this session and update:
- `workspace/HYPOTHESES.md` — Add new theories, mark resolved hypotheses, and note any new discrepancies discovered.
- `workspace/NEGATIVE_SEARCHES.md` — Log any searches that returned no results (repository/collection, search terms, date range, and date searched) so they are not repeated.
- `workspace/TODO.md` — Update with the next 3–5 concrete lookups or tasks.

### 3. Check Git Status
- Run `git status --short` to review modified and untracked files (including any new scans or notes the user added to `artifacts/` or `notes/`).
- If any unlinked files remain in `artifacts/`, mention them in the briefing (e.g., ready for `/ingest` next session).

### 4. Deliver Session Briefing
Give the user a concise summary covering:
- **Added / Updated:** New or modified sources, individuals, and relationships.
- **Key Findings & Open Questions:** What was proved, what conflicts remain, and any new leads.
- **Next Up:** Top priorities in `workspace/TODO.md`.
- **Suggested Commit:** A ready-to-run `git add -A && git commit -m "..."` command summarizing the session.
- **Fresh Chat Reminder:** End with a one-line note that everything is safely saved to `data/` and `workspace/`, and suggest starting a **new chat** (with `/status`) for the next session or family branch to save quota and keep analysis sharp.
