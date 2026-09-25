# Setu reconciliation — FastAPI

A lightweight payment reconciliation service built with **FastAPI, Pydantic, Uvicorn and SQLite**. Python 3.12 and 3.13 are supported and tested. The ASGI entry point is `main:app`.

This is the FastAPI rewrite, not the earlier Flask application. All five assignment APIs are implemented, with SQL filtering/aggregation, idempotent events, immutable history and explicit settlement discrepancies.

## 1. Run locally

Unzip the project and open a terminal in `setu-fastapi`.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m tools.seed --file sample_events.json --expect-conflicts 0
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m tools.seed --file sample_events.json --expect-conflicts 0
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Python 3.13 can be substituted. If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` instead of `python` in the remaining commands; changing the machine's execution policy is unnecessary.

Open these local addresses:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- Health: `http://127.0.0.1:8000/health`
- Transactions: `http://127.0.0.1:8000/transactions`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`

A fresh seed reports **10,165 created (201), 190 duplicates (200)**. Re-running the seed is safe: all **10,355** deliveries become duplicates. The default database is created at `data/payments.db`. No separate database server is required. `--reload` is for local development only.

### Tests

```bash
python -m unittest discover -s tests -v
python -m tools.verify_sample
```

The second command creates a temporary database and verifies the entire real sample, SQL totals, duplicate replay and reconciliation endpoints without modifying your configured database. Captured results are in `verification/`.

## 2. Architecture

```text
HTTP -> FastAPI route / Pydantic validation -> domain ingestion + parameterized SQL
                                          -> SQLite WAL, atomic transaction
GET  -> validated filters -> SQL WHERE / GROUP BY / ORDER BY / LIMIT -> typed response
```

| Component | Responsibility |
|---|---|
| `main.py` | Uvicorn ASGI entry point |
| `payments/api.py` | Routes, API-key security, errors, request IDs, bounded exact JSON parsing |
| `payments/schemas.py` | Pydantic request/query/response contracts and OpenAPI metadata |
| `payments/config.py` | Validated environment configuration |
| `payments/domain.py` | Canonicalization, idempotency and deterministic state projection |
| `payments/queries.py` | Parameterized filtering, pagination, summaries and discrepancy SQL |
| `payments/db.py`, `schema.sql` | Connections, transaction boundaries, schema and indexes |
| `tools/seed.py` | Direct-domain or HTTP sample ingestion |
| `tests/test_api.py` | 30 integration tests using FastAPI TestClient |
| `postman/` | Importable Postman collection |

FastAPI lifespan initializes the versioned schema. Endpoints doing SQLite work are synchronous `def` routes, so blocking database calls run in a worker thread, not the async event loop. Each route opens, uses and closes its SQLite connection within that same synchronous call. WAL supports readers while a writer commits. `BEGIN IMMEDIATE` serializes writers and makes the event ledger, conflict audit and transaction projection atomic. Read transactions provide a consistent view for rows plus pagination totals.

### SQL model

- **merchants**: merchant ID and first-seen display name.
- **transactions**: one row per transaction; immutable merchant, amount and currency; separate payment/settlement status; lifecycle counts and earliest/latest event timestamps.
- **payment_events**: one row per unique accepted `event_id`; normalized payload, hash, occurrence/receipt timestamps and transaction foreign key.
- **ingestion_conflicts**: rejected payload conflicts, deduplicated by event ID, normalized payload hash and reason.

Money is stored as integer minor units and returned as decimal strings. The database has foreign keys, CHECK constraints and event-ID uniqueness. Composite indexes cover merchant/status/created-time filters, with transaction ID as a stable tie-breaker. Event history has a transaction/time index; the overdue path has a partial index. See `schema.sql` for exact definitions. `EXPLAIN QUERY PLAN` evidence is included in `verification/sample-results.json`.

Filtering, sorting, grouped sums, discrepancy predicates, counts and pagination execute **in SQL**, not by loading all transactions into Python. The projection is updated incrementally, avoiding replaying every historical event on each ingestion. No join to the event ledger is needed to sum transaction amounts.

## 3. Real sample data

The bundled `sample_events.json` is the **unchanged uploaded sample**, verified by SHA-256 in `sample_manifest.json`.

| Metric | Value |
|---|---:|
| Input event deliveries | 10,355 |
| Unique accepted event IDs | 10,165 |
| Exact duplicate deliveries | 190 |
| Transactions | 3,800 |
| Merchants | 5 |
| Settled transactions | 2,565 |
| Processed but unsettled transactions | 380 |
| Failed transactions | 665 |
| Initiated transactions | 190 |

At `as_of=2026-05-01T00:00:00Z` and `grace_hours=24`, **570 distinct transactions are discrepant**. Reason counts overlap: the same failed-and-settled transaction also lacks a processed event. This sample has no event-ID payload conflicts; tests and the collection supply those adversarial cases. The optional `tools.generate_data` module is retained for synthetic experiments; use its `--help` and write to a different filename to preserve the real sample.

### Load through the running API instead

```bash
python -m tools.seed --file sample_events.json --url http://127.0.0.1:8000 --expect-conflicts 0
```

Direct seeding is faster and uses the same domain validation/ingestion logic in batches of 500; HTTP mode also exercises the transport boundary. HTTP mode uses `API_KEY` from the environment and retries transient busy/network errors with the same event IDs. A seed command exits nonzero on unexpected conflicts or unsuccessful statuses. If a later batch fails, earlier committed batches remain; an idempotent retry can continue.

## 4. API documentation

Interactive contracts, field schemas and examples are available at `/docs`; a captured schema is also included as `openapi.json`. Set `X-API-Key` when configured. In Swagger, use **Authorize** to supply the key. `/`, `/health`, `/docs`, `/redoc` and `/openapi.json` are public; business endpoints are authenticated when a key is configured.

### POST /events

All eight fields are required. Unknown fields are rejected.

```json
{
  "event_id": "demo-event-1",
  "event_type": "payment_initiated",
  "transaction_id": "demo-tx-1",
  "merchant_id": "merchant_2",
  "merchant_name": "FreshBasket",
  "amount": "15248.29",
  "currency": "INR",
  "timestamp": "2026-01-08T12:11:58.085567+00:00"
}
```

Event types: `payment_initiated`, `payment_processed`, `payment_failed`, `settled`. IDs are 1–128 ASCII alphanumeric/dot/colon/underscore/hyphen characters and start with a letter or digit. Amount is 0.01–9,999,999,999.99 with at most two fractional digits. Decimal strings are recommended; JSON numbers are parsed exactly before validation. Supported currencies are INR, USD and EUR; lowercase inputs normalize to uppercase. Timestamps require a timezone, with years 1970–2100, and normalize to UTC. Merchant names must be printable, nonempty and at most 200 characters. JSON bodies are limited to 16 KiB.

- **201**: new accepted event; receipt contains `event_id`, `transaction_id`, `duplicate:false` and a Location header.
- **200**: same event ID and same normalized payload; `duplicate:true`; no extra event row or projection update.
- **409**: same ID with different normalized payload, or a new event contradicting the transaction's immutable merchant/amount/currency. State is unchanged and a deduplicated conflict audit is committed.

Canonicalization treats equivalent decimal amounts, currency case and timezone representations consistently. IDs are globally unique across sources: upstream systems should namespace IDs if they cannot guarantee global uniqueness. Exact retries are not separate event-history records; rejected conflicting payloads have a separate audit trail. This choice keeps the history canonical, not a complete log of delivery attempts.

### GET /transactions

| Parameter | Meaning / default |
|---|---|
| `merchant_id` | Exact merchant filter |
| `status` | `unknown`, `initiated`, `processed`, `failed`, `conflicted`, `settled` |
| `currency` | INR, USD or EUR |
| `from`, `to` | Inclusive lower / exclusive upper bound on earliest event timestamp (`created_at`), timezone-aware |
| `page`, `page_size` | 1 and 50; maximum page size 200 |
| `sort_by` | `created_at` (default), `last_event_at`, `amount`, `transaction_id` |
| `sort_order` | `desc` (default) or `asc` |

Example: `/transactions?merchant_id=merchant_2&status=settled&page=1&page_size=20&sort_by=created_at&sort_order=desc`.

Returns `data` and `pagination:{page,page_size,total}`. Amount sorting is on stored minor units, not exchange-rate-converted value; filter currency when comparing amounts. Transaction ID breaks ties deterministically. Unknown or repeated query parameters are rejected rather than silently ignored.

### GET /transactions/{transaction_id}

Returns transaction money/merchant details, current `status`, independent `payment_status` and `settlement_status`, event counts and paginated `events` plus rejected `conflicts`.

History controls: `history_page`, `history_page_size`, `conflict_page`, `conflict_page_size`. Pages default to 1, sizes to 100, maximum 200. A missing transaction returns 404. History sorts by occurrence timestamp and event ID; receipt time remains available separately. Each payload is normalized rather than a byte-for-byte copy of the original JSON.

### GET /reconciliation/summary

Supports the same merchant/status/currency/date filters and page controls. `group_by` is a distinct comma-separated subset of `merchant,date,status`, defaulting to all three; currency is **always included**. The `merchant` dimension is the merchant ID; date is the UTC date of the earliest event.

Example: `/reconciliation/summary?group_by=merchant,status&as_of=2026-05-01T00:00:00Z&page_size=200`.

Each group includes transaction/discrepancy counts and total, processed, settled and outstanding amounts in both minor units and formatted strings. Aggregation occurs before pagination; `pagination.total_groups` counts groups, not transactions. Processed/settled totals describe observed evidence, including anomalous transactions; inspect discrepancy counts rather than treating every settled amount as a reconciled success. Currencies are never summed together.

### GET /reconciliation/discrepancies

Supports merchant/status/currency/date filters, page controls, `as_of`, `grace_hours`, and one optional `reason`:

| Reason | Predicate |
|---|---|
| `processed_not_settled` | Processed event exists, no settlement, and grace period elapsed |
| `settled_failed_payment` | Failed payment has settlement evidence |
| `settled_without_processed` | Settlement exists without a processed event |
| `conflicting_payment_outcomes` | Both processed and failed evidence exist |
| `multiple_settlement_events` | More than one distinct accepted settlement event ID |
| `ingestion_conflict` | Rejected payload conflict is recorded |

Example: `/reconciliation/discrepancies?as_of=2026-05-01T00:00:00Z&grace_hours=24&reason=processed_not_settled`.

A transaction is returned once with all matching `reasons`. The threshold is inclusive: processed time <= as_of minus grace. Default `as_of` is now; default grace is 24 hours. **`as_of` only controls aging; it is not historical state replay. All currently recorded evidence is considered**, even if its occurrence timestamp is after `as_of`. Use the fixed example clock for reproducible demo counts.

### Errors

Errors have `error:{code,message,request_id}`; validation errors can include field details. All responses carry `X-Request-ID` and `Cache-Control:no-store`. JSON access logs include route template, status and duration, not event bodies or API keys.

| HTTP status | Meaning |
|---|---|
| 400 | Invalid JSON, duplicate JSON keys or non-finite JSON numbers |
| 401 | Missing/incorrect configured API key |
| 404 / 405 | Missing resource / unsupported method |
| 409 | Audited ingestion conflict |
| 413 / 415 | Body too large / unsupported media type |
| 422 | Invalid fields, unknown/repeated query parameters or unsupported filters |
| 503 | SQLite busy; retry with backoff and same event ID; Retry-After supplied |
| 500 | Unexpected server/database error; internals not returned |

## 5. State and reconciliation decisions

The model does not let arrival order erase evidence. Counts are commutative: a late initiated event cannot overwrite processed state; a settlement arriving before processing is retained, and becomes consistent when processing arrives.

Payment projection: processed+failed => `conflicted`; otherwise processed, failed, initiated, or unknown. Settlement projection is independent: settled if settlement evidence exists, otherwise unsettled. Display status gives conflicting outcomes first priority, then failed, then processed+settled, then processed, initiated or unknown. Thus a failed payment does not become a normal success just because settlement arrives.

Transaction `created_at` is the earliest **event timestamp**, not server insertion time; a late older event can move its reporting date. Original received timestamps are retained. Merchant display names are first-seen values; renaming is outside scope, and per-event names remain in event payloads.

Distinct settlement IDs count as separate settlement evidence and are flagged, even if they look similar. A semantically repeated upstream event with a different ID cannot be deduplicated safely without an upstream business key. Multiple processed events alone do not multiply transaction amounts. Refunds, reversals, payment retries, partial settlements, fees, exchange rates and bank settlement files are intentionally not modeled. The sample event shape cannot prove settlement amount mismatches beyond its transaction-level amount invariant.

## 6. Configuration and security

`.env.example` documents settings. The application **does not auto-load `.env`**; set environment variables in your shell, hosting service, or Compose.

| Variable | Default / purpose |
|---|---|
| `DATABASE_PATH` | `data/payments.db`; persistent file path |
| `API_KEY` | Empty for easy local development; nonempty protects business APIs |
| `REQUIRE_API_KEY` | `false`; production should use `true`, requiring a key >=24 characters |
| `SETTLEMENT_GRACE_HOURS` | 24; allowed range 0–8760 |
| `DB_TIMEOUT_MS` | 5000; allowed range 1–60000 |
| `PORT` | Container/host listener; local CLI explicitly uses 8000 |

Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Do not commit it. Configure `API_KEY` and `REQUIRE_API_KEY=true` before a public deployment. Share the demo key with the reviewer privately, not in the public repository or recording. This is a demo-level shared key, not per-merchant authorization. Use TLS on the host. Rate limiting, scoped authentication, secret rotation and operational alerts remain production follow-ups.

## 7. Docker and deployment

### Local Docker Compose

```bash
docker compose up --build -d
docker compose exec api python -m tools.seed --file sample_events.json --expect-conflicts 0
docker compose logs -f api
```

Compose maps port 8000 and uses a named volume at `/app/data`. Container requests run as non-root UID 10001. A fresh named volume inherits the image's owned data directory; existing/bind-mounted storage must be writable by UID 10001. Do not put the SQLite file on ephemeral or network-shared storage. `docker compose down` retains the volume; adding `-v` deletes your database.

Compose is configured for a localhost-only development listener without a key. Before public exposure, configure authentication and a TLS-terminating reverse proxy. The Dockerfile runs one Uvicorn worker, does not use reload, and accepts `PORT`. Rebuild after source/dependency changes.

### Render public demo

`render.yaml` uses Render's **native Python runtime**, a paid compute plan with a persistent disk, and a generated API key. It deliberately differs from the Docker example to use the platform's native disk permissions. The disk is mounted at `/var/data`; `DATABASE_PATH` points inside it. The current example selects `1c-2g`; confirm availability and cost in your account before creating resources. No hosting resources have been created by this project.

1. Create your own public Git repository and push the project **without** `.env`, databases or keys.
2. Create a Render Blueprint from that repository and review `render.yaml`, compute cost and disk cost before approving creation. Alternatively configure a Python web service with the same build/start/env/disk values.
3. The build runs `python -m pip install -r requirements.txt`; start runs Uvicorn on `0.0.0.0:$PORT`. Use `/health` as the health check. Keep one service instance.
4. The initial-deploy hook seeds the sample after the service starts. Check its logs. If it failed or you need a replay, use the service shell: `python -m tools.seed --file sample_events.json --expect-conflicts 0`. A persistent disk is not available during build or a separate pre-deploy command, so do not seed there.
5. Obtain your **actual assigned public URL** from the successful deployment and test `/health`, `/docs` and all five APIs. Supply the generated key in Swagger/Postman. A healthy empty app is not a completed demo; confirm 3,800 sample transactions before recording.
6. Restart/redeploy and verify transaction counts persist. Set the Postman `baseUrl` to your real URL. Manual deploys are selected (`autoDeployTrigger: off`).

**Deployment status:** not deployed here. Docker builds, provider deployment, public TLS routing and persistent-disk behavior still require your smoke test. Do not submit a guessed URL. See `verification/README.md` for exactly what was executed.

## 8. Postman and recording

Import `postman/Setu-Reconciliation.postman_collection.json`. Set collection variables `baseUrl` (no trailing slash, e.g. local `http://127.0.0.1:8000`) and `apiKey` (empty locally). Run the **whole collection in order**, not only the retry requests: setup scripts create the IDs reused by later requests. The collection exercises all five APIs plus duplicates and conflict/discrepancy cases.

The optional Node 20+ runner executes this collection's requests and scripts without npm dependencies:

```bash
node tools/run_collection.mjs http://127.0.0.1:8000
```

It is a focused test runner, not a general Postman/Newman implementation. The collection was verified against actual Uvicorn: 14 requests and 14 assertions passed. Use `DEMO_WALKTHROUGH.md` for your recording. A demo video has not been recorded.

## 9. Tradeoffs and next steps

- **SQLite for reproducibility:** zero extra infrastructure, SQL constraints/indexes and WAL suit this bounded demo. Single-writer throughput, one-host storage and non-horizontal scaling are real limits. PostgreSQL with migrations and transactional uniqueness is the next step for multi-instance/high-volume deployment.
- **Offset pagination:** simple and adequate for 3,800 sample transactions; deep pages are increasingly expensive and concurrent updates can shift page membership. Keyset pagination is the next step. A single response's data/count uses a consistent read transaction, not a snapshot across requests.
- **Indexes:** query-driven composites speed common filters; writes pay their maintenance cost. Some sort/group combinations can use temporary sorting. Inspect query plans for real workloads instead of indexing every combination.
- **Schema management:** version 1 is bootstrapped transactionally; unknown schema versions fail fast. Production evolution needs tested migration/rollback tooling and backups. Use SQLite backup mechanisms rather than copying only the main DB file while WAL is active.
- **Money:** exact Decimal parsing + integer storage avoid floating-point corruption. Only explicit two-decimal currencies are supported. The custom route prepopulates Starlette's private `_body`/`_json` caches to preserve numeric JSON precision; framework versions are pinned and regression tests cover it. Revalidate that extension when upgrading dependencies.
- **Observability:** structured access logs, request IDs and busy errors are implemented; metrics, tracing, dashboards and alerts are not.
- **Evidence:** test results are reproducible checks, not a production benchmark. The sandbox blocked TCP loopback, so actual-server checks used HTTP over a Unix socket; public deployment is still unverified.

## 10. Demo Video link and AI disclosure
**Demo Video Link** 'https://drive.google.com/drive/folders/1j8Kif7zoWixtW3M2xequ2M5KGEALxWFj'
**AI disclosure:** SuperApp was used to help implement the FastAPI rewrite, inspect the supplied dataset.

### Official implementation references

Checked on September 25, 2026. These are reference addresses, not demo URLs:

- FastAPI lifespan: `https://fastapi.tiangolo.com/advanced/events/`
- FastAPI query models: `https://fastapi.tiangolo.com/tutorial/query-param-models/`
- FastAPI custom routes: `https://fastapi.tiangolo.com/how-to/custom-request-and-route/`
- FastAPI Docker deployment: `https://fastapi.tiangolo.com/deployment/docker/`
- Render Blueprint specification: `https://render.com/docs/blueprint-spec`
- Render persistent disks: `https://render.com/docs/disks`
