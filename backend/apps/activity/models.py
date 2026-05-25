"""
Normalized activity layer + review audit trail (MODEL.md §8 and §11).

Narration for a React-first developer:
- ActivityRecord is the stable, queryable representation of one ESG-relevant
  event. Reports read from here. Staging is upstream of this; emission
  calculations (later milestone) will be downstream.
- ReviewEvent is an append-only log of state changes on ActivityRecord. The
  current state lives on ActivityRecord for fast filtering ("show me all
  flagged records"); the history lives on ReviewEvent ("how did this record
  reach 'approved' and who approved it?"). Why both — see MODEL.md §11.2.
"""

import uuid

from django.db import models

from apps.core.models import DataSource, Facility, Organization
from apps.ingestion.models import IngestionRun


class ReviewStatus(models.TextChoices):
    INGESTED = "ingested"
    FLAGGED = "flagged"
    ANALYST_REVIEWED = "analyst_reviewed"
    APPROVED = "approved"
    LOCKED = "locked"


class GhgScope(models.TextChoices):
    SCOPE_1 = "scope_1"
    SCOPE_2 = "scope_2"
    SCOPE_3 = "scope_3"


class ActivityRecord(models.Model):
    """
    Normalized, ESG-classified event. One per measurable activity (one SAP
    line item = one ActivityRecord).

    Why source_type + staging_id + staging_table (rather than a FK):
      ActivityRecord is polymorphic over source — it can come from SAP
      staging, utility staging, or travel staging. Django ORM has no good
      native polymorphism (GenericForeignKey is awkward and breaks query
      optimization). Two scalar columns (table name + UUID) keep the link
      explicit and queryable. The cost: no DB-level FK constraint, so we
      must be careful in the normalizer never to delete a staging row that
      a live ActivityRecord points to.

    Why raw_quantity + raw_unit + normalized_quantity + normalized_unit on
    the same row (not a separate table):
      A regulator asks "what was the raw value and what did you convert it
      to?" The answer must be visible in one row, no join. Re-normalization
      updates normalized_* and sets review_status back to 'flagged' to force
      a fresh approval. See MODEL.md §8 for the full reasoning.

    Why review_status columns AND a separate ReviewEvent table:
      The columns answer "what state is this record in now?" — used in
      filter queries hundreds of times per page load. The events answer
      "how did it get there, who changed it, when?" — used during audit,
      rarely. Separate optimization targets, two structures.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="activity_records"
    )

    # --- Source provenance (immutable after creation) ---
    source_type = models.CharField(
        max_length=30,
        help_text=(
            "'sap_goods_movement', 'utility_interval', 'travel_air', "
            "'travel_hotel', 'travel_car_rental', 'travel_personal_mileage'"
        ),
    )
    staging_id = models.UUIDField(help_text="UUID of the staging row")
    staging_table = models.CharField(
        max_length=60, help_text="e.g. 'sap_goods_movement_staging'"
    )
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="activity_records"
    )
    ingestion_run = models.ForeignKey(
        IngestionRun, on_delete=models.PROTECT, related_name="activity_records"
    )

    # --- Temporal ---
    activity_date = models.DateField(
        help_text="Canonical date for ESG period bucketing (BUDAT for SAP)"
    )
    activity_period_start = models.DateField(null=True, blank=True)
    activity_period_end = models.DateField(null=True, blank=True)

    # --- Location ---
    facility = models.ForeignKey(
        Facility,
        on_delete=models.PROTECT,
        related_name="activity_records",
        null=True,
        blank=True,
        help_text="NULL if the source identifier was not yet mapped to a facility",
    )
    location_country_code = models.CharField(max_length=2, blank=True, default="")
    grid_region = models.CharField(max_length=50, blank=True, default="")

    # --- GHG classification ---
    ghg_scope = models.CharField(max_length=10, choices=GhgScope.choices)
    ghg_category = models.CharField(max_length=30, blank=True, default="")
    activity_type = models.CharField(
        max_length=80,
        help_text=(
            "e.g. 'diesel_combustion', 'grid_electricity', "
            "'flight_economy_short_haul'"
        ),
    )

    # --- Raw quantity (never overwritten) ---
    raw_quantity = models.DecimalField(max_digits=20, decimal_places=6)
    raw_unit = models.CharField(max_length=20)

    # --- Normalized quantity ---
    normalized_quantity = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True
    )
    normalized_unit = models.CharField(max_length=20, blank=True, default="")
    unit_conversion_factor = models.DecimalField(
        max_digits=20, decimal_places=10, null=True, blank=True
    )
    unit_conversion_source = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="'uom_normalization_rules', 'manual', 'no_conversion_needed'",
    )
    unit_conversion_notes = models.TextField(blank=True, default="")

    # --- Travel-specific computed fields (MODEL.md §8.1) ---
    # Stored on the same row (not in a side table) because they are the
    # derived inputs to emission calculation; an auditor asking "which
    # distance did you use?" sees it in this row, no join.
    computed_distance_km = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    distance_computation_method = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text=(
            "'great_circle×1.08_uplift', 'spend_proxy', 'reported' — required "
            "for audit so the emission method (distance vs spend) is explicit"
        ),
    )

    # --- Reversal tracking ---
    is_reversal = models.BooleanField(default=False)
    reverses_activity = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="reversed_by",
        null=True,
        blank=True,
    )

    # --- Manual-edit tracking ---
    is_manually_edited = models.BooleanField(default=False)
    original_values = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of fields BEFORE the first manual edit",
    )
    edited_by = models.CharField(max_length=200, blank=True, default="")
    edited_at = models.DateTimeField(null=True, blank=True)
    edit_reason = models.TextField(blank=True, default="")

    # --- Review lifecycle (current state) ---
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.INGESTED,
    )
    flagged_reason = models.TextField(blank=True, default="")
    reviewed_by = models.CharField(max_length=200, blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.CharField(max_length=200, blank=True, default="")
    approved_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=200, blank=True, default="")
    locked_at = models.DateTimeField(null=True, blank=True)

    staging_notes = models.TextField(
        blank=True,
        default="",
        help_text="Explanation of how this normalized row was derived",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "activity_records"
        constraints = [
            # One activity record per staging row. Re-running the normalizer
            # must update, not duplicate.
            models.UniqueConstraint(
                fields=["staging_table", "staging_id"],
                name="uniq_activity_per_staging_row",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "review_status"]),
            models.Index(fields=["organization", "ghg_scope", "activity_date"]),
            models.Index(fields=["facility", "activity_date"]),
        ]
        ordering = ["-activity_date", "-created_at"]

    def __str__(self) -> str:
        return f"{self.activity_type} {self.activity_date} ({self.review_status})"


class ReviewEvent(models.Model):
    """
    Append-only log of state changes on an ActivityRecord (MODEL.md §11.2).

    Why this is its own table (not signals, not serializer hooks):
      Audit trail must be readable and auditable as data, not as side
      effects buried in framework hooks. Django signals are convenient but
      they fire silently and out-of-band — a reviewer asking "where is the
      audit record for this approval?" needs to find the row in a table,
      not trace a signal receiver across the codebase. Explicit writes via
      a `transition()` service function (see services.py) keep the audit
      path single and obvious.

    Why field_changes is JSONField (not a normalized child table):
      Edits are rare and inspected one at a time. The set of fields that
      can change varies (sometimes one field, sometimes five). A JSON list
      keeps the schema flexible and the row count manageable.
    """

    EVENT_TYPE_CHOICES = [
        ("ingested", "Ingested"),
        ("auto_flagged", "Auto-flagged by system"),
        ("manually_flagged", "Manually flagged by analyst"),
        ("unflagged", "Unflagged"),
        ("analyst_reviewed", "Analyst reviewed"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("locked", "Locked"),
        ("unlocked", "Unlocked"),
        ("field_edited", "Field edited"),
        ("calculation_rerun", "Calculation re-run"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="review_events"
    )
    activity_record = models.ForeignKey(
        ActivityRecord, on_delete=models.PROTECT, related_name="review_events"
    )
    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    actor_id = models.CharField(max_length=200, help_text="User ID or 'system'")
    actor_display_name = models.CharField(max_length=200, blank=True, default="")
    occurred_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, default="")
    field_changes = models.JSONField(
        default=list,
        blank=True,
        help_text="For 'field_edited': [{field, old_value, new_value}]",
    )

    class Meta:
        db_table = "review_events"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["activity_record", "-occurred_at"]),
            models.Index(fields=["organization", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.from_status}→{self.to_status} by {self.actor_id}"
