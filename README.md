# Evidence-Based Genealogy Research Workspace

A clone-and-go, provenance-first genealogy research repository built for AI coding agents (such as Antigravity) and evidence-driven family historians. It combines strict **Genealogical Proof Standard (GPS)** guardrails, domain-specific agent skills, automated schema/graph/biological validation, SQLite + FTS5 search, GEDCOM 5.5.1/7.0 pipelines, and open archival API search tools.

---

## Quick Start

### 1. Clone & Install (`uv`)
```bash
git clone <your-repo-url> my-family-history
cd my-family-history
uv sync
```
*(Works with standard `pip install -e .` too, using Python 3.11+.)*

### 2. Try the Worked Example (`examples/west-family/`)
Your top-level `data/`, `artifacts/`, `notes/`, and `workspace/` folders ship **clean and empty** so you can start your own family tree immediately. To explore every feature first, point the CLI at the bundled example dataset:

```bash
# Validate schema, links, artifact SHA-256 hashes, biology, DAG & GPS rules
uv run genealogy validate --root examples/west-family --strict

# Inspect repository counts and artifact manifest
uv run genealogy stats --root examples/west-family
uv run genealogy intake --root examples/west-family

# Search across bios and verbatim transcriptions (SQLite FTS5)
uv run genealogy query "Cocke County" --fts --root examples/west-family

# Generate a chronological life timeline, Family Group Sheet & Mermaid graph
uv run genealogy timeline I-WEST-CLAUDE-1886 --include-family --root examples/west-family
uv run genealogy report I-WEST-CLAUDE-1886 --root examples/west-family
uv run genealogy tree --format mermaid --root examples/west-family

# Analyze inosculation / pedigree collapse & research gaps
uv run genealogy kinship I-WEST-CLAUDE-1886 I-RAINWATER-OCTA-1890 --root examples/west-family
uv run genealogy collapse I-WEST-EDGAR-1909 --root examples/west-family
uv run genealogy gaps --root examples/west-family

# Launch the interactive DAG tree viewer in your browser (or export standalone HTML!)
uv run genealogy view --root examples/west-family
uv run genealogy view --root examples/west-family --out build/west-family.html

# Export GEDCOM 7.0 (living individuals are automatically redacted by default!)
uv run genealogy export-gedcom build/west-family.ged --root examples/west-family
```

---

## Repository Structure

```text
genealogy/
├── AGENTS.md                        # Canonical AI rules, data schema & GPS workflow
├── README.md                        # Setup & onboarding guide (this file)
├── pyproject.toml                   # uv/Python package & `genealogy` CLI
├── .pre-commit-config.yaml          # Optional local git pre-commit validation hook
├── .github/workflows/validate.yml   # GitHub Actions CI (tests, lint, data validation)
├── .agents/
│   ├── hooks.json                   # Antigravity hooks (boundary guard + stop validator)
│   ├── hooks/                       # Hook scripts enforcing read-only & validation rules
│   └── skills/                      # On-demand genealogical playbooks & reference guides
│       ├── artifact-transcription/  # Verbatim OCR/transcription + U.S. Census column guide
│       ├── gps-proof-argument/      # GPS workflow + Evidence Analysis Map
│       ├── identity-disambiguation/ # Same-name separation & FAN Club analysis
│       ├── jurisdiction-history/    # County formation, boundaries & burnt-county checks
│       ├── research-planning/       # Brick walls, negative search logs & open APIs
│       └── gedcom-triage/           # Safe staging & vetting of imported GEDCOM files
├── artifacts/                       # READ-ONLY: Your raw scans, PDFs, census images
├── notes/                           # HUMAN-OWNED: Your personal logs & family lore
├── workspace/                       # AGENT-OWNED: Hypotheses, negative searches, staging
├── data/                            # CANONICAL DATA: Structured Markdown/YAML truth
│   ├── individuals/                 # One file per person (I-SURNAME-GIVEN-YEAR.md)
│   ├── sources/                     # One citation & verbatim transcription per record
│   └── relationships.yaml           # Explicit graph edges (parent_child, marriage, FAN)
├── examples/west-family/            # Complete worked example + sample.ged
├── src/genealogy/                   # CLI, validator, intake, SQLite, GEDCOM & API clients
└── tests/                           # Unit, hook, and end-to-end tests
```

---

## Starting Your Own Research

### Option A: Starting Fresh
1. Drop downloaded record scans (census pages, birth/death certificates, draft cards, obituaries) into `artifacts/`.
2. Drop any existing notes or oral history into `notes/`.
3. Run `uv run genealogy intake` (or prompt your AI agent):
   ```text
   Read AGENTS.md. Run `uv run genealogy intake`, review artifacts/ and notes/, transcribe the primary records into data/sources/ using the artifact-transcription skill, create initial profiles in data/individuals/, validate with `uv run genealogy validate`, and outline next steps in workspace/TODO.md.
   ```

### Option B: Importing an Existing Tree (Ancestry, FamilySearch, Gramps, RootsMagic)
Because Ancestry has no public API and FamilySearch restricts production API keys for personal scripts, the cleanest way to bring an existing tree into this repo is via **GEDCOM export**:

```bash
# Imports safely into workspace/staging/ (never overwrites data/ directly)
uv run genealogy import-gedcom path/to/your-tree.ged
```
All staged individuals are tagged `status: tentative` and linked to a compiled GEDCOM source (`record_type: compiled`, `source_type: authored`). Use the bundled **`gedcom-triage`** skill to audit and selectively promote verified people into `data/`.

### Option C: Migrating an Existing AI Chat Thread
1. Paste this prompt at the end of your existing genealogy chat to generate a lossless handoff:
   ```markdown
   Summarize our entire genealogy conversation into a dense, self-contained Markdown document suitable for saving to my local repository as `RESEARCH_HANDOFF.md`.

   Strip out conversational filler and superseded theories unless flagged as active hypotheses. Preserve exact spelling variants, dates, locations, and record citations with these sections:
   1. Research Objective & Scope
   2. Confirmed Individuals & Proven Facts
   3. Evidence Log & Source Citations (with verbatim transcriptions)
   4. Analytical Conclusions & Disambiguation (including negative searches)
   5. Active Hypotheses & Unresolved Questions
   6. Next Immediate Research Steps

   Output inside a single raw Markdown code block.
   ```
2. Save the output as `RESEARCH_HANDOFF.md` at the repo root, drop any scans in `artifacts/`, and ask your agent:
   ```text
   Read AGENTS.md and RESEARCH_HANDOFF.md. Ingest confirmed individuals, sources, and relationships into data/, log open questions in workspace/HYPOTHESES.md, run `uv run genealogy validate`, and list any missing source citations before archiving RESEARCH_HANDOFF.md.
   ```

---

## CLI Reference (`uv run genealogy ...`)

| Command | Purpose |
| :--- | :--- |
| `genealogy validate [--root DIR] [--strict] [--json]` | Validates YAML schemas, ID/filename rules, referential integrity, artifact existence & SHA-256 hashes, `parent_child` DAG acyclicity, biological/chronological sanity, GPS proof standards, and living-person consistency. |
| `genealogy intake [--root DIR] [--scaffold] [--dry-run]` | Scans `artifacts/`, computes SHA-256 checksums, detects duplicate downloads, lists unlinked files, and optionally scaffolds starter `data/sources/` stubs. |
| `genealogy stats [--root DIR] [--json]` | Displays dataset counts (individuals by status/living, sources by type, relationships, linked vs. unlinked artifacts). |
| `genealogy build-db [--root DIR] [--out PATH]` | Compiles `data/` into `build/genealogy.sqlite` with normalized tables and an FTS5 full-text search index over bios and verbatim transcriptions. |
| `genealogy query "<query>" [--fts] [--root DIR]` | Runs FTS5 full-text search (`--fts`) or read-only SQL `SELECT` queries directly against your Markdown dataset. |
| `genealogy timeline <ID> [--include-family]` | Generates a chronological Markdown timeline (with estimated ages, land tracts, and optional interleaved family events). |
| `genealogy tree [<ID>] [--format mermaid\|dot] [--depth N]` | Renders a Mermaid flowchart or Graphviz DOT graph of your family relationships. |
| `genealogy view [--port 8765] [--out PATH] [--include-living]` | Launches a local interactive **DAG + Marriage-Hub** web viewer built for inosculated/endogamous families (multi-path kinship inspector, pedigree-collapse badges, Ancestry/FamilySearch links, inline artifact previews, excluded leads, and live reload)—or exports a single offline HTML file via `--out`. |
| `genealogy kinship <ID1> <ID2> [--json]` | Traces every distinct blood path between two individuals and computes Wright's coefficient of relationship ($r$) and expected offspring inbreeding ($F$). |
| `genealogy collapse <ID> [--max-gen N] [--json]` | Reports per-generation pedigree collapse, multi-path ancestors (`×2`, `×3`…), and Wright's inbreeding coefficient ($F$). |
| `genealogy gaps [--json]` | Audits missing U.S. Federal Censuses (1790–1950, skipping 1890), promotion-ready `tentative` profiles, recorded discrepancies, consanguineous marriages, and excluded leads. |
| `genealogy report <ID> [--out PATH] [--include-living]` | Generates a shareable Markdown Family Group Sheet & Research Dossier with numbered citations, provenance links, excluded leads, and living-person redaction. |
| `genealogy import-gedcom <file> [--dest staging\|data]` | Imports GEDCOM 5.5.1 or 7.0 into `workspace/staging/` (default) or `data/`, preserving GPS provenance. |
| `genealogy export-gedcom <out> [--version 7.0\|5.5.1] [--include-living]` | Exports `data/` to GEDCOM 7.0 (default) or 5.5.1. **Living individuals (`living: true`) are automatically redacted by default.** |
| `genealogy search newspapers "<query>" [--state S] [--start-year Y] [--end-year Y]` | Searches Library of Congress Chronicling America historic U.S. newspapers (1770–1963) via the unified `loc.gov` JSON API (no API key needed). |
| `genealogy search nara "<query>" [--record-group RG]` | Searches the National Archives (NARA) Catalog API v2 (requires free `NARA_API_KEY` env var from `Catalog_API@nara.gov`). |
| `genealogy search wikitree [--first F] [--last L] [--profile ID] [--ancestors ID]` | Queries WikiTree's public read-only API for secondary/compiled research leads. |

---

## Automated Guardrails

* **Antigravity Hooks (`.agents/hooks.json`):**
  * **`protect-boundaries` (`PreToolUse`):** Mechanically blocks edits/deletions inside `artifacts/` and prompts for confirmation before touching `notes/`.
  * **`validate-data-on-stop` (`Stop`):** Automatically runs `genealogy validate` once the agent finishes editing `data/` and asks it to resolve any schema or referential errors before stopping. (Skips instantly when `data/` is empty.)
* **Git Pre-Commit (`.pre-commit-config.yaml`):** Run `pre-commit install` to validate `data/` automatically on every commit.
* **GitHub Actions (`.github/workflows/validate.yml`):** Automatically runs linting, tests, and dataset validation on push and pull requests.

---

## Development & Testing

```bash
uv run ruff check src tests .agents/hooks
uv run pytest
```
