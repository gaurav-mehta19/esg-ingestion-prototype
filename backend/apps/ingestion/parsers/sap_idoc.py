"""
SAP MBGMCR goods-movement parser.

Why this is a CSV reader (and not a fixed-width IDoc binary parser):
  Per research-notes §1.4 the wire-format MBGMCR03 IDoc is a 524-byte
  EDIDC control record followed by 1,063-byte EDID4 data records with a
  1,000-byte positional SDATA payload. Parsing that requires the per-
  release segment definition from SAP's WE31, which differs by SAP
  version. In practice most SAP teams hand ESG vendors a CSV export
  (LSMW / SE16N / custom ABAP program) that flattens IDoc segments into
  one row per item-line, with German column headers because the source
  SAP system runs in German locale. That CSV is what this parser handles.

  When a real wire-format IDoc arrives, we add a second parser module
  (parsers/sap_idoc_binary.py) that produces the same internal dict shape;
  the normalizer downstream is unchanged.

Output: a list of dicts, one per item-line, with verbatim raw values plus
a parse_warnings list. NO normalization happens here — that's the next
stage. Parsing only:
  - turns text into structured records
  - flags individually broken rows with explicit reasons
  - never raises on a single bad row (one bad row must not kill the batch)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


# Column header aliases — German first (most realistic) plus English
# fallbacks for systems running in English locale. Keys are normalized
# (stripped, lowercased) so the parser is tolerant of whitespace and case.
HEADER_ALIASES: dict[str, str] = {
    # Idoc envelope
    "idoc_number": "idoc_number",
    "idoc-nr": "idoc_number",
    "idocnr": "idoc_number",
    # Header
    "buchungsdatum": "raw_budat",
    "budat": "raw_budat",
    "posting date": "raw_budat",
    "belegdatum": "raw_bldat",
    "bldat": "raw_bldat",
    "document date": "raw_bldat",
    "referenzbeleg": "ref_doc_no",
    "ref_doc_no": "ref_doc_no",
    "gm-code": "gm_code",
    "gm_code": "gm_code",
    # Item
    "position": "item_line",
    "zeile": "item_line",
    "item_line": "item_line",
    "materialnummer": "matnr",
    "matnr": "matnr",
    "material": "matnr",
    "werk": "werks",
    "werks": "werks",
    "plant": "werks",
    "lagerort": "lgort",
    "lgort": "lgort",
    "bewegungsart": "bwart",
    "bwart": "bwart",
    "movement type": "bwart",
    "menge": "entry_qnt",
    "entry_qnt": "entry_qnt",
    "quantity": "entry_qnt",
    "mengeneinheit": "entry_uom",
    "meins": "entry_uom",
    "entry_uom": "entry_uom",
    "uom": "entry_uom",
    "bestellnummer": "ebeln",
    "ebeln": "ebeln",
    "po_number": "ebeln",
    "bestellposition": "ebelp",
    "ebelp": "ebelp",
    "buchungskreis": "bukrs",
    "bukrs": "bukrs",
    "company_code": "bukrs",
    "lieferant": "lifnr",
    "lifnr": "lifnr",
    "vendor": "lifnr",
    "nettobetrag": "dmbtr",
    "dmbtr": "dmbtr",
    "amount": "dmbtr",
    "waehrung": "waers",
    "waers": "waers",
    "currency": "waers",
}


# BWART values that represent a reversal/cancellation. Defined here so
# the parser can pre-tag rows; the normalizer does not need to re-evaluate.
REVERSAL_BWART_CODES = frozenset({"102", "122", "162", "202", "262", "292", "502"})


@dataclass
class ParsedRow:
    """One CSV row after parsing. Mirrors SapGoodsMovementStaging fields."""

    # Envelope
    idoc_number: str = ""
    # Header
    raw_budat: str = ""
    raw_bldat: str = ""
    parsed_budat: date | None = None
    parsed_bldat: date | None = None
    ref_doc_no: str = ""
    gm_code: str = ""
    # Item
    item_line: int = 0
    matnr: str = ""
    werks: str = ""
    lgort: str = ""
    bwart: str = ""
    entry_qnt: Decimal | None = None
    entry_uom: str = ""
    ebeln: str = ""
    ebelp: str = ""
    bukrs: str = ""
    lifnr: str = ""
    dmbtr: Decimal | None = None
    waers: str = ""
    # Derived
    is_reversal: bool = False
    # Parser output
    parse_warnings: list[dict] = field(default_factory=list)
    # If set, the parser believes the row is unrecoverable. The normalizer
    # will store it in staging with status='rejected' and skip it.
    rejected: bool = False
    rejection_reason: str = ""


def _normalize_header(h: str) -> str:
    return h.strip().lower().lstrip("﻿")


def _parse_sap_date(raw: str) -> tuple[date | None, str | None]:
    """
    SAP IDoc dates are YYYYMMDD strings. Returns (date_or_None, warning_or_None).
    Empty input is not an error — some date fields are optional.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if len(raw) != 8 or not raw.isdigit():
        return None, f"date '{raw}' is not 8-digit YYYYMMDD"
    try:
        return datetime.strptime(raw, "%Y%m%d").date(), None
    except ValueError as e:
        return None, f"date '{raw}' is unparseable: {e}"


def _parse_decimal(raw: str) -> tuple[Decimal | None, str | None]:
    raw = (raw or "").strip()
    if not raw:
        return None, None
    # SAP sometimes exports decimals with comma separator (German locale).
    # Convert before Decimal parse.
    raw = raw.replace(",", ".")
    try:
        return Decimal(raw), None
    except InvalidOperation:
        return None, f"value '{raw}' is not a number"


def parse_sap_csv(csv_text: str) -> list[ParsedRow]:
    """
    Parse a SAP goods-movement CSV export into a list of ParsedRow.

    Error policy: per-row. A row with a bad date gets a parse_warning. A
    row missing all key fields (no idoc_number AND no item_line) gets
    rejected=True. Parsing as a whole never raises — broken rows surface
    in the dashboard with reasons, not as a 500 to the uploader.
    """

    reader = csv.DictReader(csv_text.splitlines())
    if reader.fieldnames is None:
        return []

    # Build a map: original column name → internal field name. Unrecognized
    # columns are silently dropped (they're informational columns the SAP
    # team added; not our problem).
    col_to_field: dict[str, str] = {}
    for original in reader.fieldnames:
        key = _normalize_header(original)
        if key in HEADER_ALIASES:
            col_to_field[original] = HEADER_ALIASES[key]

    rows: list[ParsedRow] = []
    for raw_row in reader:
        # Skip fully blank rows (common when a CSV has a trailing newline).
        if not any((v or "").strip() for v in raw_row.values()):
            continue

        out = ParsedRow()
        for original_col, internal in col_to_field.items():
            value = (raw_row.get(original_col) or "").strip()

            if internal in {"raw_budat", "raw_bldat"}:
                setattr(out, internal, value)
                # Parse alongside, capture warning if any
                target = "parsed_budat" if internal == "raw_budat" else "parsed_bldat"
                parsed, warn = _parse_sap_date(value)
                setattr(out, target, parsed)
                if warn:
                    out.parse_warnings.append({"field": internal, "warning": warn})

            elif internal == "item_line":
                try:
                    out.item_line = int(value) if value else 0
                except ValueError:
                    out.parse_warnings.append(
                        {"field": "item_line", "warning": f"'{value}' is not an integer"}
                    )

            elif internal == "entry_qnt":
                parsed, warn = _parse_decimal(value)
                out.entry_qnt = parsed
                if warn:
                    out.parse_warnings.append({"field": "entry_qnt", "warning": warn})

            elif internal == "dmbtr":
                parsed, warn = _parse_decimal(value)
                out.dmbtr = parsed
                if warn:
                    out.parse_warnings.append({"field": "dmbtr", "warning": warn})

            else:
                setattr(out, internal, value)

        # Derived flag — reversal detection.
        out.is_reversal = out.bwart in REVERSAL_BWART_CODES

        # Reject criteria: a row with no IDoc number AND no item line is
        # almost certainly junk (blank trailer, stray text). Anything with
        # at least one identifier proceeds — even if other fields are bad,
        # we want it in staging so the analyst can see it.
        if not out.idoc_number and not out.item_line:
            out.rejected = True
            out.rejection_reason = "row has neither idoc_number nor item_line"

        rows.append(out)

    return rows
