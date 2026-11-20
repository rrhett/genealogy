# Evidence-Based Genealogy Research Workspace

An AI-assisted, provenance-first genealogy repository designed for local agentic IDEs (such as Antigravity). It separates **immutable primary records**, **human-owned research notes**, **AI working scratchpads**, and **structured, machine-readable data** so your family history stays verifiable today and ready for SQLite exports or custom tree visualizations tomorrow.

---

## Repository Structure

```text
genealogy-research/
├── AGENTS.md                 # Strict provenance rules, schemas, and directory permissions
├── README.md                 # Setup & onboarding guide (this file)
├── RESEARCH_HANDOFF.md       # Temporary context dump from prior chat sessions
├── artifacts/                # READ-ONLY: Scans, PDFs, census images, vital records
├── notes/                    # HUMAN-OWNED: Your personal logs, family lore, and theories
├── workspace/                # AGENT-OWNED: AI scratchpad, hypotheses, conflict analysis
├── data/                     # CANONICAL OUTPUT: Structured YAML/Markdown facts
│   ├── individuals/          # One file per person (I-SURNAME-GIVEN-YEAR.md)
│   ├── sources/              # One citation & verbatim transcription per record
│   └── relationships.yaml    # Explicit graph edges (parent-child, marriage, FAN club)
└── src/                      # Future home for validators, SQLite builders, or tree UI
```

---

## Quick Start: Migrating an Existing Chat Thread

If you are moving an ongoing genealogy investigation out of a web chat interface and into this repository, follow these steps to extract a clean, lossless handoff:

### Step 1: Extract Context from Your Old Chat
Paste the following prompt at the end of your existing chat conversation:

```markdown
Summarize our entire genealogy conversation into a dense, self-contained Markdown document suitable for saving directly to my local repository as `RESEARCH_HANDOFF.md` for an AI research assistant (Antigravity).

Strip out all conversational filler, superseded theories, and speculative rabbit holes unless explicitly flagged as active hypotheses. Preserve exact spelling variants, dates, locations, and record citations. Structure the output strictly with the following sections:

1. **Research Objective & Scope:** The specific brick wall, lineage question, or identity resolution target we are investigating.
2. **Confirmed Individuals & Proven Facts:** A structured list or table of established individuals, including full name, all observed spelling/orthographic variants, exact/estimated vital dates, locations (county/parish, state/province, country), and confirmed family relationships.
3. **Evidence Log & Source Citations:** Every primary and secondary record analyzed (census enumerations, vital certificates, marriage bonds, draft cards, obituaries, church/parish records, cemetery records), including record dates, repositories/archives, key extracted facts, and verbatim transcriptions alongside English translations where applicable.
4. **Analytical Conclusions & Disambiguation:** Key genealogical deductions made, negative searches (records checked that yielded nothing), and rules for distinguishing same-name individuals in the same jurisdiction.
5. **Active Hypotheses & Unresolved Questions:** Open conflicts in the records (e.g., age discrepancies, conflicting parent names), unverified leads, and candidate theories requiring proof.
6. **Next Immediate Research Steps:** A prioritized, actionable checklist of specific records, jurisdictions, or collateral lines (FAN club) to search next.

Output the entire response inside a single raw Markdown code block so I can copy it cleanly in one click.
```

### Step 2: Save & Ingest into Your Local Repo
1. Copy the generated Markdown block and save it to the root of this repository as `RESEARCH_HANDOFF.md`.
2. Drop any downloaded record images or PDFs from that research into `artifacts/`.
3. Open this repository in Antigravity and start a session with:

```text
Read AGENTS.md and RESEARCH_HANDOFF.md. Ingest the confirmed individuals, sources, and relationships into data/, log open questions in workspace/HYPOTHESES.md, and list any missing source citations you need me to verify before archiving RESEARCH_HANDOFF.md.
```

---

## Starting Fresh (No Prior Chat)

If you are starting a brand-new research line from scratch:

1. Place your initial record scans (census pages, birth/death certificates, drafts, obituaries) in `artifacts/`.
2. Drop any existing family notes or oral history into `notes/`.
3. Prompt Antigravity:

```text
Read AGENTS.md. Review the files in artifacts/ and notes/, transcribe the primary records into data/sources/, create initial profiles in data/individuals/, and outline next research steps in workspace/TODO.md.
```

---

## Core Guardrails (Enforced via `AGENTS.md`)

- **Immutable Artifacts:** Files in `artifacts/` are never renamed, edited, or deleted.
- **Protected User Notes:** Files in `notes/` are human-owned; the agent reads them for context but never modifies them unless explicitly asked.
- **Discrepancy Preservation:** Conflicting ages, spellings, or birthplaces across records are preserved verbatim rather than silently averaged or overwritten.
- **Graph-Ready Schema:** All entities in `data/` use deterministic IDs and YAML frontmatter so you can build validation scripts, GEDCOM/SQLite exporters, or interactive family tree visualizers in `src/` at any time.
