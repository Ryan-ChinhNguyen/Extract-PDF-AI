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
| `extractions` | one call to one engine for one page, kept append-only |

Two decisions worth naming:

- **A page is a fact, an extraction is an event.** Re-running a page appends a row with
  `attempt_no + 1` instead of overwriting; the failed attempt stays readable, and the same
  page can be run through a second engine for comparison.
- **Cost is snapshotted onto `extractions`, not joined from `extraction_engines`.** Repricing
  an engine must not rewrite what past runs cost.

## Retention

An uploaded file stays re-runnable for `DOCUMENT_TTL_DAYS` (default 7). Within that window
the same file cannot be uploaded again — the partial unique index
`uq_documents_content_hash_live` rejects it, and the user is pointed at the existing run,
where a failed page can be retried. Past the window the hash is released and the file may be
uploaded afresh.

Nothing sweeps expired images off disk yet; see `DESIGN_NOTES.md`.

## Running locally

```bash
cp .env.example .env      # then fill in FA_API_TOKEN
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
```

Or the whole stack: `FA_API_TOKEN=... docker compose up --build`.
