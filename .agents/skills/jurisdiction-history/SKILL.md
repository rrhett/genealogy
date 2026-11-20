---
name: jurisdiction-history
description: >-
  Verifies historical county formation dates, parent/daughter boundary changes,
  state civil vital registration start dates, and courthouse-loss ("burnt
  county") status before logging birthplace discrepancies or searching
  repositories. Activate when an ancestor's county changes between records,
  when vital or probate records cannot be found, or when planning searches in
  a new state or era.
---

# Historical Jurisdiction, Vital Registration & Burnt-County Analysis

Before flagging a birthplace discrepancy or concluding a record "does not exist," verify **which jurisdiction actually governed the land on the date of the event** and **whether that record series existed or survived**.

## Step 1: Check County Boundary & Formation History

Families frequently appear to "move" across counties without ever leaving their farm because new counties were carved from older parent counties (or borders shifted between states/territories).

1. **Verify the formation date** of every county named in an ancestor's timeline (use authoritative references such as the Newberry Library *Atlas of Historical County Boundaries* or state archive gazetteers).
2. **Trace the parent → daughter chain:**
   - *Example:* Cocke County, Tennessee was formed in **1797** from Jefferson County, which was formed in **1792** from Greene and Hawkins Counties (originally Washington District / North Carolina prior to 1790/1796). A person born on the same farm in 1791 may legitimately list North Carolina, Jefferson County, or Cocke County on later censuses!
3. **Search ALL relevant courthouses:**
   - **Pre-formation records** (early deeds, land grants, marriages, estates) usually remained at the **parent county** courthouse—not the newly created daughter county.
   - **Border-farm records** may appear in an adjacent county whose courthouse or market town was closer by road/river.
4. **Record places historically in `data/`:**
   - In `data/individuals/<ID>.md` and `data/sources/<ID>.md`, record the jurisdiction **as it existed at the time of the event** (and note parent/modern county context in the Markdown body or `## Recorded Discrepancies` so apparent conflicts are explained rather than erased).

## Step 2: Check Civil Vital Registration Start Dates

Never waste search cycles (or draw negative evidence) from the absence of a state birth or death certificate before statewide civil registration existed or reached full compliance.

### General U.S. Civil Vital Registration Eras
- **New England (MA 1841, VT 1857, RI 1853, CT 1852/1897, NH 1866/1883, ME 1892):** Early town-level vital registers often date back to the 1600s–1700s; statewide centralization began mid-to-late 1800s.
- **Mid-Atlantic & Midwest (NJ 1848, MI 1867, NY 1880/1881, IN 1882/1907, PA 1906, OH 1908, IL 1916):** Large cities (NYC, Philadelphia, Boston, Baltimore, Chicago, New Orleans, St. Louis) began municipal vital registration **decades earlier** than their states. County-level registers often bridge the 1860s–1900s gap.
- **South & West (KY 1852–1861 intermittent, 1911 statewide; VA 1853–1896, resumed 1912; TX 1903; CA 1905; TN 1908–1912 partial, 1914 continuous; NC 1913; GA 1919; AL 1908; MS 1912; AR 1914; SC 1915; OK 1908; NM 1920):** Expect **5–15 years of incomplete compliance** after the statutory start date, especially in rural counties.
- **Delayed Birth Certificates (1930s–1960s):** Filed decades after birth for Social Security or passports—treat as `information_type: secondary` unless backed by attached contemporary proof (Bible page, school census, physician ledger).

### Vital Record Substitutes Before Civil Registration
When civil vital certificates did not exist for the target year/place, pivot systematically to:
- **Birth substitutes:** Church baptisms/christenings, family Bibles, 1900 census (month/year), school enumerations, probate guardian bonds (ages of minor heirs), newspaper birth notices, Social Security SS-5 applications (for adults living past 1936).
- **Marriage substitutes:** County marriage bonds/licenses (often began at county formation), church banns/registers, newspaper wedding notices, deed dower releases (proving wife's given name), 1900/1910/1930 census marriage columns.
- **Death substitutes:** Probate/estate packets & administration bonds, cemetery stones & sexton ledgers, funeral home records, church burial registers, newspaper obituaries, Federal Mortality Schedules (1850–1880), state/county coroner inquests, Civil War/military pension drop cards.

## Step 3: Check for "Burnt Counties" & Record-Loss Workarounds

Before recording a negative search in a county courthouse, verify whether fires, floods, or wars (Revolutionary War, War of 1812, Civil War) destroyed local records—and **which specific record books** were lost (sometimes deeds burned while wills survived, or vice versa!).

### Record Workarounds for Burnt Counties
When local county wills, deeds, or marriage books were destroyed, pivot to records held **outside the county courthouse**:

1. **State-Level Records:**
   - **State Tax Lists / Duplicate Land & Personal Property Rolls** (submitted annually to the state auditor/comptroller—often survive intact even when the county burned, e.g., Virginia/Kentucky/Tennessee/North Carolina tax lists).
   - **State Land Grants, Warrants, Surveys & Patents** (held at the state land office or state archives).
   - **State Appellate / Supreme Court Cases** (reprint transcripts of lower county court exhibits, wills, and family disputes).
   - **State Censuses, Militia Lists & Confederate/State Pension Files.**
2. **Federal Records:**
   - **U.S. Federal Censuses** (Population, Agriculture, Mortality, Slave schedules—held in Washington, D.C.).
   - **Federal Land Office Tract Books & Bounty Land Warrants** (for public-land states; BLM GLO & NARA).
   - **Military Service & Pension Files** (Revolutionary War, War of 1812, Mexican-American War, Civil War, WWI/WWII draft cards—often contain family Bible pages, marriage proofs, and neighbors' depositions!).
3. **Private, Ecclesiastical & Neighboring Records:**
   - **Church Registers & Denominational Archives** (baptisms, marriages, membership transfers, disciplinary minutes).
   - **Local & Regional Newspapers** (estate administrator notices, sheriff's tax sales, chancery summonses naming non-resident heirs, obituaries).
   - **Re-recorded Deeds** (landowners often brought original parchment deeds back to the courthouse after a fire to re-record them—check deed books dated *after* the fire!).
   - **Adjacent-County Records** (relatives' estates or deeds recorded across the county line).

## Step 4: Document Jurisdictional Findings
- Log confirmed boundary shifts, vital-registration cutoffs, and record-loss dates in `workspace/HYPOTHESES.md` (and `workspace/NEGATIVE_SEARCHES.md` when explaining why a record group was unavailable or skipped).
- Never claim negative evidence from a destroyed record set or a pre-registration era; record the gap explicitly as a **missing/unavailable source**, not evidence of absence.
