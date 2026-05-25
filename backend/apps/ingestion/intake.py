"""
SAP CSV intake: parsed rows → SapGoodsMovementStaging rows + IngestionRun.

Why this layer is separate from the parser and the normalizer:
  - Parser: text → in-memory dicts (no DB).
  - Intake: in-memory dicts → staging table + run accounting (DB writes
    but no business logic about what the data means).
  - Normalizer: staging → activity_records (business logic, lookups).

The split lets each layer be tested independently. The parser can be
fuzzed without a database. The normalizer can be re-run against staging
without re-ingesting. The intake is the only layer that knows about
ingestion_runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import DataSource
from apps.ingestion.models import (
    IngestionRun,
    IngestionRunStatus,
    SapGoodsMovementStaging,
    StagingStatus,
    TravelExpenseStaging,
    UtilityIntervalStaging,
)
from apps.ingestion.parsers.sap_idoc import ParsedRow, parse_sap_csv
from apps.ingestion.parsers.travel_concur import ParsedTravelRecord, parse_concur_json
from apps.ingestion.parsers.utility_csv import ParseResult as UtilityParseResult
from apps.ingestion.parsers.utility_csv import parse_green_button_csv


@dataclass
class IntakeResult:
    run_id: str
    rows_received: int
    rows_staged_ok: int
    rows_staged_warn: int
    rows_failed: int
    rows_duplicate: int


def ingest_sap_csv(
    *,
    organization_id,
    data_source: DataSource,
    csv_text: str,
    triggered_by: str = "manual",
    triggered_by_user: str = "",
    raw_file_ref: str = "",
) -> IntakeResult:
    """
    Parse SAP CSV and write rows to staging. Always creates one IngestionRun
    even on total parse failure, because the run record is where we record
    "the file was malformed, nothing landed."
    """

    run = IngestionRun.objects.create(
        organization_id=organization_id,
        data_source=data_source,
        triggered_by=triggered_by,
        triggered_by_user=triggered_by_user,
        started_at=timezone.now(),
        status=IngestionRunStatus.RUNNING,
        raw_file_ref=raw_file_ref,
    )

    rows: list[ParsedRow] = parse_sap_csv(csv_text)

    rows_ok = 0
    rows_warn = 0
    rows_failed = 0
    rows_duplicate = 0

    for r in rows:
        # Each row in its own atomic block: a unique-constraint collision
        # on one row must not roll back the rows already written.
        try:
            with transaction.atomic():
                if r.rejected:
                    SapGoodsMovementStaging.objects.create(
                        organization_id=organization_id,
                        ingestion_run=run,
                        idoc_number=r.idoc_number or "(unknown)",
                        item_line=r.item_line or 0,
                        raw_budat=r.raw_budat,
                        raw_bldat=r.raw_bldat,
                        parsed_budat=r.parsed_budat,
                        parsed_bldat=r.parsed_bldat,
                        ref_doc_no=r.ref_doc_no,
                        gm_code=r.gm_code,
                        matnr=r.matnr,
                        werks=r.werks,
                        lgort=r.lgort,
                        bwart=r.bwart,
                        entry_qnt=r.entry_qnt,
                        entry_uom=r.entry_uom,
                        ebeln=r.ebeln,
                        ebelp=r.ebelp,
                        bukrs=r.bukrs,
                        lifnr=r.lifnr,
                        dmbtr=r.dmbtr,
                        waers=r.waers,
                        is_reversal=r.is_reversal,
                        parse_warnings=r.parse_warnings,
                        staging_status=StagingStatus.REJECTED,
                        rejection_reason=r.rejection_reason,
                    )
                    rows_failed += 1
                    continue

                SapGoodsMovementStaging.objects.create(
                    organization_id=organization_id,
                    ingestion_run=run,
                    idoc_number=r.idoc_number,
                    item_line=r.item_line,
                    raw_budat=r.raw_budat,
                    raw_bldat=r.raw_bldat,
                    parsed_budat=r.parsed_budat,
                    parsed_bldat=r.parsed_bldat,
                    ref_doc_no=r.ref_doc_no,
                    gm_code=r.gm_code,
                    matnr=r.matnr,
                    werks=r.werks,
                    lgort=r.lgort,
                    bwart=r.bwart,
                    entry_qnt=r.entry_qnt,
                    entry_uom=r.entry_uom,
                    ebeln=r.ebeln,
                    ebelp=r.ebelp,
                    bukrs=r.bukrs,
                    lifnr=r.lifnr,
                    dmbtr=r.dmbtr,
                    waers=r.waers,
                    is_reversal=r.is_reversal,
                    parse_warnings=r.parse_warnings,
                    staging_status=StagingStatus.PENDING,
                )
                if r.parse_warnings:
                    rows_warn += 1
                else:
                    rows_ok += 1

        except IntegrityError:
            # Duplicate (organization, idoc_number, item_line) — already
            # ingested in a prior run. This is the deduplication behavior
            # we want: re-running the same file is safe.
            rows_duplicate += 1

    # Finalize the run
    run.rows_received = len(rows)
    run.rows_staged_ok = rows_ok
    run.rows_staged_warn = rows_warn
    run.rows_failed = rows_failed
    run.rows_duplicate = rows_duplicate
    run.completed_at = timezone.now()
    if rows_failed and not (rows_ok or rows_warn):
        run.status = IngestionRunStatus.FAILED
    elif rows_failed:
        run.status = IngestionRunStatus.PARTIAL
    else:
        run.status = IngestionRunStatus.COMPLETED
    run.save()

    return IntakeResult(
        run_id=str(run.id),
        rows_received=run.rows_received,
        rows_staged_ok=run.rows_staged_ok,
        rows_staged_warn=run.rows_staged_warn,
        rows_failed=run.rows_failed,
        rows_duplicate=run.rows_duplicate,
    )


# ---------------------------------------------------------------------------
# Utility (Green Button CSV) intake
# ---------------------------------------------------------------------------


def ingest_utility_csv(
    *,
    organization_id,
    data_source: DataSource,
    csv_text: str,
    triggered_by: str = "manual",
    triggered_by_user: str = "",
    raw_file_ref: str = "",
) -> IntakeResult:
    run = IngestionRun.objects.create(
        organization_id=organization_id,
        data_source=data_source,
        triggered_by=triggered_by,
        triggered_by_user=triggered_by_user,
        started_at=timezone.now(),
        status=IngestionRunStatus.RUNNING,
        raw_file_ref=raw_file_ref,
    )

    tz_name = data_source.timezone or ""
    parsed: UtilityParseResult = parse_green_button_csv(csv_text, timezone_name=tz_name)

    # Meter ID comes from the file preamble (Service Account line) — see
    # parser. The DataSource may also have a default in connection_config;
    # the preamble wins because one DataSource can serve multiple meters.
    usage_point_id = (
        parsed.metadata.service_account
        or data_source.connection_config.get("default_usage_point_id", "")
    )

    rows_ok = 0
    rows_warn = 0
    rows_failed = 0
    rows_duplicate = 0

    for r in parsed.rows:
        try:
            with transaction.atomic():
                staging_status = StagingStatus.PENDING
                rejection_reason = ""
                if r.interval_start is None:
                    # Can't satisfy the partial unique constraint without
                    # interval_start. Land it as rejected so the analyst
                    # can see it; dedup doesn't apply here anyway.
                    staging_status = StagingStatus.REJECTED
                    rejection_reason = (
                        "interval_start could not be parsed — "
                        f"row notes: {r.parse_warnings or '(none)'}"
                    )
                UtilityIntervalStaging.objects.create(
                    organization_id=organization_id,
                    ingestion_run=run,
                    utility_usage_point_id=usage_point_id,
                    source_format="green_button_csv",
                    raw_date=r.raw_date,
                    raw_start_time=r.raw_start_time,
                    raw_end_time=r.raw_end_time,
                    raw_usage=r.raw_usage,
                    raw_units=r.raw_units,
                    raw_cost=r.raw_cost,
                    raw_notes=r.raw_notes,
                    interval_start=r.interval_start,
                    interval_end=r.interval_end,
                    interval_length_minutes=r.interval_length_minutes,
                    usage_value=r.usage_value,
                    usage_unit=r.usage_unit,
                    cost_value=r.cost_value,
                    cost_currency=r.cost_currency,
                    is_estimated=r.is_estimated,
                    parse_warnings=r.parse_warnings,
                    staging_status=staging_status,
                    rejection_reason=rejection_reason,
                )
                if staging_status == StagingStatus.REJECTED:
                    rows_failed += 1
                elif r.parse_warnings:
                    rows_warn += 1
                else:
                    rows_ok += 1
        except IntegrityError:
            rows_duplicate += 1

    run.rows_received = len(parsed.rows)
    run.rows_staged_ok = rows_ok
    run.rows_staged_warn = rows_warn
    run.rows_failed = rows_failed
    run.rows_duplicate = rows_duplicate
    run.completed_at = timezone.now()
    if rows_failed and not (rows_ok or rows_warn):
        run.status = IngestionRunStatus.FAILED
    elif rows_failed:
        run.status = IngestionRunStatus.PARTIAL
    else:
        run.status = IngestionRunStatus.COMPLETED
    run.save()

    return IntakeResult(
        run_id=str(run.id),
        rows_received=run.rows_received,
        rows_staged_ok=run.rows_staged_ok,
        rows_staged_warn=run.rows_staged_warn,
        rows_failed=run.rows_failed,
        rows_duplicate=run.rows_duplicate,
    )


# ---------------------------------------------------------------------------
# Travel (Concur JSON) intake
# ---------------------------------------------------------------------------


def ingest_concur_json(
    *,
    organization_id,
    data_source: DataSource,
    json_text: str,
    triggered_by: str = "manual",
    triggered_by_user: str = "",
    raw_file_ref: str = "",
) -> IntakeResult:
    run = IngestionRun.objects.create(
        organization_id=organization_id,
        data_source=data_source,
        triggered_by=triggered_by,
        triggered_by_user=triggered_by_user,
        started_at=timezone.now(),
        status=IngestionRunStatus.RUNNING,
        raw_file_ref=raw_file_ref,
    )

    rows: list[ParsedTravelRecord] = parse_concur_json(json_text)
    rows_ok = 0
    rows_warn = 0
    rows_failed = 0
    rows_duplicate = 0

    for r in rows:
        try:
            with transaction.atomic():
                staging_status = (
                    StagingStatus.REJECTED if r.rejected else StagingStatus.PENDING
                )
                TravelExpenseStaging.objects.create(
                    organization_id=organization_id,
                    ingestion_run=run,
                    source_system=r.source_system,
                    record_type=r.record_type,
                    concur_report_id=r.concur_report_id,
                    concur_entry_id=r.concur_entry_id,
                    expense_type_code=r.expense_type_code,
                    expense_type_name=r.expense_type_name,
                    spend_category_code=r.spend_category_code,
                    transaction_date=r.transaction_date,
                    transaction_amount=r.transaction_amount,
                    transaction_currency=r.transaction_currency,
                    vendor_description=r.vendor_description,
                    location_name=r.location_name,
                    location_country=r.location_country,
                    is_personal=r.is_personal,
                    journey_distance=r.journey_distance,
                    distance_unit=r.distance_unit,
                    comment_text=r.comment_text,
                    itinerary_booking_id=r.itinerary_booking_id,
                    linked_itinerary_id=r.linked_itinerary_id,
                    origin_iata=r.origin_iata,
                    destination_iata=r.destination_iata,
                    class_of_service=r.class_of_service,
                    aircraft_iata_code=r.aircraft_iata_code,
                    flight_number=r.flight_number,
                    departure_datetime=r.departure_datetime,
                    arrival_datetime=r.arrival_datetime,
                    hotel_checkin_date=r.hotel_checkin_date,
                    hotel_checkout_date=r.hotel_checkout_date,
                    room_nights=r.room_nights,
                    car_acriss_code=r.car_acriss_code,
                    car_rental_start=r.car_rental_start,
                    car_rental_end=r.car_rental_end,
                    parse_warnings=r.parse_warnings,
                    staging_status=staging_status,
                    rejection_reason=r.rejection_reason,
                )
                if r.rejected:
                    rows_failed += 1
                elif r.parse_warnings:
                    rows_warn += 1
                else:
                    rows_ok += 1
        except IntegrityError:
            rows_duplicate += 1

    run.rows_received = len(rows)
    run.rows_staged_ok = rows_ok
    run.rows_staged_warn = rows_warn
    run.rows_failed = rows_failed
    run.rows_duplicate = rows_duplicate
    run.completed_at = timezone.now()
    if rows_failed and not (rows_ok or rows_warn):
        run.status = IngestionRunStatus.FAILED
    elif rows_failed:
        run.status = IngestionRunStatus.PARTIAL
    else:
        run.status = IngestionRunStatus.COMPLETED
    run.save()

    return IntakeResult(
        run_id=str(run.id),
        rows_received=run.rows_received,
        rows_staged_ok=run.rows_staged_ok,
        rows_staged_warn=run.rows_staged_warn,
        rows_failed=run.rows_failed,
        rows_duplicate=run.rows_duplicate,
    )
