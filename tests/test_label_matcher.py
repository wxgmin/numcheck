"""Tests for deterministic label matching."""

from numcheck.config import Config
from numcheck.matching.label_matcher import label_score, metric_key, normalize_label
from numcheck.models import ModelValue


def _mv(named="", row="", col=""):
    return ModelValue("Sheet1", "A1", 1.0, row_label=row, col_label=col, named_range=named)


def test_normalize_strips_units():
    cfg = Config()
    assert normalize_label("FY24 Revenue ($m)", cfg) == "fy24 revenue"


def test_normalize_period_spelling():
    cfg = Config()
    assert normalize_label("FY2024 Revenue", cfg) == normalize_label("FY24 Revenue", cfg)


def test_metric_key_dedupes():
    cfg = Config()
    assert metric_key("Revenue_FY24 Total Revenue FY24 ($m)", cfg) == "revenue"


def test_label_score_distinguishes_implied_ev():
    cfg = Config()
    deck = normalize_label("Implied EV", cfg)
    implied = _mv(named="Implied_EV", row="Implied EV ($m)")
    ev = _mv(named="EV", row="EV ($m)")
    assert label_score(deck, implied, cfg) > label_score(deck, ev, cfg)


def test_label_score_rejects_unrelated():
    cfg = Config()
    deck = normalize_label("Total Addressable Market", cfg)
    market_cap = _mv(named="Market_Cap", row="Market Cap ($m)")
    assert label_score(deck, market_cap, cfg) < 0.35
