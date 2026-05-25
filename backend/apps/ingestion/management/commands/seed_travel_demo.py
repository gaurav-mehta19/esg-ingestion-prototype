"""
Seed a corporate-travel demo. Run AFTER seed_sap_demo.

    python manage.py seed_travel_demo
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import (
    DataSource,
    ExpenseTypeCategoryMapping,
    Organization,
)
from apps.ingestion.intake import ingest_concur_json
from apps.ingestion.normalizers.travel import normalize_travel_staging_batch


SAMPLE_JSON = (
    Path(__file__).resolve().parents[3].parent
    / "sample_data"
    / "travel_concur_sample.json"
)


# Per-tenant expense-type mapping. Codes are tenant-configurable per
# research-notes §3.5 — we don't hardcode them in the normalizer, we
# seed a mapping table the customer can edit.
DEFAULT_MAPPINGS = [
    ("AIRFR", "Airfare", "flight"),
    ("LODNG", "Hotel", "hotel"),
    ("CARRT", "Car Rental", "car_rental"),
    ("MILEG", "Personal Car Mileage", "personal_mileage"),
    ("TAXCB", "Taxi", "taxi"),
    ("RAILF", "Rail", "rail"),
    # Note: DINNER (entry ENT007) is deliberately NOT mapped, to trigger
    # the needs_expense_type_mapping flow.
]


class Command(BaseCommand):
    help = "Seed travel (Concur JSON) demo data."

    def handle(self, *args, **opts):
        try:
            org = Organization.objects.get(slug="acme")
        except Organization.DoesNotExist as e:
            raise CommandError("Run seed_sap_demo first.") from e

        ds, _ = DataSource.objects.get_or_create(
            organization=org,
            source_type="concur_expense_v3",
            display_name="Acme Concur — Expense + Itinerary",
            defaults={"is_active": True},
        )
        self.stdout.write(self.style.SUCCESS(f"DataSource: {ds}"))

        for code, name, canonical in DEFAULT_MAPPINGS:
            ExpenseTypeCategoryMapping.objects.get_or_create(
                organization=org,
                data_source=ds,
                raw_expense_type_code=code,
                defaults={
                    "raw_expense_type_name": name,
                    "canonical_activity": canonical,
                    "ghg_scope": "scope_3",
                    "ghg_category": "cat_6_business_travel",
                    "is_esg_relevant": True,
                },
            )

        with open(SAMPLE_JSON, encoding="utf-8") as fh:
            json_text = fh.read()
        intake = ingest_concur_json(
            organization_id=org.id,
            data_source=ds,
            json_text=json_text,
            triggered_by="manual",
            triggered_by_user="seed_travel_demo",
            raw_file_ref=str(SAMPLE_JSON.name),
        )
        self.stdout.write(self.style.SUCCESS(f"Ingest: {intake}"))

        result = normalize_travel_staging_batch(
            organization_id=org.id, data_source_id=ds.id
        )
        self.stdout.write(self.style.SUCCESS(f"Normalize: {result}"))
