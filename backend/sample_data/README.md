# Sample data — what each "messy" row demonstrates

Three sample files, one per source, each exercising the error classes the
ingestion pipeline is designed to handle. **Every row that looks wrong is
wrong on purpose** — these are realistic failures, not arbitrary bugs.

---

## `sap_idoc_sample.csv` — SAP goods movements (German CSV export)

Simulates a CSV export of MBGMCR03 IDoc data (research-notes §1.4–1.6).
German column headers because most SAP systems ship with German
ABAP-dictionary labels even when the business operates in English.

| Line | What's "wrong" | Why it appears in real data |
|---|---|---|
| 1 | Unit = `L` | Standard SAP UoM. Baseline correct case. |
| 2 | Unit = `LT` | SAP T006 contains BOTH `L` and `LT` as litre codes (research-notes §1.9). Different SAP releases default to different codes. |
| 3 | BWART 201, no PO | Goods issue to cost centre — Scope 1 direct consumption. No PO because fuel was already in stock. |
| 4 | Plant `DE02` | Deliberately unmapped — demonstrates `needs_plant_mapping`. |
| 5 | Unit `LTR` | Customer-added custom UoM (not in standard T006). Lands in `needs_uom_mapping`. |
| 6 | BWART 102 (reversal) | Cancellation of an earlier receipt. Auto-flagged — must be reviewed so the original isn't double-counted. |
| 7 | Unit `GAL`, US plant | US gallons (3.7854 L). Requires the `GAL → L` UoM rule. |
| 8 | BWART 501 (no PO) | Fuel spot-buy at remote site. Common gap per research-notes §1.14. |
| 9 | Date `2024-02-01` | Wrong format (IDoc spec is `YYYYMMDD` with no separators). Rejected for period assignment. |
| 10 | Plant `GB10`, GBP | UK plant — different grid, different emission factor. Multi-country handling. |
| 11 | BWART 301 (stock transfer) | Plant-to-plant transfer — NOT new consumption. Skipped as not-ESG-relevant. |
| 12 | Quantity = `abc` | Data entry error or upstream encoding bug. Rejected with explicit reason. |
| 13 | Empty unit | Custom ABAP export forgot the unit column. Rejected. |
| 14 | BWART `999` | Unknown movement type. Doesn't crash — staged with a note, no activity_record. |
| 15 | Fully blank row | Common end-of-file artifact. Silently skipped. |

---

## `utility_green_button_sample.csv` — Green Button DMD

Mimics PG&E's Green Button "Download My Data" CSV (research-notes §2.5).
Includes the 5-line metadata preamble that real PG&E exports prefix to
the file — the parser extracts `Service Account` (line 2) as the meter ID.

| Line(s) | What's "wrong" | Why it appears in real data |
|---|---|---|
| Preamble | `Service Account: 8800001234` | The meter/UsagePoint ID lives ONLY in the preamble, not in any data row. Parsers that skip the preamble lose the meter attribution. |
| 1–4 (Feb 1 night) | Low usage (~12 kWh) | Baseline overnight load — HVAC + idle equipment. |
| 5–6 (Feb 1 noon) | High usage (~42 kWh) | Peak production load. Demonstrates that interval data captures intraday variation that a monthly bill flattens. |
| Feb 2 noon | `Notes: Estimated` | Meter reader couldn't access the meter. Auto-flagged per research-notes §2.8 — an actual read later will produce a catch-up adjustment that distorts the time series. |
| Feb 3 23:45 → 00:00 | Interval crosses midnight | End time before start time. The parser detects this and treats `end` as next-day midnight. |
| Feb 4 02:00 | Units = `Wh` (not `kWh`) | Some utilities (especially small munis or third-party exporters) emit Wh. Requires `Wh → kWh × 0.001` UoM rule. |
| Feb 4 02:00 | Cost = `xyz` | Non-numeric cost. Parser warns; usage value is still good, so the row stages with a warning rather than being rejected. |
| Feb 4 03:00 | Usage = `abc` | Non-numeric usage. Rejected — without a valid measurement there's no activity to record. |
| Feb 5 (gas row) | `Type: Natural gas usage` | Dual-fuel accounts include both electricity and gas in one zip. Filtered out — gas would go to a different staging table eventually. |

---

## `travel_concur_sample.json` — Concur expense + itinerary

Combined sample of Concur expense entries and itinerary segments. Real
Concur exposes these via two separate APIs (Expense v3 and Itinerary v1
per research-notes §3.6); the prototype bundles them into one upload.

| Entry | What's "wrong" | Why it appears in real data |
|---|---|---|
| ENT001 + BK-12345 air segments | Round-trip NYC↔LHR with two air segments | Standard happy path. Itinerary has JFK/LHR codes; normalizer computes great-circle × 1.08 uplift per DEFRA. |
| ENT001 | `JourneyDistance: null` | Confirmed per research-notes §3.7: Concur expense entries NEVER carry flight distance. Must derive from IATA codes in itinerary. |
| ENT001 | `ClassOfService: C` (business) | Class drives the emission factor multiplier (~2.9× economy per DEFRA). Stored on activity_record for the future emission calculation. |
| ENT002 + hotel_segment | 5-night Hilton stay | Hotel emission = nights × per-night factor. Nights derived from CheckIn/CheckOut, not from expense Comment (which might say "5 nights" or might be blank). |
| ENT003 + car_segment | Car rental, ACRISS code `ICAR` | ACRISS encodes class/fuel but not engine size. Falls back to spend-based — no mileage in Concur car rentals (research-notes §3.9). |
| ENT004 (MILEG) | `JourneyDistance: 65`, `UnitOfMeasure: mile` | Personal mileage is the ONE expense type where distance is always present. Converted mile → km × 1.609344 (no DB lookup — fixed conversion). |
| ENT005 (Lufthansa NYC→MUC) | `LinkedItineraryID: ""` | Booked directly on airline website — no GDS data, no itinerary. Lands in `no_itinerary_linkage`, auto-flagged because >$1000 and a flight (where distance-based accuracy matters). Falls back to spend-based. |
| ENT006 (TAXCB) | Standard taxi expense | Mapped via per-tenant ExpenseTypeCategoryMapping → spend-based. |
| ENT007 (DINNER) | Unknown expense type code | Not mapped. Lands in `needs_expense_type_mapping`. Demonstrates that the platform doesn't crash on unknown codes — an analyst configures the mapping in admin. |
| ENT008 | `IsPersonal: true` | Personal trip charged to corporate card. Skipped (research-notes confirms personal flag is the right exclusion signal). |
| ENT009 + BK-99999 air | `EndCityCode: ZZZ` | Invalid IATA code. Distance computation fails gracefully; the row falls back to spend_proxy with the specific reason logged (research-notes §3.7 — production needs the full OurAirports dataset, ~70k rows). |
