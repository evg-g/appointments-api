"""Unit tests for the pure ETag / If-Match helpers. No HTTP, no I/O."""

from __future__ import annotations

import pytest

from appointments_api.api.conditional import if_match_satisfied, make_etag, parse_if_match


def test_make_etag_is_quoted_version() -> None:
    assert make_etag(1) == '"1"'
    assert make_etag(42) == '"42"'


def test_parse_if_match_absent_is_none() -> None:
    assert parse_if_match(None) is None


def test_parse_if_match_blank_is_empty_list() -> None:
    assert parse_if_match("") == []
    assert parse_if_match("  ,  ") == []


def test_parse_if_match_splits_and_trims() -> None:
    assert parse_if_match('"1"') == ['"1"']
    assert parse_if_match('"1", "2" , "3"') == ['"1"', '"2"', '"3"']


def test_if_match_satisfied_matches_current_version() -> None:
    assert if_match_satisfied('"3"', 3)
    assert if_match_satisfied('"1", "3"', 3)  # any listed tag may match


def test_if_match_not_satisfied_for_stale_version() -> None:
    assert not if_match_satisfied('"2"', 3)


def test_if_match_star_matches_any_version() -> None:
    assert if_match_satisfied("*", 99)


@pytest.mark.parametrize("header", [None, "", "   "])
def test_absent_or_blank_if_match_is_not_satisfied(header: str | None) -> None:
    # The router turns this into 428 Precondition Required.
    assert not if_match_satisfied(header, 1)
