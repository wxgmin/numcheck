"""Tests for the matching engine: labels, periods, units, alignment, value-only, staleness, performance."""

from __future__ import annotations

import random
import time

from numcheck.config import Config
from numcheck.matching.index import ModelIndex
from numcheck.matching.label_matcher import split_label
from numcheck.matching.pipeline import match_deck_to_model
from numcheck.matching.source_search import discover_sources, review_sources
from numcheck.models import DeckNumber, Match, ModelValue, ReviewWarning, SlideInfo, Source


def mv(row_label, value, *, sheet="Model", addr="B2", col_label="", row=0, col=0, unit="currency", scale="m",
       period="", named="", **kw) -> ModelValue:
    return ModelValue(sheet, addr, value, row_label=row_label, col_label=col_label, named_range=named, unit=unit,
                      scale=scale, scale_known=True, period=period, row=row, col=col, **kw)


def dn(label, value, raw, *, unit="currency", scale="m", period="", slide=1, shape="s1", decimals=0, **kw) -> DeckNumber:
    return DeckNumber(slide, shape, Source.TEXT_BOX, raw, value, unit=unit, scale=scale, period=period, label=label,
                      decimals=decimals, scale_known=bool(scale), **kw)


def run(decks, values, cfg=None, **kw):
    return match_deck_to_model(decks, values, cfg or Config(), **kw)


def test_derived_cagr_alternative_phrasings():
    from numcheck.matching.derived import verify_derived

    config = Config()
    values = [mv("Revenue", 1000, period="fy22"), mv("Revenue", 1210, period="fy24", addr="D2")]
    cases = [
        ("Revenue CAGR", "of 10% (FY22-FY24)"),
        ("Revenue compound annual growth rate", "of 10% from 2022 to 2024"),
        ("Revenue compounded annually", "at 10% between 2022 and 2024"),
        ("Revenue growth per annum", "of 10% over 2022\u20132024"),
        ("Revenue", "grew 10% annually from 2022 to 2024"),
        ("Revenue CAGR", "of 10% on a 2-year basis to FY24"),
    ]
    for label, tail in cases:
        context = f"{label} {tail}"
        start = context.index("10%")
        d = dn(label, 10.0, "10%", unit="percent", scale="", decimals=1,
               context=context, char_start=start, char_end=start + 3)
        match = Match(d, None, "unmatched")
        assert verify_derived([match], values, config) == 1
        assert match.status == "matched" and match.method == "derived", (label, match.status, match.note)
        assert {cell.cell_address for cell in match.derivation_inputs} == {"B2", "D2"}


def test_derived_growth_distinguishes_total_change_and_rejects_incompatible_series():
    from numcheck.matching.derived import verify_derived

    def claim():
        context = "Sales grew 21% between 2022 and 2024"
        return Match(dn("Sales", 21, "21%", unit="percent", scale="", context=context,
                        char_start=context.index("21%")), None, "unmatched")

    start = mv("Revenue", 1000, period="fy22", row=2, currency="USD")
    match = claim()
    assert verify_derived([match], [start, mv("Revenue", 1210, period="fy24", row=2, currency="USD")], Config()) == 1
    assert match.status == "matched"
    assert "cumulative growth" in match.note

    for end in (
        mv("Revenue", 1210, period="fy24", row=3, currency="USD"),
        mv("Revenue", 1210, period="fy24", row=2, currency="EUR"),
        mv("Revenue", 1210, period="fy24", row=2, currency="USD", workbook="other.xlsx"),
    ):
        match = claim()
        assert verify_derived([match], [start, end], Config()) == 0
        assert match.status == "unmatched"


def test_derived_return_ratio_from_model():
    from numcheck.matching.derived import verify_derived

    config = Config()
    values = [mv("Net Income", 100, period="fy24"), mv("Average Equity", 500, period="fy24")]
    d = dn("ROE", 20.0, "20%", unit="percent", scale="", period="fy24", decimals=0,
           context="ROE 20%", char_start=0, char_end=3)
    match = Match(d, None, "unmatched")
    assert verify_derived([match], values, config) == 1
    assert match.status == "matched" and match.method == "derived"

    # A year-end value alone is not evidence for an average-balance return.
    match = Match(d, None, "unmatched")
    assert verify_derived([match], [values[0], mv("Equity", 500, period="fy24")], config) == 0


def test_derived_multiples_and_premiums_use_their_display_units():
    from numcheck.matching.derived import verify_derived

    cases = [
        ("Current Ratio", "2.0x", 2, "multiple", "Current Assets", 200, "Current Liabilities", 100),
        ("Offer Premium", "25%", 25, "percent", "Offer Price", 25, "Unaffected Price", 20),
    ]
    for label, raw, expected, unit, numerator, num_value, denominator, den_value in cases:
        match = Match(dn(label, expected, raw, unit=unit, scale="", period="fy24"), None, "unmatched")
        values = [mv(numerator, num_value, period="fy24"), mv(denominator, den_value, period="fy24", addr="B3")]
        assert verify_derived([match], values, Config()) == 1
        assert match.status == "matched"
        assert match.comparison.dimension == unit


def test_source_review_requires_independent_label_scope_and_value_evidence(monkeypatch):
    def choose_first(entries, config, warnings):
        return {entry["id"]: "candidate-1" for entry in entries}

    monkeypatch.setattr("numcheck.matching.source_search.resolve_ambiguous", choose_first)
    config = Config(relative_tolerance=0.1)
    generic = dn("Revenue", 100, "$100.0m", period="fy24", decimals=1)
    segment = dn("Aerospace Components Revenue", 100, "$100.0m", period="fy24", decimals=1)

    cases = [
        (segment, [mv("Revenue", 100, period="fy24", section_label="Aerospace Components")], "matched", 1, True),
        (generic, [mv("Subscription revenue", 100, period="fy24")], "unmatched", 1, False),
        (generic, [mv("Revenue", 105, period="fy24")], "unmatched", 1, False),
        (generic, [mv("Revenue", 100, period="fy24"), mv("Revenue", 110, period="fy24", addr="B3")],
         "unmatched", 2, True),
        (generic, [mv("Revenue", 100, period="fy24"), mv("Revenue", 100, period="fy24", addr="B3")],
         "unmatched", 2, False),
        (dn("Revenue FY24A", 100, "$100.0m", period="fy24", decimals=1),
         [mv("Revenue", 100, period="fy24", col_label="FY24E"),
          mv("Revenue", 100, period="fy24", col_label="FY24 Forecast", addr="B3")], "unmatched", 2, False),
    ]
    for deck, values, expected, options, suggestion in cases:
        matches, _ = review_sources([Match(deck, None, "unmatched")], ModelIndex(values, config),
                                    config, [], Config(llm="ollama"))
        match = matches[0]
        assert match.status == expected
        assert len(match.source_options) == options and bool(match.source_suggestion) == suggestion
        if suggestion:
            assert match.source_suggestion == match.source_options[0][0].ref
        if expected == "matched":
            assert match.method == "source_ai" and match.comparison.ok
        else:
            assert match.model is None


def test_best_source_for_range_endpoint_with_changed_value():
    config = Config()
    deck = dn("Adjusted EBITDA", 13.4, "13.4x", unit="multiple", scale="", decimals=1,
              period="fy24", is_range=True, range_role="high")
    values = [
        mv("Adjusted EBITDA", 13.4, unit="multiple", scale="", period="fy24", col_label="Multiple - low"),
        mv("Adjusted EBITDA", 12.5, unit="multiple", scale="", period="fy24", col_label="Multiple - high", addr="C2"),
        mv("Adjusted EBITDA", 13.5, unit="multiple", scale="", period="fy24", col_label="Multiple - high", addr="D2"),
    ]
    candidates, eligible = discover_sources(deck, ModelIndex(values, config))
    assert [value.cell_address for value, _ in candidates] == ["D2", "C2", "B2"]
    assert not eligible


def test_range_pair_needs_one_workbook_row_with_both_endpoints():
    config = Config()
    shared = {"is_range": True, "context": "Revenue of $100m–$120m", "period": "fy24"}
    decks = [
        dn("Revenue", 100, "$100m", range_role="low", char_start=11, char_end=16, **shared),
        dn("Revenue", 120, "$120m", range_role="high", char_start=17, char_end=22, **shared),
    ]
    low = mv("Revenue", 100, addr="B2", row=2, col_label="Low", period="fy24")
    high = mv("Revenue", 120, addr="C2", row=2, col_label="High", period="fy24")
    other_high = mv("Revenue", 120, addr="C3", row=3, col_label="High", period="fy24")
    other_low = mv("Revenue", 100, addr="B3", row=3, col_label="Low", period="fy24")
    for cells, expected in [
        ([low, high, other_high], ["range_pair", "range_pair"]),
        ([low, other_high], ["", ""]),
        ([low, high, other_low, other_high], ["", ""]),
    ]:
        matches, _ = review_sources([Match(deck, None, "unmatched") for deck in decks],
                                    ModelIndex(cells, config), config, [])
        assert [match.method for match in matches] == expected


# --------------------------------------------------------------------------- label parts


def test_split_label_parts():
    cfg = Config()
    p = split_label("FY24 Adjusted EBITDA ($m)", cfg)
    assert p.period == "fy24" and "adjusted" in p.qualifiers and p.metric == ("ebitda",)
    assert split_label("EV / EBITDA", cfg).ratio


def test_synonym_longest_key_first():
    cfg = Config()
    assert split_label("Net sales", cfg).metric == split_label("Revenue", cfg).metric == ("revenue",)
    assert split_label("Sales", cfg).metric == ("revenue",)


def test_multiword_qualifier():
    p = split_label("Pro forma EBITDA", Config())
    assert "pro forma" in p.qualifiers and p.metric == ("ebitda",)


# --------------------------------------------------------------------------- basics


def test_synonym_sales_matches_revenue():
    r = run([dn("Sales", 1200, "$1,200m")], [mv("Revenue", 1200), mv("Net income", 80, addr="B3")])
    assert r[0].status == "matched" and r[0].model.row_label == "Revenue"
    assert r[0].confidence >= 0.85 and r[0].method in ("label", "named_range")


def test_one_match_per_deck_in_order_and_skipped():
    skipped = dn("Year", 2024, "2024", skip_reason="year")
    r = run([skipped, dn("Revenue", 1200, "$1,200m")], [mv("Revenue", 1200)])
    assert [m.status for m in r] == ["skipped", "matched"]
    assert r[0].model is None


def test_wrong_value_is_mismatch_at_right_cell():
    r = run([dn("Revenue", 1250, "$1,250m")], [mv("Revenue", 1200, addr="C5"), mv("EBITDA", 300, addr="C6")])
    m = r[0]
    assert m.status == "mismatch" and m.model.cell_address == "C5"
    assert m.comparison is not None and not m.comparison.ok


def test_unmatched_when_nothing_similar():
    r = run([dn("Customer churn cohort", 12.5, "12.5", unit="none", scale="", decimals=1)],
            [mv("Revenue", 1200)])
    assert r[0].status == "unmatched"


def test_error_cell_is_chosen_source():
    bad = mv("Revenue", float("nan"), is_error=True, error_text="#DIV/0!")
    r = run([dn("Revenue", 1200, "$1,200m")], [bad])
    assert r[0].status == "mismatch" and r[0].comparison is None
    assert r[0].note == "source cell is an Excel error" and r[0].model is bad


def test_hidden_excluded_when_not_included():
    cfg = Config(include_hidden=False)
    r = run([dn("Revenue", 1200, "$1,200m")], [mv("Revenue", 1200, hidden=True)], cfg)
    assert r[0].status == "unmatched"


# --------------------------------------------------------------------------- qualifiers


def test_adjusted_ebitda_prefers_adjusted_row():
    vals = [mv("EBITDA", 300, addr="B2"), mv("Adj. EBITDA", 340, addr="B3")]
    r = run([dn("Adjusted EBITDA", 340, "$340m")], vals)
    assert r[0].status == "matched" and r[0].model.cell_address == "B3"


def test_adjusted_ebitda_mismatch_goes_to_adjusted_row_not_reported():
    vals = [mv("EBITDA", 300, addr="B2"), mv("Adj. EBITDA", 340, addr="B3")]
    r = run([dn("Adjusted EBITDA", 300, "$300m")], vals)
    # The reported EBITDA equals 300 but is a different metric: must flag against the adjusted row.
    assert r[0].status == "mismatch" and r[0].model.cell_address == "B3"


def test_plain_ebitda_avoids_adjusted_row():
    vals = [mv("EBITDA", 300, addr="B2"), mv("Adjusted EBITDA", 340, addr="B3")]
    r = run([dn("EBITDA", 300, "$300m")], vals)
    assert r[0].status == "matched" and r[0].model.cell_address == "B2"


def test_diluted_vs_basic_eps():
    vals = [mv("Basic EPS", 2.10, unit="currency", scale="", addr="B2"),
            mv("Diluted EPS", 2.05, unit="currency", scale="", addr="B3")]
    r = run([dn("Diluted EPS", 2.05, "$2.05", scale="", decimals=2)], vals)
    assert r[0].status == "matched" and r[0].model.cell_address == "B3"


def test_range_verifies_vertical_endpoints_and_leaves_an_orphan_in_inspect():
    vals = [mv("EV Low", 900, addr="B2", row=2, col=2),
            mv("EV High", 1100, addr="B3", row=3, col=2)]
    shared = {"is_range": True, "context": "Enterprise value of $900m–$1,100m"}
    low = dn("Enterprise value", 900, "$900m", range_role="low", char_start=20, char_end=25, **shared)
    high = dn("Enterprise value", 1100, "$1,100m", range_role="high", char_start=26, char_end=33, **shared)
    matches, _ = review_sources([Match(deck, None, "unmatched") for deck in (low, high)],
                                ModelIndex(vals, Config()), Config(), [])
    assert [m.model.cell_address for m in matches] == ["B2", "B3"]
    orphan = run([high], vals)[0]
    assert orphan.status == "unmatched"
    assert orphan.source_options == [(vals[1], "")]


# --------------------------------------------------------------------------- periods


def _year_cells():
    return [
        mv("Revenue", 1000, addr="C5", col_label="FY23", period="fy23"),
        mv("Revenue", 1100, addr="D5", col_label="FY24", period="fy24"),
        mv("Revenue", 1210, addr="E5", col_label="FY25", period="fy25"),
    ]


def test_period_filter_picks_right_year():
    r = run([dn("Revenue FY24", 1100, "$1,100m", period="fy24")], _year_cells())
    assert r[0].status == "matched" and r[0].model.cell_address == "D5"


def test_wrong_period_value_is_mismatch_with_note():
    r = run([dn("Revenue FY24", 1000, "$1,000m", period="fy24")], _year_cells())
    m = r[0]
    assert m.status == "mismatch" and m.model.cell_address == "D5"
    assert m.note.startswith("value equals") and "C5" in m.note and "fy23" in m.note


def test_periodless_number_uses_slide_hint():
    slides = [SlideInfo(slide_number=1, period_hint="fy25")]
    r = run([dn("Revenue", 1210, "$1,210m")], _year_cells(), slides=slides)
    assert r[0].status == "matched" and r[0].model.cell_address == "E5"
    r = run([dn("Revenue", 1100, "$1,100m")], _year_cells(), slides=slides)
    assert r[0].status == "mismatch" and r[0].model.cell_address == "E5"
    assert r[0].note.startswith("value equals")


def test_periodless_number_uses_base_period_config():
    r = run([dn("Revenue", 1100, "$1,100m")], _year_cells(), Config(base_period="fy24"))
    assert r[0].status == "matched" and r[0].model.cell_address == "D5"


def test_periodless_tie_exactly_one_matches():
    r = run([dn("Revenue", 1210, "$1,210m")], _year_cells())
    m = r[0]
    assert m.status == "matched" and m.model.cell_address == "E5"
    assert m.confidence <= 0.7 and "equally labelled" in m.note


def test_calendar_vs_fiscal_year_compatible():
    vals = [mv("Revenue", 1100, col_label="2024A", period="y24")]
    r = run([dn("Revenue FY24", 1100, "$1,100m", period="fy24")], vals)
    assert r[0].status == "matched"


def test_period_from_labels_when_cell_has_none():
    vals = [mv("Revenue", 1000, addr="C5", col_label="FY2023"), mv("Revenue", 1100, addr="D5", col_label="FY2024")]
    r = run([dn("Revenue", 1100, "$1,100m", period="fy24")], vals)
    assert r[0].status == "matched" and r[0].model.cell_address == "D5"


# --------------------------------------------------------------------------- units


def test_margin_never_compared_with_dollar_cell():
    vals = [mv("EBITDA", 300, addr="B2"),
            mv("EBITDA margin", 0.25, addr="B3", unit="percent", scale="", pct_basis="ratio")]
    r = run([dn("EBITDA margin", 25, "25%", unit="percent", scale="")], vals)
    assert r[0].status == "matched" and r[0].model.cell_address == "B3"
    # With only the $ cell available the % number must not be compared against it.
    r = run([dn("EBITDA margin", 25, "25%", unit="percent", scale="")], [mv("EBITDA", 300)])
    assert r[0].status in ("unmatched", "matched_value_only") and r[0].status != "mismatch"
    assert r[0].model is None or r[0].model.unit != "currency"


def test_unitless_number_matches_millions_cell_inferred():
    r = run([dn("Revenue", 1200, "1,200", unit="none", scale="")], [mv("Revenue", 1200)])
    m = r[0]
    assert m.status == "matched" and m.comparison.ok


def test_unitless_number_inferred_flag_lowers_confidence():
    inferred = run([dn("Revenue", 1200, "1,200", unit="none", scale="")], [mv("Revenue", 1200, scale="m")])[0]
    explicit = run([dn("Revenue", 1200, "$1,200m")], [mv("Revenue", 1200, scale="m")])[0]
    assert inferred.confidence <= explicit.confidence


def test_currency_conflict_excluded():
    r = run([dn("Revenue", 1200, "€1,200m", currency="EUR")], [mv("Revenue", 1200, currency="USD")])
    assert r[0].status == "unmatched"


def test_percent_ratio_cell_matches_points():
    vals = [mv("Gross margin", 0.452, unit="percent", scale="", pct_basis="ratio")]
    r = run([dn("Gross margin", 45.2, "45.2%", unit="percent", scale="", decimals=1)], vals)
    assert r[0].status == "matched"


# --------------------------------------------------------------------------- ties and duplicates


def test_duplicate_equal_cells_not_ambiguous():
    vals = [mv("Revenue", 1200, sheet="Model", addr="B2"), mv("Revenue", 1200, sheet="Summary", addr="B2")]
    r = run([dn("Revenue", 1200, "$1,200m")], vals)
    assert r[0].status == "matched"


def test_duplicate_equal_cells_all_wrong_is_mismatch():
    vals = [mv("Revenue", 1200, sheet="Model", addr="B2"), mv("Revenue", 1200, sheet="Summary", addr="B2")]
    r = run([dn("Revenue", 999, "$999m")], vals)
    assert r[0].status == "mismatch" and r[0].model is not None


def test_ambiguous_when_tied_cells_differ_and_none_match():
    vals = [mv("Revenue", 1200, sheet="A", addr="B2"), mv("Revenue", 1300, sheet="B", addr="B2")]
    r = run([dn("Revenue", 999, "$999m")], vals)
    m = r[0]
    assert m.status == "ambiguous" and m.model is None and len(m.candidates) == 2


def test_sheet_priority_breaks_label_ties():
    vals = [mv("Revenue", 1200, sheet="Old backup", addr="B2", sheet_priority=0.3),
            mv("Revenue", 1300, sheet="Model", addr="B2")]
    r = run([dn("Revenue", 999, "$999m")], vals)
    assert r[0].status == "mismatch" and r[0].model.sheet_name == "Model"


def test_named_range_preferred():
    vals = [mv("Net revenue", 1300, addr="B2"), mv("Turnover line", 1200, addr="B3", named="Revenue_Total")]
    r = run([dn("Revenue", 1200, "$1,200m")], vals)
    assert r[0].status == "matched" and r[0].model.cell_address == "B3"


def test_context_coherence_breaks_tie_by_sheet():
    # Same label on two sheets with different values; siblings clearly live on sheet "DCF".
    vals = [
        mv("Terminal value", 5000, sheet="DCF", addr="B9"), mv("Terminal value", 4000, sheet="Other", addr="B9"),
        mv("WACC", 0.09, sheet="DCF", addr="B3", unit="percent", scale="", pct_basis="ratio"),
        mv("Discount factor", 0.8, sheet="DCF", addr="B4", unit="none", scale=""),
    ]
    decks = [dn("Terminal value", 5000, "$5,000m", shape="tbl"),
             dn("WACC", 9, "9%", unit="percent", scale="", shape="tbl")]
    r = run(decks, vals)
    assert r[0].status == "matched" and r[0].model.sheet_name == "DCF"


# --------------------------------------------------------------------------- alignment


def _grid_model(values_by_row, labels, *, sheet="Fin"):
    """A model block: labels in col A, FY23..FY26 in cols B..E starting at row 5."""
    out = []
    for r, (lab, vs) in enumerate(zip(labels, values_by_row), start=5):
        for c, v in enumerate(vs, start=2):
            out.append(ModelValue(sheet, f"{chr(64 + c)}{r}", v, row_label=lab, col_label=f"Col{c}", unit="currency",
                                  scale="m", scale_known=True, row=r, col=c))
    return out


def _table_decks(grid, row_labels, shape="t1"):
    decks = []
    for r, vs in enumerate(grid):
        for c, v in enumerate(vs):
            decks.append(dn(row_labels[r], v, f"${v:,.0f}m", shape=shape, table_row=r + 1, table_col=c + 1,
                            source=Source.TABLE) if False else
                         DeckNumber(1, shape, Source.TABLE, f"${v:,.0f}m", float(v), unit="currency", scale="m",
                                    label=row_labels[r], decimals=0, scale_known=True, table_row=r + 1,
                                    table_col=c + 1))
    return decks


def test_table_alignment_rescues_poorly_labelled_table():
    grid = [[1010, 1120, 1250, 1390], [210, 233, 260, 291], [95, 104, 118, 133]]
    model = _grid_model(grid, ["Div 1 net rev", "Div 1 op contribution", "Div 1 below-line"])
    # Deck labels share nothing with the model labels.
    decks = _table_decks(grid, ["Alpha", "Beta", "Gamma"])
    r = run(decks, model)
    assert all(m.status == "matched" for m in r), [m.status for m in r]
    assert all(m.method == "table_align" for m in r)
    assert all(m.confidence >= 0.8 for m in r)


def test_table_alignment_catches_one_wrong_cell():
    grid = [[1010, 1120, 1250, 1390], [210, 233, 260, 291], [95, 104, 118, 133]]
    model = _grid_model(grid, ["Div 1 net rev", "Div 1 op contribution", "Div 1 below-line"])
    wrong = [row[:] for row in grid]
    wrong[1][2] = 266
    decks = _table_decks(wrong, ["Alpha", "Beta", "Gamma"])
    r = run(decks, model)
    bad = [m for m in r if m.status == "mismatch"]
    assert len(bad) == 1 and bad[0].deck.value == 266
    assert bad[0].method == "table_align" and bad[0].model.cell_address == "D6"
    assert bad[0].confidence >= 0.8
    assert sum(m.status == "matched" for m in r) == 11


def test_table_alignment_transposed():
    grid = [[1010, 1120, 1250, 1390], [210, 233, 260, 291], [95, 104, 118, 133]]
    # Model stores the transpose: periods down rows, metrics across columns.
    model = []
    for c, series in enumerate(zip(*grid)):
        for k, v in enumerate(series):
            model.append(ModelValue("T", f"{chr(66 + k)}{c + 4}", v, row_label=f"P{c}", col_label=f"M{k}",
                                    unit="currency", scale="m", scale_known=True, row=c + 4, col=k + 2))
    decks = _table_decks(grid, ["Alpha", "Beta", "Gamma"])
    r = run(decks, model)
    assert all(m.status == "matched" and m.method == "table_align" for m in r)


def test_table_alignment_with_blank_spacer_columns():
    grid = [[1010, 1120, 1250], [210, 233, 260], [95, 104, 118]]
    model = []
    for r, row in enumerate(grid, start=5):
        for c, v in zip((2, 4, 6), row):   # spacer columns between periods
            model.append(ModelValue("S", f"{chr(64 + c)}{r}", v, row_label=f"L{r}", unit="currency", scale="m",
                                    scale_known=True, row=r, col=c))
    decks = _table_decks(grid, ["Alpha", "Beta", "Gamma"])
    r = run(decks, model)
    assert all(m.status == "matched" for m in r)


def test_strong_label_beats_alignment_for_mismatch():
    grid = [[1010, 1120, 1250, 1390], [210, 233, 260, 291]]
    model = _grid_model(grid, ["Revenue", "EBITDA"])
    wrong = [[1010, 1120, 1250, 1999], [210, 233, 260, 291]]
    decks = _table_decks(wrong, ["Revenue", "EBITDA"])
    r = run(decks, model)
    bad = [m for m in r if m.status == "mismatch"]
    assert len(bad) == 1 and bad[0].model.cell_address == "E5"


def test_no_alignment_when_values_do_not_fit():
    grid = [[1010, 1120, 1250, 1390], [210, 233, 260, 291]]
    model = _grid_model(grid, ["Div 1 net rev", "Div 1 op"])
    decks = _table_decks([[7, 8, 9, 10], [11, 12, 13, 14]], ["Alpha", "Beta"])
    r = run(decks, model)
    assert not any(m.method == "table_align" for m in r)


def test_series_alignment_for_chart():
    model = [ModelValue("Chart data", f"{chr(66 + k)}3", v, row_label="Segment 7 sales", col_label=f"Y{k}",
                        unit="currency", scale="m", scale_known=True, row=3, col=k + 2)
             for k, v in enumerate([310, 335, 362, 391, 425])]
    decks = [DeckNumber(2, "c1", Source.CHART, f"${v}m", float(v), unit="currency", scale="m", label="Widgets",
                        decimals=0, scale_known=True, series_index=0, point_index=k, chart_type="bar")
             for k, v in enumerate([310, 335, 362, 391, 430])]
    r = run(decks, model)
    assert [m.status for m in r] == ["matched"] * 4 + ["mismatch"]
    assert all(m.method == "series_align" for m in r)


# --------------------------------------------------------------------------- value only


def test_strict_review_does_not_choose_the_source_by_the_figure_being_checked():
    deck = [dn("Revenue", 1200, "$1,200m", period="fy24")]
    values = [
        mv("Revenue", 1200, sheet="Scenario A", period="fy24"),
        mv("Revenue", 1250, sheet="Scenario B", period="fy24"),
    ]
    match = run(deck, values, Config(require_label_evidence=True))[0]
    assert match.status == "ambiguous" and match.model is None
    assert {candidate.ref for candidate in match.candidates} == {value.ref for value in values}


def test_strict_review_leaves_value_only_coincidences_unresolved():
    match = run([dn("Mystery metric", 1234.5, "$1,234.5m", decimals=1)],
                [mv("Foo bar", 1234.5)], Config(require_label_evidence=True))[0]
    assert match.status == "unmatched" and match.model is None


def test_value_only_unique_value():
    r = run([dn("Mystery metric", 1234.5, "$1,234.5m", decimals=1)], [mv("Foo bar", 1234.5)])
    m = r[0]
    assert m.status == "matched_value_only" and m.method == "value_only" and m.confidence <= 0.45


def test_value_only_needs_enough_digits():
    r = run([dn("Mystery metric", 12, "$12m")], [mv("Foo bar", 12)])
    assert r[0].status == "unmatched"


def test_value_only_requires_one_distinct_value():
    vals = [mv("Foo bar", 1234.48, addr="B2"), mv("Baz qux", 1234.52, addr="B3")]
    r = run([dn("Mystery metric", 1234.5, "$1,234.5m", decimals=1)], vals)
    assert r[0].status == "unmatched"


def test_value_only_never_a_mismatch():
    r = run([dn("Mystery metric", 9999, "$9,999m")], [mv("Foo bar", 1234.5)])
    assert r[0].status == "unmatched"


def test_value_only_respects_explicit_period():
    vals = [mv("Foo bar", 1234, col_label="FY23", period="fy23")]
    r = run([dn("Mystery metric", 1234, "$1,234m", period="fy24")], vals)
    assert r[0].status == "unmatched"


# --------------------------------------------------------------------------- staleness


def test_staleness_same_address():
    new = [mv("Revenue", 1300, addr="C5")]
    old = [mv("Revenue", 1200, addr="C5")]
    r = run([dn("Revenue", 1200, "$1,200m")], new, old_model_values=old)
    m = r[0]
    assert m.status == "stale" and m.old_model is old[0] and m.model is new[0]


def test_staleness_by_labels_when_rows_moved():
    new = [mv("Revenue", 1300, addr="C9", col_label="FY24", period="fy24")]
    old = [mv("Revenue", 1200, addr="C5", col_label="FY24", period="fy24"),
           mv("EBITDA", 1200, addr="C6", col_label="FY24", period="fy24")]
    r = run([dn("Revenue FY24", 1200, "$1,200m", period="fy24")], new, old_model_values=old)
    assert r[0].status == "stale" and r[0].old_model.cell_address == "C5"


def test_not_stale_when_old_value_also_differs():
    new = [mv("Revenue", 1300, addr="C5")]
    old = [mv("Revenue", 1250, addr="C5")]
    r = run([dn("Revenue", 1200, "$1,200m")], new, old_model_values=old)
    assert r[0].status == "mismatch" and r[0].old_model is None


def test_matched_numbers_untouched_by_old_model():
    new = [mv("Revenue", 1200, addr="C5")]
    old = [mv("Revenue", 1100, addr="C5")]
    r = run([dn("Revenue", 1200, "$1,200m")], new, old_model_values=old)
    assert r[0].status == "matched"


def test_staleness_address_with_different_label_is_not_trusted():
    new = [mv("Revenue", 1300, addr="C5")]
    old = [mv("Capex", 1200, addr="C5")]
    r = run([dn("Revenue", 1200, "$1,200m")], new, old_model_values=old)
    assert r[0].status == "mismatch"


# --------------------------------------------------------------------------- LLM wiring


def test_ambiguous_resolved_by_llm(monkeypatch):
    seen = {}

    def fake(entries, config, warnings=None):
        seen["entries"] = entries
        return {entries[0]["id"]: entries[0]["candidates"][1]["ref"]}

    monkeypatch.setattr("numcheck.matching.pipeline.resolve_ambiguous", fake)
    vals = [mv("Revenue", 1200, sheet="A", addr="B2"), mv("Revenue", 1300, sheet="B", addr="B2")]
    r = run([dn("Revenue", 999, "$999m", context="Revenue reached $999m in the year")], vals, Config(llm="openai"))
    m = r[0]
    assert m.method == "llm" and m.status == "mismatch" and m.model.sheet_name == "B"
    assert m.confidence <= 0.75
    blob = str(seen["entries"])
    assert "999" not in blob and "1300" not in blob and "1200" not in blob and "#" in blob


def test_ambiguous_stays_when_llm_off():
    vals = [mv("Revenue", 1200, sheet="A", addr="B2"), mv("Revenue", 1300, sheet="B", addr="B2")]
    r = run([dn("Revenue", 999, "$999m")], vals, Config(llm="off"))
    assert r[0].status == "ambiguous"


def test_llm_failure_adds_warning_and_keeps_ambiguous(monkeypatch):
    class Boom:
        name = "openai"

        def resolve(self, entries, model, timeout=60.0):
            raise RuntimeError("network down")

    monkeypatch.setattr("numcheck.llm.factory.get_provider", lambda cfg: Boom())
    vals = [mv("Revenue", 1200, sheet="A", addr="B2"), mv("Revenue", 1300, sheet="B", addr="B2")]
    warns: list[ReviewWarning] = []
    r = run([dn("Revenue", 999, "$999m")], vals, Config(llm="openai"), warnings=warns)
    assert r[0].status == "ambiguous"
    assert any(w.code == "llm_failed" for w in warns)


# --------------------------------------------------------------------------- index + performance


def test_index_fills_label_norm_and_metric_key():
    cells = [mv("Net Sales ($m)", 1, col_label="FY24", named="Rev_FY24")]
    idx = ModelIndex(cells, Config())
    assert cells[0].metric_key == "revenue" and "revenue" in cells[0].label_norm
    assert idx.periods[0] == "fy24"


def test_performance_large_model():
    rng = random.Random(7)
    cfg = Config()
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo",
             "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform", "victor",
             "whiskey", "xray", "yankee", "zulu"]
    metrics = ["Revenue", "EBITDA", "Net income", "Capex", "Free cash flow", "Gross profit", "Opex", "Net debt"]
    values: list[ModelValue] = []
    labels = set()
    r = 0
    while len(values) < 100_000:
        r += 1
        label = f"{rng.choice(metrics)} {rng.choice(words)} {rng.choice(words)} {rng.choice(words)}"
        if label in labels:
            continue
        labels.add(label)
        for c in range(2, 52):
            values.append(ModelValue(f"Sheet{r % 7}", f"{chr(64 + c % 26 + 1)}{r}", rng.uniform(1, 9999),
                                     row_label=label, col_label=f"FY{c % 10 + 15}", unit="currency", scale="m",
                                     scale_known=True, row=r, col=c, period=f"fy{c % 10 + 15}"))
    decks = []
    for k in range(200):
        base = values[rng.randrange(len(values))]
        decks.append(dn(f"{base.row_label} {base.col_label}", round(base.value, 1), f"${base.value:,.1f}m",
                        period=base.period, decimals=1, shape=f"s{k % 20}"))
    t0 = time.perf_counter()
    res = run(decks, values, cfg)
    elapsed = time.perf_counter() - t0
    assert elapsed < 15, elapsed
    assert sum(m.status == "matched" for m in res) >= 190


# --------------------------------------------------------------------------- near-label handling


def test_unmentioned_company_column_is_penalised():
    vals = [mv("Revenue", 1200, sheet="Operating", addr="C5", col_label="FY24", period="fy24"),
            mv("Revenue", 1200, sheet="Comps", addr="B4", col_label="Acme Corp", period="fy24"),
            mv("Revenue", 800, sheet="Comps", addr="C4", col_label="Peer One", period="fy24")]
    r = run([dn("Revenue", 999, "$999m", period="fy24")], vals)
    assert r[0].status == "mismatch" and r[0].model.sheet_name == "Operating"


def test_company_named_in_deck_label_selects_that_column():
    vals = [mv("Revenue", 1200, sheet="Comps", addr="B4", col_label="Acme Corp", period="fy24"),
            mv("Revenue", 800, sheet="Comps", addr="C4", col_label="Peer One", period="fy24")]
    r = run([dn("Acme Corp Revenue", 1200, "$1,200m", period="fy24")], vals)
    assert r[0].status == "matched" and r[0].model.col_label == "Acme Corp"


def test_near_label_cell_that_matches_prevents_false_alarm():
    vals = [mv("Revenue", 1200, sheet="Operating", addr="C5", col_label="FY24", period="fy24"),
            mv("Revenue", 1000, sheet="Comps", addr="B4", col_label="Acme Corp", period="fy24")]
    r = run([dn("Revenue", 1000, "$1,000m", period="fy24")], vals)
    assert r[0].status == "matched" and r[0].model.sheet_name == "Comps" and r[0].confidence <= 0.6


def test_near_rule_never_bridges_strict_qualifiers():
    vals = [mv("Adjusted EBITDA", 340, addr="B2"), mv("EBITDA", 300, addr="B3")]
    r = run([dn("Adjusted EBITDA", 300, "$300m")], vals)
    assert r[0].status == "mismatch" and r[0].model.cell_address == "B2"
