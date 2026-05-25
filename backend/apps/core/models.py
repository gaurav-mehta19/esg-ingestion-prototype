"""
Reference & configuration tables (MODEL.md §5).

Narration for a React-first developer:
- A Django model is one class = one database table. Field types (CharField,
  UUIDField, etc.) map to SQL column types when `manage.py makemigrations` runs.
- `class Meta` is the equivalent of table-level config: indexes, constraints,
  ordering. Stuff that applies to the table, not to individual columns.
- ForeignKey is a typed pointer to another model's primary key. `on_delete`
  is mandatory because Django refuses to make the cascade decision for you.
  PROTECT means "raise an error if you try to delete an Organization that
  still has DataSources" — the right default for reference data.
"""

import uuid

from django.db import models


class Organization(models.Model):
    """
    Root of multi-tenancy. Every other table FK's to this (MODEL.md §2).

    Why UUID primary key:
      MODEL.md specifies UUID PKs throughout. UUIDs are safer than auto-
      increment integers in multi-tenant systems because they don't leak
      sequence information (you can't infer "how many other tenants exist"
      from your own ID), and they're safe to expose in URLs.

    Why slug:
      For URL paths like /api/orgs/acme/ — `id` is opaque and unfriendly;
      `slug` is the user-facing identifier. UNIQUE so route resolution is
      deterministic.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "organizations"

    def __str__(self) -> str:
        return self.name


class DataSource(models.Model):
    """
    A configured ingestion endpoint for one organization (MODEL.md §5.1).

    Why source_type as CharField with choices (not a separate model):
      The set of source types is small, slow-changing, and tightly coupled
      to ingestion code. A lookup table would add a join with no benefit.
      A future migration adds new choices; that's cheaper than refactoring
      cross-table joins.

    Why connection_config is JSONField:
      Different source types need wildly different config (file path for
      IDoc drop, URL+token for OData, account_id for utility). One JSON
      blob lets each ingestion driver read what it needs without forcing
      a schema migration when a new source type arrives.
    """

    SOURCE_TYPE_CHOICES = [
        ("sap_idoc", "SAP IDoc (MBGMCR03 flat file)"),
        ("sap_odata", "SAP OData (S/4HANA Material Document API)"),
        ("utility_green_button_csv", "Utility — Green Button CSV"),
        ("utility_espi_xml", "Utility — ESPI XML"),
        ("concur_expense_v3", "Concur Expense API v3"),
        ("concur_itinerary_v1", "Concur Itinerary API v1"),
        ("navan", "Navan"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="data_sources"
    )
    source_type = models.CharField(max_length=50, choices=SOURCE_TYPE_CHOICES)
    display_name = models.CharField(max_length=200)
    connection_config = models.JSONField(default=dict, blank=True)
    # Timezone matters specifically for utility sources (Green Button CSV
    # timestamps are local time with no TZ annotation — MODEL.md §5.1).
    # SAP IDoc dates are date-only so this is unused for SAP.
    timezone = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "data_sources"
        indexes = [models.Index(fields=["organization", "source_type"])]

    def __str__(self) -> str:
        return f"{self.display_name} ({self.source_type})"


class Facility(models.Model):
    """
    Physical location anchor (MODEL.md §5.2).

    Why this exists separately from plant/meter mappings:
      One physical building (e.g., "Munich Plant 01") can be referenced by
      multiple SAP plant codes (one per company code), multiple utility
      meters (electric, gas), and travel records ("trip to Munich"). The
      facility is the canonical thing; mappings translate source-system
      identifiers to it.

    Why grid_region is its own field:
      Scope 2 location-based emission factors depend on grid region, not
      country. The US has many grids (WECC, ERCOT, MISO); Germany has one
      national factor but EU emission factors vary. Storing the grid label
      here lets the future emission calculation lookup work uniformly.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="facilities"
    )
    name = models.CharField(max_length=200)
    address_line1 = models.CharField(max_length=300, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    country_code = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    region = models.CharField(max_length=100, blank=True, default="")
    latitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=10, decimal_places=7, null=True, blank=True
    )
    grid_region = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "facilities"
        verbose_name_plural = "facilities"

    def __str__(self) -> str:
        return f"{self.name} ({self.country_code})"


class PlantLocationMapping(models.Model):
    """
    SAP WERKS → physical Facility (MODEL.md §5.3).

    Why this is its own table (not a foreign key on staging):
      WERKS values are tenant-specific and not in the IDoc payload as a
      lookup. A new plant on the SAP side arrives in IDocs immediately,
      before anyone has mapped it. Keeping mapping out of staging lets us
      ingest unmapped plants and surface them as "needs_plant_mapping"
      records in the dashboard for an analyst to resolve.

    Why valid_from / valid_to (slowly changing dimension):
      Plants can be renamed, split, or moved between facilities. An IDoc
      from three years ago referring to WERKS=DE01 may need to resolve to
      a different facility than today's DE01. Date-bounded mappings keep
      historical normalization correct.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="plant_mappings"
    )
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="plant_mappings"
    )
    werks = models.CharField(max_length=4, help_text="SAP plant code, e.g. DE01")
    sap_plant_name = models.CharField(max_length=200, blank=True, default="")
    facility = models.ForeignKey(
        Facility, on_delete=models.PROTECT, related_name="plant_mappings"
    )
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "plant_location_mappings"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "data_source", "werks", "valid_from"],
                name="uniq_plant_mapping_per_source_date",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "data_source", "werks"]),
        ]

    def __str__(self) -> str:
        return f"{self.werks} → {self.facility.name}"


class MeterLocationMapping(models.Model):
    """
    Utility meter (Green Button UsagePoint ID) → physical Facility
    (MODEL.md §5.4).

    Why a separate table from PlantLocationMapping:
      They look superficially similar (source-system identifier → Facility),
      but the source identifier types are different (utility meter IDs are
      typically much longer and may include the service-account number)
      and the lifecycle is different (meters are physically swapped; SAP
      plants are renamed in metadata). Keeping them separate avoids a
      polymorphic mapping table that would couple two unrelated upstream
      systems.

    Why commodity is on the mapping:
      One facility may have an electric meter, a gas meter, and a water
      meter — same building, different physical meters, different
      emission factors. The commodity here determines which downstream
      activity_type the normalizer produces.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="meter_mappings"
    )
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="meter_mappings"
    )
    utility_usage_point_id = models.CharField(
        max_length=200, help_text="Green Button UsagePoint ID (utility-internal)"
    )
    meter_serial_number = models.CharField(max_length=100, blank=True, default="")
    service_account_id = models.CharField(max_length=200, blank=True, default="")
    facility = models.ForeignKey(
        Facility, on_delete=models.PROTECT, related_name="meter_mappings"
    )
    commodity = models.CharField(
        max_length=20,
        default="electricity",
        help_text="'electricity', 'gas', 'water'",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "meter_location_mappings"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "data_source", "utility_usage_point_id"],
                name="uniq_meter_per_source",
            )
        ]
        indexes = [models.Index(fields=["utility_usage_point_id"])]

    def __str__(self) -> str:
        return f"Meter {self.utility_usage_point_id} → {self.facility.name}"


class ExpenseTypeCategoryMapping(models.Model):
    """
    Per-tenant Concur expense_type_code → canonical ESG activity classifier
    (MODEL.md §5.5).

    Why this is per-tenant (not a global constant):
      Concur expense type codes are configurable per Concur tenant. 'AIRFR'
      is a common default for airfare but a tenant may have renamed it,
      split it, or use entirely custom codes. Research-notes §3.5 confirms
      this directly: "expense type codes are configurable per Concur
      tenant — an organization can rename or recode expense types". The
      ESG platform must never hardcode these.

    Why is_esg_relevant is a separate flag (not just an absence of mapping):
      A tenant may explicitly want to mark 'TAXIB' (taxi to airport) as
      not-ESG-relevant because they account for it under air travel
      already. A missing mapping means "we don't know yet"; an
      is_esg_relevant=False mapping means "we know, and we want to
      exclude it" — different statuses for the dashboard to surface.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="expense_mappings"
    )
    data_source = models.ForeignKey(
        DataSource, on_delete=models.PROTECT, related_name="expense_mappings"
    )
    raw_expense_type_code = models.CharField(max_length=50)
    raw_expense_type_name = models.CharField(max_length=200, blank=True, default="")
    canonical_activity = models.CharField(
        max_length=50,
        help_text=(
            "'flight', 'hotel', 'car_rental', 'taxi', 'personal_mileage', "
            "'rail', 'not_esg_relevant'"
        ),
    )
    ghg_scope = models.CharField(max_length=10, default="scope_3")
    ghg_category = models.CharField(max_length=30, default="cat_6_business_travel")
    is_esg_relevant = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "expense_type_category_mappings"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "data_source", "raw_expense_type_code"],
                name="uniq_expense_type_per_source",
            )
        ]

    def __str__(self) -> str:
        return f"[{self.raw_expense_type_code}] → {self.canonical_activity}"


class UomNormalizationRule(models.Model):
    """
    Raw unit-of-measure code → canonical unit (MODEL.md §5.6).

    Why this exists:
      SAP uses BOTH 'L' and 'LT' for litres (different internal codes in
      T006). Customers add custom codes like 'LTR'. Utilities export 'Wh'
      instead of 'kWh'. Hardcoding these conversions in parser code makes
      them invisible to analysts and impossible to correct without a code
      deploy. A table lets ops add rules at runtime.

    Why source_system in the unique constraint:
      A unit code can mean different things in different source contexts.
      'L' is unambiguous in SAP but could mean 'litre' or 'large' elsewhere.
      Scoping by source_system avoids cross-system collisions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_system = models.CharField(
        max_length=20,
        help_text="'sap', 'utility', 'travel', or 'global'",
    )
    raw_unit = models.CharField(max_length=20)
    canonical_unit = models.CharField(max_length=20)
    conversion_factor = models.DecimalField(max_digits=20, decimal_places=10)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "uom_normalization_rules"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "raw_unit"],
                name="uniq_uom_rule_per_system",
            )
        ]

    def __str__(self) -> str:
        return f"[{self.source_system}] {self.raw_unit} → {self.canonical_unit} ×{self.conversion_factor}"
