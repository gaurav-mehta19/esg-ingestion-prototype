"""
Seed a utility (electricity) demo for the existing 'acme' organization.

Run AFTER seed_sap_demo (it reuses the same Organization and three
Facilities).

    python manage.py seed_utility_demo
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import (
    DataSource,
    Facility,
    MeterLocationMapping,
    Organization,
    UomNormalizationRule,
)
from apps.ingestion.intake import ingest_utility_csv
from apps.ingestion.normalizers.utility import normalize_utility_staging_batch


SAMPLE_CSV = (
    Path(__file__).resolve().parents[3].parent
    / "sample_data"
    / "utility_green_button_sample.csv"
)


class Command(BaseCommand):
    help = "Seed utility (Green Button CSV) demo data."

    def handle(self, *args, **opts):
        try:
            org = Organization.objects.get(slug="acme")
        except Organization.DoesNotExist as e:
            raise CommandError(
                "Acme org not found. Run `python manage.py seed_sap_demo` first."
            ) from e

        ds, _ = DataSource.objects.get_or_create(
            organization=org,
            source_type="utility_green_button_csv",
            display_name="Acme Munich — Green Button feed",
            defaults={
                "is_active": True,
                "timezone": "Europe/Berlin",  # PG&E sample uses American TZ in reality;
                                              # the meter is "at Munich" for this prototype.
            },
        )
        self.stdout.write(self.style.SUCCESS(f"DataSource: {ds}"))

        # Map the Service Account from the sample CSV preamble to Munich.
        munich = Facility.objects.get(organization=org, name="Munich Plant")
        MeterLocationMapping.objects.get_or_create(
            organization=org,
            data_source=ds,
            utility_usage_point_id="8800001234",
            defaults={
                "facility": munich,
                "commodity": "electricity",
                "service_account_id": "8800001234",
                "meter_serial_number": "MTR-MUN-001",
            },
        )

        # UoM rules. Wh deliberately included so the Feb 4 02:00 row resolves.
        for raw, canon, factor, notes in [
            ("kWh", "kWh", Decimal("1.0"), "Pass-through"),
            ("Wh", "kWh", Decimal("0.001"), "Wh → kWh; some small utilities export Wh"),
        ]:
            UomNormalizationRule.objects.get_or_create(
                source_system="utility",
                raw_unit=raw,
                defaults={
                    "canonical_unit": canon,
                    "conversion_factor": factor,
                    "notes": notes,
                },
            )

        with open(SAMPLE_CSV, encoding="utf-8") as fh:
            csv_text = fh.read()
        intake = ingest_utility_csv(
            organization_id=org.id,
            data_source=ds,
            csv_text=csv_text,
            triggered_by="manual",
            triggered_by_user="seed_utility_demo",
            raw_file_ref=str(SAMPLE_CSV.name),
        )
        self.stdout.write(self.style.SUCCESS(f"Ingest: {intake}"))

        result = normalize_utility_staging_batch(
            organization_id=org.id, data_source_id=ds.id
        )
        self.stdout.write(self.style.SUCCESS(f"Normalize: {result}"))
