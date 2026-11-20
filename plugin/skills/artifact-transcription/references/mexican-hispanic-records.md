# Mexican & Hispanic Genealogical Records Reference

Use this reference when transcribing Mexican and Latin American **Catholic Parish Registers (*Registros Parroquiales*)**, **Civil Registration (*Registro Civil*)**, ***Dispensas Matrimoniales***, notarial land/will records (*Protocolos Notariales*), and the **1930 Mexican National Census**.

> **Bilingual Transcription Rule:** Always transcribe the **original Spanish (or Latin) text verbatim first**, preserving archaic spelling, accent marks, and abbreviations, followed by a clear **English translation** in `## Verbatim Transcription & Extracted Data`. Never modernize spelling or omit colonial terms.

---

## 1. Hispanic Naming Conventions & Double Surnames

In Mexico and Latin America, individuals customarily carry **two surnames**:
- **`Apellido Paterno` (`surname`):** Father's first (paternal) surname.
- **`Apellido Materno` (`maternal_surname`):** Mother's first (paternal) surname.
- **Maiden Name Retention:** Married women **keep their own surnames** on civil and church records! Occasionally a married or widowed woman appears as *María López **de** García* (or *vda. de García*, "widow of García")—record her birth surname (`López`) as `surname` and store married forms in `name_variants`.
- **Compound & Prepositional Surnames:** Keep noble/toponymic particles intact (`de la Cruz`, `del Castillo`, `de León`, `San Martín`).
- **Deterministic ID Transliteration:** Strip Spanish diacritics into ASCII for the `id` (`José María García López` → `I-GARCIA-JOSE-1870`), while keeping full accented UTF-8 in `primary_name`, `given_name`, `surname`, and `maternal_surname`:

```yaml
id: I-GARCIA-JOSE-1870
primary_name: "José María García López"
given_name: "José María"
surname: "García"
maternal_surname: "López"
name_variants:
  - "J. M. García"
  - "José García"
```

---

## 2. Core Mexican Record Series & What to Extract

### A. Catholic Parish Registers (*Registros Parroquiales*, ~1550s–Present)
Often the deepest surviving record group in Mexico, frequently intact back to the 1600s–1700s.

1. ***Bautismos* (Baptisms — `record_type: church`, `source_type: original`):**
   - Distinguish **baptism date** (*fecha de bautismo*) from **birth date** (*nacido el día...* or *"de tres días de nacido"* = 3 days old!).
   - Record legitimacy status verbatim: **`h.l.` (*hijo/a legítimo/a*)**, **`h.n.` (*hijo/a natural*)**, or ***expósito/a*** (foundling/abandoned infant).
   - **Always extract BOTH sets of grandparents** (*abuelos paternos* and *abuelos maternos*)—Mexican baptisms routinely list three generations on a single entry!
   - **Always extract *padrinos* (godparents)** and link recurring associates as `fan_associate` in `data/relationships.yaml` (padrinos were frequently aunts, uncles, cousins, or neighboring landowners).
   - Check the **left/right margin** for later marginal notes (*notas marginales*): priests often wrote the child's later marriage date and spouse right next to their baptism entry!

2. ***Informaciones Matrimoniales* & *Dispensas Matrimoniales* (Marriage Investigations & Consanguinity Dispensations — GOLDMINE FOR INOSCULATED FAMILIES!):**
   - Required before marriage to confirm freedom to marry (*soltería*) and check canonical impediments.
   - Whenever a bride and groom were related within the **4th canonical degree of consanguinity** (up to 3rd cousins!) or **affinity** (in-laws)—extremely common in land-owning families intermarrying to preserve *ranchos* and *haciendas*—the diocese opened a formal ***Dispensa Matrimonial*** file.
   - **What to extract:**
     - Exact canonical degree stated (e.g., *2° con 3° grado de consanguinidad* = 1st cousins once removed; *3° grado igual* = 2nd cousins; *4° grado* = 3rd cousins; *mixto de 3° con 4°*).
     - The **drawn family tree (*árbol genealógico*)** inside the file—trace every intermediate ancestor and cross-check against `genealogy kinship <A> <B>`!
     - **Sworn witness depositions (*testigos*):** Often elderly neighbors (ages 60–80) stating how long they knew the couple's parents and grandparents, plus exact *rancho* or *hacienda* locations.
     - Stated justification (*causa de dispensa*, e.g., *angustia loci* / small village, *conservación de bienes* / preservation of family property, *pobreza*).

3. ***Matrimonios* (Marriages — `record_type: church`):**
   - Distinguish ***desposorio*** (marriage contract/vows) from ***velación*** (nuptial blessing/mass—sometimes months or years later!).
   - Extract *originario de* (place of birth/origin) vs. *vecino de* (current residence), *soltero/viudo* (and prior spouse's name if widowed!), parents, *padrinos*, and *testigos*.

4. ***Defunciones / Entierros* (Burials/Deaths — `record_type: church`):**
   - Distinguish **burial date** (*sepultado / enterrado*) from **death date** (*falleció ayer* = died yesterday).
   - Extract marital status, spouse's name (or parents' names if single/child), cause of death, cemetery/church location, and **whether they left a will (*testó* / *hizo testamento* vs. *no testó* / *ab intestato*)**—a direct lead to notarial probate records (*Protocolos Notariales*)!

### B. Mexican Civil Registration (*Registro Civil*, 1859/1867–Present)
Established nationwide by the *Leyes de Reforma* (July 1859), with widespread compliance accelerating after **1867** (varying by state and rural *municipio*).
- ***Actas de Nacimiento* (Births — `record_type: vital`):** Usually name the informant (often the father or grandfather), child's birth date/time/rancho, parents' ages/birthplaces/occupations, **both sets of grandparents** (noting whether living or *finado*), and two witnesses. Watch for late registrations filed months or years after birth!
- ***Presentación Matrimonial* & *Matrimonio Civil*:** Often more detailed than church marriages; includes witnesses' ages, occupations, and addresses (FAN Club!).
- ***Actas de Defunción*:** Record informant (check `information_type: mixed`—death date/cause is primary, while deceased's age, birthplace, and parents' names are secondary!), spouse, surviving children, and burial cemetery.

### C. 1930 Mexican National Census (*Censo de Población de 1930*)
- **The only surviving public national population census of Mexico** (15 May 1930; earlier national censuses were largely not preserved as nominative schedules, though colonial/regional *padrones* exist in diocesan and municipal archives).
- Lists *estado*, *municipio*, *localidad/rancho*, household members, sex, age, marital status (civil vs. church marriage!), literacy, occupation, birthplace (*entidad federativa*), language spoken, and religion.

---

## 3. Common Spanish / Latin Paleography Abbreviations & Terms

| Abbreviation / Term | Expansion | English Meaning |
|---|---|---|
| `h.l.` / `h. leg.` | *hijo/a legítimo/a* | Legitimate child (born to married parents) |
| `h.n.` / `h. nat.` | *hijo/a natural* | Natural child (unmarried parents) |
| `b.` / `baut.` | *bauticé / bautizado* | Baptized |
| `n.` / `nac.` | *nacido/a* | Born |
| `c.c.` | *casado/a con* | Married to |
| `vda.` / `vdo.` | *viuda / viudo* | Widow / Widower |
| `fin.` / `dif.` | *finado / difunto* | Deceased |
| `v.o` / `vec.` | *vecino/a de* | Resident of (jurisdiction/rancho) |
| `orig.` | *originario/a de* | Native of (birthplace) |
| `dho.` / `dha.` | *dicho / dicha* | The aforementioned |
| `m.c.` | *marido y conjuge* (or *mismo cura*) | Husband and spouse (check context) |
| `padr.` / `comp.` | *padrinos / compadres* | Godparents / Co-parents |
| `test.` / `ttos.` | *testigos* | Witnesses |
| `q.e.p.d.` / `Q.D.D.G.` | *que en paz descanse* / *que de Dios goce* | May they rest in peace (deceased) |
| `N.S.` / `Ntra. Sra.` | *Nuestro Señor / Nuestra Señora* | Our Lord / Our Lady |
| `D.` / `D.a` / `Dn.` | *Don / Doña* | Honorific title (often notable/landowning families) |
| `testó` / `no testó` | *hizo testamento* | Left a will / Died intestate |
| `abuelos paternos / maternos` | Paternal / maternal grandparents | Father's parents / Mother's parents |
| `calidad` / `casta` | *español, mestizo, indio, mulato, coyote, lobo, morisco…* | Colonial socio-racial classification (pre-1821; **transcribe verbatim**, note fluidity across records) |

---

## 4. Frequent Spanish Orthographic & Phonetic Swaps
When disambiguating names or planning searches (`research-planning`), always test these historical clerk variations:
- **`b` ↔ `v`:** *Bargas / Vargas*, *Baldés / Valdés*, *Chábez / Chávez*, *Billa / Villa*
- **`c` ↔ `s` ↔ `z`:** *Gonsález / González*, *Sánchez / Sánches*, *Zavala / Sabala*, *Cerbantes / Cervantes*
- **`g` ↔ `j` ↔ `x`:** *Jiménez / Giménez / Ximénez*, *Jalisco / Xalisco*, *México / Méjico*, *Ángel / Ánjel*
- **Silent `h`:** *Hernández / Ernández*, *Herrera / Errera*, *Hinojosa / Inojosa*
- **`y` ↔ `i` ↔ `ll`:** *Ybarra / Ibarra*, *Ygnacio / Ignacio*, *Ayala / Aiala*, *Reyes / Reies*
- **Given-name contractions & dual names:** *Ma.* = María, *Jph.* / *Jph* = Joseph/José, *Fco.* / *Franco.* = Francisco, *Anto.* = Antonio, *Gpe.* = Guadalupe, *J. Refugio* / *Refugio* (people frequently went by their *second* given name after *José* or *María*!).
