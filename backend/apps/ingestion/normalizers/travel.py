"""
Travel staging → ActivityRecord normalizer.

Differences from SAP and utility (and why):

  - Classification: per-tenant via ExpenseTypeCategoryMapping (the codes
    are configurable per Concur tenant — research-notes §3.5). No global
    fallback: if a code isn't mapped, the row parks in
    'needs_expense_type_mapping' for an analyst.
  - Distance: for air segments, derive great-circle distance from IATA
    codes (apply DEFRA 1.08 uplift). Distance is stored on
    activity_record.computed_distance_km — the only source where this
    derived field is populated. For expense entries WITHOUT a linked
    itinerary, we have no distance; the record still ships but with
    a 'spend_proxy' method label so an emission calculator can route to
    the spend-based factor instead of the distance-based one.
  - Auto-flag: missing itinerary linkage (research-notes §3.12 #4) AND
    expensive air entries without distance (suspicious because air is
    the largest emission lever — analysts want to see them).

What's shared with SAP / utility:
  - Same transition / auto_flag / record_ingestion_event services.
  - Same per-row atomic loop pattern.
  - Same ActivityRecord output schema.

I did NOT abstract:
  - The classifier (it's a one-table lookup specific to expense_type_code).
  - The distance computation (only air segments need it).
  - The currency/quantity handling (travel uses spend amounts as
    quantities in some paths, which SAP/utility don't).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from apps.activity.models import ActivityRecord, GhgScope, ReviewStatus
from apps.activity.services import auto_flag, record_ingestion_event
from apps.core.models import ExpenseTypeCategoryMapping
from apps.ingestion.models import StagingStatus, TravelExpenseStaging
from apps.ingestion.normalizers.iata_distances import compute_flight_distance_km


@dataclass
class NormalizationResult:
    activity_records_created: int = 0
    staging_rows_processed: int = 0
    needs_expense_type_mapping: int = 0
    no_itinerary_linkage: int = 0
    skipped_not_esg: int = 0
    skipped_personal: int = 0
    rejected: int = 0


def _resolve_expense_mapping(
    *, organization_id, data_source_id, code: str
) -> ExpenseTypeCategoryMapping | None:
    return ExpenseTypeCategoryMapping.objects.filter(
        organization_id=organization_id,
        data_source_id=data_source_id,
        raw_expense_type_code=code,
    ).first()


def _find_linked_air_segment(
    *, organization_id, booking_id: str
) -> TravelExpenseStaging | None:
    if not booking_id:
        return None
    return TravelExpenseStaging.objects.filter(
        organization_id=organization_id,
        itinerary_booking_id=booking_id,
        record_type="air_segment",
    ).first()


def normalize_travel_staging_batch(
    *, organization_id, data_source_id, limit: int | None = None
) -> NormalizationResult:
    result = NormalizationResult()
    qs = TravelExpenseStaging.objects.filter(
        organization_id=organization_id,
        staging_status__in=[
            StagingStatus.PENDING,
            "needs_expense_type_mapping",
            "no_itinerary_linkage",
        ],
    ).order_by("ingested_at")
    if limit:
        qs = qs[:limit]

    for staging in qs:
        result.staging_rows_processed += 1
        with transaction.atomic():
            _normalize_one(staging, data_source_id=data_source_id, result=result)
    return result


def _normalize_one(
    staging: TravelExpenseStaging,
    *,
    data_source_id,
    result: NormalizationResult,
) -> None:
    warnings = list(staging.parse_warnings or [])

    # Itinerary segments (air/hotel/car) are NOT directly converted to
    # ActivityRecords on their own — they enrich the matching expense
    # entry. Mark them normalized with a note so they don't recycle.
    if staging.record_type != "expense_entry":
        staging.staging_status = StagingStatus.NORMALIZED
        staging.parse_warnings = warnings + [
            {
                "field": "record_type",
                "warning": (
                    "itinerary segment normalized as enrichment data; "
                    "joined into expense entry's activity_record"
                ),
            }
        ]
        staging.save()
        return

    # --- 1. Personal expenses excluded from ESG -------------------------
    if staging.is_personal:
        staging.staging_status = StagingStatus.NORMALIZED
        staging.parse_warnings = warnings + [
            {"field": "is_personal", "warning": "marked personal; excluded from ESG"}
        ]
        staging.save()
        result.skipped_personal += 1
        return

    # --- 2. Classification via per-tenant expense-type mapping ----------
    if not staging.expense_type_code:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing expense_type_code"
        staging.save()
        result.rejected += 1
        return

    mapping = _resolve_expense_mapping(
        organization_id=staging.organization_id,
        data_source_id=data_source_id,
        code=staging.expense_type_code,
    )
    if mapping is None:
        staging.staging_status = "needs_expense_type_mapping"
        staging.rejection_reason = (
            f"no ExpenseTypeCategoryMapping for code '{staging.expense_type_code}' "
            f"(name: '{staging.expense_type_name}'). Configure mapping in admin and "
            f"re-run normalization."
        )
        staging.save()
        result.needs_expense_type_mapping += 1
        return

    if not mapping.is_esg_relevant:
        staging.staging_status = StagingStatus.NORMALIZED
        staging.parse_warnings = warnings + [
            {"field": "expense_type_code", "warning": "mapping marks as not-ESG-relevant"}
        ]
        staging.save()
        result.skipped_not_esg += 1
        return

    # --- 3. Date check -----------------------------------------------
    if staging.transaction_date is None:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "transaction_date missing — cannot assign to a reporting period"
        staging.save()
        result.rejected += 1
        return

    # --- 4. Build raw + normalized quantity per activity type --------
    #
    # The "quantity" on a travel activity depends on the activity type:
    #   flight       → passenger_km (computed from IATA codes if available;
    #                  else NULL with method=spend_proxy)
    #   hotel        → room_nights (from linked hotel segment)
    #   car_rental   → spend amount (no mileage in standard Concur)
    #   personal_mileage → JourneyDistance value (always present)
    #   taxi / rail  → spend amount

    raw_quantity: Decimal | None = None
    raw_unit = ""
    normalized_quantity: Decimal | None = None
    normalized_unit = ""
    distance_km: float | None = None
    distance_method = ""
    no_itinerary = False
    notes_parts: list[str] = [
        f"Source: Concur expense {staging.concur_entry_id}",
        f"ExpenseTypeCode: {staging.expense_type_code} → {mapping.canonical_activity}",
    ]

    if mapping.canonical_activity == "flight":
        # Try to find the linked air segment
        air_segment = None
        if staging.linked_itinerary_id:
            air_segment = _find_linked_air_segment(
                organization_id=staging.organization_id,
                booking_id=staging.linked_itinerary_id,
            )
        if air_segment and air_segment.origin_iata and air_segment.destination_iata:
            distance, method = compute_flight_distance_km(
                air_segment.origin_iata, air_segment.destination_iata
            )
            if distance is not None:
                distance_km = distance
                distance_method = method
                raw_quantity = Decimal(str(distance))
                raw_unit = "km"
                normalized_quantity = raw_quantity
                normalized_unit = "passenger_km"
                notes_parts.append(
                    f"Distance: {air_segment.origin_iata}→{air_segment.destination_iata} "
                    f"= {distance:.1f} km ({method}); class={air_segment.class_of_service}"
                )
            else:
                no_itinerary = True
                notes_parts.append(f"Distance failed: {method}; fallback to spend_proxy")
        else:
            no_itinerary = True
            notes_parts.append(
                "No linked air segment in itinerary; fallback to spend_proxy"
            )

        if no_itinerary:
            # Spend-based fallback: raw quantity = transaction amount, unit
            # = currency. The emission calculator will route this to a
            # spend-based factor (when emission factors are wired up).
            raw_quantity = staging.transaction_amount or Decimal("0")
            raw_unit = staging.transaction_currency or "USD"
            normalized_quantity = raw_quantity
            normalized_unit = raw_unit
            distance_method = "spend_proxy"

    elif mapping.canonical_activity == "hotel":
        nights = None
        hotel_seg = TravelExpenseStaging.objects.filter(
            organization_id=staging.organization_id,
            itinerary_booking_id=staging.linked_itinerary_id,
            record_type="hotel_segment",
        ).first() if staging.linked_itinerary_id else None
        if hotel_seg and hotel_seg.room_nights:
            nights = hotel_seg.room_nights
            notes_parts.append(
                f"Room nights from itinerary: {hotel_seg.hotel_checkin_date} → "
                f"{hotel_seg.hotel_checkout_date} = {nights}"
            )
        else:
            # Hotel without itinerary: we can't infer nights from an expense
            # entry alone (the comment might say '2 nights' but it's free
            # text and unreliable). Fall back to spend proxy.
            no_itinerary = True
            notes_parts.append("No linked hotel segment; spend_proxy")

        if nights is not None:
            raw_quantity = Decimal(str(nights))
            raw_unit = "night"
            normalized_quantity = raw_quantity
            normalized_unit = "room_night"
        else:
            raw_quantity = staging.transaction_amount or Decimal("0")
            raw_unit = staging.transaction_currency or "USD"
            normalized_quantity = raw_quantity
            normalized_unit = raw_unit

    elif mapping.canonical_activity == "personal_mileage":
        if staging.journey_distance is None:
            staging.staging_status = StagingStatus.REJECTED
            staging.rejection_reason = "personal mileage expense missing JourneyDistance"
            staging.save()
            result.rejected += 1
            return
        raw_quantity = staging.journey_distance
        raw_unit = staging.distance_unit or "mile"
        # mile → km conversion (no DB lookup needed for this fixed pair)
        if raw_unit.lower() in ("mile", "mi", "miles"):
            normalized_quantity = (raw_quantity * Decimal("1.609344")).quantize(Decimal("0.000001"))
            normalized_unit = "km"
            notes_parts.append("Converted miles → km × 1.609344")
        else:
            normalized_quantity = raw_quantity
            normalized_unit = raw_unit

    else:
        # car_rental, taxi, rail, etc. — spend-based by default.
        raw_quantity = staging.transaction_amount or Decimal("0")
        raw_unit = staging.transaction_currency or "USD"
        normalized_quantity = raw_quantity
        normalized_unit = raw_unit
        notes_parts.append("Spend-based: no activity data available")

    activity_defaults = dict(
        organization_id=staging.organization_id,
        source_type=f"travel_{mapping.canonical_activity}",
        staging_id=staging.id,
        staging_table="travel_expense_staging",
        data_source_id=data_source_id,
        ingestion_run_id=staging.ingestion_run_id,
        activity_date=staging.transaction_date,
        activity_period_start=None,
        activity_period_end=None,
        # Travel doesn't necessarily map to one of our facilities. We
        # leave facility NULL and rely on location_country_code for the
        # geographic anchor — that's enough for country-level emission
        # factors and avoids forcing every customer to create a facility
        # row for every destination city.
        facility=None,
        location_country_code=(staging.location_country or "")[:2].upper(),
        grid_region="",
        ghg_scope=GhgScope.SCOPE_3,
        ghg_category=mapping.ghg_category,
        activity_type=mapping.canonical_activity,
        raw_quantity=raw_quantity if raw_quantity is not None else Decimal("0"),
        raw_unit=raw_unit,
        normalized_quantity=normalized_quantity,
        normalized_unit=normalized_unit,
        unit_conversion_factor=Decimal("1.0"),
        unit_conversion_source="travel_normalizer",
        unit_conversion_notes="; ".join(notes_parts),
        computed_distance_km=Decimal(str(distance_km)) if distance_km is not None else None,
        distance_computation_method=distance_method,
        is_reversal=False,
        review_status=ReviewStatus.INGESTED,
        staging_notes=" | ".join(notes_parts),
    )

    record, created = ActivityRecord.objects.update_or_create(
        staging_table="travel_expense_staging",
        staging_id=staging.id,
        defaults=activity_defaults,
    )

    staging.staging_status = (
        "no_itinerary_linkage" if no_itinerary else StagingStatus.NORMALIZED
    )
    staging.rejection_reason = (
        "Expense booked outside Concur Travel — no itinerary segment to derive "
        "distance from; activity_record uses spend-based method"
        if no_itinerary
        else ""
    )
    staging.save()

    if created:
        record_ingestion_event(
            record=record,
            note=f"Normalized from Concur expense {staging.concur_entry_id}",
        )
        result.activity_records_created += 1

    if no_itinerary:
        result.no_itinerary_linkage += 1

    # --- Auto-flag rules ---------------------------------------------
    flag_reasons: list[str] = []
    if no_itinerary and mapping.canonical_activity == "flight":
        flag_reasons.append(
            "Flight booked outside Concur — no distance available, falling back to "
            "spend-based emission factor (lower confidence). Analyst should verify."
        )
    if mapping.canonical_activity == "flight" and (staging.transaction_amount or 0) > 1000 and no_itinerary:
        flag_reasons.append(
            "High-value flight (>$1000) without itinerary — likely a long-haul trip "
            "where distance-based accuracy matters most."
        )
    if warnings:
        flag_reasons.append(
            f"Parser warnings: {'; '.join(w['warning'] for w in warnings)}"
        )

    if flag_reasons and record.review_status == ReviewStatus.INGESTED:
        auto_flag(record=record, reason=" | ".join(flag_reasons))
