"""Result objects and payload handling for the FastAccounting APIs.

The receipt payload is stored verbatim and never flattened into columns: what a
receipt yields is not knowable in advance, and the `options` block differs per
contract. ``normalize_receipt`` is therefore a *read-time* view of a stored
payload, not a write-time transformation -- the database keeps what the vendor
said, and this decides how to present it.

Keeping it here, apart from the HTTP call, is what lets the awkward parts --
amounts as strings, blank fields meaning "not found", confidence keys that do
not match the field names -- be unit-tested without a network stub.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

# Fields to present, in order, with the key each one's confidence hides behind.
# The confidence keys are not the field names: `issuer` is scored as
# `issuer_name`, and there is no `issuer` entry at all.
RECEIPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("date", "date"),
    ("amount", "amount"),
    ("tel", "tel"),
    ("issuer", "issuer_name"),
)


@dataclass(slots=True)
class ConvertResult:
    """Output of ``convert_to_jpg``: one JPEG per page of the PDF."""

    images: list[bytes] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.images)


@dataclass(slots=True)
class ExtractedField:
    """One presented value and how sure the engine was of it."""

    name: str
    value: Any
    confidence: float | None = None


def _clean(value: Any) -> str | None:
    """Vendor absence shows up as null, "" or whitespace; normalise to None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_amount(value: Any) -> Decimal | None:
    """Amounts arrive as strings, sometimes with separators ("1,200")."""
    text = _clean(value)
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", "").replace("¥", ""))
    except InvalidOperation:
        # An unreadable value is not worth hiding the rest of the receipt over;
        # the original is in the stored payload either way.
        return None


def parse_date(value: Any) -> date | None:
    text = _clean(value)
    if text is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_confidence(value: Any) -> float | None:
    """Read one score, or None when the engine did not report a usable one.

    A zero is the vendor's way of saying "no such value was found", not "found
    it, but with no confidence" -- every empty field in the sample payloads
    scores exactly 0. Reporting that as 0% would state something the engine
    never claimed. Scores for repeating fields arrive as lists and are skipped
    rather than guessed at.
    """
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    score = float(value)
    if not 0 < score <= 1:
        return None
    return score


def normalize_receipt(payload: dict[str, Any] | None) -> list[ExtractedField]:
    """Typed, ordered view of a stored receipt payload, for display.

    Returns only the fields the payload actually carries, so nothing pretends a
    value was returned when it was not.
    """
    if not payload:
        return []

    parsers = {"date": parse_date, "amount": parse_amount, "tel": _clean, "issuer": _clean}
    confidences = (payload.get("options") or {}).get("confidences") or {}

    fields: list[ExtractedField] = []
    for name, confidence_key in RECEIPT_FIELDS:
        if name not in payload:
            continue
        fields.append(
            ExtractedField(
                name=name,
                value=parsers[name](payload[name]),
                confidence=parse_confidence(confidences.get(confidence_key)),
            )
        )
    return fields
