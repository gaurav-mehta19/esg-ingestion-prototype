from django.contrib import admin

from .models import (
    IngestionRun,
    SapGoodsMovementStaging,
    TravelExpenseStaging,
    UtilityIntervalStaging,
)


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = [
        "started_at",
        "data_source",
        "status",
        "rows_received",
        "rows_staged_ok",
        "rows_staged_warn",
        "rows_failed",
        "rows_duplicate",
    ]
    list_filter = ["status", "data_source"]
    readonly_fields = ["id", "started_at", "completed_at"]


@admin.register(SapGoodsMovementStaging)
class SapStagingAdmin(admin.ModelAdmin):
    list_display = [
        "idoc_number",
        "item_line",
        "werks",
        "bwart",
        "matnr",
        "entry_qnt",
        "entry_uom",
        "staging_status",
        "is_reversal",
    ]
    list_filter = ["staging_status", "bwart", "is_reversal"]
    search_fields = ["idoc_number", "werks", "matnr"]
    readonly_fields = ["id", "ingested_at"]


@admin.register(UtilityIntervalStaging)
class UtilityStagingAdmin(admin.ModelAdmin):
    list_display = [
        "utility_usage_point_id",
        "interval_start",
        "interval_length_minutes",
        "usage_value",
        "usage_unit",
        "is_estimated",
        "staging_status",
    ]
    list_filter = ["staging_status", "source_format", "is_estimated"]
    search_fields = ["utility_usage_point_id"]
    readonly_fields = ["id", "ingested_at"]


@admin.register(TravelExpenseStaging)
class TravelStagingAdmin(admin.ModelAdmin):
    list_display = [
        "record_type",
        "expense_type_code",
        "transaction_date",
        "vendor_description",
        "transaction_amount",
        "transaction_currency",
        "origin_iata",
        "destination_iata",
        "staging_status",
    ]
    list_filter = ["staging_status", "record_type", "source_system"]
    search_fields = ["concur_entry_id", "vendor_description"]
    readonly_fields = ["id", "ingested_at"]
