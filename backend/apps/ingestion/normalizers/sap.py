"""
SAP staging → ActivityRecord normalizer.

Inputs: SapGoodsMovementStaging rows with staging_status='pending'.
Outputs: ActivityRecord rows + ReviewEvent rows (via the services module).

Where the rules live:
  - UoM conversion: looked up in UomNormalizationRule (DB). If no rule for
    a (source='sap', raw_unit=...) pair exists, the staging row is moved
    to status 'needs_uom_mapping' and NO ActivityRecord is created — an
    analyst will create the rule and re-run normalization for that row.
  - Plant → Facility: looked up in PlantLocationMapping. Same pattern:
    no mapping → 'needs_plant_mapping' and no ActivityRecord.
  - BWART → ESG relevance: tabulated below. Only ESG-relevant movement
    types produce an ActivityRecord; the rest are marked 'normalized'
    in staging with a note explaining why no activity was emitted.

Auto-flag rules:
  - is_reversal=True → ActivityRecord is auto-flagged (analysts must see
    each reversal explicitly so they can verify the original).
  - parse_warnings present → auto-flagged with the warning summary.

Why this is a single function with a loop (not a class / pipeline):
  Right now we have one source and one set of rules. A class hierarchy
  would be premature abstraction — we'd be designing for a flexibility
  we don't yet need. When utility and travel normalizers arrive, we can
  extract shared helpers then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction

from apps.activity.models import (
    ActivityRecord,
    GhgScope,
    ReviewStatus,
)
from apps.activity.services import auto_flag, record_ingestion_event
from apps.core.models import PlantLocationMapping, UomNormalizationRule
from apps.ingestion.models import (
    SapGoodsMovementStaging,
    StagingStatus,
)


# BWART → ESG mapping (research-notes §1.11).
# `activity_type` and `ghg_scope` columns on ActivityRecord come from here.
@dataclass(frozen=True)
class BwartRule:
    activity_type: str
    scope: str
    note: str
    esg_relevant: bool = True


BWART_RULES: dict[str, BwartRule] = {
    "101": BwartRule(
        activity_type="purchased_goods_receipt",
        scope=GhgScope.SCOPE_3,
        note="GR for PO — Scope 3 Cat 1 (purchased goods)",
    ),
    "201": BwartRule(
        activity_type="fuel_consumption_cost_center",
        scope=GhgScope.SCOPE_1,
        note="GI to cost center — direct consumption (Scope 1)",
    ),
    "261": BwartRule(
        activity_type="fuel_consumption_production_order",
        scope=GhgScope.SCOPE_1,
        note="GI to production order — raw material in mfg (Scope 1)",
    ),
    "501": BwartRule(
        activity_type="purchased_goods_no_po",
        scope=GhgScope.SCOPE_3,
        note="Receipt without PO — common for fuel deliveries (Scope 3 Cat 1)",
    ),
    "551": BwartRule(
        activity_type="scrap_disposal",
        scope=GhgScope.SCOPE_3,
        note="Scrapping — may include refrigerant disposal",
    ),
    # Reversals point back to the same activity_type/scope so the
    # normalizer can compute net consumption.
    "102": BwartRule(
        activity_type="purchased_goods_receipt",
        scope=GhgScope.SCOPE_3,
        note="Reversal of 101 — subtract from purchased goods totals",
    ),
    "202": BwartRule(
        activity_type="fuel_consumption_cost_center",
        scope=GhgScope.SCOPE_1,
        note="Reversal of 201 — subtract from Scope 1 totals",
    ),
    "262": BwartRule(
        activity_type="fuel_consumption_production_order",
        scope=GhgScope.SCOPE_1,
        note="Reversal of 261 — subtract from Scope 1 totals",
    ),
    # Stock transfers are NOT new consumption — only relocation.
    "301": BwartRule(
        activity_type="stock_transfer",
        scope=GhgScope.SCOPE_3,
        note="Stock transfer between plants — no new consumption",
        esg_relevant=False,
    ),
    "303": BwartRule(
        activity_type="stock_transfer",
        scope=GhgScope.SCOPE_3,
        note="Stock transfer between plants — no new consumption",
        esg_relevant=False,
    ),
    "305": BwartRule(
        activity_type="stock_transfer",
        scope=GhgScope.SCOPE_3,
        note="Stock transfer between plants — no new consumption",
        esg_relevant=False,
    ),
}


@dataclass
class NormalizationResult:
    activity_records_created: int = 0
    staging_rows_processed: int = 0
    needs_uom_mapping: int = 0
    needs_plant_mapping: int = 0
    rejected: int = 0
    skipped_not_esg: int = 0


def _resolve_plant(
    *, organization_id, data_source_id, werks: str, on_date: date
) -> PlantLocationMapping | None:
    """Find an active PlantLocationMapping for (werks, date)."""
    qs = PlantLocationMapping.objects.filter(
        organization_id=organization_id,
        data_source_id=data_source_id,
        werks=werks,
        valid_from__lte=on_date,
    ).filter(models_q_valid_to_after(on_date))
    return qs.first()


def models_q_valid_to_after(on_date: date):
    """
    Equivalent to: valid_to IS NULL OR valid_to > on_date

    Extracted because the same Q() expression is used in two places below.
    """
    from django.db.models import Q

    return Q(valid_to__isnull=True) | Q(valid_to__gt=on_date)


def _resolve_uom(raw_unit: str) -> UomNormalizationRule | None:
    return UomNormalizationRule.objects.filter(
        source_system="sap", raw_unit=raw_unit
    ).first()


def normalize_sap_staging_batch(
    *, organization_id, data_source_id, limit: int | None = None
) -> NormalizationResult:
    """
    Walk pending SAP staging rows and produce ActivityRecords.

    Each row is processed in its own atomic block so one bad row's
    rollback doesn't undo the entire batch.
    """

    result = NormalizationResult()

    qs = SapGoodsMovementStaging.objects.filter(
        organization_id=organization_id,
        staging_status__in=[
            StagingStatus.PENDING,
            StagingStatus.NEEDS_UOM_MAPPING,
            StagingStatus.NEEDS_PLANT_MAPPING,
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
    staging: SapGoodsMovementStaging,
    *,
    data_source_id,
    result: NormalizationResult,
) -> None:
    """Normalize a single staging row. Updates `result` in place."""

    warnings = list(staging.parse_warnings or [])

    # --- 1. ESG relevance via BWART ---------------------------------------
    rule = BWART_RULES.get(staging.bwart)
    if rule is None:
        # Unknown movement type. We don't reject (might be ESG-relevant in
        # future) but we don't create an ActivityRecord either. The row
        # stays in staging marked 'normalized' with a note for the analyst.
        staging.staging_status = StagingStatus.NORMALIZED
        staging.parse_warnings = warnings + [
            {"field": "bwart", "warning": f"unknown BWART '{staging.bwart}'; skipped"}
        ]
        staging.save()
        result.skipped_not_esg += 1
        return

    if not rule.esg_relevant:
        staging.staging_status = StagingStatus.NORMALIZED
        staging.parse_warnings = warnings + [
            {"field": "bwart", "warning": rule.note}
        ]
        staging.save()
        result.skipped_not_esg += 1
        return

    # --- 2. Date must be parseable for period bucketing -------------------
    if staging.parsed_budat is None:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = (
            f"BUDAT '{staging.raw_budat}' unparseable — cannot assign to a reporting period"
        )
        staging.save()
        result.rejected += 1
        return

    # --- 3. Plant lookup --------------------------------------------------
    if not staging.werks:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing WERKS — cannot attribute to a facility"
        staging.save()
        result.rejected += 1
        return

    plant_mapping = _resolve_plant(
        organization_id=staging.organization_id,
        data_source_id=data_source_id,
        werks=staging.werks,
        on_date=staging.parsed_budat,
    )
    if plant_mapping is None:
        staging.staging_status = StagingStatus.NEEDS_PLANT_MAPPING
        staging.rejection_reason = (
            f"no PlantLocationMapping for WERKS='{staging.werks}' on {staging.parsed_budat}. "
            f"Create the mapping in admin and re-run normalization."
        )
        staging.save()
        result.needs_plant_mapping += 1
        return

    # --- 4. Quantity & UoM ------------------------------------------------
    if staging.entry_qnt is None:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing quantity (MENGE) — cannot compute consumption"
        staging.save()
        result.rejected += 1
        return

    if not staging.entry_uom:
        staging.staging_status = StagingStatus.REJECTED
        staging.rejection_reason = "missing unit of measure (MEINS) — cannot normalize"
        staging.save()
        result.rejected += 1
        return

    uom_rule = _resolve_uom(staging.entry_uom)
    if uom_rule is None:
        staging.staging_status = StagingStatus.NEEDS_UOM_MAPPING
        staging.rejection_reason = (
            f"no UomNormalizationRule for SAP unit '{staging.entry_uom}'. "
            f"Add the rule in admin and re-run normalization."
        )
        staging.save()
        result.needs_uom_mapping += 1
        return

    normalized_quantity = (staging.entry_qnt * uom_rule.conversion_factor).quantize(
        Decimal("0.000001")
    )

    # --- 5. Build / update the ActivityRecord -----------------------------
    facility = plant_mapping.facility
    activity_defaults = dict(
        organization_id=staging.organization_id,
        source_type="sap_goods_movement",
        staging_id=staging.id,
        staging_table="sap_goods_movement_staging",
        data_source_id=data_source_id,
        ingestion_run_id=staging.ingestion_run_id,
        activity_date=staging.parsed_budat,
        activity_period_start=None,
        activity_period_end=None,
        facility=facility,
        location_country_code=facility.country_code,
        grid_region=facility.grid_region,
        ghg_scope=rule.scope,
        ghg_category=(
            "cat_1_purchased_goods"
            if rule.scope == GhgScope.SCOPE_3
            else "direct_combustion"
        ),
        activity_type=rule.activity_type,
        raw_quantity=staging.entry_qnt,
        raw_unit=staging.entry_uom,
        normalized_quantity=normalized_quantity,
        normalized_unit=uom_rule.canonical_unit,
        unit_conversion_factor=uom_rule.conversion_factor,
        unit_conversion_source="uom_normalization_rules",
        unit_conversion_notes=uom_rule.notes,
        is_reversal=staging.is_reversal,
        review_status=ReviewStatus.INGESTED,
        staging_notes=(
            f"Source: SAP IDoc {staging.idoc_number} line {staging.item_line}. "
            f"BWART {staging.bwart}: {rule.note}. "
            f"Date field used: BUDAT (posting date) per MODEL.md convention."
        ),
    )

    record, created = ActivityRecord.objects.update_or_create(
        staging_table="sap_goods_movement_staging",
        staging_id=staging.id,
        defaults=activity_defaults,
    )

    staging.staging_status = StagingStatus.NORMALIZED
    staging.rejection_reason = ""
    staging.save()

    if created:
        record_ingestion_event(
            record=record,
            note=f"Normalized from SAP IDoc {staging.idoc_number} line {staging.item_line}",
        )
        result.activity_records_created += 1

    # --- 6. Auto-flag rules ----------------------------------------------
    flag_reasons: list[str] = []
    if staging.is_reversal:
        flag_reasons.append(
            f"Reversal movement type (BWART={staging.bwart}); verify original entry"
        )
    if warnings:
        flag_reasons.append(
            f"Parser warnings: {'; '.join(w['warning'] for w in warnings)}"
        )

    if flag_reasons and record.review_status == ReviewStatus.INGESTED:
        auto_flag(record=record, reason=" | ".join(flag_reasons))
