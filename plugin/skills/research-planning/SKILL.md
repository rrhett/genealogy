---
name: research-planning
description: >-
  Plans systematic archival research to break genealogical brick walls, builds
  phonetic and orthographic name-variant matrices, maintains
  workspace/NEGATIVE_SEARCHES.md and workspace/TODO.md, and queries open
  archival APIs (Chronicling America newspapers, NARA Catalog, and WikiTree).
  Activate when hitting a brick wall, planning next research steps, tracking
  exhausted searches, or querying open archival APIs.
---

# Genealogical Research Planning & Brick-Wall Strategy

Use this workflow to break dead ends methodically, avoid repeating failed searches, and document negative searches to Genealogical Proof Standard (GPS) rigor.

## Step 1: Define a Narrow, Testable Research Question
Never plan around a vague goal like "find more about the West family." Anchor every plan to one concrete question in `workspace/TODO.md` and `workspace/HYPOTHESES.md`:
- **Who:** Specific individual ID (`I-...`), approximate birth window, and known kin/FAN associates.
- **What:** One unknown fact (e.g., *maiden name of Sarah, wife of John West*, or *parentage of Thomas West born ca. 1825*).
- **Where & When:** Exact historical jurisdiction (verify with the `jurisdiction-history` skill before searching!).

Review what is already known first:
```bash
genealogy stats
genealogy gaps
genealogy timeline <INDIVIDUAL_ID> --include-family
genealogy query "<SURNAME>" --fts
```
For heavily intermarried (inosculated) families, also check shared ancestry and pedigree collapse before assuming two branches are unrelated:
```bash
genealogy kinship <ID_A> <ID_B>
genealogy collapse <INDIVIDUAL_ID>
```
Also read `notes/` for family lore, clues, and offline sources the user has already mentioned (read-only—never edit `notes/`).

## Step 2: Build a Phonetic & Orthographic Name-Variant Matrix
Before querying any index or archival API, list every plausible spelling variation in `workspace/HYPOTHESES.md` so searches are genuinely exhaustive:

| Category | Examples to Include |
|---|---|
| **Vowel swaps & dropped finals** | `Claude` / `Claud` / `Clawd`; `Octa` / `Octia` / `Octavia` |
| **Double consonants & phonetic equivalents** | `Rainwater` / `Rainwaters` / `Ranwater`; `Smyth` / `Smith` / `Schmidt`; `C`/`K`, `Ph`/`F`, `W`/`V`, `B`/`P`, `D`/`T` |
| **Initials, middle names & nicknames** | `J. W. West`, `John W. West`, `William West` (went by middle name!), `Polly`/`Mary`, `Peggy`/`Margaret`, `Patsy`/`Martha`, `Sally`/`Sarah`, `Jack`/`John`, `Hank`/`Henry` |
| **OCR & cursive misreads** | `West` ↔ `Best` / `Vest` / `Wist` / `Wess`; capital `L`/`S`/`T`/`F`, `rn`/`m`, `cl`/`d`, `u`/`n` |
| **Double surnames & Spanish/Hispanic variants** | `b`/`v` (`Bargas`/`Vargas`), `c`/`s`/`z` (`González`/`Gonsález`), `g`/`j`/`x` (`Jiménez`/`Giménez`/`Ximénez`), silent `h` (`Hernández`/`Ernández`), `y`/`i` (`Ybarra`/`Ibarra`), paternal vs. maternal surname indexing (`García López` indexed under `López` or `García`), second given name usage (`José Refugio` → `Refugio`) |
| **Surname prefixes & anglicizations** | `Mc`/`Mac`, `O'`, `De`/`De la`/`Del`/`Van`, translated European surnames |

## Step 3: Prioritize Sources by Signal & Availability
Work outward from the highest-signal surviving records for the era and jurisdiction (cross-check with `jurisdiction-history`):
1. **Direct surviving primary records:**
   - *U.S. / General:* Civil vital records, probate/estate packets, deeds (heirs' deeds & dower releases), church registers, military service & pension files.
   - *Mexico / Latin America:* **Catholic Parish Registers** (*Bautismos* with grandparents & *padrinos*, *Matrimonios*, *Defunciones*), ***Informaciones / Dispensas Matrimoniales*** (especially for consanguineous / inosculated land-owning families!), ***Registro Civil*** (1859/1867+), and ***Protocolos Notariales*** (notarial land sales, *testamentos*, powers of attorney).
2. **Every-name & periodic series:** Applicable regional national censuses (e.g., U.S. 1790–1950, Mexico 1930, UK 1841–1921, Canada 1851–1931), colonial *padrones*, state/municipal tax lists, city directories, border crossing manifests (for binational families!).
3. **Local & contextual records:** Historical newspapers (obituaries, probate notices, local social columns), cemetery/funeral home logs, school rosters.
4. **Collateral & FAN Club pivots:** When direct searches for the target ancestor fail, research **every sibling, spouse's family, *padrino* (godparent), witness, bondsman, and neighbor**—siblings' baptisms, marriage dispensations, death certificates, obituaries, and estates often name shared parents and grandparents when the target's own records do not.

## Step 4: Query Open Archival APIs via the CLI
Use the built-in CLI search commands to prospect open collections rapidly:

```bash
# 1. Chronicling America historical U.S. newspapers (1770–1963, loc.gov)
genealogy search newspapers "Claude West" --state tennessee --start-year 1885 --end-year 1960 --limit 15

# 2. NARA National Archives Catalog (federal records, census, military, land)
genealogy search nara "Cocke County Tennessee" --record-group 29 --limit 15

# 3. WikiTree public read-only API (SECONDARY LEADS ONLY!)
genealogy search wikitree --first Claude --last West --birth-date 1886 --birth-location Tennessee
genealogy search wikitree --profile West-12345
genealogy search wikitree --ancestors West-12345 --depth 3
```

> **Strict GPS Warning on WikiTree & Online Trees:** Treat every WikiTree result strictly as an **authored/compiled lead** (`record_type: compiled`, `source_type: authored`). Mine WikiTree profiles for clues, linked original citations, and external IDs (`external_ids.wikitree`), but **never** promote WikiTree assertions directly to `status: confirmed` or `confidence: proven` without independent primary/original verification.

## Step 5: Maintain `workspace/NEGATIVE_SEARCHES.md`
Log **every** unsuccessful search immediately so neither you nor the user repeats it—and so negative findings can later qualify as GPS negative evidence. Keep `workspace/NEGATIVE_SEARCHES.md` in a consistent Markdown table:

```markdown
| Date | Target Individual | Repository / Collection | Exact Query & Name Variants Tried | Date Range | Result | Next Step |
|---|---|---|---|---|---|---|
| 2026-11-20 | `I-WEST-JOHN-1858` | Chronicling America (`loc.gov`) | `"John West" OR "J. West"` + `--state tennessee` | 1880–1925 | 0 relevant hits (Newport papers not digitized for 1890–1910) | Check Tennessee State Library & Archives microfilm holdings for *Newport Plain Talk* |
```

Rules for logging negative searches:
- Always record the **exact query string**, filters, and name variants tried.
- Note **collection coverage gaps** (missing years, damaged pages, unindexed volumes) so a coverage gap is never mistaken for proof of absence.
- Keep `workspace/TODO.md` updated with prioritized follow-up tasks (both agent-executable searches and offline/paywalled lookups for the user).
