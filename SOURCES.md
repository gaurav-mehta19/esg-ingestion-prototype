# Sources

What I learned about each of the three data sources, what the prototype actually handles, and what would break the first day this hit a real customer.

This is the application-level summary. The full primary-source research with citations is in `research-notes.md`; the data-model decisions that came out of it are in `MODEL.md`; what I built (and didn't build) is in `DECISIONS.md`. Read those if you want the underlying detail. Read this to get the practitioner-level picture of each source.

---

## 1. SAP fuel & procurement

### The real format
The system of record is SAP MM (Materials Management). ESG-relevant data — fuel deliveries, raw material receipts, refrigerant consumption — lives in two tables: `MKPF` (material document header) and `MSEG` (line items). The originating purchase order data is in `EKKO` / `EKPO`.

There are four ways to get this data out, in increasing order of modernity:

| Mechanism | What it actually is |
|---|---|
| **IDoc (MBGMCR03)** | Fixed-width text records — a 524-byte EDIDC control record followed by 1,063-byte EDID4 data records, each with a 1,000-byte positional SDATA payload. Per segment definition in SAP's WE31 transaction. |
| **Flat-file (LSMW / SE16N export)** | CSV/TSV with no schema enforcement. Column order varies by export configuration. |
| **OData (`API_MATERIAL_DOCUMENT_SRV`)** | REST + JSON, S/4HANA only. The right modern answer. |
| **BAPI (RFC)** | Direct ABAP function call. Requires RFC access to the SAP system. |

I chose IDoc as the "primary" mechanism in MODEL.md because it's what most current ECC and early-S/4HANA on-premise installations actually ship — research suggests the majority of customers in the heavy-emitting industries (manufacturing, chemicals, energy) are still here. OData is better long-term but you can't make a customer migrate to S/4HANA so you can integrate.

In practice — and this is important — most SAP teams hand ESG vendors a **CSV that was exported from the IDoc** rather than the IDoc binary itself. That CSV is what the prototype parses.

### What we learned that drove the design
1. **German everywhere.** SAP is German software. Field labels in ABAP Dictionary, in SE16N column headers, and in many CSV exports use the German term: `Buchungsdatum`, `Belegdatum`, `Werk`, `Menge`, `Mengeneinheit`. The parser handles both German and English aliases.
2. **Plant codes are opaque keys.** `WERKS` is a 4-character string. `DE01` at Customer A is unrelated to `DE01` at Customer B. The SAP system has a lookup table (`T001W`) that maps `WERKS` → plant name + country, but that table is *not* included in IDoc exports. The ESG platform must maintain its own mapping.
3. **Unit codes are messy and customer-specific.** SAP's `T006` (Units of Measurement) ships with both `L` and `LT` as litre codes — same meaning, different keys. Customers can add their own (`LTR`, `LITRE`, custom locale-specific codes). A parser that hardcodes one of them silently miscounts the others. There is no universal SAP UoM dictionary.
4. **Dates are 8-character `YYYYMMDD` strings with no separators.** `BUDAT = '20231015'`. Custom ABAP exports sometimes get this wrong (ISO with dashes, or US M/D/YYYY).
5. **`BUDAT` ≠ `BLDAT`.** Posting date drives the period; document date is informational. Using `BLDAT` for ESG period bucketing puts a 28 Dec delivery into Q1 instead of Q4. Common source of "our totals don't match accounting" complaints.
6. **`BWART` (Bewegungsart, movement type) determines ESG relevance.** Only a small subset of the ~50+ movement types matter for emissions:

   | BWART | Meaning | ESG handling |
   |---|---|---|
   | 101 | Goods receipt for PO | Scope 3 purchased goods |
   | 102 | Reversal of 101 | Must subtract (or flag) |
   | 201 / 202 | Issue / reversal to cost center | Scope 1 direct consumption |
   | 261 / 262 | Issue / reversal to production order | Scope 1 raw material consumption |
   | 301 / 303 / 305 | Stock transfers between plants | Not consumption, skip |
   | 501 | Receipt without PO | Common for fuel spot-buys at remote sites |
   | 551 | Scrapping | Refrigerant disposal — ESG-relevant |

   The platform must filter and net-out reversals without losing audit history.

7. **`MBGMCR03` cannot be extended via standard customization.** Customer-added fields go through the `E1BPPAREX` extension segment, which requires per-customer ABAP work. Not every customer has built it.

### What the sample looks like and why
`backend/sample_data/sap_idoc_sample.csv` is 14 rows + a blank trailer line. German column headers (`Buchungsdatum`, `Werk`, `Mengeneinheit`, etc.). Each row was constructed to exercise a specific failure mode the platform should handle:

| Row | Trigger | Why this happens in real data |
|---|---|---|
| 1 | Unit = `L` | Baseline correct case |
| 2 | Unit = `LT` | SAP T006 has both codes for litres |
| 4 | Plant `DE02` (unmapped) | Customer adds a plant before ops updates the mapping |
| 5 | Unit = `LTR` | Customer-defined custom UoM |
| 6 | BWART 102 (reversal) | Cancelled delivery, accounting correction |
| 7 | Unit = `GAL`, US plant | Mixed-locale customer |
| 9 | Date `2024-02-01` (ISO instead of `YYYYMMDD`) | Custom ABAP export with wrong format |
| 11 | BWART 301 (stock transfer) | Plant-to-plant move, not new consumption |
| 12 | Quantity = `abc` | Data entry error or upstream encoding bug |
| 13 | Missing unit | Custom export omits the unit column on some rows |
| 14 | BWART `999` | Unknown movement type — platform must not crash |

Full row-by-row commentary in `backend/sample_data/README.md`.

### What would break in a real deployment
1. **Custom UoM the analyst hasn't mapped yet.** A new plant in Brazil starts shipping with unit `KGM`, the rule doesn't exist, all those rows land in `needs_uom_mapping`. The dashboard surfaces it; fix is a one-row insert in the admin.
2. **Plant master drift.** Customer adds plant `IT05`. Three weeks of `IT05` rows pile up in `needs_plant_mapping` before someone notices. Need a scheduled "what's stuck" alert.
3. **Reversal forgotten.** Customer cancels a fuel delivery in SAP (102 reverses an earlier 101). If the analyst approves the 101 without approving the 102, totals are overstated. The auto-flag on reversals catches this; the analyst still has to act on it.
4. **`MBGMCR03` version drift.** The prototype assumes the v03 segment layout. Some older SAP releases send `MBGMCR01` or `MBGMCR02` with slightly different fields. A parser dispatch on `idoc_type` is needed.
5. **`BWART 501` orphans.** Fuel spot-buys with no PO mean no `EBELN`/`EBELP`, so we can't enrich with vendor / commodity / contract details. These records are technically complete but qualitatively poorer.
6. **Multi-currency `DMBTR`.** Same export can contain EUR, USD, GBP rows. The platform stores currency alongside amount but does no FX conversion. Spend-based emission methods (rare for SAP, common for other sources) would mis-aggregate without it.
7. **The IDoc binary parser doesn't exist.** A customer that sends real IDoc files instead of CSVs is currently blocked. The architecture supports adding a binary parser; nobody has written it.

---

## 2. Utility electricity

### The real format
Three main channels, in decreasing order of usefulness:

| Channel | What it is | Why |
|---|---|---|
| **Utility API (Green Button "Connect My Data")** | OAuth2-authenticated REST. Best — automated pulls, machine-readable, standardized. | Requires per-utility provisioning and customer consent. |
| **Portal CSV download (Green Button "Download My Data")** | The analyst logs in, downloads a CSV, uploads it. | Widely supported, no API plumbing, manual labor. **What this prototype handles.** |
| **PDF bill** | Scanned monthly statement. | The last resort. OCR is unreliable. Every utility's PDF is different. |

The Green Button standard is based on **ESPI** (Energy Services Provider Interface), an NAESB XML schema from 2011. Green Button has two layers: the CSV/XML format (the data shape) and the CMD protocol (the OAuth2 pull mechanism).

### What we learned that drove the design
1. **The CSV has a metadata preamble.** PG&E and other Green Button exporters prefix the file with 4–5 lines like `Customer Name: …`, `Service Account: …`, `Rate Schedule: …`, then a blank line, then the column header row. The `Service Account` value is the **only** place the meter / UsagePoint identifier appears. A parser that skips the preamble loses the meter attribution entirely. (`backend/apps/ingestion/parsers/utility_csv.py` extracts this.)
2. **Dates are `M/D/YYYY`, not ISO.** Cost is a `$0.08` string, not a number. The Notes column carries the free-text "Estimated" flag. None of these are obvious from a glance at the file.
3. **The interval is 15 minutes by default but varies.** Some utilities send 30-min or 60-min. The platform stores `interval_length_minutes` per row.
4. **Timestamps are local time with no timezone annotation.** This is the single hardest-to-spot Green Button gotcha. The timezone must be configured per `DataSource`. At DST transitions the platform has to handle a 23-hour or 25-hour day correctly — interval aggregation to daily/monthly bypassing this produces wrong totals twice a year.
5. **ESPI XML uses `powerOfTenMultiplier`.** A value of `432` with `powerOfTenMultiplier=0` and `uom=72` (kWh) means 432 kWh. With `powerOfTenMultiplier=-3` it means 0.432 kWh. A parser that hardcodes "divide by 1000 to convert Wh to kWh" gets this wrong in both directions silently.
6. **`Notes: Estimated` is the most consequential flag.** Estimated reads happen when the meter reader can't access the meter (locked gate, bad weather, smart-meter outage). Utilities estimate based on history. The next actual read produces a catch-up adjustment that creates either a sudden spike or a sudden dip. Both distort the time series. ESG platforms need to flag every estimated reading explicitly.
7. **Billing periods don't align with calendar months.** A typical bill: `2023-12-19 → 2024-01-23` (35 days, spans December and January). If the customer reports on a January–December calendar year, that bill needs to be prorated. The platform must store both period endpoints, not just a single date.
8. **Dual-fuel accounts.** Electric + gas in one zip file. Two separate CSVs. Different `Type` value, different `Units` (kWh vs Therms).

### What the sample looks like and why
`backend/sample_data/utility_green_button_sample.csv` simulates a week of intervals from a single PG&E meter. Includes the metadata preamble. Includes deliberately broken rows:

| Row | Trigger | Why this happens in real data |
|---|---|---|
| Preamble | `Service Account: 8800001234` | The only place the meter ID lives in the file |
| Feb 1 noon | Spike to ~42 kWh | Daytime production load — proves interval data captures intraday variation |
| Feb 2 noon | `Notes: Estimated` (two rows) | Meter reader couldn't access; auto-flagged |
| Feb 3 23:45 → 00:00 | Interval crosses midnight | Parser must treat end as next-day |
| Feb 4 02:00 | Units = `Wh` not `kWh` | Some small munis export Wh; needs UoM rule |
| Feb 4 02:00 | Cost = `xyz` | Non-numeric — usage still good, row stages with a warning |
| Feb 4 03:00 | Usage = `abc` | Non-numeric — row rejected, usage is unrecoverable |
| Feb 5 (gas row) | `Type: Natural gas usage` | Dual-fuel; filtered out |

### What would break in a real deployment
1. **Forgetting the `Notes: Estimated` flag.** Treating estimated reads as actuals plus the subsequent correction creates a misleading time series. The first month looks too high, the second too low, both wrong.
2. **`powerOfTenMultiplier` mis-parsing.** Silent scale errors of 1,000× either way. Catastrophic if undetected; the only protection is to test against known totals.
3. **Naive billing-period proration.** Attributing the entire 35-day December–January bill to "January" because that's when it was issued. The platform stores the right fields; the calculator (not yet built) must apply them.
4. **Meter ID lost.** Skipping the preamble means every row gets attributed to "unknown meter." Multi-site customers become a single anonymous lump.
5. **DST.** Spring-forward = 23-hour day, fall-back = 25-hour day. Sum-the-intervals-as-if-each-day-is-24-hours produces wrong daily totals.
6. **CSV column variation across utilities.** Green Button standardizes the content but not the column names. PG&E's columns are not SCE's are not ConEd's. The current parser handles PG&E shape; adding SCE would mean another header-alias map. The ESPI XML format is more consistent — that's why MODEL.md prefers XML for any customer on more than one utility.
7. **No UK equivalent.** SMETS2 (UK smart meter standard) has no national CSV format. Octopus, British Gas, EDF, E.ON all have different portal exports. UK customers need per-utility parsers or a third-party data aggregator.
8. **Demand charges, power factor, TOU rates not captured.** Interval CSV is kWh-only. The bill-level `UsageSummary` ESPI element carries the rest. Customers who want demand-charge breakdowns need the XML path.

---

## 3. Corporate travel (Concur + Navan)

### The real format
SAP Concur is the dominant Travel & Expense (T&E) platform in enterprise. Navan (formerly TripActions) is the newer competitor. Both expose REST APIs.

Concur splits the data across two API surfaces:

| API | What's in it | Versions |
|---|---|---|
| **Expense API** (`/api/v3.0/expense/entries`) | The financial side — what was charged, expense type, amount, vendor name, location, currency. | v3 (active legacy), v4 (current) |
| **Itinerary API** (`/api/travel/trip/v1.1`) | The booked-trip side — flight segments, hotel reservations, car rentals, with IATA codes, dates, class of service. | v1 (active), v4 (preview) |

These are **separate endpoints** with **different authentication** and **no built-in cross-linking** beyond a shared `BookingID` you have to join on yourself.

Navan has a similar split but its API is gated — you have to be an existing customer with provisioning to get access. No public schema.

### What we learned that drove the design
1. **Expense entries do not carry flight distance.** This is the single most consequential thing about Concur for ESG. `JourneyDistance` on an air expense is always `null`. The expense entry tells you NYC → London cost $1245; it does not tell you it was 5,571 km. Confirmed in Concur community discussions: companies asking "how do I get distance for CO2 reporting?" are told to either add a custom field (free text, employees enter inaccurate values) or integrate with a third-party carbon tool that joins to the itinerary API.
2. **The itinerary API has IATA codes, not distances.** `StartCityCode: JFK`, `EndCityCode: LHR`. The platform must convert codes → lat/lon coordinates → great-circle distance, then apply the DEFRA 1.08 uplift for non-direct routing. This requires an airport database (OurAirports is the standard source, ~70k rows, free).
3. **Expense type codes are tenant-configurable.** `AIRFR` for airfare is a common Concur default, but a tenant can rename it `AIRFL`, split it into `DOMAIR`/`INTAIR`, or use entirely custom codes. The ESG platform **must not hardcode** these. It must fetch the Expense Group Configurations from each tenant and have an analyst confirm the mapping. (This is why `ExpenseTypeCategoryMapping` is its own per-tenant table.)
4. **Multi-leg trips are one expense.** A round-trip ticket = one charge = one expense entry. The itinerary has multiple segments. Distance computation has to sum segments, not use just the first.
5. **Class of service uses single-letter IATA codes.** `Y` = economy, `W` = premium economy, `C` = business, `F` = first. DEFRA business-class factor is roughly 2.9× economy; first is 4×. Premium economy is the awkward one — DEFRA sometimes treats it as economy, some methodologies break it out.
6. **Many flights have no itinerary linkage.** Employees book direct on the airline website, or through corporate AmEx, or through a TMC that doesn't push GDS data into Concur. The corp card charge shows up as an expense entry, but there's no `BookingID` to join on. These are common, not exceptional. Must be handled as a graceful fallback (spend-based), not as an error.
7. **DEFRA factors change every year.** The 2025 update reduced flight factors by 16–42% versus 2024 (because the methodology corrected for COVID-era low-load-factor data). A platform that applies 2025 factors retroactively to 2024 travel misreports emissions. Factors must be vintage-matched.
8. **Personal trips on corp cards.** `IsPersonal: True` exists on the expense entry. Whether to silently exclude or surface for verification is a policy question — both are defensible.
9. **Hotel emission factors are noisy.** Per-property is best (Hilton publishes some, most chains don't). Per-country (DEFRA) is the common fallback — order-of-magnitude correct, not precise. HCMI publishes a hotel-industry benchmark.

### What the sample looks like and why
`backend/sample_data/travel_concur_sample.json` is a JSON object with one report and 13 entries combining expense entries and itinerary segments. The JSON shape is a deliberate simplification — real Concur sends expense entries and itinerary segments via two separate API calls; the sample bundles them for upload simplicity.

| Entry | Trigger | Why this happens in real data |
|---|---|---|
| ENT001 + 2 air segments | NYC↔LHR round trip in business class | Standard happy path with itinerary linkage |
| ENT001 | `JourneyDistance: null` | Confirmed real Concur behavior — expense never has distance |
| ENT002 + hotel segment | 5-night Hilton London | Nights derived from CheckIn/CheckOut, not from Comment |
| ENT003 + car segment | Enterprise rental, ACRISS `ICAR` | No mileage in the data → spend-based fallback |
| ENT004 (MILEG) | Personal mileage with `JourneyDistance: 65 mile` | The one expense type where distance is always present |
| ENT005 | Lufthansa, no `LinkedItineraryID`, $1820 | Booked outside Concur — auto-flagged as high-value-no-itinerary |
| ENT006 (TAXCB) | Uber airport transfer | Spend-based by default |
| ENT007 (DINNER) | Unknown expense type code | Lands in `needs_expense_type_mapping` |
| ENT008 (`IsPersonal: true`) | Personal trip on corp card | Silently excluded |
| ENT009 + segment with `EndCityCode: ZZZ` | Invalid IATA code | Distance computation fails; fallback to spend with the specific reason logged |

### What would break in a real deployment
1. **Tenant configures custom expense type codes.** A new customer's Concur uses `AIRFL`/`DOMAIR`/`INTAIR` instead of `AIRFR`. Until ops adds the mappings, all their air expenses sit in `needs_expense_type_mapping`. Solvable, but a per-customer onboarding step.
2. **IATA database needs ~70k airports, not 17.** The prototype's hardcoded mini-database covers the sample. A real customer with a French regional airport in their itinerary gets a "code not found" warning and falls through to spend-based.
3. **No-itinerary spend-based is significantly less accurate.** Spend-based methods carry uncertainty ranges of ±50% or worse. A customer with 60% of their flights booked outside Concur is essentially getting a guess.
4. **Multi-leg flights single-counted.** Round-trip JFK↔LHR ticket gets the distance of one leg, not both. Under-counts by half. The fix is to sum all air segments matching a `BookingID`; not done in the prototype.
5. **DEFRA vintage mismatch.** If we apply 2025 factors to 2024 travel because that's what's loaded, customer's historical year-over-year comparison breaks. Factor vintage matching is in MODEL.md §10 and not yet implemented.
6. **Navan customers cannot be served.** No public API. The data shape is similar but every customer needs Navan-side provisioning before the integration can even start.
7. **Currency mixing.** A trip across three countries generates expenses in three currencies on one report. The platform stores currency but does not FX-normalize. Spend-based emission factors are usually in USD or GBP per spend-currency — mixing currencies without conversion misreports.
8. **Premium economy ambiguity.** Currently mapped to `flight` with whatever DEFRA factor applies. If a customer specifically wants premium-economy broken out (they're allowed to under DEFRA 2024 onwards if they have the data), we'd need a separate `class_of_service` lookup in the calculator.
9. **Hotel emission factor specificity.** Country-level factors are coarse. A solar-powered eco-hotel in Spain has the same factor as a glass tower in Madrid. Customers who care about this need property-level data we don't have a source for.
