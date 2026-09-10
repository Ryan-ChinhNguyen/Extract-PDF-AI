"""Parsing of receipt payloads, independent of HTTP."""

from datetime import date
from decimal import Decimal

import pytest

from app.clients.fastaccounting.schemas import (
    normalize_receipt,
    parse_amount,
    parse_confidence,
    parse_date,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("840", Decimal("840")),
        ("1,200", Decimal("1200")),
        ("", None),
        ("   ", None),
        (None, None),
        ("not-a-number", None),
    ],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2015-09-07", date(2015, 9, 7)),
        ("2015/09/07", date(2015, 9, 7)),
        ("", None),
        (None, None),
        ("07-09-2015", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def _as_dict(payload):
    return {f.name: f.value for f in normalize_receipt(payload)}


def test_normalize_types_the_values_it_recognises():
    payload = {
        "date": "2015-09-07",
        "amount": "840",
        "tel": "0118211332",
        "issuer": "Kyowa Foods",
    }

    assert _as_dict(payload) == {
        "date": date(2015, 9, 7),
        "amount": Decimal("840"),
        "tel": "0118211332",
        "issuer": "Kyowa Foods",
    }


def test_normalize_keeps_the_field_order():
    """Display order comes from the code, not from key order in the payload."""
    payload = {"issuer": "Kyowa", "tel": "011", "amount": "1", "date": "2015-09-07"}

    assert [f.name for f in normalize_receipt(payload)] == ["date", "amount", "tel", "issuer"]


def test_normalize_skips_keys_the_payload_does_not_carry():
    """An engine with a different shape shows what it returned, not blanks."""
    assert _as_dict({"amount": "840"}) == {"amount": Decimal("840")}
    assert normalize_receipt({}) == []
    assert normalize_receipt(None) == []


def test_normalize_treats_a_blank_field_as_absent_value():
    """A blank means "not found", not an empty string."""
    assert _as_dict({"tel": "  ", "issuer": "Kyowa"}) == {"tel": None, "issuer": "Kyowa"}


def test_confidence_for_issuer_comes_from_issuer_name():
    """The score keys are not the field names -- `issuer` is scored as `issuer_name`."""
    payload = {
        "issuer": "Kyowa",
        "amount": "840",
        "options": {"confidences": {"issuer_name": 0.691, "amount": 0.977, "issuer": 0.1}},
    }

    scores = {f.name: f.confidence for f in normalize_receipt(payload)}

    assert scores == {"amount": 0.977, "issuer": 0.691}


def test_a_field_the_engine_did_not_find_carries_no_score():
    """The vendor scores a missing field 0; showing "0%" would misreport it."""
    payload = {"tel": "", "options": {"confidences": {"tel": 0}}}

    assert normalize_receipt(payload)[0].confidence is None


def test_a_payload_without_confidences_still_normalises():
    """`options` is only returned for contracts that enable it."""
    fields = normalize_receipt({"amount": "840"})

    assert fields[0].value == Decimal("840")
    assert fields[0].confidence is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.977, 0.977),
        (1, 1.0),
        (0, None),  # "not found", not a score
        (-0.5, None),
        (1.5, None),
        ([], None),  # repeating fields score as a list
        (None, None),
        ("0.9", None),
        (True, None),  # bool is an int in Python; not a score
    ],
)
def test_parse_confidence(raw, expected):
    assert parse_confidence(raw) == expected
