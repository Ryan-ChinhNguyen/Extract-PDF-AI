"""Status vocabularies.

Stored as VARCHAR with a CHECK constraint rather than a native PostgreSQL
ENUM: adding a value to a native enum needs a migration and locks the type,
which is a poor trade for a set that will grow as the pipeline grows.
"""

from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "pending"
    CONVERTING = "converting"
    EXTRACTING = "extracting"
    COMPLETED = "completed"          # every page succeeded
    PARTIAL_FAILED = "partial_failed"  # some pages succeeded, some failed
    FAILED = "failed"                # conversion failed, or every page failed


class PageStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BillingUnit(StrEnum):
    """What one unit of engine usage is priced by.

    The FastAccounting receipt API bills per image, so ``PAGE``. A future
    LLM-backed engine would use ``TOKENS_1K`` with a fractional usage_qty --
    same table, same cost arithmetic.
    """

    PAGE = "page"
    REQUEST = "request"
    TOKENS_1K = "1k_tokens"
