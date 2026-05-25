"""
Green Button "Download My Data" CSV parser.

Two structural quirks vs a normal CSV that this parser handles:

  1. Metadata preamble. PG&E (and other Green Button exports) prefix the
     file with 4–5 colon-delimited metadata lines:
         Customer Name: Acme Corporation
         Service Account: 1234567890
         Rate Schedule: E-20 Medium General Demand-Metered Service
         Start Date: 01/01/2024
         End Date: 12/31/2024
     Then a blank line, then the column header row, then data. The
     `Service Account` value is the only place the meter identifier
     appears — without it the data rows would be unattributable to a
     UsagePoint.

  2. Cost is a $-prefixed string ('$0.08'), not a numeric value. Date is
     M/D/YYYY (not ISO 8601). Notes carries the free-text 'Estimated'
     flag. All three are preserved verbatim AND parsed — see ParsedInterval.

Per-row error policy: a row with an unparseable timestamp or non-numeric
usage is still emitted (with parse_warnings and a reject flag set in the
intake layer) so it shows up in the dashboard. Don't drop rows silently —
the analyst needs to see them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HEADER_ALIASES: dict[str, str] = {
    "type": "type",
    "date": "raw_date",
    "start time": "raw_start_time",
    "end time": "raw_end_time",
    "usage": "raw_usage",
    "consumption": "raw_usage",
    "units": "raw_units",
    "unit": "raw_units",
    "cost": "raw_cost",
    "amount": "raw_cost",
    "notes": "raw_notes",
}


@dataclass
class GreenButtonMetadata:
    """The preamble values. usage_point_id comes from Service Account."""

    customer_name: str = ""
    service_account: str = ""
    rate_schedule: str = ""
    start_date: str = ""
    end_date: str = ""


@dataclass
class ParsedInterval:
    """One interval row after parsing. Mirrors UtilityIntervalStaging."""

    # Raw values (verbatim)
    raw_date: str = ""
    raw_start_time: str = ""
    raw_end_time: str = ""
    raw_usage: str = ""
    raw_units: str = ""
    raw_cost: str = ""
    raw_notes: str = ""
    # Parsed
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    interval_length_minutes: int | None = None
    usage_value: Decimal | None = None
    usage_unit: str = ""
    cost_value: Decimal | None = None
    cost_currency: str = ""
    is_estimated: bool = False
    # Parser output
    parse_warnings: list[dict] = field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""


@dataclass
class ParseResult:
    metadata: GreenButtonMetadata
    rows: list[ParsedInterval]


def _parse_currency(raw: str) -> tuple[Decimal | None, str, str | None]:
    """
    '$0.08' → (Decimal('0.08'), 'USD', None)
    ''      → (None, '', None)
    'oops'  → (None, '', "cost 'oops' is not a number")
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "", None
    currency = ""
    if raw.startswith("$"):
        currency = "USD"
        raw = raw[1:]
    elif raw.startswith("£"):
        currency = "GBP"
        raw = raw[1:]
    elif raw.startswith("€"):
        currency = "EUR"
        raw = raw[1:]
    try:
        return Decimal(raw), currency, None
    except InvalidOperation:
        return None, currency, f"cost '{raw}' is not a number"


def _parse_local_datetime(
    raw_date: str, raw_time: str, tz_name: str
) -> tuple[datetime | None, str | None]:
    raw_date = (raw_date or "").strip()
    raw_time = (raw_time or "").strip()
    if not raw_date or not raw_time:
        return None, "missing date or time"
    try:
        # M/D/YYYY first (the PG&E format), fall back to D/M/YYYY for UK
        # exporters, then ISO. We don't auto-detect; we try in order.
        for fmt in ("%m/%d/%Y %H:%M", "%-m/%-d/%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                naive = datetime.strptime(f"{raw_date} {raw_time}", fmt)
                break
            except ValueError:
                continue
        else:
            return None, f"datetime '{raw_date} {raw_time}' not in any expected format"
    except ValueError as e:
        return None, str(e)

    if not tz_name:
        # We can't assume UTC — Green Button local time without TZ is
        # genuinely ambiguous, especially at DST transitions. Mark as
        # warning; the staging row will land in needs_timezone_config.
        return None, "no timezone configured for data source"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return None, f"unknown timezone '{tz_name}'"
    return naive.replace(tzinfo=tz), None


def parse_green_button_csv(csv_text: str, *, timezone_name: str = "") -> ParseResult:
    """
    Parse a Green Button DMD CSV. timezone_name should come from the
    DataSource config — Green Button itself doesn't include a TZ.
    """

    lines = csv_text.splitlines()
    metadata = GreenButtonMetadata()

    # Phase 1: scan for the preamble. Loop until we hit the data header row
    # (the one starting with 'Type' or 'Date').
    data_start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Metadata lines are 'Key: Value'. We stop when a line looks like
        # a CSV header (has commas, no colon in the field-name position).
        if ":" in stripped and "," not in stripped.split(":", 1)[0]:
            key, _, value = stripped.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "customer name":
                metadata.customer_name = value
            elif key == "service account":
                metadata.service_account = value
            elif key == "rate schedule":
                metadata.rate_schedule = value
            elif key == "start date":
                metadata.start_date = value
            elif key == "end date":
                metadata.end_date = value
            continue
        # First line that doesn't look like preamble = the data header.
        data_start_idx = i
        break

    data_text = "\n".join(lines[data_start_idx:])
    reader = csv.DictReader(data_text.splitlines())
    if reader.fieldnames is None:
        return ParseResult(metadata=metadata, rows=[])

    col_to_field: dict[str, str] = {}
    for original in reader.fieldnames:
        key = original.strip().lower().lstrip("﻿")
        if key in HEADER_ALIASES:
            col_to_field[original] = HEADER_ALIASES[key]

    rows: list[ParsedInterval] = []
    for raw_row in reader:
        if not any((v or "").strip() for v in raw_row.values()):
            continue

        out = ParsedInterval()

        # We only emit 'Electric usage' rows. Some files include gas in
        # the same archive. Type=Natural gas usage gets silently skipped.
        row_type = (raw_row.get("Type") or raw_row.get("type") or "").strip().lower()
        if row_type and "electric" not in row_type:
            # Skip gas/water rows — different staging table eventually
            continue

        for original_col, internal in col_to_field.items():
            value = (raw_row.get(original_col) or "").strip()
            setattr(out, internal, value)

        out.usage_unit = out.raw_units
        # Estimated flag derived once at parse time.
        out.is_estimated = "estimated" in out.raw_notes.lower()

        # Parse usage value (decimal)
        if out.raw_usage:
            try:
                out.usage_value = Decimal(out.raw_usage)
            except InvalidOperation:
                out.parse_warnings.append(
                    {"field": "usage", "warning": f"'{out.raw_usage}' is not a number"}
                )

        # Parse cost
        cost, currency, warn = _parse_currency(out.raw_cost)
        out.cost_value = cost
        out.cost_currency = currency
        if warn:
            out.parse_warnings.append({"field": "cost", "warning": warn})

        # Parse timestamps
        start, start_warn = _parse_local_datetime(
            out.raw_date, out.raw_start_time, timezone_name
        )
        end, end_warn = _parse_local_datetime(
            out.raw_date, out.raw_end_time, timezone_name
        )
        out.interval_start = start
        out.interval_end = end
        if start_warn:
            out.parse_warnings.append({"field": "interval_start", "warning": start_warn})
        if end_warn:
            out.parse_warnings.append({"field": "interval_end", "warning": end_warn})
        if start and end:
            delta = end - start
            # Handle the midnight-crossing edge: 23:45 → 00:00. If end <= start,
            # assume the end is the next day.
            if delta.total_seconds() <= 0:
                end = end + timedelta(days=1)
                out.interval_end = end
                delta = end - start
            out.interval_length_minutes = int(delta.total_seconds() // 60)

        rows.append(out)

    return ParseResult(metadata=metadata, rows=rows)
