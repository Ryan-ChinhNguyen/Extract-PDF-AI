"""The redirect the browser screens answer with."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.web.routes import _redirect


def _query(response) -> dict[str, list[str]]:
    return parse_qs(urlparse(response.headers["location"]).query)


def test_a_redirect_is_a_303_so_a_refresh_does_not_resubmit():
    response = _redirect("/documents/1", notice="hello")

    assert response.status_code == 303
    assert urlparse(response.headers["location"]).path == "/documents/1"


@pytest.mark.parametrize(
    "message",
    [
        "Only PDF files are accepted.",
        "Fish & chips #1 = 100% done",
        "line one\nline two",
        "日本語のメッセージ",
    ],
)
def test_a_message_survives_the_round_trip_through_the_query_string(message):
    """An unencoded "&" or "#" would cut the message short."""
    assert _query(_redirect("/", notice=message)) == {"notice": [message]}
    assert _query(_redirect("/", error=message)) == {"error": [message]}


def test_without_a_message_there_is_no_query_string():
    assert _redirect("/").headers["location"] == "/"


def test_a_notice_wins_over_an_error():
    assert _query(_redirect("/", notice="n", error="e")) == {"notice": ["n"]}
