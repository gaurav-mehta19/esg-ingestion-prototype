"""
Concur expense + itinerary JSON parser.

Real Concur exposes two endpoints:
  - /api/v3.0/expense/entries  → expense records (financial)
  - /api/travel/trip/v1.1      → itinerary records (air/hotel/car segments)

For this prototype, the sample bundles both into one JSON file with an
`entries` array. Each entry has a `record_type` discriminator so the
parser knows which fields to expect.

Why a custom JSON shape (not raw Concur dumps):
  Concur's response shapes differ between v3 and v4 of the expense API,
  and the itinerary v4 is still in preview (research-notes §3.6). A wrapper
  shape lets us add a real Concur fetcher later that adapts whatever the
  API returns into this normalized JSON. The parser stays stable.

Per-row error policy: same as SAP/utility — broken rows still land in
staging with warnings/rejection reasons so the analyst can see them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


@dataclass
class ParsedTravelRecord:
    """Mirrors TravelExpenseStaging."""

    source_system: str = "concur"
    record_type: str = ""

    # Expense entry
    concur_report_id: str = ""
    concur_entry_id: str = ""
    expense_type_code: str = ""
    expense_type_name: str = ""
    spend_category_code: str = ""
    transaction_date: date | None = None
    transaction_amount: Decimal | None = None
    transaction_currency: str = ""
    vendor_description: str = ""
    location_name: str = ""
    location_country: str = ""
    is_personal: bool = False
    journey_distance: Decimal | None = None
    distance_unit: str = ""
    comment_text: str = ""

    # Itinerary linkage
    itinerary_booking_id: str = ""
    linked_itinerary_id: str = ""

    # Air segment
    origin_iata: str = ""
    destination_iata: str = ""
    class_of_service: str = ""
    aircraft_iata_code: str = ""
    flight_number: str = ""
    departure_datetime: datetime | None = None
    arrival_datetime: datetime | None = None

    # Hotel segment
    hotel_checkin_date: date | None = None
    hotel_checkout_date: date | None = None
    room_nights: int | None = None

    # Car segment
    car_acriss_code: str = ""
    car_rental_start: date | None = None
    car_rental_end: date | None = None

    # Parser output
    parse_warnings: list[dict] = field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""


def _parse_decimal(raw, field_name: str, out: ParsedTravelRecord) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        out.parse_warnings.append({"field": field_name, "warning": f"'{raw}' is not a number"})
        return None


def _parse_iso_date(raw, field_name: str, out: ParsedTravelRecord) -> date | None:
    if not raw:
        return None
    try:
        # Concur ships 'YYYY-MM-DDTHH:MM:SS' even for date-only fields.
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError as e:
        out.parse_warnings.append({"field": field_name, "warning": f"unparseable date '{raw}': {e}"})
        return None


def _parse_iso_datetime(raw, field_name: str, out: ParsedTravelRecord) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        out.parse_warnings.append({"field": field_name, "warning": f"unparseable datetime '{raw}': {e}"})
        return None


def parse_concur_json(json_text: str) -> list[ParsedTravelRecord]:
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as e:
        # Whole-file failure: return one rejected row so the run records
        # something visible. The intake layer attaches it to the run.
        bad = ParsedTravelRecord()
        bad.rejected = True
        bad.rejection_reason = f"JSON parse failed: {e}"
        return [bad]

    entries = payload.get("entries") or []
    rows: list[ParsedTravelRecord] = []

    for raw in entries:
        out = ParsedTravelRecord()
        out.record_type = (raw.get("record_type") or "").strip()

        if out.record_type == "expense_entry":
            out.concur_report_id = raw.get("ReportID") or ""
            out.concur_entry_id = raw.get("ID") or ""
            out.expense_type_code = raw.get("ExpenseTypeCode") or ""
            out.expense_type_name = raw.get("ExpenseTypeName") or ""
            out.spend_category_code = raw.get("SpendCategoryCode") or ""
            out.transaction_date = _parse_iso_date(
                raw.get("TransactionDate"), "transaction_date", out
            )
            out.transaction_amount = _parse_decimal(
                raw.get("TransactionAmount"), "transaction_amount", out
            )
            out.transaction_currency = raw.get("TransactionCurrencyCode") or ""
            out.vendor_description = raw.get("VendorDescription") or ""
            out.location_name = raw.get("LocationName") or ""
            out.location_country = raw.get("LocationCountry") or ""
            out.is_personal = bool(raw.get("IsPersonal", False))
            out.journey_distance = _parse_decimal(
                raw.get("JourneyDistance"), "journey_distance", out
            )
            out.distance_unit = raw.get("UnitOfMeasure") or ""
            out.comment_text = raw.get("Comment") or ""
            out.linked_itinerary_id = raw.get("LinkedItineraryID") or ""

            if not out.concur_entry_id:
                out.rejected = True
                out.rejection_reason = "expense entry missing ID"

        elif out.record_type == "air_segment":
            out.itinerary_booking_id = raw.get("BookingID") or ""
            out.origin_iata = (raw.get("StartCityCode") or "").upper()
            out.destination_iata = (raw.get("EndCityCode") or "").upper()
            out.class_of_service = (raw.get("ClassOfService") or "").upper()
            out.aircraft_iata_code = raw.get("AircraftCode") or ""
            out.flight_number = raw.get("FlightNumber") or ""
            out.departure_datetime = _parse_iso_datetime(
                raw.get("StartDateUtc"), "departure_datetime", out
            )
            out.arrival_datetime = _parse_iso_datetime(
                raw.get("EndDateUtc"), "arrival_datetime", out
            )
            out.vendor_description = raw.get("VendorName") or ""

            if not out.itinerary_booking_id:
                out.rejected = True
                out.rejection_reason = "air segment missing BookingID"

        elif out.record_type == "hotel_segment":
            out.itinerary_booking_id = raw.get("BookingID") or ""
            out.vendor_description = raw.get("VendorName") or ""
            out.location_name = raw.get("City") or ""
            out.location_country = raw.get("Country") or ""
            out.hotel_checkin_date = _parse_iso_date(
                raw.get("CheckIn"), "hotel_checkin_date", out
            )
            out.hotel_checkout_date = _parse_iso_date(
                raw.get("CheckOut"), "hotel_checkout_date", out
            )
            if out.hotel_checkin_date and out.hotel_checkout_date:
                out.room_nights = (out.hotel_checkout_date - out.hotel_checkin_date).days

        elif out.record_type == "car_segment":
            out.itinerary_booking_id = raw.get("BookingID") or ""
            out.vendor_description = raw.get("VendorName") or ""
            out.car_acriss_code = raw.get("CarType") or ""
            out.car_rental_start = _parse_iso_date(
                raw.get("StartDate"), "car_rental_start", out
            )
            out.car_rental_end = _parse_iso_date(
                raw.get("EndDate"), "car_rental_end", out
            )

        else:
            out.rejected = True
            out.rejection_reason = f"unknown record_type '{out.record_type}'"

        rows.append(out)

    return rows
