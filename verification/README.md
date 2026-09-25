# Verification evidence

The code in this archive was exercised with the **unchanged user-provided `sample_events.json`**, not a replacement synthetic dataset.

## Automated tests

- Python 3.12: **30 tests passed** (`tests-python312.txt`).
- Python 3.13: **30 tests passed** (`tests-python313.txt`).
- Tests cover canonical duplicate delivery, changed-payload conflicts, immutable transaction fields, all lifecycle arrival orders, settlement anomalies, precise money handling, invalid input/query parameters, pagination, currency-separated summaries, API keys, concurrent duplicates, persistence, SQLite busy responses, OpenAPI and indexed SQL.
- Reproduce: `python -m unittest discover -s tests -v`.

## Real sample

Run `python -m tools.verify_sample` to reproduce the data checks in an isolated temporary database. It does not change your configured database.

| Metric | Verified value |
|---|---:|
| Input deliveries | 10,355 |
| Unique accepted event IDs | 10,165 |
| Exact duplicate deliveries | 190 |
| Transactions | 3,800 |
| Merchants | 5 |
| Rejected-payload conflict audit rows in this sample | 0 |

A complete second replay returned 200 for all 10,355 deliveries and did not add event rows. Current transaction states: **2,565 settled; 380 processed; 665 failed; 190 initiated**.

At aging clock `2026-05-01T00:00:00Z` with `grace_hours=24`, there are **570 distinct discrepant transactions**: 380 processed without settlement, 95 failed payments with settlement, and 95 transactions with multiple distinct settlement events. The same 95 settled-failed transactions also satisfy `settled_without_processed`; reason counts overlap and must not be added as if disjoint. `as_of` is an aging clock, not historical state reconstruction.

The sample has no contradictory processed-and-failed outcomes or event-ID payload conflicts. Those cases are explicitly covered by the automated tests and Postman collection.

SQLite integrity check returned `ok`, foreign-key check returned no violations, and `EXPLAIN QUERY PLAN` selected `idx_transactions_merchant_status_created` for the merchant/status/date query. See `sample-results.json` for exact computed values and sample SHA-256.

## Actual HTTP server and collection

An actual Uvicorn process ingested every input record through **POST /events**: 10,165 returned 201 and 190 returned 200. Authentication without the configured API key returned 401. The transaction, detail, reconciliation, health, Swagger, ReDoc and OpenAPI smoke requests succeeded. The bundled Postman collection was exercised by `tools/run_collection.mjs`: **14 requests and 14 assertions passed**. This validates this collection's scripts, not compatibility with every Postman feature; import the collection into Postman for your own demo.

See `http-results.json` and `collection-results.json`. The single-run timing is diagnostic only and is **not a production performance benchmark**.

## What was not verified

- This sandbox rejected TCP loopback binding (`Cannot assign requested address`). Server tests therefore used **real HTTP over a Unix-domain socket**, not an internet-accessible URL.
- Docker image build/Compose deployment and hosting-provider deployment were **not executed** here. The included deployment configuration still needs a real deployment smoke test, writable persistent storage, and an API key.
- No public Git repository, public deployment URL, or video recording was created. These remain submission tasks. Do not present local verification as proof of public deployment.
- A sandbox-only Python 3.12 setup issue (no pip module in that interpreter) was resolved using the available pip with `--python` and offline wheels. It did not require changing the project installation instructions; both interpreter test runs then passed. Offline test wheels are deliberately not shipped in this project.

## Artifacts

- `tests-python312.txt`, `tests-python313.txt`: captured unittest output.
- `sample-results.json`: real-data replay, SQL totals and integrity/index checks.
- `http-results.json`: actual Uvicorn HTTP ingestion and endpoint checks.
- `collection-results.json`: executed collection request/assertion outcomes.
- `results.json`: consolidated machine-readable verification record.
- `../openapi.json`: API schema exported from the verified running application.
