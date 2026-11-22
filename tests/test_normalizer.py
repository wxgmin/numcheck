"""Tests for numeric token parsing and canonicalization."""

from numcheck.matching.normalizer import (
    canonical_deck,
    parse_token,
    significant_digits,
    values_match,
)


def _parse(tok, hint=""):
    return parse_token(tok, hint)


def test_currency_with_scale():
    p = _parse("$1,234.56m")
    assert p.dimension == "currency"
    assert p.scale == "m"
    assert p.value == 1234.56


def test_percent():
    p = _parse("12.5%")
    assert p.dimension == "percent"
    assert p.value == 12.5


def test_multiple():
    p = _parse("5.0x")
    assert p.dimension == "multiple"
    assert p.value == 5.0


def test_parentheses_negative():
    p = _parse("(1,234)")
    assert p.value == -1234


def test_bare_scale_is_currency():
    p = _parse("1.2bn")
    assert p.dimension == "currency"
    assert p.scale == "bn"
    assert p.value == 1.2


def test_unit_hint_inheritance():
    p = _parse("1,235", "$m")
    assert p.dimension == "currency"
    assert p.scale == "m"
    assert p.value == 1235


def test_significant_digits():
    assert significant_digits("540") == 3
    assert significant_digits("1.2") == 2
    assert significant_digits("1235") == 4


def test_canonical_deck_currency():
    p = _parse("$1.2bn")
    dim, val = canonical_deck(p, {"bn": 1e9})
    assert dim == "currency"
    assert abs(val - 1.2e9) < 1


def test_rounding_tolerance():
    deck = _parse("$1.2bn")
    # 1.2bn should tolerate 1.23456bn (presentation rounding).
    assert values_match(("currency", 1.2e9), ("currency", 1.23456e9), deck, 0.006, 0.5)
    # but not a materially different value
    assert not values_match(("currency", 1.2e9), ("currency", 1.4e9), deck, 0.006, 0.5)
