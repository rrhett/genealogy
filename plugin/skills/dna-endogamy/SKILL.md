---
name: dna-endogamy
description: >-
  Evaluates autosomal DNA shared centiMorgans (cM) in heavily intermarried
  (inosculated / endogamous) families using Malecot coancestry across all
  documented blood paths. Activate when analyzing DNA match cM values, testing
  hypothesized parentage or cousin links, diagnosing higher-than-expected cM
  caused by pedigree collapse, or checking for NPEs/half-relationships.
---

# Endogamy-Aware DNA Corroboration Workflow

Use this workflow whenever incorporating autosomal DNA match data (AncestryDNA, 23andMe, FTDNA, MyHeritage, GEDmatch) into a Genealogical Proof Standard (GPS) investigation—especially in endogamous or heavily intermarried families.

> **GPS Rule:** Autosomal DNA is **indirect corroborating evidence**, never a standalone substitute for documentary primary sources. Always pair DNA analysis with documentary citations and log DNA hypotheses in `workspace/HYPOTHESES.md`.

## Why Standard Shared-cM Charts Break Under Endogamy

Single-path predictors (such as standard DNA Painter lookups) assume two testers share only **one** most-recent ancestral couple. In an inosculated family—where cousins intermarried across multiple generations (e.g., to consolidate land tracts)—shared DNA **compounds across every distinct blood path**:
- Two 2nd cousins who are *also* 3rd cousins twice over can easily share `350–450 cM`, tricking a single-path predictor into guessing *1st cousin 1x removed*!
- Conversely, a match whose observed cM is **still** higher than the sum across all *documented* paths is a high-value clue that another hidden cousin path exists in the tree.

## Step-by-Step Procedure

### 1. Run the Multi-Path Expected-cM Calculator
Compare observed shared cM against all currently documented blood paths in `data/` using the CLI:

```bash
genealogy dna <ID_A> <ID_B> --observed-cm <CM>
```

Inspect every output field:
- **Documented blood paths & per-path cM:** Verify that every known shared ancestor path is already encoded in `data/relationships.yaml` (with `type: parent_child` and `subtype: biological` or `unknown`).
- **Expected total cM (`2 * coancestry * 6800 cM`) and plausibility band:** Accounts for multi-generational endogamy automatically via Malécot's coancestry recurrence.
- **Assessment:**
  - `consistent` — Observed DNA supports the documented pedigree (corroborating evidence).
  - `higher_than_expected` — Strong signal of an **undiscovered additional kinship path** (deeper endogamy) or a closer relationship than currently modeled. Pivot to collateral lines and land/probate FAN networks!
  - `lower_than_expected` — Check for a **half-relationship** (different second spouse), **non-paternity event (NPE)**, **adoption** (`subtype: adoptive`), or a misattached same-name ancestor (run `identity-disambiguation`).
  - `unexplained_match` — 0 known blood paths in `data/` yet meaningful shared DNA; build a hypothesis tree in `workspace/HYPOTHESES.md`.

### 2. Account for Testing-Company IBD2 & Segment Differences
Before flagging a discrepancy, check how the testing company reports segments:
- **IBD2 (Fully Identical Regions):** Full siblings and double cousins share regions identical on *both* chromosomes. **AncestryDNA** counts IBD2 once (~2,600 cM average for full siblings), while **23andMe / FTDNA / GEDmatch** count both copies (~3,400 cM).
- **Longest Segment & Segment Count:** In heavily endogamous populations, many tiny `7–12 cM` pile-up segments accumulate from 6–10 generations back. Always record both **total cM** AND **longest segment (cM)** + **number of segments** in `workspace/HYPOTHESES.md`—a single `35+ cM` segment is much stronger evidence of a recent shared ancestor than `120 cM` split across 14 tiny segments.

### 3. Protect Living Testers' Privacy
- Never commit raw DNA kits, living match names, or private kit IDs into a public repository.
- Any living tester represented in `data/individuals/` MUST be marked `living: true` so `genealogy export-gedcom`, `genealogy report`, and `genealogy view` redact them automatically.

### 4. Log Hypotheses & Corroborate Documentarily
1. Record the match comparison (`ID_A`, `ID_B`, company, total cM, longest segment, segment count, CLI output, and competing hypotheses) in `workspace/HYPOTHESES.md`.
2. Use `genealogy kinship <ID_A> <ID_B>` and `genealogy collapse <ID>` to inspect the underlying pedigree paths.
3. Convert DNA clues into targeted documentary searches (see `research-planning` and `gps-proof-argument`) before upgrading any individual to `status: confirmed` or relationship to `confidence: proven`.
