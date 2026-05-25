from rest_framework import serializers

from .models import (
    IngestionRun,
    SapGoodsMovementStaging,
    TravelExpenseStaging,
    UtilityIntervalStaging,
)


class IngestionRunSerializer(serializers.ModelSerializer):
    data_source_name = serializers.CharField(
        source="data_source.display_name", read_only=True
    )

    class Meta:
        model = IngestionRun
        fields = [
            "id",
            "data_source",
            "data_source_name",
            "triggered_by",
            "triggered_by_user",
            "started_at",
            "completed_at",
            "status",
            "rows_received",
            "rows_staged_ok",
            "rows_staged_warn",
            "rows_failed",
            "rows_duplicate",
            "raw_file_ref",
        ]


class SapStagingSerializer(serializers.ModelSerializer):
    """
    Surface staging issues (rejected, needs_uom_mapping, needs_plant_mapping)
    in the dashboard. Excludes fully-normalized rows by default — those are
    redundant with the ActivityRecord they produced.
    """

    source_label = serializers.SerializerMethodField()

    def get_source_label(self, obj):
        return f"SAP IDoc {obj.idoc_number}/{obj.item_line}"

    class Meta:
        model = SapGoodsMovementStaging
        fields = [
            "id",
            "source_label",
            "idoc_number",
            "item_line",
            "raw_budat",
            "raw_bldat",
            "werks",
            "bwart",
            "matnr",
            "entry_qnt",
            "entry_uom",
            "is_reversal",
            "parse_warnings",
            "staging_status",
            "rejection_reason",
            "ingested_at",
        ]


class UtilityStagingSerializer(serializers.ModelSerializer):
    source_label = serializers.SerializerMethodField()

    def get_source_label(self, obj):
        return f"Meter {obj.utility_usage_point_id} @ {obj.interval_start}"

    class Meta:
        model = UtilityIntervalStaging
        fields = [
            "id",
            "source_label",
            "utility_usage_point_id",
            "interval_start",
            "interval_end",
            "interval_length_minutes",
            "usage_value",
            "usage_unit",
            "is_estimated",
            "parse_warnings",
            "staging_status",
            "rejection_reason",
            "ingested_at",
        ]


class TravelStagingSerializer(serializers.ModelSerializer):
    source_label = serializers.SerializerMethodField()

    def get_source_label(self, obj):
        if obj.record_type == "expense_entry":
            return f"Concur expense {obj.concur_entry_id} ({obj.expense_type_code})"
        return f"{obj.record_type} for booking {obj.itinerary_booking_id}"

    class Meta:
        model = TravelExpenseStaging
        fields = [
            "id",
            "source_label",
            "source_system",
            "record_type",
            "concur_entry_id",
            "expense_type_code",
            "expense_type_name",
            "transaction_date",
            "transaction_amount",
            "transaction_currency",
            "vendor_description",
            "origin_iata",
            "destination_iata",
            "class_of_service",
            "linked_itinerary_id",
            "parse_warnings",
            "staging_status",
            "rejection_reason",
            "ingested_at",
        ]
