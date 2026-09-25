# Demo recording checklist (about 5–8 minutes)

Record only after the service works locally and, ideally, at your actual public URL. No video or public deployment has been created for you.

## Before recording

1. Seed the unchanged real sample. Show `10,355` input deliveries, `3,800` transactions and `5` merchants; these are baseline counts before the collection adds demo transactions.
2. Run `python -m unittest discover -s tests -v` and `python -m tools.verify_sample`.
3. Import the Postman collection and set `baseUrl` to the running service. Set `apiKey` privately if enabled. Do not expose keys in the recording.
4. Open the README and Swagger `/docs`. Use `as_of=2026-05-01T00:00:00Z` and `grace_hours=24` when demonstrating baseline discrepancies. Do not imply that as_of replays historical state.

## Walkthrough

1. **Health and architecture:** show `/health`, the real host, the schema/indexes and the separation of API, domain, SQL and database layers. Explain why SQLite is appropriate for this single-host demo and where PostgreSQL would be needed.
2. **POST /events:** create an initiated event, show 201. Send the exact same request again, show 200 and `duplicate:true`. Change amount while keeping event_id, show audited 409. Explain that the original transaction amount and canonical history did not change.
3. **GET /transactions:** show merchant and status filters, timezone-aware from/to range, page/page_size and ascending/descending amount/date sorting. Point out that the database performs filtering and pagination.
4. **GET /transactions/{id}:** fetch the new transaction; show current statuses, merchant, normalized event payloads and the conflict audit. Then add processed and settled events with NEW event IDs. Show settled status. Use a separate transaction to demonstrate settlement arriving before processing.
5. **GET /reconciliation/summary:** group by merchant/status, then date/status. Explain currency-separated totals, SQL grouping before pagination and transaction-based sums that do not multiply because a transaction has several events.
6. **GET /reconciliation/discrepancies:** show pending settlements past grace, failed-but-settled payments and multiple distinct settlement IDs. Baseline sample has 570 distinct discrepant transactions; reason counts overlap. After demo requests mutate the DB, totals can legitimately differ.
7. **Validation and testing:** show malformed input or an unsupported query receiving a structured error with request ID. Show the complete collection run and test results. Do not edit test output or claim a failed check passed.
8. **Deployment:** show actual deployment settings without secrets: one instance, persistent DB path, health endpoint and required API key. Show persistence after a restart if possible.

## Submit

Upload the recording to your chosen shareable service, verify view permissions in a signed-out window, and put the real recording/repository/deployment URLs in your submission. Hand the reviewer any required API key through a private channel. Include the AI disclosure from the README and be ready to explain every design choice.
