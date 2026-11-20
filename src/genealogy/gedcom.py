"""GEDCOM 5.5.1 / 7.0 parser, staging importer, and privacy-aware exporter."""

from __future__ import annotations

import datetime
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from genealogy import __version__
from genealogy.loader import load_dataset
from genealogy.schema import parse_genealogy_date, render_frontmatter

# Multilingual month lookup (English, Spanish, French, German, Italian, Portuguese; accent-stripped lowercase)
_MONTH_MAP: dict[str, str] = {
    # January
    "jan": "01",
    "january": "01",
    "ene": "01",
    "enero": "01",
    "janv": "01",
    "janvier": "01",
    "gen": "01",
    "gennaio": "01",
    # February
    "feb": "02",
    "february": "02",
    "febr": "02",
    "febrero": "02",
    "fev": "02",
    "fevr": "02",
    "fevrier": "02",
    "febbraio": "02",
    # March
    "mar": "03",
    "march": "03",
    "marzo": "03",
    "mars": "03",
    "marz": "03",
    # April
    "apr": "04",
    "april": "04",
    "abr": "04",
    "abril": "04",
    "avr": "04",
    "avril": "04",
    "aprile": "04",
    # May
    "may": "05",
    "mayo": "05",
    "mai": "05",
    "maio": "05",
    "maggio": "05",
    # June
    "jun": "06",
    "june": "06",
    "junio": "06",
    "juin": "06",
    "juni": "06",
    "junho": "06",
    "giugno": "06",
    # July
    "jul": "07",
    "july": "07",
    "julio": "07",
    "juil": "07",
    "juill": "07",
    "juillet": "07",
    "juli": "07",
    "julho": "07",
    "luglio": "07",
    # August
    "aug": "08",
    "august": "08",
    "ago": "08",
    "agosto": "08",
    "aout": "08",
    # September
    "sep": "09",
    "sept": "09",
    "september": "09",
    "set": "09",
    "septiembre": "09",
    "setiembre": "09",
    "septembre": "09",
    "settembre": "09",
    "setembro": "09",
    # October
    "oct": "10",
    "october": "10",
    "octubre": "10",
    "octobre": "10",
    "okt": "10",
    "oktober": "10",
    "ottobre": "10",
    "outubro": "10",
    # November
    "nov": "11",
    "november": "11",
    "noviembre": "11",
    "novembre": "11",
    "novembro": "11",
    # December
    "dec": "12",
    "december": "12",
    "dic": "12",
    "diciembre": "12",
    "decembre": "12",
    "dez": "12",
    "dezember": "12",
    "dezembro": "12",
    "dicembre": "12",
}

_QUALIFIER_PREFIXES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^(?:abt\.?|about|ca\.?|c\.|circa|aprox\.?|aproximadamente|hacia|vers|env\.?|environ)(?:\s+|(?=\d))",
            re.IGNORECASE,
        ),
        "abt",
    ),
    (
        re.compile(r"^(?:bef\.?|before|antes\s+de|antes|avant)(?:\s+|(?=\d))", re.IGNORECASE),
        "bef",
    ),
    (
        re.compile(r"^(?:aft\.?|after|despues\s+de|despues|apres)(?:\s+|(?=\d))", re.IGNORECASE),
        "aft",
    ),
    (re.compile(r"^(?:est\.?|estimated)(?:\s+|(?=\d))", re.IGNORECASE), "est"),
    (re.compile(r"^(?:cal\.?|calculated)(?:\s+|(?=\d))", re.IGNORECASE), "cal"),
]

_LINE_RE = re.compile(r"^\s*(\d+)\s+(?:(@[^@]+@)\s+)?([A-Z0-9_]+)(?:\s(.*))?$")


@dataclass
class GedcomNode:
    level: int
    tag: str
    xref: str | None = None
    value: str = ""
    children: list[GedcomNode] = field(default_factory=list)

    def find_first(self, tag: str) -> GedcomNode | None:
        for ch in self.children:
            if ch.tag == tag:
                return ch
        return None

    def find_all(self, tag: str) -> list[GedcomNode]:
        return [ch for ch in self.children if ch.tag == tag]

    def full_value(self) -> str:
        parts = [self.value]
        for ch in self.children:
            if ch.tag == "CONT":
                parts.append("\n" + ch.value)
            elif ch.tag == "CONC":
                parts.append(ch.value)
        return "".join(parts).strip()


def parse_gedcom_text(text: str) -> list[GedcomNode]:
    """Parse GEDCOM 5.5.1 or 7.0 text into a tree of top-level GedcomNode records."""
    cleaned = text.lstrip("\ufeff")
    roots: list[GedcomNode] = []
    stack: list[GedcomNode] = []

    for line in cleaned.splitlines():
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        level = int(m.group(1))
        xref = m.group(2)
        tag = m.group(3)
        value = (m.group(4) or "").rstrip("\r\n")
        node = GedcomNode(level=level, tag=tag, xref=xref, value=value)

        while stack and stack[-1].level >= level:
            stack.pop()
        if not stack:
            roots.append(node)
        else:
            stack[-1].children.append(node)
        stack.append(node)

    return roots


def _fold_ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _validate_iso_date(candidate: str) -> str | None:
    try:
        parse_genealogy_date(candidate)
        return candidate
    except ValueError:
        return None


def _parse_single_gedcom_date(raw: str) -> str | None:
    s = _fold_ascii(raw).strip().lower()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None

    # Strip trailing time-of-day if present (e.g., "4/13/2013 11:20:25 am")
    s = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]m)?$", "", s)

    # ISO already: YYYY-MM-DD or YYYY-MM
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        if m.group(3):
            return _validate_iso_date(f"{y:04d}-{mo:02d}-{int(m.group(3)):02d}")
        return _validate_iso_date(f"{y:04d}-{mo:02d}")

    # DD MON YYYY (e.g. "1 March 1986", "15 Mayo 1930", "12 Aout 1668", "24 Fevr. 1729")
    m = re.fullmatch(r"(\d{1,2})\s+(?:de\s+)?([a-z]+)\.?\s+(?:de\s+)?(\d{4})", s)
    if m and m.group(2) in _MONTH_MAP:
        return _validate_iso_date(f"{int(m.group(3)):04d}-{_MONTH_MAP[m.group(2)]}-{int(m.group(1)):02d}")

    # MON DD, YYYY or MON DD YYYY (e.g. "February 16, 1750", "January 18 1637")
    m = re.fullmatch(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1) in _MONTH_MAP:
        return _validate_iso_date(f"{int(m.group(3)):04d}-{_MONTH_MAP[m.group(1)]}-{int(m.group(2)):02d}")

    # MON YYYY (e.g. "July 1745", "Mayo 1930", "Sept. 1824")
    m = re.fullmatch(r"([a-z]+)\.?\s+(?:de\s+)?(\d{4})", s)
    if m and m.group(1) in _MONTH_MAP:
        return _validate_iso_date(f"{int(m.group(2)):04d}-{_MONTH_MAP[m.group(1)]}")

    # Numeric MM/DD/YYYY or MM-DD-YYYY (with DD/MM fallback if first > 12)
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= a <= 12:
            res = _validate_iso_date(f"{y:04d}-{a:02d}-{b:02d}")
            if res:
                return res
        if 1 <= b <= 12:
            res = _validate_iso_date(f"{y:04d}-{b:02d}-{a:02d}")
            if res:
                return res

    # YYYY
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return _validate_iso_date(m.group(1))

    return None


def gedcom_date_to_iso(raw: str | None) -> str | None:
    """Convert English, Spanish, or French GEDCOM dates into our schema's genealogical date string."""
    if not raw:
        return None
    s = _fold_ascii(raw).strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None

    # Ranges: BET/BETWEEN <d1> AND <d2>, ENTRE <d1> Y/ET <d2>, FROM <d1> TO <d2>
    m_bet = re.fullmatch(
        r"(?:bet\.?|between|entre|from)\s+(.+?)\s+(?:and|y|et|to)\s+(.+)",
        s,
        re.IGNORECASE,
    )
    if m_bet:
        d1 = _parse_single_gedcom_date(m_bet.group(1))
        d2 = _parse_single_gedcom_date(m_bet.group(2))
        if d1 and d2:
            return _validate_iso_date(f"bet {d1} and {d2}")
        return None

    # Hyphenated year or date-to-year ranges: "1993-2020", "1 Sep 1775-1778"
    m_yr_rng = re.fullmatch(r"(.+?)\s*-\s*(\d{4})", s)
    if m_yr_rng and not re.fullmatch(r"\d{1,2}-\d{1,2}", m_yr_rng.group(1)):
        d1 = _parse_single_gedcom_date(m_yr_rng.group(1))
        d2 = _parse_single_gedcom_date(m_yr_rng.group(2))
        if d1 and d2:
            return _validate_iso_date(f"bet {d1} and {d2}")

    # Multilingual qualifiers: Abt., About, aprox1760, aproximadamente, Before, Bef., Aft., Environ, etc.
    for pat, qual in _QUALIFIER_PREFIXES:
        m_q = pat.match(s)
        if m_q:
            inner = _parse_single_gedcom_date(s[m_q.end() :])
            if inner:
                return _validate_iso_date(f"{qual} {inner}")
            return None

    return _parse_single_gedcom_date(s)


def _clean_token(text: str) -> str:
    """Normalize accented/Unicode characters (e.g., García -> GARCIA) into ASCII ID tokens."""
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").upper()
    return cleaned or "UNKNOWN"


def _parse_gedcom_name(raw_name: str) -> tuple[str, str, str, str | None, str | None, str | None]:
    """Return (primary_name, surname_tok, given_tok, given_name, surname, maternal_surname).

    Handles Hispanic/compound slash surnames such as `Juan /García López/` so the
    paternal surname (`García`) anchors the ID token while preserving both surnames.
    """
    m = re.search(r"/([^/]*)/", raw_name)
    if m:
        raw_surname = m.group(1).strip()
        given = (raw_name[: m.start()] + " " + raw_name[m.end() :]).strip()
        given = re.sub(r"\s+", " ", given)
    else:
        parts = raw_name.strip().split()
        if len(parts) >= 2:
            raw_surname = parts[-1]
            given = " ".join(parts[:-1])
        elif len(parts) == 1:
            raw_surname = parts[0]
            given = ""
        else:
            raw_surname = ""
            given = ""

    surname_parts = raw_surname.split()
    paternal: str | None = None
    maternal: str | None = None
    if len(surname_parts) == 2 and surname_parts[0].lower() not in {"de", "del", "la", "las", "los", "san", "santa", "van", "von", "mc", "mac"}:
        paternal, maternal = surname_parts[0], surname_parts[1]
    elif raw_surname:
        paternal = raw_surname

    primary = " ".join(p for p in (given, raw_surname) if p).strip() or "Unknown"
    given_first = given.split()[0] if given else "UNKNOWN"
    id_surname = paternal or raw_surname or "UNKNOWN"
    return (
        primary,
        _clean_token(id_surname),
        _clean_token(given_first),
        given or None,
        paternal,
        maternal,
    )


def _extract_year(date_str: str | None) -> str:
    if not date_str:
        return "UNKNOWN"
    m = re.search(r"\b(\d{4})\b", date_str)
    return m.group(1) if m else "UNKNOWN"


@dataclass
class ImportSummary:
    destination: Path
    source_id: str
    individuals_imported: int
    relationships_imported: int
    warnings: list[str] = field(default_factory=list)


def import_gedcom(
    gedcom_path: Path | str,
    root: Path | str,
    *,
    dest: str = "staging",
) -> ImportSummary:
    """Import a GEDCOM 5.5.1 or 7.0 file into workspace staging (default) or data/.

    All imported records are marked tentative/speculative and linked to a compiled
    GEDCOM source record so GPS provenance is preserved.
    """
    ged_file = Path(gedcom_path).resolve()
    root_path = Path(root).resolve()
    nodes = parse_gedcom_text(ged_file.read_text(encoding="utf-8", errors="replace"))

    if dest == "staging":
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", ged_file.stem).strip("_") or "import"
        out_root = root_path / "workspace" / "staging" / f"gedcom_{slug}"
        data_out = out_root / "data"
    elif dest == "data":
        out_root = root_path
        data_out = root_path / "data"
    else:
        raise ValueError("dest must be 'staging' or 'data'")

    ind_dir = data_out / "individuals"
    src_dir = data_out / "sources"
    ind_dir.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.date.today()
    slug_id = _clean_token(ged_file.stem)[:24]
    compiled_source_id = f"S-{today.year}-COMPILED-GEDCOM-{slug_id}"

    warnings: list[str] = []
    xref_to_id: dict[str, str] = {}
    used_ids: set[str] = {p.stem for p in ind_dir.glob("*.md")}
    indi_nodes = [n for n in nodes if n.tag == "INDI" and n.xref]
    fam_nodes = [n for n in nodes if n.tag == "FAM"]

    # First pass: assign deterministic IDs
    parsed_indis: list[
        tuple[GedcomNode, str, str, list[str], str | None, str | None, str | None]
    ] = []
    for n in indi_nodes:
        name_nodes = n.find_all("NAME")
        raw_primary = name_nodes[0].value if name_nodes else "Unknown /Unknown/"
        primary_name, surname_tok, given_tok, given_val, pat_val, mat_val = _parse_gedcom_name(
            raw_primary
        )
        variants = []
        for extra_name in name_nodes[1:]:
            v_name, *_ = _parse_gedcom_name(extra_name.value)
            if v_name != primary_name and v_name not in variants:
                variants.append(v_name)

        birt = n.find_first("BIRT")
        b_date_raw = birt.find_first("DATE").value if birt and birt.find_first("DATE") else None
        b_iso = gedcom_date_to_iso(b_date_raw)
        year_tok = _extract_year(b_iso)

        base_id = f"I-{surname_tok}-{given_tok}-{year_tok}"
        cid = base_id
        counter = 0
        while cid in used_ids:
            counter += 1
            cid = f"{base_id}-{counter:02d}"
        used_ids.add(cid)
        assert n.xref is not None
        xref_to_id[n.xref] = cid
        parsed_indis.append((n, cid, primary_name, variants, given_val, pat_val, mat_val))

    # Write individual files
    event_tag_map = {
        "RESI": "residence",
        "OCCU": "occupation",
        "BURI": "burial",
        "CENS": "census",
        "IMMI": "immigration",
        "NATU": "naturalization",
        "BAPM": "baptism",
        "CHR": "baptism",
        "PROB": "probate",
        "EDUC": "education",
    }

    cited_individuals: list[str] = []
    for n, cid, primary_name, variants, given_val, pat_val, mat_val in parsed_indis:
        sex_node = n.find_first("SEX")
        sex = sex_node.value.strip().upper() if sex_node else "U"
        if sex not in {"M", "F", "X", "U"}:
            sex = "U"

        meta: dict[str, Any] = {
            "id": cid,
            "primary_name": primary_name,
        }
        if given_val:
            meta["given_name"] = given_val
        if pat_val:
            meta["surname"] = pat_val
        if mat_val:
            meta["maternal_surname"] = mat_val
        meta["name_variants"] = variants
        meta["sex"] = sex
        meta["living"] = False

        unparsed_notes: list[str] = []

        birt = n.find_first("BIRT")
        if birt:
            b_date_node = birt.find_first("DATE")
            b_plac_node = birt.find_first("PLAC")
            b_iso = gedcom_date_to_iso(b_date_node.value) if b_date_node else None
            if b_date_node and b_date_node.value.strip() and not b_iso:
                raw_val = b_date_node.value.strip()
                warnings.append(f"{cid}: could not parse birth date '{raw_val}'")
                unparsed_notes.append(f"Original unparsed GEDCOM birth date: `{raw_val}`")
            meta["birth"] = {
                "date": b_iso,
                "place": b_plac_node.value.strip() if b_plac_node else None,
                "sources": [compiled_source_id],
            }

        deat = n.find_first("DEAT")
        if deat:
            d_date_node = deat.find_first("DATE")
            d_plac_node = deat.find_first("PLAC")
            d_iso = gedcom_date_to_iso(d_date_node.value) if d_date_node else None
            if d_date_node and d_date_node.value.strip() and not d_iso:
                raw_val = d_date_node.value.strip()
                warnings.append(f"{cid}: could not parse death date '{raw_val}'")
                unparsed_notes.append(f"Original unparsed GEDCOM death date: `{raw_val}`")
            meta["death"] = {
                "date": d_iso,
                "place": d_plac_node.value.strip() if d_plac_node else None,
                "sources": [compiled_source_id],
            }

        events: list[dict[str, Any]] = []
        for tag, ev_type in event_tag_map.items():
            for ev_node in n.find_all(tag):
                ed = ev_node.find_first("DATE")
                ep = ev_node.find_first("PLAC")
                ev_iso = gedcom_date_to_iso(ed.value) if ed else None
                if ed and ed.value.strip() and not ev_iso:
                    raw_val = ed.value.strip()
                    warnings.append(f"{cid}: could not parse {ev_type} date '{raw_val}'")
                    unparsed_notes.append(f"Original unparsed GEDCOM {ev_type} date: `{raw_val}`")
                events.append(
                    {
                        "type": ev_type,
                        "date": ev_iso,
                        "place": ep.value.strip() if ep else None,
                        "description": ev_node.value.strip() or None,
                        "sources": [compiled_source_id],
                    }
                )
        if events:
            meta["events"] = events

        # Ensure at least one source reference on the individual
        if "birth" not in meta and "death" not in meta and not events:
            meta["events"] = [
                {
                    "type": "other",
                    "date": None,
                    "place": None,
                    "description": f"Imported from GEDCOM ({ged_file.name})",
                    "sources": [compiled_source_id],
                }
            ]

        meta["status"] = "tentative"

        notes_text = [nt.full_value() for nt in n.find_all("NOTE") if nt.full_value()] + unparsed_notes
        body_lines = [
            "## Biographical Summary",
            f"Imported from compiled GEDCOM file `{ged_file.name}` (`[{compiled_source_id}]`). Verify against primary records before marking confirmed.",
        ]
        if notes_text:
            body_lines.extend(["", "## Imported GEDCOM Notes"])
            for nt in notes_text:
                body_lines.append(f"- {nt}")

        (ind_dir / f"{cid}.md").write_text(
            render_frontmatter(meta, "\n".join(body_lines) + "\n"),
            encoding="utf-8",
        )
        cited_individuals.append(cid)

    # Relationships from FAM records
    relationships: list[dict[str, Any]] = []
    for fam in fam_nodes:
        husb = fam.find_first("HUSB")
        wife = fam.find_first("WIFE")
        parents = []
        if husb and husb.value.strip() in xref_to_id:
            parents.append(xref_to_id[husb.value.strip()])
        if wife and wife.value.strip() in xref_to_id:
            parents.append(xref_to_id[wife.value.strip()])

        if len(parents) == 2:
            marr = fam.find_first("MARR")
            m_date = (
                gedcom_date_to_iso(marr.find_first("DATE").value)
                if marr and marr.find_first("DATE")
                else None
            )
            m_place = (
                marr.find_first("PLAC").value.strip()
                if marr and marr.find_first("PLAC")
                else None
            )
            relationships.append(
                {
                    "type": "marriage",
                    "person_1": parents[0],
                    "person_2": parents[1],
                    "date": m_date,
                    "place": m_place,
                    "confidence": "speculative",
                    "sources": [compiled_source_id],
                    "notes": f"Imported from {ged_file.name}",
                }
            )

        for chil in fam.find_all("CHIL"):
            child_id = xref_to_id.get(chil.value.strip())
            if not child_id:
                continue
            for parent_id in parents:
                relationships.append(
                    {
                        "type": "parent_child",
                        "person_1": parent_id,
                        "person_2": child_id,
                        "subtype": "unknown",
                        "confidence": "speculative",
                        "sources": [compiled_source_id],
                        "notes": f"Imported from {ged_file.name}",
                    }
                )

    rel_file = data_out / "relationships.yaml"
    existing_rels: list[dict[str, Any]] = []
    if dest == "data" and rel_file.exists():
        loaded = yaml.safe_load(rel_file.read_text(encoding="utf-8")) or {}
        existing_rels = loaded.get("relationships", []) or []

    rel_file.write_text(
        yaml.safe_dump(
            {"relationships": existing_rels + relationships},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    # Write compiled GEDCOM source file
    src_meta = {
        "id": compiled_source_id,
        "title": f"Imported GEDCOM Tree ({ged_file.name})",
        "record_type": "compiled",
        "date": today.isoformat(),
        "jurisdiction": None,
        "repository": f"Local file: {ged_file.name}",
        "source_type": "authored",
        "information_type": "secondary",
        "individuals_cited": cited_individuals,
    }
    src_body = (
        "## Formal Citation\n"
        f"Compiled family tree exported as `{ged_file.name}`, imported on {today.isoformat()}.\n\n"
        "## Provenance Note\n"
        "Compiled GEDCOM trees are authored/secondary leads. Verify every vital date, place, "
        "and relationship against primary archival records before promoting records to `confirmed`.\n"
    )
    (src_dir / f"{compiled_source_id}.md").write_text(
        render_frontmatter(src_meta, src_body),
        encoding="utf-8",
    )

    return ImportSummary(
        destination=out_root,
        source_id=compiled_source_id,
        individuals_imported=len(cited_individuals),
        relationships_imported=len(relationships),
        warnings=warnings,
    )


def _format_gedcom_name(
    primary_name: str,
    *,
    given_name: str | None = None,
    surname: str | None = None,
    maternal_surname: str | None = None,
) -> str:
    """Format a name in GEDCOM `Given /Surname/` syntax, preserving Hispanic double surnames."""
    if surname or maternal_surname:
        full_surname = " ".join(p for p in (surname, maternal_surname) if p).strip()
        given = given_name
        if not given:
            # Strip trailing surname tokens from primary_name to derive given name
            if full_surname and primary_name.endswith(full_surname):
                given = primary_name[: -len(full_surname)].strip()
            else:
                given = primary_name.replace(full_surname, "").strip()
        if given and full_surname:
            return f"{given} /{full_surname}/"
        if full_surname:
            return f"/{full_surname}/"
    parts = primary_name.strip().split()
    if len(parts) >= 2:
        return f"{' '.join(parts[:-1])} /{parts[-1]}/"
    if len(parts) == 1:
        return f"/{parts[0]}/"
    return "/Unknown/"


def export_gedcom(
    root: Path | str,
    out_path: Path | str,
    *,
    version: str = "7.0",
    redact_living: bool = True,
) -> Path:
    """Export `<root>/data/` to GEDCOM 7.0 (default) or 5.5.1, redacting living individuals by default."""
    if version not in {"7.0", "5.5.1"}:
        raise ValueError("GEDCOM version must be '7.0' or '5.5.1'")

    ds = load_dataset(root)
    out_file = Path(out_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    ind_xref: dict[str, str] = {}
    exportable_inds = sorted(
        iid for iid, ind in ds.individuals.items() if ind.data.status != "excluded"
    )
    for idx, iid in enumerate(exportable_inds, start=1):
        ind_xref[iid] = f"@I{idx}@"

    src_xref: dict[str, str] = {}
    exportable_srcs = sorted(
        sid for sid, src in ds.sources.items() if src.data.relevance != "excluded"
    )
    for idx, sid in enumerate(exportable_srcs, start=1):
        src_xref[sid] = f"@S{idx}@"

    # Build FAM records from marriage + parent_child edges
    # Group children by frozenset of parents, and attach matching marriages
    child_parents: dict[str, set[str]] = defaultdict(set)
    for rel in ds.relationships:
        if rel.type == "parent_child" and rel.person_1 in ind_xref and rel.person_2 in ind_xref:
            child_parents[rel.person_2].add(rel.person_1)

    families: dict[tuple[str, ...], dict[str, Any]] = {}

    # Seed with marriage edges
    for rel in ds.relationships:
        if rel.type == "marriage" and rel.person_1 in ind_xref and rel.person_2 in ind_xref:
            key = tuple(sorted((rel.person_1, rel.person_2)))
            fam = families.setdefault(key, {"parents": list(key), "children": [], "marriage": None})
            fam["marriage"] = rel

    # Attach children to matching parent tuple
    for child_id, parents in sorted(child_parents.items()):
        key = tuple(sorted(parents))
        fam = families.setdefault(key, {"parents": list(key), "children": [], "marriage": None})
        if child_id not in fam["children"]:
            fam["children"].append(child_id)

    fam_xref: dict[tuple[str, ...], str] = {}
    fams_by_person: dict[str, list[str]] = defaultdict(list)
    famc_by_person: dict[str, list[str]] = defaultdict(list)
    for idx, (key, fam) in enumerate(sorted(families.items()), start=1):
        fx = f"@F{idx}@"
        fam_xref[key] = fx
        for p in fam["parents"]:
            fams_by_person[p].append(fx)
        for c in fam["children"]:
            famc_by_person[c].append(fx)

    lines: list[str] = [
        "0 HEAD",
        "1 GEDC",
        f"2 VERS {version}",
    ]
    if version == "5.5.1":
        lines.append("2 FORM LINEAGE-LINKED")
        lines.append("1 CHAR UTF-8")
    lines.extend(
        [
            "1 SOUR genealogy-workspace",
            f"2 VERS {__version__}",
            f"1 DATE {datetime.date.today().strftime('%d %b %Y').upper()}",
        ]
    )

    event_to_tag = {
        "residence": "RESI",
        "occupation": "OCCU",
        "burial": "BURI",
        "census": "CENS",
        "immigration": "IMMI",
        "naturalization": "NATU",
        "baptism": "BAPM",
        "probate": "PROB",
        "education": "EDUC",
    }

    for iid in exportable_inds:
        ind = ds.individuals[iid].data
        xref = ind_xref[iid]
        lines.append(f"0 {xref} INDI")
        if redact_living and ind.living:
            lines.append("1 NAME Living /Private/")
            lines.append("1 SEX U")
            lines.append("1 NOTE Redacted (living individual)")
        else:
            lines.append(
                f"1 NAME {_format_gedcom_name(ind.primary_name, given_name=ind.given_name, surname=ind.surname, maternal_surname=ind.maternal_surname)}"
            )
            for variant in ind.name_variants:
                lines.append(f"1 NAME {_format_gedcom_name(variant)}")
            lines.append(f"1 SEX {ind.sex}")
            if ind.birth:
                lines.append("1 BIRT")
                if ind.birth.date:
                    pd = parse_genealogy_date(ind.birth.date)
                    if pd:
                        lines.append(f"2 DATE {pd.to_gedcom()}")
                if ind.birth.place:
                    lines.append(f"2 PLAC {ind.birth.place}")
                for sid in ind.birth.sources:
                    if sid in src_xref:
                        lines.append(f"2 SOUR {src_xref[sid]}")
            if ind.death:
                lines.append("1 DEAT")
                if ind.death.date:
                    pd = parse_genealogy_date(ind.death.date)
                    if pd:
                        lines.append(f"2 DATE {pd.to_gedcom()}")
                if ind.death.place:
                    lines.append(f"2 PLAC {ind.death.place}")
                for sid in ind.death.sources:
                    if sid in src_xref:
                        lines.append(f"2 SOUR {src_xref[sid]}")
            for ev in ind.events:
                tag = event_to_tag.get(ev.type, "EVEN")
                val = f" {ev.description}" if (tag == "OCCU" and ev.description) else ""
                lines.append(f"1 {tag}{val}")
                if tag == "EVEN":
                    lines.append(f"2 TYPE {ev.type}")
                if ev.date:
                    pd = parse_genealogy_date(ev.date)
                    if pd:
                        lines.append(f"2 DATE {pd.to_gedcom()}")
                if ev.place:
                    lines.append(f"2 PLAC {ev.place}")
                for sid in ev.sources:
                    if sid in src_xref:
                        lines.append(f"2 SOUR {src_xref[sid]}")

        for fx in fams_by_person.get(iid, []):
            lines.append(f"1 FAMS {fx}")
        for fx in famc_by_person.get(iid, []):
            lines.append(f"1 FAMC {fx}")

    for key, fam in sorted(families.items()):
        fx = fam_xref[key]
        lines.append(f"0 {fx} FAM")
        parents = fam["parents"]
        assigned_husb = False
        assigned_wife = False
        for pid in parents:
            sex = ds.individuals[pid].data.sex
            if sex == "M" and not assigned_husb:
                lines.append(f"1 HUSB {ind_xref[pid]}")
                assigned_husb = True
            elif sex == "F" and not assigned_wife:
                lines.append(f"1 WIFE {ind_xref[pid]}")
                assigned_wife = True
            elif not assigned_husb:
                lines.append(f"1 HUSB {ind_xref[pid]}")
                assigned_husb = True
            else:
                lines.append(f"1 WIFE {ind_xref[pid]}")
                assigned_wife = True

        marr = fam["marriage"]
        any_living_spouse = any(ds.individuals[p].data.living for p in parents)
        if marr and not (redact_living and any_living_spouse):
            lines.append("1 MARR")
            if marr.date:
                pd = parse_genealogy_date(marr.date)
                if pd:
                    lines.append(f"2 DATE {pd.to_gedcom()}")
            if marr.place:
                lines.append(f"2 PLAC {marr.place}")
            for sid in marr.sources:
                if sid in src_xref:
                    lines.append(f"2 SOUR {src_xref[sid]}")

        for cid in fam["children"]:
            lines.append(f"1 CHIL {ind_xref[cid]}")

    for sid in exportable_srcs:
        src = ds.sources[sid].data
        lines.append(f"0 {src_xref[sid]} SOUR")
        lines.append(f"1 TITL {src.title}")
        if src.repository:
            lines.append(f"1 PUBL {src.repository}")
        if src.url:
            lines.append(f"1 NOTE URL: {src.url}")
        for lnk in src.links:
            auth_tag = " [auth required]" if lnk.requires_auth else ""
            lines.append(f"1 NOTE {lnk.provider}{auth_tag}: {lnk.url}")

    lines.append("0 TRLR")
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_file
