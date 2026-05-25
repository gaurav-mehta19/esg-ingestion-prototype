# Decisions

Every ambiguity I resolved while building this prototype, what I picked, and why. Where I'd want to push back on a PM, those questions are listed at the end.

The goal here is to be honest about what's load-bearing and what's a shortcut. If you skim only one section, skim "Shortcuts I took" — that's where the gap between this prototype and a production system lives.

---

## Architecture

### PostgreSQL, not SQLite
MODEL.md depends on JSONB columns (`parse_warnings`, `connection_config`, `field_changes`), partial unique constraints, and eventually row-level security. SQLite supports none of those well. Postgres in a docker-compose container costs one `docker compose up -d` and removes a whole class of "this works in dev, fails in prod" problems. There's no scenario where the right answer for this prototype is SQLite.

### Three Django apps (`core`, `ingestion`, `activity`)
MODEL.md has three layers: reference data, staging, normalized activity. One app per layer keeps the dependency direction clean — `ingestion` and `activity` both import from `core`; `activity` imports from `ingestion`; nothing flows the other way. A single app would have worked and saved a few `__init__.py` files, but it would have made the layering invisible to anyone reading the tree.

### Typed per-source staging tables, not a JSON blob
This was the trickiest call in MODEL.md and the one I'd most expect to be challenged on. Three options on the table: one unified staging table with a JSONB payload; separate typed tables per source; a hybrid with a blob plus extracted scalars. I went with typed.

Reason: each source has source-specific validations the database can enforce — `werks CHAR(4)`, `bwart` whitelist, `interval_start NOT NULL` partial unique, `concur_entry_id` partial unique. Pushing these into application code on a JSON blob makes them invisible at query time and at admin time. The cost is three tables to maintain; we paid that cost three times and it was fine.

The fallback if you hit a fourth source with an unstable schema in the first 90 days is the hybrid form. The typed tables don't preclude switching.

### Raw and normalized values on the same row
`ActivityRecord` carries `raw_quantity` / `raw_unit` and `normalized_quantity` / `normalized_unit` side by side. Alternative was a separate `normalized_activity_records` table FK'd to a raw table. Same row wins for one reason: an auditor's first question is "what arrived and what did you convert it to?" — and they should get the answer without a join. Re-normalization updates `normalized_*` in place and flips `review_status` back to `flagged`, forcing a fresh approval cycle.

### Emission calculations live in a future table
Activity records don't carry computed CO2e. The `emission_calculations` table lives in MODEL.md §10 but I didn't build it for this prototype — scope was ingestion → normalization → flagging → review. The next milestone wires factor lookup and writes calc rows that link `activity_record_id` → `emission_factor_id` → tCO2e. The drawer surfaces this gap honestly: *"Emission factor: not yet calculated."*

### One audit chokepoint: `activity/services.transition()`
Only one function in the codebase mutates `ActivityRecord.review_status`. It writes the matching `ReviewEvent` row in the same `@transaction.atomic` block. No Django signals, no `save()` override. Rejected the signals approach because audit rows that get written by silent receivers are exactly the kind of thing auditors don't trust.

### Three independent normalizers, no base class
I checked twice whether `normalizers/sap.py`, `utility.py`, and `travel.py` should share a base class. The shared part — the per-row atomic loop — is 5 lines. Everything else (classification rules, lookup tables, auto-flag triggers, distance computation, result counters) is divergent. A base class would mostly host override stubs, which is the giveaway. I left them as independent files that call into the same shared services (`transition`, `auto_flag`, `record_ingestion_event`).

### Row-level multi-tenancy, RLS not implemented
Every table has `organization_id`. Every view filters by it. I did *not* configure Postgres row-level-security policies — the filter is purely in the application layer right now. RLS is the right production answer; the data model is shaped for it. Adding it is mechanical, but not necessary to demo the pipeline.

---

## SAP

### Subset handled
- IDoc type `MBGMCR03` only (goods movements). No invoices, no transfers as separate flows, no master-data IDocs.
- BWART codes: `101`, `201`, `261`, `501`, `551`, plus their reversals (`102`, `202`, `262`). Stock transfers (`301/303/305`) are recognized but skipped as not-ESG-relevant. Anything else gets staged with a note, no `ActivityRecord`.
- German CSV export format (what most SAP teams hand to vendors). NOT the binary IDoc wire format.

### Subset ignored
- True binary EDIDC/EDID4 fixed-width parsing. Different SAP releases have different SDATA byte offsets — that's per-customer parsing work, not generic. The architecture supports adding it as a second parser (`parsers/sap_idoc_binary.py`) that emits the same `ParsedRow` shape.
- OData (`API_MATERIAL_DOCUMENT_SRV`) — the better modern path for S/4HANA, but not what most current SAP MM installations ship by default.
- Custom IDoc extension segments (`E1BPPAREX`) — these carry customer-added fields and require customer-specific ABAP work. Out of scope.
- The `T001W` plant master IDoc — we maintain plant mappings manually in admin instead of ingesting plant master.
- `DMBTR` currency conversion — we store amount and currency, don't normalize FX.

### Date field choice: BUDAT
`BUDAT` (Buchungsdatum, posting date) is the canonical period anchor. `BLDAT` (Belegdatum, document date) is also stored but not used for bucketing. This matches MODEL.md and research-notes §1.10 — a delivery dated 28 Dec posted 5 Jan should land in Q1, not Q4.

### Reversal policy: flag, don't auto-subtract
A BWART 102 reversal lands as its own `ActivityRecord` with `is_reversal=True` and `reverses_activity` (eventually) pointing at the original. The normalizer auto-flags it. The downstream emission calculator will need to handle netting; for now the analyst must explicitly verify.

I'd ask the PM whether net or gross is the right reporting mode. Different customers may have different preferences.

---

## Utility

### Subset handled
- Green Button "Download My Data" CSV format only. PG&E-style with the 5-line metadata preamble.
- The `Service Account:` line from the preamble becomes the meter / UsagePoint identifier. This is the only place the meter ID appears in the CSV — losing it means you can't attribute consumption.
- Electricity rows only. Gas rows in dual-fuel files are skipped (gas would need its own staging table eventually).
- Local-time timestamps parsed with the timezone configured on the `DataSource`. No auto-detection.

### Subset ignored
- ESPI XML format. The staging table has the XML-specific columns (`espi_power_of_ten_mult`, `espi_uom_code`, `espi_reading_quality`) but no parser yet. Adding it is one more `parsers/utility_espi.py` that emits `ParsedInterval` records.
- Green Button "Connect My Data" (the OAuth-based pull). The staging table supports it; the actual OAuth flow is real plumbing work.
- UK SMETS2 / DCC. Research-notes §2.9 flagged this as having no national standard — every UK utility's portal export is different. Not solvable without per-utility parsers.
- Demand charges, time-of-use breakdowns, power factor charges. The bill-level `UsageSummary` ESPI element carries these but I haven't modeled them — interval data is enough for Scope 2 kWh-based accounting.

### Estimated-read policy: flag, keep
When `Notes` contains "Estimated", the row stages with `is_estimated=True` and the normalizer auto-flags the resulting `ActivityRecord`. The reading is kept (not excluded) so totals are complete. The analyst is told that an actual read will eventually produce a catch-up adjustment.

I'd ask the PM: do we want to (a) keep estimates, (b) keep estimates and reverse them when the actual lands, or (c) exclude estimates from final reports?

### Billing-period misalignment: deferred
The pipeline correctly captures `activity_period_start` / `activity_period_end` per interval but doesn't yet prorate consumption across calendar months. Proration logic belongs in the emission calculator (MODEL.md §10 has `proration_factor` and `proration_method` fields), which I haven't built. The data model is ready for it.

---

## Travel

### Subset handled
- Concur expense entries + Concur itinerary segments (air, hotel, car).
- Per-tenant `ExpenseTypeCategoryMapping` — codes like `AIRFR`, `LODNG`, `CARRT`, `MILEG`, `TAXCB`, `RAILF` are mapped to canonical activities in the seed command. An organization can edit these in admin.
- Air-segment IATA → great-circle distance with DEFRA 1.08 uplift. Hardcoded mini-database of ~17 airports.
- Hotel nights derived from itinerary check-in / check-out dates, NOT from the expense `Comment` field.
- Personal-mileage `JourneyDistance` mile → km via fixed `× 1.609344` (no DB lookup needed for a fixed pair).
- "No itinerary linkage" handled as a distinct, non-fatal status — the record still ships, with `distance_computation_method='spend_proxy'`, and the analyst is auto-flagged for high-value flights.

### Subset ignored
- Navan / TripActions. Research-notes §3.10 flagged that the API is behind account provisioning with no public schema. Sample data only.
- Amex GBT / Egencia / other TMCs.
- Concur Expense v4 (current). v3 is in long-term active support; v4 is what greenfield Concur tenants use. Either could be added as a parser variant.
- Class-of-service multiplier application. We store `class_of_service` (Y/W/C/F) on the staging row; the emission calculator (deferred) will use it.
- Aircraft-type-specific factors. Stored on staging, not yet used.
- Multi-leg trips. A round-trip ticket appears as one `ActivityRecord` with the distance of the first matched segment. This is a known under-count for round-trips — the staging notes say which segment was used so an analyst can spot it.
- Hotel emission factors. We have `room_nights` and country; we don't yet apply per-region kg-CO2e-per-night factors.
- Currency conversion. Multi-currency expenses stage with their original currency; nothing FX-normalizes yet.

### The "no itinerary" case
Per research-notes §3.7, Concur expense entries never carry flight distance. When the expense has a `LinkedItineraryID` we find the air segment by `BookingID` and derive distance. When it doesn't (booked outside Concur Travel), we fall back to spend-based and tag the method `spend_proxy`. The analyst sees a yellow "$" badge in the table.

---

## Review lifecycle

### State machine
`ingested → flagged → analyst_reviewed → approved → locked`. Locked is terminal. Allowed transitions in `services.ALLOWED_TRANSITIONS` — anything else raises `InvalidTransitionError`.

### Auto-flag rules
The normalizer auto-flags on:
- SAP: any `is_reversal=True` record (BWART 102/202/262)
- SAP: any record produced from a staging row with `parse_warnings`
- Utility: any `is_estimated=True` reading
- Utility: parser warnings
- Travel: high-value flights (>$1000) with `no_itinerary_linkage`
- Travel: parser warnings

I did not implement statistical outlier detection (3σ from site historical mean). MODEL.md mentions it; doing it requires a historical baseline that doesn't exist in seed data.

### Manual edits
MODEL.md says manual edits set `is_manually_edited=True`, snapshot the prior values in `original_values`, and re-flag the record. The columns exist; the editing UI does not. Edits today only happen via Django admin (where they bypass the re-flag rule). This is a real gap — see "Shortcuts" below.

### Unlock
MODEL.md specifies unlock requires a supervisor role. No auth was implemented, so there is no role concept and no unlock path. Locked is genuinely terminal in this prototype.

---

## Frontend

### Source-first navigation
Tabs are by source group (All / SAP / Utility / Travel), not by status. The hypothesis: an analyst typically owns one source at a time during a review cycle. Counter-hypothesis: an analyst looking at "all flagged records across sources" is a real workflow. The "All" tab covers that.

### Plain-English label dictionary
Everything in `src/lib/labels.js`. `scope_3` becomes "Scope 3 — Value chain", `needs_uom_mapping` becomes "Needs unit setup". One file, one place to fix if backend codes change.

### Issue cards, not table rows
Each failed staging row is a card with three lines: what came in, why it's stuck, what to do. The "what to do" is the part a non-engineer needs and a table row doesn't fit.

### Explicit confirmation
Approve and Lock both go through a modal. Lock additionally requires typing a reason — confirm button stays disabled until the textarea has content. This is the only place I added a friction layer; everything else (flag, mark reviewed) is one click after a single confirm.

### No bulk approve
Considered "Approve all clean rows in this source". Skipped because bulk-action in an audit workflow is exactly the kind of UI shortcut that creates the bad-data problem it's supposed to solve. If the PM wants this, we'd want it gated behind an explicit "auditor mode" with a stronger confirm.

---

## Shortcuts I took

These are the gaps between this prototype and something I'd hand to a real customer. Listing them so they're not implicit:

1. **No authentication.** The API is open. The "actor" for audit-log purposes comes from an `X-Analyst-Id` header. Anyone with the URL can approve and lock records.
2. **No RLS.** `organization_id` is filtered in application code, not enforced at the database. A bug in a query that forgets the org filter would leak cross-tenant data.
3. **No background jobs.** Ingest and normalize are synchronous API calls. They run in 200ms on the sample data; on a 100k-row SAP export they'd time out. Wrap with Celery or RQ in production.
4. **No file storage abstraction.** Uploaded files are read into memory and discarded. The `raw_file_ref` column stores the original filename, not a durable S3 path.
5. **IATA database is 17 airports hardcoded.** Production needs the full OurAirports CSV (~70k rows). My code calls out the airport that wasn't found, so the failure mode is clear.
6. **No emission factors.** Activity records have no computed CO2e yet. The schema is ready; the data isn't loaded.
7. **No tests.** I tested by running the seed commands and clicking through the dashboard. Real tests of the parsers, normalizers, and transition function are an obvious next investment.
8. **No CI.** Same.
9. **No pagination handling in the frontend.** It assumes the org has fewer than ~500 records total. The DRF endpoints paginate; the React code requests `limit=500` and ignores the next/previous links.
10. **`alert()` for error toasts.** Good enough for a demo, not for analyst use.
11. **Single tenant in seed.** Only one Organization (`acme`). Multi-tenant code paths exist but aren't exercised.
12. **Manual edit UI doesn't exist.** Edits today only happen via Django admin, which bypasses the audit rules. The `is_manually_edited` / `original_values` columns are present and tested via admin, but the analyst UI doesn't expose them.
13. **No unlock path.** Locked records are terminal in this prototype. MODEL.md describes a supervisor-role unlock; not implemented because no auth.
14. **No proration of billing-period consumption across calendar months.** Utility intervals carry the right period bounds; calculation is in the next milestone.
15. **No round-trip distance summation for flights.** A round-trip expense matches one outbound segment and uses its one-way distance.

---

## What I'd ask the PM

Things I genuinely don't know that affect the next milestone:

- **Approver identity.** Is the approver always the same person as the analyst, or do we need a separate role + two-person rule (segregation of duties for SOX-adjacent work)?
- **Reporting framework.** GHG Protocol corporate standard, CDP, CSRD (EU), CDP supply chain — which one is the customer's primary obligation? They differ in scope boundaries and which Scope 3 categories are required.
- **Reversal accounting.** When a BWART 102 cancels a 101, do we net them silently in the activity table, surface both legs and let the report do the arithmetic, or require the analyst to confirm the netting each time?
- **Estimated read policy.** Three options listed under Utility above. Customer-by-customer or platform-default?
- **Hotel emission factor granularity.** Per-property (best, hardest to source), per-chain (Hilton publishes some), per-country (DEFRA), per-region (HCMI)? Drives the data acquisition strategy.
- **Class of service.** Premium economy (`W`) — treat as economy or as its own factor? DEFRA doesn't always break it out; some customers want it broken out anyway.
- **Personal-trip-on-corp-card.** When `IsPersonal=True`, do we silently exclude or surface as "please verify"? Silent exclusion is safer but may hide expense-policy violations.
- **Multi-currency conversion timing.** Convert at transaction date, posting date, period-average, or use a fixed customer-specified date?
- **Unlock workflow.** Ever unlockable? Who can unlock? With what audit trail?
- **Plant master maintenance.** Does the customer maintain plant mappings themselves (good — they know which DE01 is which) or does ops at our company (worse — drift)?
- **Year-end close.** Is there a "lock everything for FY2024" button? Who clicks it?
- **Audit log retention.** How long must `review_events` rows live? Forever? 7 years (SOX)? Customer-configurable?
- **The OData vs IDoc decision per customer.** Do we commit to supporting both or pick one per customer at onboarding?
- **Source confidence levels.** Do we expose a "this number is high/medium/low confidence" indicator in customer reports, or hide it?
