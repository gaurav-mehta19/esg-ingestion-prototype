# ESG Platform — Data Model Design

**Prepared:** 2026-05-25  
**Status:** Pre-implementation — decisions pending reviewer sign-off  
**Basis:** `research-notes.md` (2026-05-25)  
**No application code in this document.**

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Multi-tenancy](#2-multi-tenancy)
3. [The Trickiest Decision: Staging Architecture](#3-the-trickiest-decision-staging-architecture)
4. [Layer Overview](#4-layer-overview)
5. [Reference & Configuration Tables](#5-reference--configuration-tables)
6. [Ingestion Tracking Tables](#6-ingestion-tracking-tables)
7. [Staging Layer](#7-staging-layer)
8. [Normalized Activity Layer](#8-normalized-activity-layer)
9. [Emission Factor Versioning](#9-emission-factor-versioning)
10. [Emission Calculations](#10-emission-calculations)
11. [Review & Audit Lifecycle](#11-review--audit-lifecycle)
12. [Entity Relationship Summary](#12-entity-relationship-summary)
13. [Tradeoff Register](#13-tradeoff-register)

---

## 1. Design Principles

Every structural choice below is optimized for four non-negotiable properties, in this priority order:

| Priority | Property | What it means in practice |
|---|---|---|
| 1 | **Auditability** | Regulator can ask "where did this number come from?" and get a complete answer from the database alone |
| 2 | **Correctness of provenance** | Raw ingested values are never overwritten; derivations are always traceable to factors and methods |
| 3 | **Correctness under re-ingestion** | Running the same feed twice produces no duplicates and does not change approved values |
| 4 | **Evolvability** | Adding a fourth data source should not require altering the core emission or review tables |

The research identified several failure modes that directly drive specific model choices:
- SAP `LT` vs `L` (same quantity, different unit codes) → raw unit must be preserved alongside normalized unit
- Green Button `powerOfTenMultiplier` → raw integer value must be preserved to reconstruct the parse
- DEFRA factor changes 16–42% year-over-year → emission factors must be independently versioned
- Billing periods crossing calendar months → activity records must carry both `period_start` and `period_end`
- Concur expense type codes are tenant-configurable → mapping must be stored per-tenant, not hardcoded

---

## 2. Multi-tenancy

**Decision: row-level `organization_id` on every table, with PostgreSQL Row Level Security (RLS).**

Rationale: Schema-per-tenant provides the strongest isolation but makes cross-tenant analytics (e.g., platform-wide benchmarking) require dynamic schema switching, and makes schema migrations multiply with tenant count. Database-per-tenant is operationally expensive for a prototype and typically unnecessary until strict regulatory data residency requirements are known. Row-level with RLS is the standard B2B SaaS pattern and sufficient for the prototype phase.

**Risk of row-level tenancy:** A missing `organization_id` filter in a query leaks cross-tenant data. Mitigation: RLS policies enforced at the database layer mean application bugs cannot bypass isolation. Every table definition below includes `organization_id NOT NULL`.

```
organizations
  id            UUID        PRIMARY KEY  DEFAULT gen_random_uuid()
  name          VARCHAR(200) NOT NULL
  slug          VARCHAR(100) NOT NULL UNIQUE     -- URL-safe identifier
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()

  WHY: The root of all tenancy. Every other table references this via organization_id.
       slug is used for display and external references (never use id in URLs).
```

---

## 3. The Trickiest Decision: Staging Architecture

**The question:** When raw source data arrives (SAP IDoc fixed-width segments, Green Button CSV rows, Concur JSON expense entries), do we store it in:

- **Option A — One unified staging table with a JSON blob per row**
- **Option B — Separate typed staging tables per source**
- **Option C — Hybrid: one table with JSON blob + key scalar columns extracted**

This choice propagates everywhere: validation, deduplication, monitoring, and the shape of the normalization pipeline.

---

### Option A — One Unified Staging Table (JSON blob)

```
staging_records
  id                UUID PK
  organization_id   UUID NOT NULL
  source_type       ENUM('sap', 'utility', 'travel')
  ingestion_run_id  UUID
  raw_payload       JSONB        -- full source record verbatim
  source_record_id  VARCHAR      -- source system's own ID (for dedup)
  ingested_at       TIMESTAMPTZ
  staging_status    VARCHAR
```

**Pros:**
- Single ingestion pipeline writes one table.
- Adding a fourth source requires zero schema changes to staging.
- JSON preserves the exact bytes received, including fields we don't yet know we need.

**Cons:**
- Cannot put a database constraint on `bwart IN ('101','201','261')` — validation moves entirely into application code.
- Queries like "all unprocessed SAP records from plant DE01" require `->` JSON path operators on an untyped blob — slow, fragile, no index support without GIN indexes on specific paths.
- When a source changes its field name (Concur v3 → v4), there is no schema migration to signal the break — it silently produces `null` on JSON path queries.
- Deduplication across re-ingests requires comparing JSONB blobs.

**This option is appropriate if:** The source schema changes frequently and the team prefers to push all parsing into the normalization step rather than at ingest time.

---

### Option B — Separate Typed Staging Tables Per Source

```
sap_goods_movement_staging    -- one row per MBGMCR IDoc item segment
utility_interval_staging      -- one row per Green Button CSV/XML interval reading
travel_expense_staging        -- one row per Concur/Navan expense entry or itinerary segment
```

Each table carries the source's native field names as typed SQL columns (e.g., `werks VARCHAR(4)`, `bwart VARCHAR(3)`, `menge NUMERIC(13,3)`), plus ingestion metadata columns.

**Pros:**
- Database constraints enforce source-specific rules at ingest time (e.g., `CHECK (bwart ~ '^[0-9]{3}$')`).
- Source fields are individually indexable — `WHERE werks = 'DE01' AND bwart = '101'` uses a B-tree index.
- Deduplication is straightforward: unique constraint on `(organization_id, idoc_number, zeile)` for SAP.
- Monitoring is per-source: "how many SAP records failed staging this week" is a simple count query.
- When Concur changes a field name, there is a schema migration to track it.

**Cons:**
- Three tables to write, test, and maintain.
- Union queries across sources during normalization require explicit `UNION ALL`.
- Adding a new source requires a new migration and a new staging table.

**This option is appropriate if:** The sources are well-understood and stable (they are — we have research notes), and per-source validation matters (it does — see `powerOfTenMultiplier` for utilities, `bwart` reversal logic for SAP).

---

### Option C — Hybrid: JSON Blob + Extracted Scalars

```
staging_records
  id                UUID PK
  organization_id   UUID NOT NULL
  source_type       ENUM('sap_goods_movement', 'utility_interval', 'travel_expense', 'travel_itinerary')
  ingestion_run_id  UUID
  raw_payload       JSONB        -- full fidelity archive of the source record
  -- Key scalar columns extracted at ingest time for indexing and dedup:
  source_record_id  VARCHAR      -- MBLNR+ZEILE for SAP, UsagePoint+interval_start for utility, etc.
  record_date       DATE         -- BUDAT for SAP, interval date for utility, transaction_date for travel
  parse_status      ENUM('ok', 'partial', 'failed')
  parse_warnings    JSONB        -- array of {field, warning} objects
  ingested_at       TIMESTAMPTZ
  staging_status    ENUM('pending', 'normalized', 'rejected', 'duplicate')
```

**Pros:**
- Single table simplifies the write path.
- JSON blob preserves full fidelity for future re-parsing.
- Extracted scalars allow indexed dedup and date-range queries.
- Parse warnings are structured, not buried in a log file.

**Cons:**
- Source-specific validation is still in application code, not the database.
- The extracted scalar set must cover every query pattern — if a new query needs `werks`, it either hits the JSON or requires a migration to add the column.
- The `source_record_id` computation differs per source type and must be centralized — easy to get wrong.

**This option is appropriate if:** The team expects many source types quickly and wants to defer schema design per source.

---

### Recommendation: **Option B — Separate Typed Staging Tables**

**Reason:** The research identified specific, named failure modes for each source that require source-specific validation logic:
- SAP: `bwart` reversal detection, `meins` custom UoM codes, `budat` vs `bldat` disambiguation.
- Utility: `powerOfTenMultiplier` scaling, `is_estimated` flag, interval length normalization.
- Travel: `expense_type_code` per-tenant mapping, `journey_distance` null pattern, IATA code extraction.

These validations are far cleaner as database constraints and typed column operations than as JSON path queries. The cost (three staging tables) is fixed and manageable. **The choice to make is Option B.**

Option A is a viable fallback if the team discovers a fourth source with a highly volatile schema in the first 90 days.

---

## 4. Layer Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  SOURCE SYSTEMS                                                 │
│  SAP IDoc / OData │ Green Button CSV/XML │ Concur API / Navan   │
└────────────────────────────┬────────────────────────────────────┘
                             │ ingestion run
┌────────────────────────────▼────────────────────────────────────┐
│  INGESTION TRACKING                                             │
│  data_sources │ ingestion_runs                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ raw rows
┌────────────────────────────▼────────────────────────────────────┐
│  STAGING LAYER  (raw, typed, per-source)                        │
│  sap_goods_movement_staging                                     │
│  utility_interval_staging                                       │
│  travel_expense_staging                                         │
└────────────────────────────┬────────────────────────────────────┘
                             │ normalization pipeline
┌────────────────────────────▼────────────────────────────────────┐
│  REFERENCE TABLES  (lookup, config, mappings)                   │
│  facilities │ plant_location_mappings │ meter_location_mappings │
│  expense_type_category_mappings │ uom_normalization_rules       │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  NORMALIZED ACTIVITY LAYER                                      │
│  activity_records  (one row per measurable event, all sources)  │
└────────────────────────────┬────────────────────────────────────┘
                             │ emission calculation pipeline
┌────────────────────────────▼────────────────────────────────────┐
│  EMISSION FACTOR VERSIONING                                     │
│  emission_factor_datasets │ emission_factors                    │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  EMISSION CALCULATIONS                                          │
│  emission_calculations  (activity × factor → tCO2e)            │
└────────────────────────────┬────────────────────────────────────┘
                             │ review workflow
┌────────────────────────────▼────────────────────────────────────┐
│  AUDIT TRAIL                                                    │
│  review_events  (full history of state transitions)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Reference & Configuration Tables

### 5.1 `data_sources`

```
data_sources
  id                UUID        PK
  organization_id   UUID        NOT NULL FK → organizations
  source_type       VARCHAR(50) NOT NULL
                    -- ENUM values: 'sap_idoc', 'sap_odata', 'utility_green_button_csv',
                    --              'utility_espi_xml', 'concur_expense_v3', 'concur_itinerary_v1',
                    --              'navan'
  display_name      VARCHAR(200) NOT NULL   -- "SAP ECC Production — DE01 plant group"
  connection_config JSONB                   -- connection params; credential values reference secrets vault
  timezone          VARCHAR(50)            -- for utility sources: 'America/Los_Angeles'
  is_active         BOOLEAN     NOT NULL DEFAULT TRUE
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()

  WHY: One organization may have multiple SAP instances, multiple utility accounts, or multiple
       Concur tenants. Source type drives which staging table is written and which normalization
       rules apply.

  WHY timezone: Green Button CSV timestamps are local time without timezone annotation.
                The platform cannot recover the timezone from the data alone — it must be
                configured here.
```

### 5.2 `facilities`

```
facilities
  id                UUID        PK
  organization_id   UUID        NOT NULL FK → organizations
  name              VARCHAR(200) NOT NULL    -- "London Headquarters", "Munich Plant 01"
  address_line1     VARCHAR(300)
  city              VARCHAR(100)
  country_code      CHAR(2)     NOT NULL     -- ISO 3166-1 alpha-2
  region            VARCHAR(100)             -- state/province
  latitude          NUMERIC(10,7)
  longitude         NUMERIC(10,7)
  grid_region       VARCHAR(50)              -- 'GB', 'WECC', 'ERCOT', 'EU_DE' etc.
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()

  WHY: The physical location is the anchor for two distinct operations:
       (1) Scope 2 location-based method requires the grid region to select the correct
           electricity emission factor (e.g., UK national grid vs Bavarian mix).
       (2) Scope 1 reporting boundary — emissions are attributed to the facility where
           fuel was consumed, not where the PO was raised.
       Without this table, SAP plant codes and utility meter IDs are opaque.
```

### 5.3 `plant_location_mappings`

```
plant_location_mappings
  id                UUID        PK
  organization_id   UUID        NOT NULL FK → organizations
  data_source_id    UUID        NOT NULL FK → data_sources
  werks             CHAR(4)     NOT NULL     -- SAP plant code, e.g. 'DE01'
  sap_plant_name    VARCHAR(200)             -- NAME1 from T001W, captured at config time
  facility_id       UUID        NOT NULL FK → facilities
  valid_from        DATE        NOT NULL
  valid_to          DATE                     -- NULL = currently active

  UNIQUE (organization_id, data_source_id, werks, valid_from)

  WHY: WERKS is a 4-character opaque key meaningful only within one SAP system.
       'DE01' at client A is entirely different from 'DE01' at client B.
       Without this mapping, the platform cannot determine the geographic location
       of any SAP goods movement, making location-based Scope 2 and Scope 1 attribution
       impossible. The valid_from/valid_to pattern handles plant renaming and splits.
```

### 5.4 `meter_location_mappings`

```
meter_location_mappings
  id                    UUID        PK
  organization_id       UUID        NOT NULL FK → organizations
  data_source_id        UUID        NOT NULL FK → data_sources
  utility_usage_point_id VARCHAR(200) NOT NULL  -- Green Button UsagePoint ID (utility-internal)
  meter_serial_number   VARCHAR(100)
  service_account_id    VARCHAR(200)            -- utility account number
  facility_id           UUID        NOT NULL FK → facilities
  commodity             VARCHAR(20) NOT NULL    -- 'electricity', 'gas', 'water'
  is_active             BOOLEAN     NOT NULL DEFAULT TRUE
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()

  WHY: The Green Button `UsagePoint` ID is a utility-internal identifier. Without this table,
       the platform cannot assign electricity consumption to a physical building or cost centre.
       Multi-site accounts (one utility account, many meters) make this a 1-to-many relationship
       that must be maintained manually or via the utility's API.
```

### 5.5 `expense_type_category_mappings`

```
expense_type_category_mappings
  id                    UUID        PK
  organization_id       UUID        NOT NULL FK → organizations
  data_source_id        UUID        NOT NULL FK → data_sources
  raw_expense_type_code VARCHAR(50) NOT NULL    -- 'AIRFR', 'LODNG', 'CARRT', customer-specific
  raw_expense_type_name VARCHAR(200)
  canonical_activity    VARCHAR(50) NOT NULL
                        -- ENUM: 'flight', 'hotel', 'car_rental', 'taxi', 'personal_mileage',
                        --       'rail', 'not_esg_relevant'
  ghg_scope             VARCHAR(10) NOT NULL    -- 'scope_3'
  ghg_category          VARCHAR(30) NOT NULL    -- 'cat_6_business_travel'
  is_esg_relevant       BOOLEAN     NOT NULL DEFAULT TRUE
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()

  UNIQUE (organization_id, data_source_id, raw_expense_type_code)

  WHY: Concur expense type codes are configurable per tenant. 'AIRFR' is a common default
       for airfare but a tenant may have renamed it 'AIRFL' or split it into 'DOMAIR'/'INTAIR'.
       The ESG platform must NOT hardcode expense type codes. This table is populated during
       tenant onboarding by fetching the Expense Group Configurations endpoint and having
       an analyst confirm the mapping.
```

### 5.6 `uom_normalization_rules`

```
uom_normalization_rules
  id                UUID        PK
  source_system     VARCHAR(20) NOT NULL    -- 'sap', 'utility', 'travel', 'global'
  raw_unit          VARCHAR(20) NOT NULL    -- 'LT', 'LTR', 'GAL', 'Wh', 'MMBTU'
  canonical_unit    VARCHAR(20) NOT NULL    -- 'L', 'kWh', 'GJ'
  conversion_factor NUMERIC(20,10) NOT NULL -- canonical = raw * factor
  notes             TEXT                    -- 'SAP LT = litres, same as L, T006 custom UoM'
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()

  UNIQUE (source_system, raw_unit)

  WHY: The research identified that SAP uses both 'L' and 'LT' for litres (different internal
       UoM keys in T006), and utilities may export 'Wh' instead of 'kWh' (factor 0.001).
       Custom UoM codes added by individual SAP customers ('LTR') cannot be anticipated in
       code. This table is the authoritative conversion registry and must be maintained.
       When a record arrives with an unknown unit, it is staged with a parse_warning and
       this table is queried; if no rule exists, staging_status is set to 'needs_uom_mapping'.
```

---

## 6. Ingestion Tracking Tables

### 6.1 `ingestion_runs`

```
ingestion_runs
  id                UUID        PK
  organization_id   UUID        NOT NULL FK → organizations
  data_source_id    UUID        NOT NULL FK → data_sources
  triggered_by      VARCHAR(50) NOT NULL    -- 'scheduler', 'manual', 'webhook'
  triggered_by_user VARCHAR(200)            -- user ID if manual
  started_at        TIMESTAMPTZ NOT NULL
  completed_at      TIMESTAMPTZ
  status            VARCHAR(20) NOT NULL    -- 'running', 'completed', 'failed', 'partial'
  rows_received     INT
  rows_staged_ok    INT
  rows_staged_warn  INT         -- staged but with parse warnings
  rows_failed       INT
  rows_duplicate    INT
  source_date_range_start DATE  -- the date range of data requested (not the run date)
  source_date_range_end   DATE
  error_summary     JSONB       -- structured errors, not a text blob
  raw_file_ref      TEXT        -- S3/GCS path to the raw file, if applicable

  WHY: Every staging row links to an ingestion run. If an IDoc file is re-delivered,
       the run record makes it possible to detect and deduplicate the re-ingest.
       If a parser bug is fixed, the run record allows re-processing specifically the
       affected runs rather than re-processing all history.
       rows_staged_warn (not just rows_failed) matters because Green Button estimated
       reads succeed staging but need analyst attention.
```

---

## 7. Staging Layer

### 7.1 `sap_goods_movement_staging`

One row per line item in a goods movement IDoc (one `E1BP2017_GM_ITEM_CREATE` segment = one row).

```
sap_goods_movement_staging
  id                    UUID        PK
  organization_id       UUID        NOT NULL FK → organizations
  ingestion_run_id      UUID        NOT NULL FK → ingestion_runs

  -- IDoc envelope (from EDIDC / EDID4 control record)
  idoc_number           VARCHAR(16)             -- DOCNUM
  idoc_type             VARCHAR(30)             -- 'MBGMCR03'
  idoc_message_type     VARCHAR(30)             -- 'MBGMCR'
  sender_partner        VARCHAR(50)             -- SNDPRN (SAP system sending)

  -- Header fields (from E1BP2017_GM_HEAD_01)
  raw_budat             CHAR(8)                 -- '20231015' YYYYMMDD, verbatim
  raw_bldat             CHAR(8)                 -- '20231012' YYYYMMDD, verbatim
  parsed_budat          DATE                    -- NULL if raw_budat unparseable
  parsed_bldat          DATE
  ref_doc_no            VARCHAR(16)             -- delivery note reference
  gm_code               CHAR(2)                 -- '01','02' etc.

  -- Item fields (from E1BP2017_GM_ITEM_CREATE)
  item_line             INT         NOT NULL    -- sequence within the IDoc
  matnr                 VARCHAR(18)             -- material number (right-padded in IDoc)
  werks                 CHAR(4)                 -- plant code
  lgort                 CHAR(4)                 -- storage location
  bwart                 CHAR(3)                 -- movement type: '101','102','201','261' etc.
  entry_qnt             NUMERIC(15,3)           -- quantity as received
  entry_uom             VARCHAR(3)              -- unit as received: 'L', 'LT', 'M3', 'KG'
  ebeln                 VARCHAR(10)             -- purchase order number
  ebelp                 CHAR(5)                 -- PO line item
  bukrs                 CHAR(4)                 -- company code
  lifnr                 VARCHAR(10)             -- vendor account number
  dmbtr                 NUMERIC(15,2)           -- amount in local currency
  waers                 CHAR(5)                 -- currency key

  -- Derived at ingest time (not from source, computed by ingestion pipeline)
  is_reversal           BOOLEAN     NOT NULL DEFAULT FALSE
                        -- TRUE if bwart IN ('102','202','262','502','122','161','122')
                        -- WHY: reversals must subtract from totals; detecting them here
                        --      prevents double-counting in the normalization step.

  -- Deduplication key
  UNIQUE (organization_id, idoc_number, item_line)

  -- Ingestion metadata
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  parse_warnings        JSONB       -- [{field:'entry_uom', warning:'unknown UoM LTR'}]
  staging_status        VARCHAR(20) NOT NULL DEFAULT 'pending'
                        -- 'pending','normalized','rejected','duplicate','needs_uom_mapping','needs_plant_mapping'
  rejection_reason      TEXT

  WHY (table): Typed staging means the database enforces that WERKS is exactly 4 characters
               and BWART is exactly 3 — format violations are caught at ingest, not days later
               during normalization. The raw_budat/parsed_budat split preserves the original
               string (needed if a bug in date parsing must be corrected) while the parsed
               column is what the normalization pipeline uses.
```

### 7.2 `utility_interval_staging`

One row per interval reading (15-min, 30-min, or 60-min depending on utility and meter).

```
utility_interval_staging
  id                        UUID        PK
  organization_id           UUID        NOT NULL FK → organizations
  ingestion_run_id          UUID        NOT NULL FK → ingestion_runs

  -- Source identification
  utility_usage_point_id    VARCHAR(200) NOT NULL  -- Green Button UsagePoint ID
  source_format             VARCHAR(30)  NOT NULL  -- 'green_button_csv', 'espi_xml', 'green_button_cmd'

  -- Raw fields — Green Button CSV (preserved verbatim)
  raw_date                  VARCHAR(20)             -- '1/1/2024' not ISO 8601
  raw_start_time            VARCHAR(10)             -- '00:00'
  raw_end_time              VARCHAR(10)             -- '00:15'
  raw_usage                 VARCHAR(30)             -- '0.432' as a string
  raw_units                 VARCHAR(20)             -- 'kWh', 'Wh', 'kW'
  raw_cost                  VARCHAR(30)             -- '$0.08' — has $ prefix
  raw_notes                 TEXT                    -- 'Estimated' or blank

  -- ESPI XML specific (preserved verbatim from XML, for XML sources)
  espi_interval_start_epoch BIGINT                  -- Unix epoch UTC (start of interval)
  espi_interval_duration_s  INT                     -- seconds: 900=15min, 1800=30min, 3600=1h
  espi_value_raw            BIGINT                  -- integer value before multiplier
  espi_power_of_ten_mult    INT                     -- powerOfTenMultiplier; 0=no scaling, -3=÷1000
  espi_uom_code             INT                     -- 72=kWh, 73=kW, 119=Wh
  espi_reading_quality      INT                     -- 19=validated, 20=estimated

  -- Parsed / derived
  interval_start            TIMESTAMPTZ             -- NULL if timezone unknown at parse time
  interval_end              TIMESTAMPTZ
  interval_length_minutes   INT                     -- 15, 30, or 60
  usage_value               NUMERIC(20,6)           -- parsed quantity (after multiplier applied)
  usage_unit                VARCHAR(10)             -- 'kWh' (normalized from raw_units)
  is_estimated              BOOLEAN     NOT NULL DEFAULT FALSE
                            -- TRUE if raw_notes ILIKE '%estimated%'
                            --   or espi_reading_quality = 20

  -- Deduplication key
  UNIQUE (organization_id, utility_usage_point_id, interval_start)

  -- Ingestion metadata
  ingested_at               TIMESTAMPTZ NOT NULL DEFAULT now()
  parse_warnings            JSONB       -- [{field:'interval_start', warning:'timezone unknown'}]
  staging_status            VARCHAR(20) NOT NULL DEFAULT 'pending'
                            -- 'pending','normalized','rejected','duplicate','needs_meter_mapping',
                            --  'needs_timezone_config'

  WHY (table): Separating the raw CSV strings from the parsed values is critical because the
               Green Button date format ('1/1/2024', not '2024-01-01') and the ESPI
               powerOfTenMultiplier scaling have both caused real data quality failures.
               Preserving raw strings means a parser bug can be corrected by re-processing
               staging without re-ingesting from the utility portal.
               is_estimated is derived here (not in normalization) so that dashboards can
               immediately show "X% of this month's data is estimated" without running the
               full normalization pipeline.
```

### 7.3 `travel_expense_staging`

One row per Concur expense entry or itinerary segment. Expense entries and itinerary segments are separate records linked by booking ID.

```
travel_expense_staging
  id                    UUID        PK
  organization_id       UUID        NOT NULL FK → organizations
  ingestion_run_id      UUID        NOT NULL FK → ingestion_runs

  -- Source
  source_system         VARCHAR(20) NOT NULL    -- 'concur', 'navan', 'amex_gbt'
  record_type           VARCHAR(20) NOT NULL    -- 'expense_entry', 'air_segment',
                                               --  'hotel_segment', 'car_segment'

  -- Concur expense entry fields (when record_type = 'expense_entry')
  concur_report_id      VARCHAR(50)
  concur_entry_id       VARCHAR(50)
  expense_type_code     VARCHAR(20)             -- 'AIRFR', 'LODNG', 'CARRT', 'MILEG' (tenant-specific)
  expense_type_name     VARCHAR(200)
  spend_category_code   VARCHAR(50)             -- 'TRAVEL'
  transaction_date      DATE
  transaction_amount    NUMERIC(15,2)
  transaction_currency  CHAR(3)                 -- 'USD', 'GBP', 'EUR'
  vendor_description    VARCHAR(500)            -- 'BRITISH AIRWAYS', 'HILTON LONDON'
  location_name         VARCHAR(500)
  location_country      VARCHAR(100)
  is_personal           BOOLEAN
  journey_distance      NUMERIC(10,2)           -- NULL for almost all air/hotel/car; populated for MILEG
  distance_unit         VARCHAR(10)             -- 'mile', 'km', or NULL
  comment_text          TEXT

  -- Concur itinerary API — air segment (when record_type = 'air_segment')
  itinerary_booking_id  VARCHAR(50)
  origin_iata           CHAR(3)                 -- airport code, e.g. 'JFK'
  destination_iata      CHAR(3)                 -- 'LHR'
  class_of_service      CHAR(1)                 -- 'Y'=economy, 'W'=premium eco, 'C'=business, 'F'=first
  aircraft_iata_code    CHAR(3)                 -- IATA aircraft type, e.g. '789'=Boeing 787-9
  flight_number         VARCHAR(10)
  departure_datetime    TIMESTAMPTZ
  arrival_datetime      TIMESTAMPTZ

  -- Concur itinerary API — hotel segment (when record_type = 'hotel_segment')
  hotel_checkin_date    DATE
  hotel_checkout_date   DATE
  room_nights           INT                     -- computed: checkout_date - checkin_date

  -- Concur itinerary API — car segment (when record_type = 'car_segment')
  car_acriss_code       CHAR(4)                 -- e.g. 'ICAR' = Intermediate Car, auto, A/C, regular fuel
  car_rental_start      DATE
  car_rental_end        DATE

  -- Linking expense entries to itinerary segments
  linked_itinerary_id   VARCHAR(50)             -- booking_id from itinerary, if linkable
                                               -- NULL for orphan expenses (booked outside Concur)

  -- Deduplication key
  UNIQUE (organization_id, source_system, concur_entry_id)  -- for expense_entry records
  -- (itinerary segments deduplicated on booking_id + segment sequence, handled in app logic)

  -- Ingestion metadata
  ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  parse_warnings        JSONB
  staging_status        VARCHAR(20) NOT NULL DEFAULT 'pending'
                        -- 'pending','normalized','rejected','duplicate',
                        --  'needs_expense_type_mapping','no_itinerary_linkage'

  WHY (table): Travel is the only source where the ESG-critical fields (origin/destination
               IATA codes) live in a different API endpoint than the financial fields
               (expense entry). Staging both in one table allows normalization to join them
               via linked_itinerary_id without requiring an additional intermediate table.
               The no_itinerary_linkage status flags the common case where a corporate card
               charge exists (expense entry present) but the booking was made outside Concur
               Travel (no itinerary record) — these must fall back to spend-based method.
```

---

## 8. Normalized Activity Layer

### The core design decision: raw and normalized values in the same row vs. separate tables

**Option considered: separate tables**
A `raw_activity_records` table and a `normalized_activity_records` table, linked by FK.

Pro: Clean separation — if normalization is re-run, only the normalized table changes.  
Con: Every audit query that a regulator or analyst asks ("what was the raw value for this record?") becomes a join. The whole point of this layer is human-readable audit, and joins obscure it.

**Decision: same row, with explicit raw_* and normalized_* column pairs.**

Rationale: The raw → normalized transformation is deterministic (it is a unit conversion with a conversion factor). The conversion factor is also stored on the row. A regulator can verify the arithmetic without any additional join. If a correction is needed, the `is_manually_edited` flag and `original_values JSONB` snapshot preserve the before-state. Re-normalization (e.g., when a UoM rule is corrected) writes new values to normalized_* columns but leaves raw_* untouched.

The emission calculation (normalized quantity × emission factor = tCO2e) is **separate** because emission factors change annually and must be re-run against fixed activity data — this is the only place a separate table is justified.

### 8.1 `activity_records`

```
activity_records
  id                        UUID        PK
  organization_id           UUID        NOT NULL FK → organizations

  -- Source provenance (immutable after creation)
  source_type               VARCHAR(30) NOT NULL
                            -- 'sap_goods_movement', 'utility_interval', 'travel_air',
                            --  'travel_hotel', 'travel_car_rental', 'travel_personal_mileage'
  staging_id                UUID        NOT NULL    -- FK into the appropriate staging table
  staging_table             VARCHAR(60) NOT NULL    -- 'sap_goods_movement_staging' etc.
  data_source_id            UUID        NOT NULL FK → data_sources
  ingestion_run_id          UUID        NOT NULL FK → ingestion_runs

  -- Temporal: the canonical date(s) for this activity event
  activity_date             DATE        NOT NULL
                            -- For SAP: parsed_budat (posting date, not document date)
                            -- For utility: interval_start date (local)
                            -- For travel: transaction_date (expense) or departure_date (itinerary)
                            -- WHY: a single canonical date is needed for period bucketing in reports.
                            --      The choice of field is documented in staging_notes.
  activity_period_start     DATE                    -- For multi-day events: hotel check-in, billing period start
  activity_period_end       DATE                    -- Hotel check-out, billing period end
                            -- WHY: A hotel stay of 3 nights or a 35-day billing cycle cannot
                            --      be accurately prorated without both endpoints. Single-date
                            --      activities leave these NULL.

  -- Location
  facility_id               UUID        FK → facilities   -- NULL if plant/meter not yet mapped
  location_country_code     CHAR(2)                 -- ISO 3166-1 alpha-2, populated from facility
  grid_region               VARCHAR(50)             -- from facility.grid_region, for Scope 2 factor selection

  -- GHG scope and classification
  ghg_scope                 VARCHAR(10) NOT NULL    -- 'scope_1', 'scope_2', 'scope_3'
  ghg_category              VARCHAR(30)             -- 'cat_1_purchased_goods', 'cat_6_business_travel'
  activity_type             VARCHAR(80) NOT NULL
                            -- Granular type, determines which emission factor row applies:
                            -- 'diesel_combustion', 'natural_gas_combustion',
                            -- 'grid_electricity', 'flight_economy_short_haul',
                            -- 'flight_business_long_haul', 'hotel_stay_uk', 'car_rental_petrol',
                            -- 'personal_car_mileage'

  -- Raw quantity: preserved exactly from staging, never overwritten
  raw_quantity              NUMERIC(20,6) NOT NULL
  raw_unit                  VARCHAR(20)   NOT NULL  -- 'LT', 'M3', 'kWh', 'mile', as received

  -- Normalized quantity: after unit conversion to canonical unit
  normalized_quantity       NUMERIC(20,6)           -- NULL if unit conversion failed
  normalized_unit           VARCHAR(20)             -- 'L', 'kWh', 'passenger_km', 'room_night'
  unit_conversion_factor    NUMERIC(20,10)          -- normalized = raw * factor
  unit_conversion_source    VARCHAR(50)             -- 'uom_normalization_rules', 'manual', 'no_conversion_needed'
  unit_conversion_notes     TEXT                    -- 'LT→L: SAP custom UoM, same quantity per T006'

  -- Supplementary computed fields (travel-specific)
  computed_distance_km      NUMERIC(10,2)           -- for air travel, computed from IATA codes
  distance_computation_method VARCHAR(80)           -- 'great_circle_1.08_uplift', 'spend_proxy', 'reported'
                            -- WHY: The method matters for uncertainty quantification.
                            --      Great-circle distances are more accurate than spend-based;
                            --      analysts need to know which was used.

  -- Reversal / cancellation tracking
  is_reversal               BOOLEAN     NOT NULL DEFAULT FALSE
  reverses_activity_id      UUID        FK → activity_records (self)   -- NULL if not a reversal
                            -- WHY: SAP movement types 102/202/262 reverse prior receipts.
                            --      Marking reversals here (rather than deleting the original)
                            --      preserves the complete history for audit while ensuring
                            --      aggregations can exclude both legs.

  -- Edit tracking (for analyst corrections)
  is_manually_edited        BOOLEAN     NOT NULL DEFAULT FALSE
  original_values           JSONB                   -- snapshot of columns before first manual edit
                            -- e.g. {"normalized_quantity": 500, "normalized_unit": "L", "activity_type": "diesel_combustion"}
  edited_by                 VARCHAR(200)
  edited_at                 TIMESTAMPTZ
  edit_reason               TEXT        NOT NULL    -- required when is_manually_edited is set

  -- Review lifecycle (current state — history is in review_events)
  review_status             VARCHAR(20) NOT NULL DEFAULT 'ingested'
                            -- 'ingested' → 'flagged' → 'analyst_reviewed' → 'approved' → 'locked'
  flagged_reason            TEXT                    -- why it was flagged (system or manual)
  reviewed_by               VARCHAR(200)
  reviewed_at               TIMESTAMPTZ
  approved_by               VARCHAR(200)
  approved_at               TIMESTAMPTZ
  locked_by                 VARCHAR(200)
  locked_at                 TIMESTAMPTZ
                            -- WHY these are columns (not only in review_events):
                            --   Fast filtering — "show me all unapproved records for Q4"
                            --   is a single table scan on review_status, not a subquery on
                            --   review_events. review_events carries the full history;
                            --   these columns carry the current state.

  -- Staging notes (explanation of how this record was derived)
  staging_notes             TEXT        -- e.g. 'Date field: used BUDAT not BLDAT; BLDAT was 3 days prior'

  -- Standard metadata
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()

  -- Constraints
  CHECK (review_status IN ('ingested','flagged','analyst_reviewed','approved','locked'))
  CHECK (ghg_scope IN ('scope_1','scope_2','scope_3'))
  CHECK (NOT (is_reversal = TRUE AND reverses_activity_id IS NULL))
        -- a reversal must point to the record it reverses

  WHY (table): The single most important table in the model. It is the stable layer that
               reporting, emission calculations, and analyst review all operate against.
               Every row has one immutable source (staging_id + staging_table) and one
               current review status. The raw/normalized column pair on the same row means
               a regulator can verify "did the unit conversion change the quantity?" without
               any join. The emission calculation is not here — it is in a separate table
               because factors change and calculations must be re-runnable without altering
               activity data.
```

---

## 9. Emission Factor Versioning

**The problem:** DEFRA aviation factors dropped 16–42% between 2024 and 2025 (research-notes §3.11). An organization reporting FY2024 data in early 2025 must use 2024 factors. If factors are stored as a single current row, historical re-runs become impossible without a code change.

**Decision:** Two-table design — a `emission_factor_datasets` table names and versions the factor set, and `emission_factors` contains individual rows linked to a dataset.

**Alternative considered:** SCD Type 2 on a single `emission_factors` table (valid_from/valid_to per row). Rejected because: (1) "DEFRA 2024" is a named artifact that practitioners reference explicitly — a version record is more natural than inferring version from date ranges; (2) querying "all factors that were active on 2024-07-01" with SCD Type 2 requires `WHERE valid_from <= '2024-07-01' AND (valid_to IS NULL OR valid_to > '2024-07-01')` on every join — the dataset approach is simpler.

### 9.1 `emission_factor_datasets`

```
emission_factor_datasets
  id                UUID        PK
  name              VARCHAR(100) NOT NULL UNIQUE   -- 'DEFRA 2024', 'DEFRA 2025', 'EPA 2023'
  publisher         VARCHAR(200)                   -- 'UK DESNZ', 'US EPA', 'IEA'
  vintage_year      INT         NOT NULL            -- 2024, 2025 — the year the factors apply TO
  publication_date  DATE                            -- when the dataset was published
  effective_from    DATE        NOT NULL            -- when to start applying these factors
  source_url        TEXT
  notes             TEXT
  is_platform_default BOOLEAN   NOT NULL DEFAULT FALSE
                    -- which dataset is used for new calculations if not explicitly specified

  WHY: A named, versioned dataset is the unit a practitioner thinks in ("I used DEFRA 2025").
       Calculations link to a specific dataset ID, making it possible to answer "which version
       of DEFRA was used for Q3 2024 reporting?" years later.
```

### 9.2 `emission_factors`

```
emission_factors
  id                UUID        PK
  dataset_id        UUID        NOT NULL FK → emission_factor_datasets
  activity_type     VARCHAR(80) NOT NULL    -- must match values used in activity_records.activity_type
  scope             VARCHAR(10) NOT NULL
  geography         CHAR(2)                 -- ISO country code, or 'GLOBAL', 'EU'
  sub_geography     VARCHAR(50)             -- grid region, state
  input_unit        VARCHAR(20) NOT NULL    -- 'L', 'kWh', 'passenger_km', 'room_night'
  output_unit       VARCHAR(20) NOT NULL DEFAULT 'kg_co2e'
  factor_value      NUMERIC(20,10) NOT NULL -- kg CO2e per 1 input_unit
  factor_co2        NUMERIC(20,10)          -- CO2 component only
  factor_ch4        NUMERIC(20,10)          -- CH4 component (converted to CO2e via GWP)
  factor_n2o        NUMERIC(20,10)          -- N2O component
  gwp_version       VARCHAR(10)             -- 'AR5', 'AR6' — which IPCC GWP100 was used
  source_note       TEXT                    -- exact table/row in source publication

  UNIQUE (dataset_id, activity_type, geography, sub_geography, input_unit)

  WHY: The activity_type column must use the same controlled vocabulary as
       activity_records.activity_type — this is the join key between activity and factor.
       Storing component gases (CO2, CH4, N2O) separately allows re-calculation if GWP
       factors change without requiring new factor rows.
       factor_value is the pre-multiplied total — used for most calculations.
       Component columns are for completeness and regulatory breakdowns.
```

---

## 10. Emission Calculations

**Design decision: emission calculations are separate from activity records.**

Rationale: Emission factors change annually. An organization must be able to:
1. Re-run calculations for all 2024 activity using 2025 DEFRA factors (for comparison).
2. Re-run calculations after correcting a UoM mapping error (which changes normalized_quantity).
3. Show, in audit, exactly which factor version produced a given tCO2e number.

If calculations were columns on `activity_records`, re-running would require updating the table that analysts have approved and locked. A separate table allows new calculation rows to be written while the activity record remains immutable.

### 10.1 `emission_calculations`

```
emission_calculations
  id                        UUID        PK
  organization_id           UUID        NOT NULL FK → organizations
  activity_record_id        UUID        NOT NULL FK → activity_records
  emission_factor_id        UUID        NOT NULL FK → emission_factors
  emission_factor_dataset_id UUID       NOT NULL FK → emission_factor_datasets

  -- Inputs used (snapshot at calculation time — factor may later be superseded)
  activity_quantity         NUMERIC(20,6) NOT NULL    -- from activity_records.normalized_quantity
  activity_unit             VARCHAR(20)   NOT NULL    -- from activity_records.normalized_unit
  factor_value_used         NUMERIC(20,10) NOT NULL   -- from emission_factors.factor_value at calc time

  -- Output
  calculated_co2e_kg        NUMERIC(20,6) NOT NULL    -- activity_quantity * factor_value_used
  calculated_co2e_tonne     NUMERIC(20,9) NOT NULL    -- calculated_co2e_kg / 1000

  -- Methodology metadata
  calculation_method        VARCHAR(50)   NOT NULL
                            -- 'distance_based', 'spend_based', 'direct_measurement', 'supplier_specific'
  confidence_level          VARCHAR(10)   NOT NULL
                            -- 'high', 'medium', 'low'
                            -- high = distance-based with validated data
                            -- medium = estimated read, spend-based with activity data
                            -- low = spend-based proxy, no activity data
  calculation_notes         TEXT          -- 'No itinerary; fell back to spend-based method'

  -- Reporting period assignment
  reporting_year            INT           NOT NULL    -- 2024
  reporting_period          VARCHAR(10)               -- '2024-Q4', '2024' (for annual)
  proration_factor          NUMERIC(6,4)              -- for billing period spanning months;
                                                     -- 1.0 if not prorated
  proration_method          VARCHAR(50)              -- 'uniform_daily', 'interval_data', 'none'

  -- Provenance
  is_current                BOOLEAN       NOT NULL DEFAULT TRUE
                            -- FALSE when superseded by a re-calculation
                            -- WHY: keeps history without deletions; aggregations filter WHERE is_current=TRUE
  superseded_by             UUID          FK → emission_calculations (self)
  calculated_at             TIMESTAMPTZ   NOT NULL DEFAULT now()
  calculated_by             VARCHAR(100)  NOT NULL    -- 'system_v1.2', 'analyst:j.smith'

  UNIQUE (activity_record_id, emission_factor_dataset_id, reporting_year)
         -- one calculation per activity record per factor dataset per reporting year
         -- (if re-calculated, the old row is set is_current=FALSE and new row inserted)

  WHY (table): The separation of activity from calculation is the core audit property.
               An auditor can ask "what number did you submit for Q4 2024?" and the answer
               is emission_calculations WHERE is_current=TRUE AND reporting_period='2024-Q4'.
               They can then ask "what factor did you use?" and follow the FK to emission_factors
               and emission_factor_datasets without any ambiguity about factor vintage.
               proration_factor handles the billing period misalignment problem identified in
               research-notes §2.8: a 35-day utility bill that spans two quarters needs its
               consumption split proportionally.
```

---

## 11. Review & Audit Lifecycle

### 11.1 State Machine

```
ingested ──► flagged ──────────────────────────────► analyst_reviewed ──► approved ──► locked
    │             │                                         │
    │             └── (unflagged) ──► ingested              └── (rejected) ──► flagged
    │
    └── (auto-flagged by system rule)
         - is_estimated = TRUE
         - unit_conversion_factor applied was non-1 and unusual
         - normalized_quantity > 3σ from site historical mean
         - no facility_id mapping found
         - staging had parse_warnings
```

**Transition rules:**
- `locked` records cannot be edited or re-approved. Unlocking requires a supervisor role and creates a `review_events` entry.
- Manual edits (changing `normalized_quantity`, `activity_type` etc.) automatically set review_status back to `flagged` and require a new approval cycle.
- System auto-flags are cleared by analyst review; they do not require a second approver.

### 11.2 `review_events`

```
review_events
  id                    UUID        PK
  organization_id       UUID        NOT NULL FK → organizations
  activity_record_id    UUID        NOT NULL FK → activity_records
  event_type            VARCHAR(30) NOT NULL
                        -- 'ingested','auto_flagged','manually_flagged','unflagged',
                        --  'analyst_reviewed','approved','rejected','locked','unlocked',
                        --  'field_edited','calculation_rerun'
  from_status           VARCHAR(20)             -- review_status before this event
  to_status             VARCHAR(20)             -- review_status after this event
  actor_id              VARCHAR(200) NOT NULL   -- user ID or 'system'
  actor_display_name    VARCHAR(200)
  occurred_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
  note                  TEXT                    -- required for 'manually_flagged', 'rejected', 'unlocked'
  field_changes         JSONB                   -- for 'field_edited': [{field, old_value, new_value}]
                        -- WHY field_changes on the event rather than only original_values on
                        --    activity_records: a record may be edited multiple times before
                        --    approval. review_events captures each edit as a discrete event
                        --    with before/after values. activity_records.original_values captures
                        --    only the pre-first-edit snapshot.

  WHY (table): The current status columns on activity_records answer "what is the state now?"
               review_events answers "how did it get there, and who touched it?"
               This distinction matters for SOX-adjacent compliance scenarios where you need
               to show that no approved record was modified without a documented approval chain.
```

---

## 12. Entity Relationship Summary

```
organizations ──────────────────────────────────────────────────────────────────────────────────┐
      │                                                                                         │
      ├── data_sources ──────────────────────────────────────────────────────────────────┐      │
      │        │                                                                         │      │
      │        ├── ingestion_runs ─────────────────────────────────────────────┐        │      │
      │        │                                                                │        │      │
      │        │   ┌── sap_goods_movement_staging ──┐                          │        │      │
      │        │   ├── utility_interval_staging ──┐  │                         │        │      │
      │        │   └── travel_expense_staging ──┐ │  │                         │        │      │
      │        │                               │ │  │ (staging_id)             │        │      │
      ├── facilities ◄── plant_location_mappings │ │  │                         │        │      │
      │        │    ◄── meter_location_mappings  │ │  │                         │        │      │
      │        │                                 ▼ ▼  ▼                         │        │      │
      │        │                          activity_records ◄──────────────────┘        │      │
      │        │                                 │                                      │      │
      │        │           ┌─────────────────────┘                                      │      │
      │        │           │                                                             │      │
      │        │    emission_calculations ──────► emission_factors                       │      │
      │        │                          ──────► emission_factor_datasets               │      │
      │        │                                                                         │      │
      │        └─── expense_type_category_mappings ◄────────────────────────────────────┘      │
      │        └─── uom_normalization_rules                                                     │
      │                                                                                         │
      └── review_events (links to activity_records) ───────────────────────────────────────────┘
```

---

## 13. Tradeoff Register

Every significant choice, explicitly stated, so a reviewer knows what was decided and what was left open.

| Decision | Choice Made | Alternative Rejected | Reason for Choice | What it costs |
|---|---|---|---|---|
| Staging architecture | Separate typed tables per source | JSON blob / hybrid | Source-specific validation (bwart whitelist, UoM constraints) is cleanest in typed columns | Three staging tables to maintain; UNION ALL in normalization |
| Raw vs normalized values | Same row, raw_* and normalized_* columns | Separate tables | Single-row audit — regulator sees raw and normalized without join | Row is wider; re-normalization updates the same row (mitigated by review lifecycle resetting to flagged) |
| Emission calculation placement | Separate table from activity_records | Columns on activity_records | Factors change annually; calculations must be re-runnable without touching approved records | Extra join on every report query |
| Emission factor versioning | Named dataset table + factor rows | SCD Type 2 single table | Practitioners reference "DEFRA 2024" as a named artifact; dataset table matches that mental model | Any query for "factors at a point in time" goes through dataset.effective_from rather than per-row valid_from |
| Review lifecycle history | review_events table + status columns on activity_records | Status columns only / event sourcing only | Status columns for fast filtering; events for complete history. Event sourcing is overkill for MVP. | Two places to keep in sync; mitigated by the fact that status transitions always write both |
| Multi-tenancy | Row-level organization_id + RLS | Schema-per-tenant | Standard B2B SaaS pattern; cross-tenant queries possible for platform analytics | Application bugs that bypass RLS could leak data; mitigated by database-enforced RLS policies |
| Reversal tracking | is_reversal flag + reverses_activity_id self-FK on activity_records | Delete original and reversal | SAP reversals (BWART 102/202/262) must not erase history; both legs preserved | Aggregations must filter `WHERE is_reversal = FALSE OR (include reversals for net calculation)` — query complexity |
| Billing period proration | proration_factor on emission_calculations | Separate proration events table | Single multiplier is sufficient for uniform-daily assumption; interval data bypasses proration | Uniform-daily proration is an approximation; interval-data-derived proration is more accurate but requires a separate calculation path |
| Plant code mapping | plant_location_mappings with valid_from/valid_to | Static mapping without dates | Customers add and rename plants; a plant that moves countries changes its Scope 2 factor | Adds query complexity; the join must filter on date range, not just organization+werks |
| Expense type code mapping | Per-tenant table (expense_type_category_mappings) | Hardcoded mapping in application code | Research confirmed codes are tenant-configurable; 'AIRFR' is a common default, not a standard | Requires onboarding step for each new Concur tenant to populate the mapping |

---

*End of MODEL.md*
