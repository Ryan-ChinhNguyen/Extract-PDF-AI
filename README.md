# PDF Receipt Extraction

OCR pipeline for multi-page PDF receipts, built on the FastAccounting sandbox APIs.

A PDF is uploaded, converted to one JPEG per page (`convert_to_jpg`), and each page is sent
to the receipt OCR endpoint (`receipt`). Every attempt — successful or failed — is stored,
so the history screen can show per-page status and a failed page can be re-run on its own.

## Stack

FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 (async) · Alembic · httpx

## Layout

```
app/
  api/v1/         HTTP endpoints, thin: parse, delegate, serialise
  clients/        adapters for external APIs (FastAccounting)
  core/           settings, logging, domain exceptions
  db/models/      SQLAlchemy models — the schema lives here
  repositories/   database access, one module per aggregate
  schemas/        pydantic request/response models
  services/       orchestration and business rules
  workers/        the polling worker that runs the pipeline
  web/            server-rendered UI (Jinja2)
migrations/       Alembic revisions
docs/schema.sql   generated DDL, for reading only
tests/
```

The layering rule: `api` → `services` → `repositories` → `db`. Services never import
`fastapi`; they raise the exceptions in `app/core/exceptions.py`, which `app/main.py`
translates into HTTP responses.

## Data model

Four tables (`docs/schema.sql` for the full DDL):

| table | holds |
| --- | --- |
| `documents` | one uploaded PDF: filename, `content_hash`, status, retention window |
| `pages` | one page of a PDF — the unit of work the OCR API is called for |
| `extraction_engines` | registry of engines results can come from, with billing unit and price |
| `extractions` | one call to one engine for one page, kept append-only; the result is one JSONB column |

Two decisions worth naming:

- **A page is a fact, an extraction is an event.** Re-running a page appends a row with
  `attempt_no + 1` instead of overwriting; the failed attempt stays readable, and the same
  page can be run through a second engine for comparison.
- **Cost is snapshotted onto `extractions`, not joined from `extraction_engines`.** Repricing
  an engine must not rewrite what past runs cost.
- **No extracted field gets its own column.** What a receipt yields is not knowable in
  advance and varies by engine, so `raw_response` holds the payload as sent and
  `normalize_receipt` interprets it at read time — including the per-field confidence
  the engine reports. See `docs/DATA_MODEL.md`.
- **Confidence is shown, not acted on.** Fields scored below
  `OCR_LOW_CONFIDENCE_THRESHOLD` (0.80) are flagged on the detail screen for a human to
  check against the image. Nothing is rejected, retried or hidden because of the score,
  and the stored value is untouched — the threshold is a display choice, and the raw
  score is what the API returns.

## How work actually runs

`POST /documents` stores the file, returns `202`, and ends. Nothing is processed in the
request: a 3-page PDF is four vendor calls, which is far too long to hold a connection.

`PipelineWorker` (`app/workers/pipeline_worker.py`) polls the database and does the work:

```
tick:  release claims older than WORKER_CLAIM_TIMEOUT_SECONDS
       claim documents  status pending    -> converting   (FOR UPDATE SKIP LOCKED)
       claim pages      status pending    -> processing   (FOR UPDATE SKIP LOCKED)
       for each: call the vendor OUTSIDE any transaction, then write the result
```

Three consequences worth stating:

- **Both retry endpoints just set rows back to `pending`.** They need no scheduling of their
  own, and a retry queued while the worker is down is picked up when it returns.
- **A crash strands nothing.** The claim is a column (`claimed_at`), not a held lock, because
  the vendor call happens outside the transaction. A stale claim is released on a later tick.
- **`SKIP LOCKED` means replicas are safe.** Run the image with `WORKER_ENABLED=false` for
  API-only pods and `true` for workers; no two of them take the same row.

Client-side, the UI polls `GET /documents/{id}` for progress.

## Retention

An uploaded file stays re-runnable for `DOCUMENT_TTL_DAYS` (default 7). Within that window
the same file cannot be uploaded again — the partial unique index
`uq_documents_content_hash_live` rejects it, and the user is pointed at the existing run,
where a failed page can be retried. Past the window the hash is released and the file may be
uploaded afresh.

Nothing sweeps expired images off disk yet; see `DESIGN_NOTES.md`.

## Running locally

Needs PostgreSQL 14+ and Python 3.11+.

```bash
cp .env.example .env      # then fill in FA_API_TOKEN
```

Create the database the `DATABASE_URL` in `.env` points at, then:

```bash
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

The UI is at `/`, Swagger at `/docs`. The background worker starts with the application;
set `WORKER_ENABLED=false` for an API-only process.

`docker compose up --build` runs the same thing with a database, for anyone who prefers it.

## Tests

```bash
pytest                    # everything
pytest tests/unit         # no database, no network
pytest tests/integration  # needs the database in DATABASE_URL, migrated
```

`tests/unit` stubs HTTP with respx and opens no connection.

`tests/integration` runs against the **same database the application uses**, because
what it checks only exists in PostgreSQL: the partial unique index behind deduplication,
`FOR UPDATE ... SKIP LOCKED` behind multi-worker claiming, and JSONB. It never truncates
a table — each test registers the documents it creates and only those rows are deleted
afterwards, so the suite is safe to run against a database holding real data. Page images
written during a test go to a pytest `tmp_path`, not to `storage/`.

The vendor is stubbed there too (`tests/integration/fakes.py`): the interesting cases are
the ones where a page *fails*, and the sandbox cannot be asked to fail on command.

> **Stop the application before running the integration tests.** Both the running app and
> the tests drive a `PipelineWorker` against the same `pages` table, and a claim is not
> scoped to a document — so a live worker will pick up a test's pages (spending real API
> quota on fake images) and a test's stubbed worker will pick up real pending pages
> (writing stub results into a real document). This is the cost of pointing tests at the
> working database; see `DESIGN_NOTES.md`.
