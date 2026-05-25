"""
Utility staging → ActivityRecord normalizer.

Differences from the SAP normalizer (and why they stay source-specific):

  - Scope: always Scope 2 grid_electricity. No table lookup needed — every
    Green Button interval is by definition Scope 2 location-based.
  - Lookup: MeterLocationMapping (meter ID → facility), not WERKS.
  - Auto-flag: estimated reads (research-notes §2.8 #2). The estimated
    flag is the single highest-value flag for utility data — analysts
    must see every estimated interval explicitly because the subsequent
    actual read will produce a catch-up adjustment.
  - Period: each row is a short interval (15 min typically). For the
    activity_record we store interval_start as activity_date but ALSO
    fill activity_period_start/end so downstream proration (when a
    billing-period spans calendar months) has the precise boundaries.

What's shared with SAP:
  - The transition() service for the ingested ReviewEvent.
  - The auto_flag() service for review status changes.
  - UomNormalizationRule lookup pattern (same model, source_system='utility').
  - The per-row @transaction.atomic loop.

I did NOT abstract the loop into a base class. The body shares maybe
5 lines with SAP; the rest is divergent. A base class would mostly be
override-points, which means the abstraction isn't paying for itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from apps.activity.models import ActivityRecord, GhgScope, ReviewStatus
from apps.activity.services import auto_flag, record_ingestion_event
from apps.core.models import MeterLocationMapping, UomNormalizationRule
from apps.ingestion.models import StagingStatus, UtilityIntervalStaging


@dataclass
class NormalizationResult:
    activity_records_created: int = 0
    staging_rows_processed: int = 0
    needs_uom_mapping: int = 0
    needs_meter_mapping: int = 0
    rejected: int = 0
    estimated_flagged: int = 0


def _resolve_meter(
    *, organization_id, data_source_id, usage_point_id: str
) -> MeterLocationMapping | None:
    return MeterLocationMapping.objects.filter(
        organization_id=organization_id,
        data_source_id=data_source_id,
        utility_usage_point_id=usage_point_id,
        is_active=True,
    ).first()


def _resolve_uom(raw_unit: str) -> UomNormalizationRule | None:
    return UomNormalizationRule.objects.filter(
        source_system="utility", raw_unit=raw_unit
    ).first()


def normalize_utility_staging_batch(
    *, organization_id, data_source_id, limit: int | None = None
) -> NormalizationResult:
    result = NormalizationResult()
    qs = UtilityIntervalStaging.objects.filter(
        organization_id=organization_id,
        staging_status__in=[
            StagingStatus.PENDING,
            StagingStatus.NEEDS_UOM_MAPPING,
            "needs_meter_mapping",  # not in StagingStatus enum but valid value
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
    staging: UtilityIntervalStaging,
    *,
    data_source_id,
    result: NormalizationResult,
) -> None:
    warnings = list(staging.parse_warnings or [])

    # --- 1. Timestamps must be resolved -----------------------------------
    if staging.interval_start is None or staging.interval_end is None:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = (
            "interval_start/end could not be resolved — typically a missing "
            "timezone in the DataSource config"
        )
        staging.save()
        result.rejected += 1
        return

    # --- 2. Meter → Facility ---------------------------------------------
    if not staging.utility_usage_point_id:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing utility_usage_point_id"
        staging.save()
        result.rejected += 1
        return

    meter = _resolve_meter(
        organization_id=staging.organization_id,
        data_source_id=data_source_id,
        usage_point_id=staging.utility_usage_point_id,
    )
    if meter is None:
        # Reuse the StagingStatus.NEEDS_PLANT_MAPPING-style pattern but with
        # a meter-specific status string. Stored as a literal because the
        # enum was originally written for SAP — a future refactor can add it.
        staging.staging_status = "needs_meter_mapping"
        staging.rejection_reason = (
            f"no MeterLocationMapping for UsagePoint='{staging.utility_usage_point_id}'. "
            f"Create the mapping in admin and re-run normalization."
        )
        staging.save()
        result.needs_meter_mapping += 1
        return

    # --- 3. Quantity & UoM -----------------------------------------------
    if staging.usage_value is None:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing or non-numeric usage value"
        staging.save()
        result.rejected += 1
        return

    if not staging.usage_unit:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing usage_unit"
        staging.save()
        result.rejected += 1
        return

    uom_rule = _resolve_uom(staging.usage_unit)
    if uom_rule is None:
        staging.staging_status = StagingStatus.NEEDS_UOM_MAPPING
        staging.rejection_reason = (
            f"no UomNormalizationRule for utility unit '{staging.usage_unit}'. "
            f"Add the rule in admin and re-run normalization."
        )
        staging.save()
        result.needs_uom_mapping += 1
        return

    normalized_quantity = (staging.usage_value * uom_rule.conversion_factor).quantize(
        Decimal("0.000001")
    )

    facility = meter.facility
    activity_defaults = dict(
        organization_id=staging.organization_id,
        source_type="utility_interval",
        staging_id=staging.id,
        staging_table="utility_interval_staging",
        data_source_id=data_source_id,
        ingestion_run_id=staging.ingestion_run_id,
        # For interval data, the canonical "date" is the local date of the
        # interval start. The exact start/end go in period_* so that
        # downstream proration across calendar months has the right bounds.
        activity_date=staging.interval_start.date(),
        activity_period_start=staging.interval_start.date(),
        activity_period_end=staging.interval_end.date(),
        facility=facility,
        location_country_code=facility.country_code,
        grid_region=facility.grid_region,
        ghg_scope=GhgScope.SCOPE_2,
        ghg_category="cat_2_grid_electricity",
        activity_type=f"grid_electricity_{facility.grid_region or 'unknown'}".lower(),
        raw_quantity=staging.usage_value,
        raw_unit=staging.usage_unit,
        normalized_quantity=normalized_quantity,
        normalized_unit=uom_rule.canonical_unit,
        unit_conversion_factor=uom_rule.conversion_factor,
        unit_conversion_source="uom_normalization_rules",
        unit_conversion_notes=uom_rule.notes,
        is_reversal=False,
        review_status=ReviewStatus.INGESTED,
        staging_notes=(
            f"Source: utility meter {staging.utility_usage_point_id} "
            f"interval {staging.interval_start.isoformat()} – "
            f"{staging.interval_end.isoformat()} "
            f"({staging.interval_length_minutes} min)."
        ),
    )

    record, created = ActivityRecord.objects.update_or_create(
        staging_table="utility_interval_staging",
        staging_id=staging.id,
        defaults=activity_defaults,
    )

    staging.staging_status = StagingStatus.NORMALIZED
    staging.rejection_reason = ""
    staging.save()

    if created:
        record_ingestion_event(
            record=record,
            note=(
                f"Normalized from utility meter {staging.utility_usage_point_id} "
                f"@ {staging.interval_start.isoformat()}"
            ),
        )
        result.activity_records_created += 1

    flag_reasons: list[str] = []
    if staging.is_estimated:
        flag_reasons.append(
            "Estimated meter reading — actual read will produce a catch-up "
            "adjustment; do not approve until the actual lands"
        )
        result.estimated_flagged += 1
    if warnings:
        flag_reasons.append(
            f"Parser warnings: {'; '.join(w['warning'] for w in warnings)}"
        )

    if flag_reasons and record.review_status == ReviewStatus.INGESTED:
        auto_flag(record=record, reason=" | ".join(flag_reasons))
