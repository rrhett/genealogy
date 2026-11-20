# Evidence-Based Genealogy Research Toolkit & Agent Plugin

An AI-assisted, provenance-first genealogy CLI toolkit, interactive DAG tree viewer, and Antigravity plugin. Built around the **Genealogical Proof Standard (GPS)**, it keeps **your private family-history repository 100% separate from this Python toolkit** so you can pull future skill, hook, viewer, and validator updates in two commands with zero merge conflicts.

---

## How the Architecture Works

Rather than cloning this Python codebase and mixing your private family archive into it, you keep **two cleanly separated pieces**:

1. **This Toolkit (`genealogy-workspace`)** — Installed once globally via `uv tool install`. Ships the `genealogy` CLI plus the bundled researcher `AGENTS.md`, 7 domain skills, and read-only lifecycle hooks (`plugin/`).
2. **Your Private Family Repo (`~/my-family-history`)** — Created with `genealogy init`. Contains **only** your family records (`data/`, `artifacts/`, `notes/`, `workspace/`), your local overrides (`AGENTS.local.md`), and synced agent rules/skills. Zero Python source code!

---

## Quick Start: Bootstrap Your Own Family-History Repo

### Step 1: Install `uv` (if you don't already have it)
[`uv`](https://docs.astral.sh/uv/) is a fast Python tool manager. Install it once:
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Step 2: Install the `genealogy` CLI Tool
`uv tool install` downloads this repository into an isolated environment and places the `genealogy` command on your `PATH` (no manual virtualenv activation needed):

```bash
uv tool install git+https://github.com/rrhett/genealogy.git
```
Verify it works from anywhere:
```bash
genealogy --version
```

### Step 3: Bootstrap a Fresh Private Git Repo for Your Family
Run `genealogy init` to create a clean, standalone Git repository pre-wired with `data/`, `artifacts/`, `notes/`, `workspace/`, `AGENTS.md`, `AGENTS.local.md`, skills, hooks, and CI:

```bash
# Standard setup (embeds AGENTS.md, skills & hooks; auto-detects region profiles per person)
genealogy init ~/my-family-history

# Optional: set explicit regional record profiles (auto, mexico, us, uk, canada, general)
genealogy init ~/my-family-history --regions mexico,us

# Optional: enable Git LFS for medium scan libraries (1–8 GB; GitHub Free includes 10 GiB/mo)
genealogy init ~/my-family-history --lfs

# Optional: store multi-GB scans outside Git (Dropbox/iCloud/NAS) while Git tracks SHA-256 hashes
genealogy init ~/my-family-history --artifacts-dir ~/Dropbox/FamilyScans
```

### Choosing Artifact Storage: Plain Git vs. Git LFS vs. External Folder

| Strategy | Best For | Tradeoffs |
| :--- | :--- | :--- |
| **Plain Git (`artifacts/` in repo)** | **< ~500 MB–1 GB** (e.g., hundreds of standard Ancestry/FamilySearch JPGs/PDFs) | Zero extra setup; single backup; stays well below GitHub's 100 MB per-file cap. |
| **Git LFS (`--lfs`)** | **1–8 GB** in one GitHub backup | GitHub Free includes **10 GiB storage + 10 GiB bandwidth/month** (up to 2 GB/file). Generated CI automatically passes `--skip-artifacts` so Actions doesn't burn LFS bandwidth; `genealogy validate` also verifies SHA-256 hashes directly from LFS pointers! |
| **External Folder (`--artifacts-dir`)** | **> 8 GB**, raw photo albums, or easy phone/Finder browsing via Dropbox/iCloud/Google Drive | Keeps the Git repo tiny (< 5 MB) forever while `data/sources/` keeps portable `artifacts/...` paths and SHA-256 integrity checks. You can move `artifacts/` out at any time by setting `artifacts_dir` in `.genealogy.yaml`. |

### International & Mexican Genealogy Support
This toolkit is designed for international and binational research—not just U.S. censuses:
* **Region-Aware Record Gaps (`regions: [auto]`):** Automatically detects each individual's region(s) from their places (or respects `regions:` in `.genealogy.yaml` / `genealogy gaps --regions mexico,us`). Mexican lines check **Catholic Parish Registers** (*Bautismos*, *Matrimonios*, *Defunciones*), ***Registro Civil*** (1859+), the **1930 Mexican National Census**, and flag consanguineous couples to search diocesan ***Dispensas Matrimoniales***—without spamming irrelevant U.S. census gaps!
* **Hispanic Double Surnames & Accents:** Individuals support optional `given_name`, `surname` (*apellido paterno*), and `maternal_surname` (*apellido materno*) with automatic ASCII ID transliteration (`José García López` → `I-GARCIA-JOSE-1870`) and accurate GEDCOM `Given /García López/` formatting.
* **Colonial-Era Viewer Range:** The interactive viewer's Era Slider adapts automatically to your dataset's date span (supporting 1600s–1960s colonial parish lineages) and applies census badges only for each individual's applicable regional census years.
* **Mexican & Hispanic Transcription Guide:** Bundled in `plugin/skills/artifact-transcription/references/mexican-hispanic-records.md` covering bilingual transcription, *abuelos*, *padrinos*, *dispensas matrimoniales*, and Spanish/Latin paleography abbreviations.

Commit your fresh workspace and connect it to a **private** GitHub (or GitLab) repository:
```bash
cd ~/my-family-history
git add -A
git commit -m "Initial genealogy workspace"
git remote add origin https://github.com/<you>/my-family-history.git
git push -u origin main
```

---

## Pulling Future Toolkit Updates (Zero Merge Conflicts)

Whenever new skills, validation checks, API integrations, or interactive viewer features are added to this toolkit, update your CLI and refresh your research repo in **two commands**:

```bash
# 1. Pull the latest CLI + bundled skills/hooks/rules from GitHub
uv tool upgrade genealogy-workspace

# 2. Refresh AGENTS.md and .agents/ inside your family repo
cd ~/my-family-history
genealogy upgrade-workspace --dry-run   # Optional: preview what will change
genealogy upgrade-workspace
```

**Safety guarantee:** `genealogy upgrade-workspace` updates *only* `AGENTS.md`, `.agents/skills/`, `.agents/hooks/`, and the version stamp in `.genealogy.yaml`. It **never** modifies `data/`, `artifacts/`, `notes/`, `workspace/`, `README.md`, or `AGENTS.local.md`. Put any family-specific rules, surname lists, or privacy notes in `AGENTS.local.md`.

### Optional: Native Antigravity Plugin Mode
For almost everyone, **default `genealogy init` (embedded per-workspace mode) is recommended**: it scopes `AGENTS.md`, skills, and hooks strictly to `~/my-family-history` so they never bleed into unrelated coding projects, while still upgrading cleanly via `genealogy upgrade-workspace`.

If you prefer loading `plugin/` directly as a native Antigravity plugin (with `genealogy init --plugin-only`), you have two options:

1. **Per-Workspace Plugin Pointer (Recommended if using plugin mode):**
   Clone this toolkit repo locally (e.g. `~/src/genealogy`), then create `~/my-family-history/.agents/plugins.json`:
   ```json
   {
     "entries": [
       { "path": "~/src/genealogy/plugin" }
     ]
   }
   ```
   Antigravity loads `genealogy-research` *only* when you open `~/my-family-history`.

2. **Global Plugin Install:**
   ```bash
   # From a local clone of this toolkit repo:
   agy plugin install ./plugin
   ```
   > **Important:** Because Antigravity loads a plugin's `rules/AGENTS.md` unconditionally whenever the plugin is enabled (unlike skills, which load on demand), toggle it off when working in unrelated coding projects:
   > ```bash
   > agy plugin disable genealogy-research   # when working on non-genealogy repos
   > agy plugin enable genealogy-research    # when doing genealogy research
   > ```
   > *(The boundary hook itself checks for `.genealogy.yaml` or `data/relationships.yaml`, so it will never block writes in non-genealogy repos.)*

---

## Starting Your Research (Inside `~/my-family-history`)

### Option A: Starting Fresh with Scans & Notes
1. Drop primary record scans (census pages, vital certificates, draft cards, deeds, obituaries) into `artifacts/`.
2. Drop any existing notes or family lore into `notes/`.
3. Open `~/my-family-history` in Antigravity (or your preferred AI coding IDE) and prompt:
   ```text
   Read AGENTS.md and AGENTS.local.md. Run `genealogy intake`, review artifacts/ and notes/, transcribe the primary records into data/sources/ using the artifact-transcription skill, create initial profiles in data/individuals/, validate with `genealogy validate`, and outline next steps in workspace/TODO.md.
   ```

### Option B: Importing an Existing Tree (Ancestry, FamilySearch, Gramps, RootsMagic)
Because Ancestry has no public API and FamilySearch restricts production API keys for personal scripts, export a GEDCOM (`.ged`) file and stage it safely:

```bash
# Imports into workspace/staging/ as tentative/speculative leads (never pollutes data/ directly)
genealogy import-gedcom ~/Downloads/my-tree.ged
```
Use the bundled **`gedcom-triage`** skill to audit staged records for living-person privacy, duplicates, and impossible dates before promoting verified individuals into `data/`.

### Option C: Migrating an Existing AI Chat Thread
1. Paste this prompt at the end of your existing genealogy chat to extract a lossless handoff:
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
2. Save it as `RESEARCH_HANDOFF.md` in `~/my-family-history`, drop scans in `artifacts/`, and ask your agent:
   ```text
   Read AGENTS.md and RESEARCH_HANDOFF.md. Ingest confirmed individuals, sources, and relationships into data/, log open questions in workspace/HYPOTHESES.md, run `genealogy validate`, and list any missing source citations before archiving RESEARCH_HANDOFF.md.
   ```

---

## CLI Reference (`genealogy ...`)

| Command | Purpose |
| :--- | :--- |
| `genealogy init [DIR] [--regions auto\|mexico,us] [--lfs] [--artifacts-dir PATH] [--plugin-only]` | Bootstraps a fresh, private family-history Git repo separated from the Python toolkit. |
| `genealogy upgrade-workspace [--dry-run] [--root DIR]` | Refreshes managed `AGENTS.md`, skills, and hooks after `uv tool upgrade genealogy-workspace` without touching user data. |
| `genealogy validate [--root DIR] [--strict] [--skip-artifacts] [--json]` | Validates YAML schemas, IDs, referential integrity, artifact SHA-256 hashes (including Git LFS pointers!), `parent_child` DAG acyclicity, biological/chronological sanity, excluded-lead guardrails, and GPS sourcing rules. |
| `genealogy intake [--root DIR] [--scaffold] [--url URL] [--dry-run]` | Scans `artifacts/` (or configured `artifacts_dir`), computes SHA-256 checksums, flags duplicate downloads of **previously excluded leads**, and scaffolds starter `data/sources/` stubs (optionally auto-parsing `--url` into `links:`). |
| `genealogy add-link <SOURCE_ID> <URL> [--note TEXT]` | Auto-parses an Ancestry, FamilySearch, Newspapers.com, Find a Grave, Fold3, LoC, NARA, or WikiTree URL and appends it to `data/sources/<SOURCE_ID>.md` with `provider`, `requires_auth`, `accessed`, and extracted record IDs. |
| `genealogy stats [--root DIR] [--json]` | Displays dataset counts (individuals by status/living, sources by type, relationships, linked vs. unlinked artifacts). |
| `genealogy build-db [--root DIR] [--out PATH]` | Compiles `data/` into `build/genealogy.sqlite` with normalized tables and FTS5 full-text search (tagging `[EXCLUDED]` leads clearly). |
| `genealogy query "<query>" [--fts] [--root DIR]` | Runs FTS5 full-text search (`--fts`) or read-only SQL `SELECT` queries against your Markdown dataset. |
| `genealogy timeline <ID> [--include-family]` | Generates a chronological Markdown timeline (with estimated ages, land tracts, and optional interleaved family events). |
| `genealogy tree [<ID>] [--format mermaid\|dot] [--depth N]` | Renders a Mermaid flowchart or Graphviz DOT graph of your family relationships. |
| `genealogy view [--port 8765] [--out PATH] [--include-living]` | Launches a local interactive **DAG + Marriage-Hub** web viewer built for inosculated/endogamous families (multi-path kinship inspector, pedigree-collapse badges, **land-tract filter**, **era/census-year slider**, **SVG export**, **copy citation**, Ancestry/FamilySearch links, inline artifact previews, excluded leads, and live reload)—or exports a single offline HTML file via `--out`. |
| `genealogy kinship <ID1> <ID2> [--json]` | Traces every distinct blood path between two individuals and computes Wright's coefficient of relationship ($r$) and expected offspring inbreeding ($F$). |
| `genealogy collapse <ID> [--max-gen N] [--json]` | Reports per-generation pedigree collapse, multi-path ancestors (`×2`, `×3`…), and Wright's inbreeding coefficient ($F$). |
| `genealogy tracts [--tract FILTER] [--json]` | Prints chronological chain-of-title tables grouped by land tract across deeds and probate events. |
| `genealogy dna <ID1> <ID2> [--observed-cm CM] [--json]` | Endogamy-aware autosomal DNA expected shared cM calculator across all documented blood paths, flagging `higher_than_expected`, `lower_than_expected`, or `unexplained_match` results. |
| `genealogy gaps [--regions LIST] [--json]` | Audits region-aware missing censuses (U.S., Mexico 1930, UK, Canada) and Mexican parish/civil vital gaps, promotion-ready `tentative` profiles, discrepancies, consanguineous marriages (with *Dispensa Matrimonial* tips), and excluded leads. |
| `genealogy report <ID> [--out PATH] [--include-living]` | Generates a shareable Markdown Family Group Sheet & Research Dossier with numbered citations, provenance links, excluded leads, and living-person redaction. |
| `genealogy import-gedcom <file> [--dest staging\|data]` | Imports GEDCOM 5.5.1 or 7.0 into `workspace/staging/` (default) or `data/`, preserving GPS provenance. |
| `genealogy export-gedcom <out> [--version 7.0\|5.5.1] [--include-living]` | Exports `data/` to GEDCOM 7.0 (default) or 5.5.1. **Living individuals (`living: true`) are automatically redacted by default.** |
| `genealogy search newspapers "<query>" [--state S] [--start-year Y] [--end-year Y]` | Searches Library of Congress Chronicling America historic U.S. newspapers (1770–1963) via the unified `loc.gov` JSON API (no API key needed). |
| `genealogy search nara "<query>" [--record-group RG]` | Searches the National Archives (NARA) Catalog API v2 (requires free `NARA_API_KEY` env var from `Catalog_API@nara.gov`). |
| `genealogy search wikitree [--first F] [--last L] [--profile ID] [--ancestors ID]` | Queries WikiTree's public read-only API for secondary/compiled research leads. |

---

## Bundled Domain Skills & Automated Guardrails (`plugin/`)

* **7 On-Demand Skills (`plugin/skills/`):**
  * `artifact-transcription` — Verbatim OCR/transcription + 1790–1950 U.S. Census column reference + Ancestry/FamilySearch `links:` provenance.
  * `gps-proof-argument` — Genealogical Proof Standard workflow + Elizabeth Shown Mills's 3×3 Evidence Analysis Map.
  * `identity-disambiguation` — Same-name separation, FAN Club analysis, and `relevance: excluded` false-lead tracking.
  * `jurisdiction-history` — Historical county formation dates, parent counties, vital-registration start dates, and burnt-county workarounds.
  * `research-planning` — Brick-wall strategy, phonetic name-variant matrices, negative search logs, and open archival API commands.
  * `gedcom-triage` — Safe staging and vetting of external GEDCOM trees before promoting anything into `data/`.
  * `dna-endogamy` — Endogamy-aware autosomal DNA shared-cM corroboration across multi-path pedigrees (`genealogy dna`).
* **Mechanical Guardrails (`plugin/hooks.json`):**
  * `protect-boundaries` (`PreToolUse`): Hard-blocks writes/deletes inside `artifacts/` (and any external `artifacts_dir`) and asks for confirmation before modifying `notes/`.
  * `validate-data-on-stop` (`Stop`): Runs `genealogy validate` once the agent finishes editing `data/` and asks it to fix schema or reference errors before stopping.

---

## Trying the Worked Example & Contributing (Toolkit Developers)

If you clone this toolkit repo to test it out or contribute:

```bash
git clone https://github.com/rrhett/genealogy.git
cd genealogy
uv sync

# Explore the bundled inosculated example dataset (examples/west-family/)
uv run genealogy validate --root examples/west-family --strict
uv run genealogy kinship I-WEST-CLAUDE-1886 I-RAINWATER-OCTA-1890 --root examples/west-family
uv run genealogy collapse I-WEST-EDGAR-1909 --root examples/west-family
uv run genealogy tracts --root examples/west-family
uv run genealogy dna I-WEST-CLAUDE-1886 I-RAINWATER-OCTA-1890 --observed-cm 860 --root examples/west-family
uv run genealogy gaps --root examples/west-family
uv run genealogy view --root examples/west-family

# Run linter, tests, and Antigravity plugin validation
uv run ruff check src tests plugin/hooks
uv run pytest -v
agy plugin validate plugin --strict
```

---

## License

This toolkit and plugin are licensed under the **GNU General Public License v3.0 or later (`GPL-3.0-or-later`)**—see [LICENSE](LICENSE).

**Your family history is 100% yours:** Under the GPLv3 Section 7 exception in [LICENSE-EXCEPTION.md](LICENSE-EXCEPTION.md), your private research data (`data/`, `artifacts/`, `notes/`, `workspace/`, `AGENTS.local.md`) and any files you export or share with relatives (standalone `genealogy view --out` HTML trees, SVGs, GEDCOMs, SQLite databases, and Family Group Sheet reports) carry **no GPL obligations**—keep them private or share them under whatever terms you choose.
