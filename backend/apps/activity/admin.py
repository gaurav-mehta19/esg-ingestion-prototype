from django.contrib import admin

from .models import ActivityRecord, ReviewEvent


@admin.register(ActivityRecord)
class ActivityRecordAdmin(admin.ModelAdmin):
    list_display = [
        "activity_date",
        "ghg_scope",
        "activity_type",
        "raw_quantity",
        "raw_unit",
        "normalized_quantity",
        "normalized_unit",
        "review_status",
        "is_reversal",
    ]
    list_filter = ["ghg_scope", "review_status", "is_reversal"]
    search_fields = ["activity_type", "id"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(ReviewEvent)
class ReviewEventAdmin(admin.ModelAdmin):
    list_display = [
        "occurred_at",
        "event_type",
        "from_status",
        "to_status",
        "actor_id",
        "activity_record",
    ]
    list_filter = ["event_type"]
    readonly_fields = ["id", "occurred_at"]
