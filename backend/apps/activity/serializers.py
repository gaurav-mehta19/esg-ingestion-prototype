from rest_framework import serializers

from .models import ActivityRecord, ReviewEvent


class ActivityRecordListSerializer(serializers.ModelSerializer):
    """Compact shape for dashboard tables."""

    facility_name = serializers.CharField(
        source="facility.name", read_only=True, default=""
    )

    class Meta:
        model = ActivityRecord
        fields = [
            "id",
            "source_type",
            "activity_date",
            "facility_name",
            "location_country_code",
            "ghg_scope",
            "activity_type",
            "raw_quantity",
            "raw_unit",
            "normalized_quantity",
            "normalized_unit",
            "is_reversal",
            "review_status",
            "flagged_reason",
            "created_at",
        ]


class ReviewEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewEvent
        fields = [
            "id",
            "event_type",
            "from_status",
            "to_status",
            "actor_id",
            "actor_display_name",
            "occurred_at",
            "note",
            "field_changes",
        ]


class ActivityRecordDetailSerializer(serializers.ModelSerializer):
    """
    Full detail shape — used for the record drawer in the dashboard. Includes
    the audit trail (review_events) inline so the UI doesn't make a second
    round-trip for it.
    """

    facility_name = serializers.CharField(
        source="facility.name", read_only=True, default=""
    )
    review_events = ReviewEventSerializer(many=True, read_only=True)

    class Meta:
        model = ActivityRecord
        fields = [
            "id",
            "source_type",
            "staging_id",
            "staging_table",
            "data_source",
            "ingestion_run",
            "activity_date",
            "activity_period_start",
            "activity_period_end",
            "facility",
            "facility_name",
            "location_country_code",
            "grid_region",
            "ghg_scope",
            "ghg_category",
            "activity_type",
            "raw_quantity",
            "raw_unit",
            "normalized_quantity",
            "normalized_unit",
            "unit_conversion_factor",
            "unit_conversion_source",
            "unit_conversion_notes",
            "computed_distance_km",
            "distance_computation_method",
            "is_reversal",
            "reverses_activity",
            "is_manually_edited",
            "original_values",
            "edited_by",
            "edited_at",
            "edit_reason",
            "review_status",
            "flagged_reason",
            "reviewed_by",
            "reviewed_at",
            "approved_by",
            "approved_at",
            "locked_by",
            "locked_at",
            "staging_notes",
            "created_at",
            "updated_at",
            "review_events",
        ]
