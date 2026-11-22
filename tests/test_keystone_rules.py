"""Regression tests for rules found with the Project Keystone board-deck test pack."""
from numcheck.matching.normalizer import parse_unit_banner


def test_rate_inside_instrument_name_is_not_the_row_unit():
    assert parse_unit_banner("6.375% Senior notes").dimension != "percent"
    assert parse_unit_banner("5% preferred stock").dimension != "percent"
    assert parse_unit_banner("Gross margin (%)").dimension == "percent"
    assert parse_unit_banner("Growth %").dimension == "percent"
