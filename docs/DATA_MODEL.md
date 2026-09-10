# Data model

Four tables. The shape follows one idea: **a page is a fact, an extraction is an event.**
A page exists as soon as the PDF is converted and never changes identity; every call to an
engine for that page is appended as a new row. That is what makes a retry non-destructive
and what makes two engines comparable on the same input.

Generated DDL: [`schema.sql`](schema.sql). Source of truth: `app/db/models/`.

```
documents ──< pages ──< extractions >── extraction_engines
                │                              │
                └──────────────────────────────┘
                   pages.requested_engine_key
```

---

## `documents` — one uploaded PDF

One row per accepted upload. Also the unit of retention: a row holds the slot for its
content hash until its window lapses.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | uuid, PK | Identifier used in every URL. |
| `original_filename` | text | Name as the browser sent it. Display only — never used to locate the file or to decide the type. |
| `content_hash` | varchar(64) | SHA-256 of the raw bytes. The deduplication key. Named for its role, not its algorithm, so the hash function can change without renaming the column. |
| `size_bytes` | int | Size of the uploaded PDF. |
| `page_count` | int, null | Number of pages, known only after conversion. NULL while pending or if conversion failed. |
| `status` | varchar(32) | `pending` → `converting` → `extracting` → `completed` / `partial_failed` / `failed`. **Derived from the pages**, never incrementally counted, so two workers finishing at once cannot leave it inconsistent. |
| `error_message` | text, null | Why the *document* failed — that is, a conversion failure. Per-page failures live on `extractions`, not here. |
| `source_path` | text, null | Where the uploaded PDF sits, relative to `STORAGE_DIR`. Relative, so moving the volume does not invalidate rows. Needed because the pipeline runs after the upload request has ended. |
| `expires_at` | timestamptz | End of the retention window (`created_at + DOCUMENT_TTL_DAYS`). **The policy.** Gates whether re-running is still allowed. |
| `expired_at` | timestamptz, null | When the content hash was actually released. **The fact.** Separate from `expires_at` because nothing sweeps rows on a timer — this is stamped lazily, on the next upload of the same file. |
| `claimed_at` | timestamptz, null | When a worker took this row for conversion. A claim older than `WORKER_CLAIM_TIMEOUT_SECONDS` is treated as abandoned and released. It is a column rather than a held lock because the vendor call happens outside the transaction. |
| `created_at` / `updated_at` | timestamptz | Row timestamps. |

**Indexes**

- `uq_documents_content_hash_live` — `UNIQUE (content_hash) WHERE expired_at IS NULL`.
  The whole deduplication rule in one line: at most one *live* document per file. Releasing
  the hash (setting `expired_at`) lets the file be uploaded again, and two simultaneous
  uploads of the same file collide here rather than both paying the vendor twice.
- `ix_documents_created_at` — `(created_at DESC)`, for the history screen.

---

## `pages` — one page of a PDF, the unit of work

Created by the conversion step, one row per JPEG the vendor returns. This is what the OCR
API is called *for*, and what a retry targets.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | uuid, PK | |
| `document_id` | uuid, FK → documents, cascade | Deleting a document removes its pages. |
| `page_no` | int | 1-based, matching the order in the PDF. Unique per document. |
| `image_path` | text, null | Converted JPEG, relative to `STORAGE_DIR`. NULL once the file is gone. |
| `status` | varchar(32) | `pending` → `processing` → `succeeded` / `failed`. Follows the page's most recent attempt. |
| `requested_engine_key` | varchar(64), FK → extraction_engines, null | Which engine the *next* run should use; NULL means "whichever is active". Exists because the pipeline runs out of band — the choice made in a retry request has to outlive that request. Cleared once the attempt finishes. |
| `claimed_at` | timestamptz, null | As on `documents`: worker claim, released if stale. |
| `created_at` / `updated_at` | timestamptz | |

**Constraints**: `UNIQUE (document_id, page_no)`, `page_no >= 1`.

---

## `extraction_engines` — registry of what can produce a result

Reference data, not user data. One row per (provider, model, version) results can come
from. Seeded by migration `0001` with the FastAccounting receipt endpoint.

Its purpose is comparability: when the backend is swapped, old results stay attributable to
the engine that produced them, and cost and latency can be compared on the same page.

| Column | Type | Meaning |
| --- | --- | --- |
| `key` | varchar(64), PK | Slug, e.g. `fa_receipt_v1_5`. A slug rather than a surrogate id so raw SQL over `extractions` stays readable during an incident. |
| `provider` | varchar(64) | `FastAccounting`. |
| `model_name` | varchar(128) | `receipt`. |
| `version` | varchar(32) | `v1.5`. **Current** version; each extraction keeps its own copy. |
| `endpoint_url` | text, null | Where it is called. Documentation, not configuration — the client builds its URL from settings. |
| `unit` | varchar(16) | What one unit of usage is: `page`, `request`, or `1k_tokens`. This is what lets a per-page OCR engine and a per-token LLM engine live in the same table and be costed by the same arithmetic. |
| `unit_cost` | numeric(12,6), null | **Current** price per unit. NULL for the sandbox engine, which publishes no price — inventing one would make every cost column downstream a lie. |
| `currency` | char(3), null | Currency of `unit_cost`. |
| `is_active` | bool | Whether new work runs on it. More than one may be active; the lowest key is the default, so the choice is deterministic. |
| `config` | jsonb, null | Engine-specific knobs (requested OCR options, model parameters). |
| `created_at` | timestamptz | |

---

## `extractions` — one call to one engine for one page

Append-only. A retry adds a row; it never overwrites the failed one. Failed attempts are
kept deliberately — they are the raw material for "which failures are we seeing", and half
of any engine comparison.

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | uuid, PK | |
| `page_id` | uuid, FK → pages, cascade | |
| `engine_key` | varchar(64), FK → extraction_engines, restrict | `RESTRICT`, not cascade: deleting an engine must not silently erase the results it produced. |
| `attempt_no` | int | 1 for the first run, incremented per retry. Counted **per (page, engine)**, so a second engine starts again at 1 and the two runs read as independent rather than as a sequence. |
| `status` | varchar(16) | `succeeded` or `failed`. Only terminal values — a row is written once the call has returned. |
| `raw_response` | jsonb, null | **The extracted result, verbatim — and the only place it lives.** Stored even on failure. |
| `error_code` | int, null | The **vendor's** error code (e.g. `400003`). Its own column because the API reports failures in the body with HTTP 400 — the status code alone does not say what happened. |
| `error_message` | text, null | The vendor's message, or ours when the failure was local (missing image file, no engine registered). |
| `latency_ms` | int, null | Wall time of the call. The cheapest signal that an engine is degrading. |
| `usage_qty` | numeric(12,4), null | How many units this call consumed — `1` for a per-page engine, a fraction of 1k tokens for an LLM. |
| `engine_version` | varchar(32), null | **Snapshot** of the engine version at call time. |
| `unit_cost_snapshot` | numeric(12,6), null | **Snapshot** of the price at call time. |
| `currency` | char(3), null | **Snapshot** of the currency. |
| `cost_amount` | numeric(14,6), null | `usage_qty × unit_cost_snapshot`, computed once and stored. |
| `created_at` / `updated_at` | timestamptz | |

**Why no extracted field has its own column.** What a receipt yields is not knowable in
advance. The `options` block (confidences, positions, amount_detail, …) is only returned for
contracts that enable it, and a different engine returns a different shape entirely — an LLM
might return line items, or call the issuer something else. Columns for `date`, `amount`,
`tel`, `issuer` would encode a guess about today's vendor into the schema and need a
migration the first time that guess is wrong. The payload is stored as sent and interpreted
at read time by `normalize_receipt` in `app/clients/fastaccounting/schemas.py`, which returns
only the fields the payload actually carries, each with the engine's own confidence score.

Two things that only became visible against real responses, and that a column layout would
have had to encode as a guess: the confidence keys are **not** the field names (`issuer` is
scored as `issuer_name`), and a score of **0 means the field was not found**, not that the
engine had no confidence in what it read — so a zero is reported as "no score" rather than
as 0%.

The cost, stated plainly: filtering or sorting on an extracted value means an expression over
JSONB — `(raw_response->>'amount')::numeric` — plus an expression index if it ever gets hot.
Nothing queries these values today; the screens read whole rows.

**Why the four snapshot columns exist**: cost is never reported by joining to
`extraction_engines`. If it were, repricing an engine would silently rewrite what every
past run cost, which defeats the point of tracking cost at all.

**Constraints**: `UNIQUE (page_id, engine_key, attempt_no)`, `attempt_no >= 1`.
**Index**: `(page_id, created_at DESC)` — serves "the latest attempt for this page", which
is what both the detail screen and the page status depend on.

---

## Conventions

- **Statuses are `varchar` + `CHECK`**, not native PostgreSQL `ENUM`. Adding a value to a
  native enum needs a migration and locks the type — a poor trade for a set that will grow
  as the pipeline grows.
- **Blobs are on disk, never in the database.** One scanned receipt is already megabytes.
  Tables hold paths relative to `STORAGE_DIR`.
- **Nothing is hard-deleted by the application.** Expiry stamps `expired_at`; it does not
  remove rows, so the history a user can see outlives the file behind it.
