# Evidence Analysis Process Map Reference

Adapted from Elizabeth Shown Mills's *Evidence Explained* 3×3 Evidence Analysis Process Map and mapped directly to this repository's YAML frontmatter schema (`data/sources/`, `data/individuals/`, `data/relationships.yaml`).

> **Core Principle:** Never collapse **Source**, **Information**, and **Evidence** into a single label. An *original* source can contain *secondary* information (e.g., parents' names on a death certificate), and a *derivative* source can preserve *primary* information (e.g., a county clerk's transcription of a contemporary marriage return).

---

## The 3×3 Framework

### Axis 1: Sources (`source_type` in `data/sources/<ID>.md`)
Evaluates the **physical or digital artifact itself**—how close it is to the original creation.

| Value | Definition | Concrete Genealogical Examples | Reliability Notes |
|---|---|---|---|
| `original` | The first recorded form of a record—or an accurate photographic, microfilm, or digital image of that original. | Digital scan of a 1910 census page (`NARA T624`), scan of an original signed WWI draft card, original county marriage bond, original death certificate image, contemporary newspaper obituary scan. | Highest fidelity to what the creator wrote; still check whether the *information* inside is primary or secondary. |
| `derivative` | Material produced by copying, abstracting, indexing, translating, or transcribing an earlier record. | Printed county marriage abstract book, FamilySearch/Ancestry index-only database entry, Find a Grave transcription without a headstone photo, typed WPA transcript, clerk's later ledger copy of loose deeds. | Vulnerable to transcription, OCR, omission, and indexing errors. Always seek the underlying `original` image before marking a claim `confirmed`/`proven`. |
| `authored` | A narrative, synthesis, or tree created by an author who selected, interpreted, and merged multiple sources (often unevenly cited). | Published county mugbook biography, family history book, online user tree (Ancestry, FamilySearch Family Tree, WikiTree), imported GEDCOM file. | **Never sufficient for proof on its own.** Use strictly as research leads and mine their footnotes for original records. |

---

### Axis 2: Information (`information_type` in `data/sources/<ID>.md`)
Evaluates **the informant's firsthand knowledge** of the specific fact at the time it was recorded.

| Value | Definition | Concrete Genealogical Examples | Mapping Guidance |
|---|---|---|---|
| `primary` | Supplied by an eyewitness or direct participant at or near the time of the event. | Bride & groom's ages/signatures on their marriage license; doctor's date & cause of death on a death certificate; parents reporting a newborn's birth on a timely birth certificate; self-reported residence & occupation on a signed WWI draft card. | Strongest weight for the event witnessed—note that even a person reporting their *own* birth date is technically relying on family tradition (secondary), though self-reported adult identity details carry high weight. |
| `secondary` | Supplied by someone who did **not** witness the event, or recorded long after the fact from hearsay/memory. | Deceased's birth date, birthplace, and parents' names on a death certificate (reported by a spouse or adult child); birth year in an obituary; parent birthplaces reported by a neighbor on a census; delayed birth certificate filed 50 years later by a younger sibling. | Useful corroboration when consistent, but prone to memory drift and family myth. Must be corroborated before treating as proof. |
| `mixed` | A single record contains **both** primary and secondary information items (very common!). | **Death certificate:** death date/place/cause = primary; birth date/parents' names = secondary. **1940 Census:** circled-`x` (`⊗`) head reporting own occupation/residence = primary; reporting spouse's childhood birthplace = secondary. **Probate estate packet:** heir list at settlement = primary; recitations of older family events = secondary. | Use `mixed` at the source-file level and specify per-item primary vs. secondary status in your Evidence Correlation Matrix in `workspace/`. |
| `undetermined` | The informant is not identified on the record, making firsthand knowledge impossible to verify. | Pre-1940 U.S. Federal Census schedules (no informant column); unsigned gravestone inscription (erected years later by unknown kin); anonymous newspaper death notice. | Treat conservatively; weigh against timing, internal consistency, and corroborating records. |

---

### Axis 3: Evidence (Evaluated Per Research Question in `workspace/`)
Evaluates **how a piece of information answers a specific research question**. Evidence is *not* a fixed property of a source—it depends on the question being asked!

| Evidence Type | Definition | Example Research Question & Record | How to Use in Proof Arguments |
|---|---|---|---|
| **Direct** | Explicitly states an answer to the research question on its face. | *Question:* Who were Claude West's parents? *Direct evidence:* 1954 death certificate naming father "John West" and mother "Sarah ...". | Direct evidence can still be **wrong** (e.g., mistaken informant on a death certificate). Always corroborate with at least one independent source. |
| **Indirect** | Does not explicitly answer the question on its own, but combines with other records to prove the answer by logical inference. | *Question:* Did John West die between 1875 and 1880? *Indirect evidence:* John signs a deed in 1875; his wife appears as "Widowed" head of household in the 1880 census; probate inventory filed 1878. | Often stronger than a single shaky direct record when multiple independent indirect records interlock without contradiction. |
| **Negative** | Meaningful **absence** of information in a record where it *should* appear if a hypothesis were true. | *Question:* Was Thomas West still living in Cocke County in 1850? *Negative evidence:* Exhaustive page-by-page search of the 1850 Cocke County census and 1849–1851 tax lists shows his neighbors intact but Thomas absent, while his estate is divided among heirs in 1849. | Only valid after proving the record survives, is complete, and covers the target group. Always document the exact search in `workspace/NEGATIVE_SEARCHES.md`. |

---

## Mapping Common Record Types to Repository YAML

| Record | `record_type` | Typical `source_type` | Typical `information_type` | Key Cautions |
|---|---|---|---|---|
| U.S. Census scan (1790–1930) | `census` | `original` | `undetermined` | Informant unknown; pre-1880 lacks explicit relationships. |
| U.S. Census scan (1940) | `census` | `original` | `mixed` (or `primary`/`secondary` per informant `⊗`) | Check who has the circled `x` (`⊗`)! |
| Civil Birth Certificate (timely) | `vital` | `original` | `primary` | Check filing date vs. birth date (watch for delayed certs filed decades later). |
| Civil Death Certificate | `vital` | `original` | `mixed` | Death facts = primary; birth/parentage facts = secondary (check informant's relationship). |
| Civil Marriage License / Return | `vital` | `original` | `primary` | Check whether license was *returned* (solemnized) vs. license-only. |
| Church Baptism / Marriage Register | `church` | `original` | `primary` | Note godparents/sponsors (prime FAN Club leads). |
| WWI / WWII Draft Registration Card | `military` | `original` | `primary` | Self-signed; watch for age fudging near registration cutoff ages. |
| Newspaper Obituary Scan | `newspaper` | `original` | `secondary` (or `mixed`) | Death timing/survivors often accurate; early life/birth year often imprecise. |
| Gravestone Photograph | `cemetery` | `original` | `undetermined` | Stones are frequently carved years later; distinguish photo (`original`) from text-only Find a Grave listing (`derivative`). |
| Probate / Estate Settlement Packet | `probate` | `original` | `primary` (heirs/property) or `mixed` | Gold standard for proving parent-child and married-daughter surname links. |
| County Deed / Land Grant | `land` | `original` (or `derivative` clerk's book copy) | `primary` | Check witnesses, adjoining landowners, and dower releases (names wife!). |
| WikiTree / FamilySearch Tree / GEDCOM | `compiled` | `authored` | `secondary` | **Lead only.** Never use alone to justify `status: confirmed` or `confidence: proven`. |
