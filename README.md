# ESG Ingestion Platform — Prototype

Two long-form docs precede the code:
- `research-notes.md` — primary-source research on SAP, utility, and travel data.
- `MODEL.md` — the data model + design decisions, with every choice explained.

This prototype implements all three sources end-to-end —
**ingestion → normalization → flagging → review dashboard** — for:

- SAP fuel & procurement (MBGMCR03 IDoc CSV export)
- Utility electricity (Green Button DMD CSV)
- Corporate travel (Concur expense + itinerary JSON)

## Run it

Prerequisites: Python 3.11+, Node 18+, Docker.

```bash
# 1. Start Postgres
cd backend
docker compose up -d

# 2. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # create local config — never commit .env
python manage.py makemigrations core ingestion activity
python manage.py migrate
python manage.py createsuperuser   # for the /admin/ UI
python manage.py seed_sap_demo      # SAP goods movements
python manage.py seed_utility_demo  # Green Button electricity
python manage.py seed_travel_demo   # Concur travel
python manage.py runserver

# 3. Frontend (new terminal)
cd ../frontend
npm install
npm run dev
```

- Dashboard: http://localhost:5173
- Admin (for editing reference data): http://localhost:8000/admin/
- API root: http://localhost:8000/api/

## What you'll see after seeding

Each sample file deliberately exercises every error class its source can throw.
After running all three seed commands, the dashboard's "Needs review" panel
will hold flagged activity records (reversal IDocs, estimated meter reads,
spend-fallback flights) and the "Staging issues" panel will hold per-source
rows that couldn't normalize — each with a specific reason and the exact admin
fix.

See `backend/sample_data/README.md` for a row-by-row explanation of why each
"messy" element is realistic.

## Architecture summary

1. **Three Django apps** map to MODEL.md's three layers: `core` (reference data + per-source mappings), `ingestion` (staging tables + parsers + normalizers), `activity` (normalized records + review events).
2. **One staging table per source** (`sap_goods_movement_staging`, `utility_interval_staging`, `travel_expense_staging`), each with its source's native field names typed at the column level. All three feed into the single `activity_records` table.
3. **Three thin normalizers** (`normalizers/sap.py`, `utility.py`, `travel.py`) — same per-row `@transaction.atomic` pattern, same `update_or_create` into `ActivityRecord`, same `auto_flag()` + `record_ingestion_event()` services — but each has its own classification rules, lookup tables, and auto-flag triggers (see the docstring at the top of each).
4. **Single audit chokepoint** (`activity/services.transition`) is the only path that mutates `review_status`; it writes the `ReviewEvent` row in the same DB transaction. Used identically by all three normalizers and by the API flag/approve/lock endpoints.
5. **Per-row error handling**: each source has its own staging-status vocabulary (`needs_plant_mapping`, `needs_meter_mapping`, `needs_expense_type_mapping`, `needs_uom_mapping`, `no_itinerary_linkage`, `rejected`) and each surfaces in the dashboard with a specific, source-appropriate reason — never a generic 500.

## Where I drew the abstraction line

**Genuinely shared and reused:**
- `ActivityRecord` schema + `ReviewEvent` table
- `services.transition` / `services.auto_flag` / `services.record_ingestion_event`
- `UomNormalizationRule` (scoped by `source_system`)
- The per-row `@transaction.atomic` loop pattern

**Intentionally kept source-specific:**
- Staging tables (different shapes, different unique constraints)
- Date/timestamp parsing (SAP `YYYYMMDD` vs utility local-with-TZ vs travel ISO 8601)
- Activity classification (SAP via BWART table, utility hardcoded Scope 2, travel via per-tenant mapping)
- Auto-flag triggers (SAP flags reversals, utility flags estimated reads, travel flags no-itinerary spend fallback)
- The three `NormalizationResult` dataclasses (each tracks the source-relevant counters)

**Why no `BaseNormalizer` class:** the shared part is ~5 lines of atomic loop; the divergent part is the entire normalize_one body. A class hierarchy would mostly be override-points — the giveaway that the abstraction isn't paying for itself.
