"""
Staging layer (MODEL.md §6 + §7.1).

Narration for a React-first developer:
- Staging tables hold raw source data, typed but unnormalized. They are the
  "what arrived" record. Nothing in this app touches business logic — that
  lives in the normalizer module.
- The split between staging and the normalized activity_records (in the
  activity app) is the most important architectural choice in MODEL.md.
  See the "Why normalization lives in a separate step" block at the bottom
  of this file.
"""

import uuid

from django.db import models

from apps.core.models import DataSource, Organization


# ---------------------------------------------------------------------------
# Status enums — declared once, reused below.
# ---------------------------------------------------------------------------

# Why TextChoices: it's Django's typed equivalent of a Python Enum that also
# generates the SQL CHECK constraint values and gives the admin a dropdown.
# Strings (not ints) so log lines and database inspection stay readable.


class IngestionRunStatus(models.TextChoices):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # some rows succeeded, some failed


class StagingStatus(models.TextChoices):
    PENDING = "pending"  # ingested but not yet normalized
    NORMALIZED = "normalized"  # successfully produced an activity_record
    REJECTED = "rejected"  # unrecoverable parse error
    DUPLICATE = "duplicate"  # already-seen idoc/item line
    NEEDS_UOM_MAPPING = "needs_uom_mapping"  # unit not in uom_normalization_rules
    NEEDS_PLANT_MAPPING = "needs_plant_mapping"  # WERKS not in plant_location_mappings


# ---------------------------------------------------------------------------
# Ingestion run — one row per batch (MODEL.md §6.1)
# ---------------------------------------------------------------------------


class IngestionRun(models.Model):
    """
    Audit record for one ingestion batch.

    Why this is its own table (and not just timestamps on staging rows):
      If an IDoc file is re-delivered (vendor sends the same export twice),
      we need to know which rows belong to which delivery so we can detect
      and quarantine duplicates without losing the original. The run is
      also where parser-level errors live (e.g., "file header was malformed,
      no rows could be read") — those have no staging row to attach to.

    Why rows_staged_warn is separate from rows_failed:
      A row with a parse warning (e.g., unknown unit, but everything else
      is fine) is still in the staging table — the analyst can fix the UoM
      rule and re-normalize. A failed row never made it in. Counting them
      together would hide a class of fixable problems.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="ingestion_runs"
    )
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="ingestion_runs"
    )
    triggered_by = models.CharField(
        max_length=50,
        help_text="'scheduler', 'manual', 'webhook'",
    )
    triggered_by_user = models.CharField(max_length=200, blank=True, default="")
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=IngestionRunStatus.choices,
        default=IngestionRunStatus.RUNNING,
    )
    rows_received = models.IntegerField(default=0)
    rows_staged_ok = models.IntegerField(default=0)
    rows_staged_warn = models.IntegerField(default=0)
    rows_failed = models.IntegerField(default=0)
    rows_duplicate = models.IntegerField(default=0)
    source_date_range_start = models.DateField(null=True, blank=True)
    source_date_range_end = models.DateField(null=True, blank=True)
    error_summary = models.JSONField(default=dict, blank=True)
    raw_file_ref = models.TextField(
        blank=True,
        default="",
        help_text="Path or URI to the original source file (S3 in production)",
    )

    class Meta:
        db_table = "ingestion_runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["organization", "data_source", "-started_at"]),
        ]

    def __str__(self) -> str:
        return f"Run {self.id} [{self.status}] {self.data_source.display_name}"


# ---------------------------------------------------------------------------
# SAP goods movement staging (MODEL.md §7.1)
# ---------------------------------------------------------------------------


class SapGoodsMovementStaging(models.Model):
    """
    One row per item segment (E1BP2017_GM_ITEM_CREATE) in a goods-movement IDoc.

    Why raw_budat (CHAR 8) AND parsed_budat (DATE):
      The raw column is the verbatim 'YYYYMMDD' string SAP sent us. The
      parsed column is the result of converting it to a real date. If our
      date parser has a bug (e.g., it mishandles leap years), we can fix
      the parser and re-run normalization without re-ingesting. Lose the
      raw and you can never reconstruct what arrived.

    Why every "real" SAP field is here as a typed column:
      Per MODEL.md §3 we deliberately chose typed staging over a JSON blob
      so the database enforces format (e.g., werks = CHAR 4) at write time.
      Bad data fails to land instead of polluting downstream queries.

    Why is_reversal is derived and stored (not computed at read time):
      Reversal detection is a small lookup against a fixed list of BWART
      values (102, 202, 262, etc.). Doing it at ingest means downstream
      queries can index on a boolean instead of re-evaluating the list on
      every report. The list won't drift — these are SAP standard codes
      established for decades.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="sap_staging_rows"
    )
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="sap_staging_rows"
    )

    # IDoc envelope (from EDIDC control record)
    idoc_number = models.CharField(max_length=16)
    idoc_type = models.CharField(max_length=30, default="MBGMCR03")
    idoc_message_type = models.CharField(max_length=30, default="MBGMCR")
    sender_partner = models.CharField(max_length=50, blank=True, default="")

    # Header fields (E1BP2017_GM_HEAD_01)
    raw_budat = models.CharField(
        max_length=8, blank=True, default="", help_text="YYYYMMDD, verbatim"
    )
    raw_bldat = models.CharField(max_length=8, blank=True, default="")
    parsed_budat = models.DateField(null=True, blank=True)
    parsed_bldat = models.DateField(null=True, blank=True)
    ref_doc_no = models.CharField(max_length=16, blank=True, default="")
    gm_code = models.CharField(max_length=2, blank=True, default="")

    # Item fields (E1BP2017_GM_ITEM_CREATE)
    item_line = models.IntegerField(help_text="Position within the IDoc, 1-based")
    matnr = models.CharField(max_length=18, blank=True, default="")
    werks = models.CharField(max_length=4, blank=True, default="")
    lgort = models.CharField(max_length=4, blank=True, default="")
    bwart = models.CharField(max_length=3, blank=True, default="")
    entry_qnt = models.DecimalField(
        max_digits=15, decimal_places=3, null=True, blank=True
    )
    entry_uom = models.CharField(max_length=3, blank=True, default="")
    ebeln = models.CharField(max_length=10, blank=True, default="")
    ebelp = models.CharField(max_length=5, blank=True, default="")
    bukrs = models.CharField(max_length=4, blank=True, default="")
    lifnr = models.CharField(max_length=10, blank=True, default="")
    dmbtr = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    waers = models.CharField(max_length=5, blank=True, default="")

    # Derived at ingest
    is_reversal = models.BooleanField(default=False)

    # Ingestion metadata
    ingested_at = models.DateTimeField(auto_now_add=True)
    parse_warnings = models.JSONField(
        default=list,
        blank=True,
        help_text="[{field, warning}] entries from the parser",
    )
    staging_status = models.CharField(
        max_length=30,
        choices=StagingStatus.choices,
        default=StagingStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "sap_goods_movement_staging"
        constraints = [
            # Deduplication: re-running the same IDoc batch must not insert
            # duplicate rows. The (idoc_number, item_line) pair is the
            # natural key within an organization.
            models.UniqueConstraint(
                fields=["organization", "idoc_number", "item_line"],
                name="uniq_sap_staging_per_idoc_item",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "staging_status"]),
            models.Index(fields=["ingestion_run"]),
            models.Index(fields=["werks"]),
        ]

    def __str__(self) -> str:
        return f"IDoc {self.idoc_number} line {self.item_line} ({self.staging_status})"


# ---------------------------------------------------------------------------
# Utility electricity staging (MODEL.md §7.2)
# ---------------------------------------------------------------------------


class UtilityIntervalStaging(models.Model):
    """
    One row per interval reading (typically 15-min from Green Button).

    Why we preserve raw_* string fields ALONGSIDE parsed values:
      Green Button CSV ships dates as 'M/D/YYYY' (not ISO 8601), costs as
      '$0.08' (with a $ prefix), and the 'Notes' column carries the
      free-text 'Estimated' flag. If the parser has a bug — for instance,
      the powerOfTenMultiplier handling for an unusual utility — we need
      the original characters to re-parse without re-downloading. Raw
      fidelity is the audit primitive for utility data the same way it
      is for SAP IDocs.

    Why is_estimated is denormalized into its own column:
      Estimated reads are the single biggest data-quality issue for
      Scope 2 (research-notes §2.8 + §2.10). Computing "is the Notes
      column ILIKE 'Estimated'" at every dashboard query is wasteful
      when ingestion knows the answer once. The column lets us index on
      it and run "what % of this month is estimated" in a single scan.

    Why ESPI XML scalar fields live here too (powerOfTenMultiplier, etc.):
      The same staging table serves both Green Button CSV uploads and
      ESPI XML pulls. Different inputs, same downstream activity_records.
      The XML-only fields stay NULL for CSV-sourced rows; the CSV-only
      raw_date/raw_units stay NULL for XML rows. Two small null columns
      beat two near-duplicate tables.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="utility_staging_rows"
    )
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="utility_staging_rows"
    )

    # Source identification
    utility_usage_point_id = models.CharField(max_length=200)
    source_format = models.CharField(
        max_length=30,
        default="green_button_csv",
        help_text="'green_button_csv', 'espi_xml', 'green_button_cmd'",
    )

    # Raw CSV fields (preserved verbatim)
    raw_date = models.CharField(max_length=20, blank=True, default="")
    raw_start_time = models.CharField(max_length=10, blank=True, default="")
    raw_end_time = models.CharField(max_length=10, blank=True, default="")
    raw_usage = models.CharField(max_length=30, blank=True, default="")
    raw_units = models.CharField(max_length=20, blank=True, default="")
    raw_cost = models.CharField(max_length=30, blank=True, default="")
    raw_notes = models.TextField(blank=True, default="")

    # ESPI XML scalars (NULL for CSV-sourced rows)
    espi_interval_start_epoch = models.BigIntegerField(null=True, blank=True)
    espi_interval_duration_s = models.IntegerField(null=True, blank=True)
    espi_value_raw = models.BigIntegerField(null=True, blank=True)
    espi_power_of_ten_mult = models.IntegerField(null=True, blank=True)
    espi_uom_code = models.IntegerField(null=True, blank=True)
    espi_reading_quality = models.IntegerField(null=True, blank=True)

    # Parsed values (what normalization uses)
    interval_start = models.DateTimeField(null=True, blank=True)
    interval_end = models.DateTimeField(null=True, blank=True)
    interval_length_minutes = models.IntegerField(null=True, blank=True)
    usage_value = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    usage_unit = models.CharField(max_length=10, blank=True, default="")
    cost_value = models.DecimalField(
        max_digits=15, decimal_places=4, null=True, blank=True
    )
    cost_currency = models.CharField(max_length=5, blank=True, default="")
    is_estimated = models.BooleanField(default=False)

    # Ingestion metadata
    ingested_at = models.DateTimeField(auto_now_add=True)
    parse_warnings = models.JSONField(default=list, blank=True)
    staging_status = models.CharField(
        max_length=30,
        # Utility has one extra status beyond the shared set: needs_meter_mapping.
        # Listed inline (not added to the shared enum) because it's specific to
        # utility — the SAP normalizer would never emit it.
        choices=StagingStatus.choices + [("needs_meter_mapping", "Needs meter mapping")],
        default=StagingStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "utility_interval_staging"
        constraints = [
            # One row per (org, meter, interval_start). Re-uploading the
            # same CSV is safe — duplicates are detected, not re-stored.
            models.UniqueConstraint(
                fields=[
                    "organization",
                    "utility_usage_point_id",
                    "interval_start",
                ],
                name="uniq_utility_per_meter_interval",
                condition=models.Q(interval_start__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "staging_status"]),
            models.Index(fields=["utility_usage_point_id", "interval_start"]),
        ]

    def __str__(self) -> str:
        return (
            f"Meter {self.utility_usage_point_id} @ {self.interval_start} "
            f"({self.staging_status})"
        )


# ---------------------------------------------------------------------------
# Corporate travel staging (MODEL.md §7.3)
# ---------------------------------------------------------------------------


class TravelExpenseStaging(models.Model):
    """
    One row per Concur expense entry OR itinerary segment. The two record
    types share a table because they are linked downstream via
    `linked_itinerary_id` and almost always queried together.

    Why one table (not separate expense / itinerary tables):
      In Concur, the same trip generates both an expense entry (the credit
      card charge) and one or more itinerary segments (the booked flights/
      hotels). They join on booking_id. Reporting always wants both. A
      single table avoids the UNION ALL on every dashboard query and lets
      the unique constraint (org, source_system, concur_entry_id) cover
      both record types with one rule.

      The cost is that the table is wider (many nullable columns since
      not every record_type uses every field). Cheaper than two-table
      maintenance + joins for this prototype.

    Why staging_status='no_itinerary_linkage' is a distinct value:
      Research-notes §3.12 #4: many expenses are booked outside Concur
      (corp card charges, direct airline website). The expense row
      exists but no itinerary segment was found. This is normal, not an
      error — the row should still produce an activity_record, but via
      the spend-based method (lower confidence), and the dashboard should
      surface it so analysts can decide whether to chase down the flight
      detail.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="travel_staging_rows"
    )
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="travel_staging_rows"
    )

    source_system = models.CharField(
        max_length=20, help_text="'concur', 'navan', 'amex_gbt'"
    )
    record_type = models.CharField(
        max_length=20,
        help_text="'expense_entry', 'air_segment', 'hotel_segment', 'car_segment'",
    )

    # Concur expense entry fields (record_type='expense_entry')
    concur_report_id = models.CharField(max_length=50, blank=True, default="")
    concur_entry_id = models.CharField(max_length=50, blank=True, default="")
    expense_type_code = models.CharField(max_length=20, blank=True, default="")
    expense_type_name = models.CharField(max_length=200, blank=True, default="")
    spend_category_code = models.CharField(max_length=50, blank=True, default="")
    transaction_date = models.DateField(null=True, blank=True)
    transaction_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    transaction_currency = models.CharField(max_length=3, blank=True, default="")
    vendor_description = models.CharField(max_length=500, blank=True, default="")
    location_name = models.CharField(max_length=500, blank=True, default="")
    location_country = models.CharField(max_length=100, blank=True, default="")
    is_personal = models.BooleanField(default=False)
    journey_distance = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    distance_unit = models.CharField(max_length=10, blank=True, default="")
    comment_text = models.TextField(blank=True, default="")

    # Itinerary linkage
    itinerary_booking_id = models.CharField(max_length=50, blank=True, default="")
    linked_itinerary_id = models.CharField(max_length=50, blank=True, default="")

    # Air segment (record_type='air_segment')
    origin_iata = models.CharField(max_length=3, blank=True, default="")
    destination_iata = models.CharField(max_length=3, blank=True, default="")
    class_of_service = models.CharField(max_length=1, blank=True, default="")
    aircraft_iata_code = models.CharField(max_length=3, blank=True, default="")
    flight_number = models.CharField(max_length=10, blank=True, default="")
    departure_datetime = models.DateTimeField(null=True, blank=True)
    arrival_datetime = models.DateTimeField(null=True, blank=True)

    # Hotel segment (record_type='hotel_segment')
    hotel_checkin_date = models.DateField(null=True, blank=True)
    hotel_checkout_date = models.DateField(null=True, blank=True)
    room_nights = models.IntegerField(null=True, blank=True)

    # Car segment (record_type='car_segment')
    car_acriss_code = models.CharField(max_length=4, blank=True, default="")
    car_rental_start = models.DateField(null=True, blank=True)
    car_rental_end = models.DateField(null=True, blank=True)

    # Ingestion metadata
    ingested_at = models.DateTimeField(auto_now_add=True)
    parse_warnings = models.JSONField(default=list, blank=True)
    staging_status = models.CharField(
        max_length=30,
        choices=StagingStatus.choices
        + [
            ("needs_expense_type_mapping", "Needs expense type mapping"),
            ("no_itinerary_linkage", "No itinerary linkage (spend-only)"),
        ],
        default=StagingStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "travel_expense_staging"
        constraints = [
            # Dedup on (org, source_system, entry_id) — only meaningful for
            # expense entries (itinerary segments use a different ID space).
            models.UniqueConstraint(
                fields=["organization", "source_system", "concur_entry_id"],
                name="uniq_travel_per_concur_entry",
                condition=models.Q(concur_entry_id__gt=""),
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "staging_status"]),
            models.Index(fields=["itinerary_booking_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.record_type} {self.concur_entry_id or self.itinerary_booking_id}"


# ---------------------------------------------------------------------------
# Why normalization lives in a separate step (ingest → stage → normalize)
# ---------------------------------------------------------------------------
#
# Three plausible approaches and the tradeoffs:
#
# 1. Normalize at ingest time (in the parser):
#    + Fewer database rows, simpler mental model.
#    - If normalization rules change (new UoM rule, plant remapping), you
#      must re-ingest from source. Source data may no longer be available
#      (vendor only keeps 90 days).
#    - Parser failures and normalization failures get conflated.
#
# 2. Normalize at read time (compute in the API/serializer):
#    + Always reflects the current rule set.
#    - Every dashboard query re-runs normalization across millions of rows.
#    - No place to flag a row for analyst attention — "this needs a UoM
#      rule" is invisible until somebody queries it.
#
# 3. Normalize as a separate step (CHOSEN):
#    + Rules can change; re-run normalization against existing staging.
#    + Staging row + activity_record + review_event together form a complete
#      audit trail of what arrived and what we did with it.
#    + Failed normalization parks the row in staging with a status code
#      that the dashboard surfaces directly to the analyst.
#    - Two-table writes per row; eventual consistency between staging and
#      activity_records during a normalization run.
#
# Approach 3 matches MODEL.md §3 and §4. The cost is the extra table; the
# benefit is that an analyst can fix a UoM rule in the admin and re-run the
# normalizer to clear the backlog without ever calling SAP again.
