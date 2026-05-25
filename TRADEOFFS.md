# Tradeoffs

Three things I deliberately did not build, and why each was the right thing to skip for a 4-day prototype. The fuller list of shortcuts is in DECISIONS.md; this document is about the three big ones that a reviewer should expect to challenge.

For each: what it is, why a real product needs it, why this prototype doesn't, and what changes when we do build it.

---

## 1. Emission factors and the calculation pipeline

**What's missing.** MODEL.md §9 and §10 specify three tables — `emission_factor_datasets`, `emission_factors`, and `emission_calculations` — plus the calculator that links an `ActivityRecord` to a factor row and writes a kg-CO2e value. None of that exists in code. The detail drawer in the dashboard says so explicitly: *"Emission factor: not yet calculated."*

**Why a real product needs it.** This is literally the output. Customers don't pay to ingest data; they pay to get a kg-CO2e number they can publish in their annual report. Without the calculator the system is a glorified data validation tool.

**Why it was right to skip.** Two reasons.

First, the calculator is downstream of everything else here. If activity records are wrong, the factor doesn't matter. Getting ingestion → normalization → flagging → review correct first is a precondition; building the calculator on top of an unstable base would have meant rewriting it as soon as the upstream changed. Given four days, ordering matters.

Second, loading factors is real research work that the prototype didn't need to validate. DEFRA publishes a new vintage every year; the 2025 update changed flight factors by 16–42% (research-notes §3.11). Factor "vintage matching" — using the factor that was current at the time of the activity, not the factor current at the time of reporting — is the kind of subtle requirement that takes a conversation with the customer to get right. Doing it wrong is worse than not doing it.

What I did do: every field the calculator will need is already on `ActivityRecord` — `normalized_quantity`, `normalized_unit`, `activity_type`, `ghg_scope`, `grid_region`, `computed_distance_km`, `distance_computation_method`. The calculator reads from there. The schema is ready.

**What changes when we build it.**
- New Django app `apps/emissions/` with the three tables.
- Factor data loaded from DEFRA / EPA / GHG Protocol PDFs (manual work, ~1 day per dataset).
- A `calculate_emissions(activity_record)` service that's called after `record_ingestion_event()` — same pattern as the existing pipeline.
- A `reporting_year` parameter on the calculator so the same activity can produce 2024-DEFRA and 2025-DEFRA calc rows side by side.
- The drawer's "Emission factor: not yet calculated" message replaced with the actual kg-CO2e value and a link to the factor row used.

Approximate effort: 4–5 days for the calculator skeleton + first factor dataset, then 1 day per additional dataset.

---

## 2. Authentication, authorization, and row-level security

**What's missing.** No login. The API is open. The audit-log "actor" comes from an `X-Analyst-Id` header that the React app hardcodes to `demo-analyst`. There are no roles, no permissions, no Postgres RLS policies. The data model has `organization_id` on every table — that part is correct — but the isolation between organizations is enforced only by the application layer's query filters.

**Why a real product needs it.** It's a B2B SaaS handling regulated data. Customer A absolutely cannot see Customer B's fuel volumes; an analyst at Customer A should not be able to approve a record at Customer B even by accident. The audit log is meaningless if "actor" is a string anyone can set in a header.

**Why it was right to skip.** Auth in a Django + DRF stack is 2–3 days of plumbing that doesn't validate any product hypothesis. JWT or session auth, password reset, invite flow, role definitions, permission decorators on every viewset, RLS policy migrations, tests for the negative cases ("user from org A cannot read row from org B") — none of it tells you whether the data model is correct or the analyst workflow is usable. Those are the questions the prototype is trying to answer.

The data shape is already auth-ready. Every table has `organization_id`. Every queryset filters by it. Switching from "anyone can hit the endpoint" to "authenticated user's org_id is used to filter" is a five-line change per view once the auth system exists. Switching from "application-layer filter" to "Postgres RLS policy" is a single migration that adds a policy per table.

What this does mean: **don't run this prototype on customer data.** It's a development-only artifact.

**What changes when we build it.**
- Add `djangorestframework-simplejwt` for token auth (or just use session auth — depends on the client architecture).
- A `User` model with FK to `Organization`. Maybe `OrganizationMembership` if a user can belong to more than one.
- Roles: at minimum `analyst`, `approver`, `admin`. Maybe a separate `auditor` (read-only).
- Replace `permission_classes = [AllowAny]` in `settings.py` with `IsAuthenticated` defaults.
- Auto-filter every viewset's queryset by `request.user.organization_id`. Either via a `TenantFilteredViewSet` base class or a DRF filter backend.
- Postgres RLS policies as the last line of defense — `CREATE POLICY` per table, `ALTER TABLE … ENABLE ROW LEVEL SECURITY`, and the connection sets a `SET app.current_org_id` GUC at request time.
- Replace `X-Analyst-Id` header with `request.user.id` in the audit-log writes.
- Negative-case tests: user from org A querying for org B's record returns 404 (not 403, because revealing existence is itself a leak).

Approximate effort: 3 days for the auth + RLS layer, including tests.

---

## 3. Background job processing and large-file handling

**What's missing.** Ingest and normalize are synchronous API endpoints. `POST /api/ingest/sap/` reads the entire uploaded CSV into memory, parses it, writes rows to staging, and returns when finished. The uploaded file is discarded after the request — no S3, no durable raw-file storage, no retry. There's no task queue, no Redis, no Celery worker.

**Why a real product needs it.** Real SAP exports are 10k–500k rows. A real Concur month is hundreds of expense entries. Parsing 500k rows synchronously means the HTTP request takes minutes, hits proxy timeouts, and blocks the gunicorn worker for the whole time. Multiple customers ingesting at once would saturate the web tier. Customers also expect to upload via SFTP drop or scheduled API pull, not via a "click here to upload" button — both of which need a job runner.

**Why it was right to skip.** The interface is the same regardless of execution backend. `ingest_sap_csv(organization_id, data_source, csv_text)` is a pure function that returns an `IntakeResult`. Wrapping it in a Celery task or running it in a `subprocess` doesn't change its shape. The synchronous-API version runs in 200ms on the 15-row sample, which is fine for the prototype.

What this means: the prototype handles small files well, breaks on big ones, and has the right factoring to fix later.

Background plumbing also brings operational complexity that's premature here — Redis to run, Celery beat for scheduled work, dead-letter queue handling, retry policies, idempotency keys per task, observability (Flower, Prometheus). All of that is right for production and exactly wrong for "can the data model handle three real-world sources?".

**What changes when we build it.**
- Add `celery` + a Redis broker to the stack. `docker-compose.yml` gets two more services.
- Move the body of `SapIngestView.post()` (and the utility / travel equivalents) into a Celery task `ingest_sap_csv_task.delay(args)`. The endpoint just enqueues and returns the run ID immediately.
- A status endpoint for the React app to poll: `GET /api/ingestion-runs/{id}/`. The dashboard shows "Running…" and updates when the run finishes.
- File storage: uploads go to S3 (or compatible). `raw_file_ref` stores the durable URI. This lets re-ingestion be a re-parse of the same source file, not a "please upload again."
- A scheduler that runs the normalizer hourly per data source (currently the seed command runs it explicitly).
- SFTP/email ingestion adapters for customers who can't use the upload UI.
- Idempotency: today the unique constraint on `(organization, idoc_number, item_line)` is what protects us from duplicate inserts. With Celery retries, that becomes load-bearing. Worth a test.

Approximate effort: 2–3 days for Celery + S3 + the status-polling UI work.

---

## What I did not skip

For contrast, things I built into the prototype that a stricter reading of "MVP" might cut:

- **Per-source typed staging tables** (vs JSON blob). Defensible because the database constraints catch real data quality problems early; cutting them would have pushed validation into application code and made dashboard "this row is broken" detection harder.
- **A separate ReviewEvent table** (vs status columns only). Audit trail is the product's value proposition; cutting it would have produced a demo that looked nice but failed the first compliance question.
- **Explicit confirm dialogs** for approve/lock. Adds friction the analyst will thank you for the first time they almost-clicked.
- **Plain-English label dictionary**. Took 30 minutes; without it, the dashboard shows enum codes and the demo fails the "non-engineer can use it" test that motivated the whole frontend rebuild.
- **All three sources, not just SAP**. The exercise was specifically about whether the unified `ActivityRecord` model survives contact with all three. It does; cutting two of them would have hidden the answer.
