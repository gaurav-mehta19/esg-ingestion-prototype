"""
One-shot demo seed:

    python manage.py seed_sap_demo

  1. Creates a demo Organization, DataSource, two Facilities, three
     PlantLocationMappings, and three UomNormalizationRules.
  2. Ingests sample_data/sap_idoc_sample.csv into staging.
  3. Runs the normalizer.

After running, the React dashboard at http://localhost:5173 will show
the resulting ActivityRecords + staging issues.

React-developer note: Django "management commands" are CLI subcommands of
manage.py. They get the Django app context (settings, ORM) without needing
a web request. Use them for seed scripts, one-off migrations, periodic
batch jobs.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.core.models import (
    DataSource,
    Facility,
    Organization,
    PlantLocationMapping,
    UomNormalizationRule,
)
from apps.ingestion.intake import ingest_sap_csv
from apps.ingestion.normalizers.sap import normalize_sap_staging_batch


SAMPLE_CSV = (
    Path(__file__).resolve().parents[3].parent
    / "sample_data"
    / "sap_idoc_sample.csv"
)


class Command(BaseCommand):
    help = "Seed a demo org, ingest the sample SAP CSV, and run normalization."

    def handle(self, *args, **opts):
        org, _ = Organization.objects.get_or_create(
            slug="acme",
            defaults={"name": "Acme Manufacturing GmbH"},
        )
        self.stdout.write(self.style.SUCCESS(f"Organization: {org}"))

        ds, _ = DataSource.objects.get_or_create(
            organization=org,
            source_type="sap_idoc",
            display_name="Acme SAP ECC — MBGMCR feed",
            defaults={"is_active": True},
        )
        self.stdout.write(self.style.SUCCESS(f"DataSource: {ds}"))

        # Facilities
        munich, _ = Facility.objects.get_or_create(
            organization=org,
            name="Munich Plant",
            defaults={
                "country_code": "DE",
                "city": "Munich",
                "grid_region": "DE",
            },
        )
        houston, _ = Facility.objects.get_or_create(
            organization=org,
            name="Houston Refinery",
            defaults={
                "country_code": "US",
                "city": "Houston",
                "grid_region": "ERCOT",
            },
        )
        london, _ = Facility.objects.get_or_create(
            organization=org,
            name="London Depot",
            defaults={
                "country_code": "GB",
                "city": "London",
                "grid_region": "GB",
            },
        )
        # Note: DE02 plant deliberately unmapped to demo needs_plant_mapping.
        for werks, facility in [("DE01", munich), ("US03", houston), ("GB10", london)]:
            PlantLocationMapping.objects.get_or_create(
                organization=org,
                data_source=ds,
                werks=werks,
                valid_from=date(2020, 1, 1),
                defaults={
                    "facility": facility,
                    "sap_plant_name": facility.name,
                },
            )

        # UoM rules. LTR deliberately omitted to demo needs_uom_mapping.
        uom_rules = [
            ("L", "L", Decimal("1.0"), "SAP standard litre code"),
            ("LT", "L", Decimal("1.0"), "SAP alternate litre code (T006); same as L"),
            ("KG", "kg", Decimal("1.0"), "Kilogram pass-through"),
            ("GAL", "L", Decimal("3.7854118"), "US gallon → litre"),
        ]
        for raw, canon, factor, notes in uom_rules:
            UomNormalizationRule.objects.get_or_create(
                source_system="sap",
                raw_unit=raw,
                defaults={
                    "canonical_unit": canon,
                    "conversion_factor": factor,
                    "notes": notes,
                },
            )

        # Ingest sample CSV
        with open(SAMPLE_CSV, encoding="utf-8") as fh:
            csv_text = fh.read()

        intake = ingest_sap_csv(
            organization_id=org.id,
            data_source=ds,
            csv_text=csv_text,
            triggered_by="manual",
            triggered_by_user="seed_sap_demo",
            raw_file_ref=str(SAMPLE_CSV.name),
        )
        self.stdout.write(self.style.SUCCESS(f"Ingest: {intake}"))

        result = normalize_sap_staging_batch(
            organization_id=org.id, data_source_id=ds.id
        )
        self.stdout.write(self.style.SUCCESS(f"Normalize: {result}"))

        self.stdout.write(
            self.style.NOTICE(
                f"\nOrganization ID for the dashboard: {org.id}\n"
                f"Data source ID:                    {ds.id}\n"
            )
        )
