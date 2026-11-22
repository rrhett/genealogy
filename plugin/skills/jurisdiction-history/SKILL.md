---
name: jurisdiction-history
description: >-
  Verifies historical administrative and ecclesiastical boundaries (U.S.
  counties, Mexican estados/municipios/parroquias/diócesis, and international
  jurisdictions), civil vital registration start dates, and record-loss
  ("burnt county" / conflict loss) workarounds before logging birthplace
  discrepancies or negative searches. Activate when an ancestor's jurisdiction
  changes between records, when vital, parish, or probate records cannot be
  found, or when planning searches in a new state, country, or era.
---

# Historical Jurisdiction, Civil Registration & Record-Loss Analysis

Before flagging a birthplace discrepancy or concluding a record "does not exist," verify **which civil AND ecclesiastical jurisdiction actually governed the place on the date of the event** and **whether that record series existed or survived**.

## Step 1: Check Civil & Ecclesiastical Boundary History

Families frequently appear to "move" across jurisdictions without ever leaving their farm or *rancho* because new counties, *municipios*, states, or parishes were carved from older parent jurisdictions.

### A. United States (State → County → Township / Civil District)
1. **Verify the county formation date** (e.g., via the Newberry Library *Atlas of Historical County Boundaries*): pre-formation deeds, marriages, and estates typically remain at the **parent county** courthouse.
2. *Example:* Cocke County, Tennessee was formed in **1797** from Jefferson County (1792), previously Greene/Hawkins Counties and North Carolina before 1790/1796.

### B. Mexico & Latin America (País → Estado → Municipio → Localidad/Rancho/Hacienda + Diócesis → Parroquia)
1. **Track BOTH civil and Catholic ecclesiastical jurisdictions independently:**
   - **Civil hierarchy:** *Estado* → *Municipio* (formerly *Partido* / *Cantón* / *Distrito* in the 1800s) → *Localidad / Rancho / Hacienda / Congregación*. New *municipios* were carved frequently in the 19th and 20th centuries—check the parent *municipio* for older *Registro Civil* books!
   - **Ecclesiastical hierarchy:** *Arquidiócesis / Diócesis* → *Parroquia* (parish) → *Vicaría / Capilla*. Parish boundaries often **crossed *municipio* and even state lines**! A family living on a *rancho* in one *municipio* commonly baptized and married at a closer *parroquia* in an adjacent *municipio* or neighboring state.
   - **Diocesan Archives (*Archivo Histórico del Arzobispado / Obispado*):** ***Dispensas Matrimoniales*** (required for consanguineous marriages up to the 4th degree!) were sent up to the **Diocese** (e.g., Guadalajara, Monterrey, Morelia, Durango, Zacatecas, Mexico City)—NOT kept solely at the local parish!
2. **Colonial Provinces (pre-1821):** *Nueva España*, *Nueva Galicia*, *Nuevo Reino de León*, *Nueva Vizcaya*, *Intendencias* (1786). A birthplace listed as *Nueva Galicia* in 1790 and *Jalisco* in 1830 is the same place, not a discrepancy.

### C. Recording Places Historically
In `data/individuals/<ID>.md` and `data/sources/<ID>.md`, record the jurisdiction **as it existed at the time of the event** and note parent/modern jurisdictions in the Markdown body so apparent place conflicts are explained rather than erased.

## Step 2: Check Civil Vital Registration Start Dates

Never draw negative evidence from the absence of a civil certificate before civil registration began or achieved rural compliance.

### Mexico (*Registro Civil*)
- **Nationwide Law:** July **1859** (*Leyes de Reforma*), with uneven early compliance during the War of Reform and French Intervention; widespread continuous coverage generally starts **1867–1880** (and occasionally **1890s** in remote rural *ranchos*).
- **Before Civil Registration (and alongside it!):** **Catholic Parish Registers (*Registros Parroquiales*)**—*Bautismos*, *Informaciones/Dispensas Matrimoniales*, *Matrimonios*, and *Defunciones*—are the primary vital records from the 1550s–1600s onward.
- **National Censuses:** Only the **1930 Mexican National Census** (*Censo de Población de 1930*) survives broadly as a public nominative census. Never expect U.S.-style decennial federal censuses for Mexican ancestors!

### United States (State Civil Vital Registration)
- **New England (MA 1841, VT 1857, RI 1853, CT 1852/1897, NH 1866/1883, ME 1892):** Early town registers often begin in the 1600s–1700s.
- **Mid-Atlantic & Midwest (NJ 1848, MI 1867, NY 1880/1881, IN 1882/1907, PA 1906, OH 1908, IL 1916):** Major cities began municipal registration decades earlier than their states.
- **South & West (KY 1852–1861, 1911; VA 1853–1896, 1912; TX 1903; CA 1905; TN 1908–1912, 1914; NC 1913; GA 1919; AL 1908; MS 1912; AR 1914; SC 1915; OK 1908; NM 1920):** Expect 5–15 years of incomplete rural compliance after statutory start.
- **Delayed Birth Certificates:** Treat as `information_type: secondary` unless backed by contemporary proof.

### Vital Record Substitutes Before Civil Registration
- **Birth substitutes:** Catholic/church baptisms (*bautismos*), family Bibles, guardian bonds, school rosters, 1900 U.S. census (month/year), Social Security SS-5 applications.
- **Marriage substitutes:** *Informaciones / Dispensas Matrimoniales*, church marriage registers, county marriage bonds/licenses, deed dower releases, notarial marriage contracts (*cartas de dote*), newspaper notices.
- **Death substitutes:** Church burial registers (*defunciones/entierros*), probate/notarial wills (*protocolos notariales / testamentos*), cemetery stones, funeral home logs, obituaries, mortality schedules.

## Step 3: Check for Archive Losses ("Burnt Counties" & Conflict Losses)

Before logging a negative search, verify whether fires, floods, or wars destroyed local books—and **which specific books** survived:
- **U.S. Burnt Counties** (courthouse fires, Revolutionary/Civil War): Pivot to **state duplicate tax lists**, **state/federal land grants**, **state appellate court transcripts**, **U.S. federal censuses & military pension files**, **church registers**, **re-recorded deeds** dated *after* the fire, and **newspapers**.
- **Mexico (Mexican Revolution 1910–1920, Cristero War 1926–1929, local fires):**
  - If a municipal *Registro Civil* office burned, check the **state *Archivo General del Registro Civil*** (duplicate *libros duplicados* were required to be sent to the state capital!) and the **local Catholic *Parroquia***.
  - If local parish books are missing for a gap of years, check the **Diocesan Archive** (especially ***Dispensas Matrimoniales***, *Padrones parroquiales*, and *Diezmos* / tithe records), **adjacent parishes**, and ***Protocolos Notariales*** (notarial land deeds, powers of attorney, and wills!).

## Step 4: Document Jurisdictional Findings
- Log confirmed boundary shifts, civil/parish start dates, and archive-loss gaps in `workspace/HYPOTHESES.md` and `workspace/NEGATIVE_SEARCHES.md`.
- For recurring *ranchos*, *haciendas*, estates, or parishes where multiple families, owners, and *compadres* cluster across generations, promote the locality summary and `jurisdiction_history` into a canonical `data/places/<PLACE_ID>.md` profile and inspect community ties with `genealogy places <PLACE_ID> [--focal <INDIVIDUAL_ID>]`.
- Never claim negative evidence from a destroyed record series or pre-registration era; record the gap explicitly as an **unavailable source**, not proof of absence.
