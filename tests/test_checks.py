"""Tests for issue building, deck-internal checks, severity and coverage (workstream D)."""

from __future__ import annotations

import itertools

import pytest

from numcheck.checks import (
    IssueCollector,
    assign_severity,
    build_issues,
    canonical_of,
    compute_coverage,
    finalize_issues,
    run_checks,
    severity_for,
    values_close,
)
from numcheck.checks.severity import is_key_metric
from numcheck.config import Config
from numcheck.matching.normalizer import Comparison
from numcheck.models import (
    DeckExtraction,
    DeckNumber,
    Issue,
    IssueType,
    Match,
    ModelValue,
    Severity,
    SlideInfo,
    Source,
)

CFG = Config()
_uid = itertools.count(1)


def num(text, value, *, slide=1, shape="s1", key="", unit="currency", scale="m", decimals=0, period="fy24",
        source=Source.TEXT_BOX, row=-1, col=-1, row_label="", col_label="", label="", chart_type="",
        series=-1, qualifiers=(), **kw) -> DeckNumber:
    return DeckNumber(
        slide_number=slide, shape_id=shape, source=source, raw_text=text, value=value, unit=unit, scale=scale,
        scale_known=True, period=period, label=label or row_label or key, metric_key=key, decimals=decimals,
        table_row=row, table_col=col, row_label=row_label, col_label=col_label, chart_type=chart_type,
        series_index=series, qualifiers=list(qualifiers), uid=f"u{next(_uid)}", paragraph_index=0,
        char_start=0, char_end=len(text), **kw,
    )


def pct(text, value, **kw):
    kw.setdefault("decimals", 1 if "." in text else 0)
    return num(text, value, unit="percent", scale="", **kw)


def mult(text, value, **kw):
    return num(text, value, unit="multiple", scale="", decimals=1, **kw)


def run(nums, slides=None, footnotes=None, matches=None):
    col = IssueCollector(CFG)
    run_checks(nums, matches or [], slides or [], footnotes or [], CFG, col)
    return col.issues


def of_type(issues, t):
    return [i for i in issues if i.issue_type == t]


def table(rows, col=1, slide=1, shape="t1", key_prefix="", **kw):
    """Single-column table: [(row_label, text, value)]."""
    out = []
    for r, (label, text, value) in enumerate(rows, start=1):
        out.append(num(text, value, source=Source.TABLE, row=r, col=col, row_label=label, slide=slide,
                       shape=shape, key=label.lower(), decimals=1 if "." in text else 0, **kw))
    return out


# --------------------------------------------------------------------------- sums
class TestSums:
    def test_rounded_components_ok(self):
        nums = table([("Segment A", "1.4", 1.4), ("Segment B", "1.4", 1.4), ("Total", "2.9", 2.9)])
        assert of_type(run(nums), IssueType.SUM_ERROR) == []

    def test_wrong_total_flagged(self):
        nums = table([("Segment A", "100", 100), ("Segment B", "200", 200), ("Total", "350", 350)])
        issues = of_type(run(nums), IssueType.SUM_ERROR)
        assert len(issues) == 1 and "300" in issues[0].expected_value and issues[0].deck_value == "350"

    def test_pnl_two_subtotals_ok(self):
        nums = table([
            ("Product revenue", "100", 100), ("Service revenue", "50", 50), ("Total revenue", "150", 150),
            ("COGS", "40", 40), ("Opex", "30", 30), ("Total costs", "70", 70),
        ])
        # grand total of the subtotals' components
        nums += table([("Total", "220", 220)], shape="t1")
        for n in nums[-1:]:
            n.table_row = 7
        assert of_type(run(nums), IssueType.SUM_ERROR) == []

    def test_subtotals_then_grand_total_rollup(self):
        nums = table([
            ("A", "10", 10), ("B", "20", 20), ("Subtotal 1", "30", 30),
            ("C", "5", 5), ("D", "15", 15), ("Subtotal 2", "20", 20), ("Grand total", "50", 50),
        ])
        assert of_type(run(nums), IssueType.SUM_ERROR) == []
        nums[-1] = num("55", 55, source=Source.TABLE, row=7, col=1, row_label="Grand total", shape="t1",
                       key="grand total")
        assert len(of_type(run(nums), IssueType.SUM_ERROR)) == 1

    def test_less_row_subtracts(self):
        nums = table([("Revenue", "500", 500), ("Less: discounts", "50", 50), ("Net revenue total", "450", 450)])
        assert of_type(run(nums), IssueType.SUM_ERROR) == []

    def test_horizontal_total(self):
        cells = [
            num("100", 100, source=Source.TABLE, row=1, col=1, row_label="Sales", col_label="North", shape="t"),
            num("200", 200, source=Source.TABLE, row=1, col=2, row_label="Sales", col_label="South", shape="t"),
            num("300", 300, source=Source.TABLE, row=1, col=3, row_label="Sales", col_label="Total", shape="t"),
        ]
        assert of_type(run(cells), IssueType.SUM_ERROR) == []
        cells[2] = num("320", 320, source=Source.TABLE, row=1, col=3, row_label="Sales", col_label="Total", shape="t")
        assert len(of_type(run(cells), IssueType.SUM_ERROR)) == 1

    def test_pie_must_sum_to_100(self):
        good = [pct(f"{v}%", v, source=Source.CHART, chart_type="pie", series=0, shape="c", key=f"seg{n}")
                for n, v in enumerate((50, 30, 20))]
        assert run(good) == []
        bad = [pct(f"{v}%", v, source=Source.CHART, chart_type="pie", series=0, shape="c", key=f"seg{n}")
               for n, v in enumerate((50, 30, 17))]
        issues = of_type(run(bad), IssueType.SUM_ERROR)
        assert len(issues) == 1 and "97" in issues[0].message

    def test_percent_of_total_column(self):
        cells = [num(f"{v}%", v, unit="percent", scale="", source=Source.TABLE, row=r, col=2, shape="t",
                     row_label=f"Seg{r}", col_label="% of total") for r, v in enumerate((40, 35, 20), start=1)]
        assert len(of_type(run(cells), IssueType.SUM_ERROR)) == 1


# --------------------------------------------------------------------------- ratios
class TestRatios:
    def test_margin_ok_with_rounding(self):
        nums = [num("$300m", 300, key="ebitda"), num("$1,180m", 1180, key="revenue"),
                pct("25%", 25, key="ebitda margin")]
        assert run(nums) == []

    def test_wrong_margin_flagged(self):
        nums = [num("$300m", 300, key="ebitda"), num("$1,000m", 1000, key="revenue"),
                pct("40%", 40, key="ebitda margin")]
        issues = of_type(run(nums), IssueType.PCT_RECONCILE)
        assert len(issues) == 1 and issues[0].expected_value == "30%"

    def test_operating_margin_uses_ebit_not_ebitda(self):
        nums = [num("$300m", 300, key="ebitda"), num("$200m", 200, key="ebit"),
                num("$1,000m", 1000, key="revenue"), pct("20.0%", 20.0, key="operating margin")]
        assert run(nums) == []

    def test_ebit_margin_not_confused_with_ebitda(self):
        nums = [num("$300m", 300, key="ebitda"), num("$1,000m", 1000, key="revenue"),
                pct("20.0%", 20.0, key="ebit margin")]
        assert run(nums) == []   # no EBIT operand -> nothing to reconcile

    def test_revenue_growth_is_not_revenue(self):
        nums = [num("$300m", 300, key="ebitda"), num("10%", 10, key="revenue growth", unit="percent", scale=""),
                pct("30.0%", 30.0, key="ebitda margin")]
        assert of_type(run(nums), IssueType.PCT_RECONCILE) == []

    def test_ev_ebitda(self):
        ok = [num("$3,000m", 3000, key="enterprise value"), num("$300m", 300, key="ebitda"),
              mult("10.0x", 10.0, key="ev ebitda")]
        assert run(ok) == []
        bad = [num("$3,000m", 3000, key="enterprise value"), num("$300m", 300, key="ebitda"),
               mult("12.0x", 12.0, key="ev ebitda")]
        issues = of_type(run(bad), IssueType.RATIO_RECONCILE)
        assert len(issues) == 1 and issues[0].expected_value == "10.0x"

    def test_premium(self):
        ok = [num("$12.00", 12, key="offer price", scale="", decimals=2),
              num("$10.00", 10, key="unaffected price", scale="", decimals=2), pct("20%", 20, key="premium")]
        assert run(ok) == []
        bad = [num("$12.00", 12, key="offer price", scale="", decimals=2),
               num("$10.00", 10, key="unaffected price", scale="", decimals=2), pct("30%", 30, key="premium")]
        assert len(of_type(run(bad), IssueType.RATIO_RECONCILE)) == 1

    def test_zero_denominator_skipped(self):
        nums = [num("$300m", 300, key="ebitda"), num("$0m", 0, key="revenue"), pct("25%", 25, key="ebitda margin")]
        assert run(nums) == []

    def test_different_slides_not_compared(self):
        nums = [num("$300m", 300, key="ebitda", slide=1), num("$1,000m", 1000, key="revenue", slide=1),
                pct("90%", 90, key="ebitda margin", slide=2)]
        assert of_type(run(nums), IssueType.PCT_RECONCILE) == []


# --------------------------------------------------------------------------- growth
class TestGrowth:
    def base(self, g, **kw):
        return [num("$1,000m", 1000, key="revenue", period="fy23"), num("$1,100m", 1100, key="revenue", period="fy24"),
                pct(g, float(g.rstrip("%")), key="revenue growth", period="fy24")]

    def test_growth_ok(self):
        assert run(self.base("10%")) == []

    def test_growth_wrong(self):
        issues = of_type(run(self.base("15%")), IssueType.GROWTH_RECONCILE)
        assert len(issues) == 1 and issues[0].expected_value == "10%"

    def test_cagr_ok_and_wrong(self):
        def nums(v):
            return [num("$1,000m", 1000, key="revenue", period="fy22"), num("$1,210m", 1210, key="revenue", period="fy24"),
                    pct(f"{v}%", v, key="revenue cagr", period="", label="Revenue CAGR FY22-FY24", decimals=1)]
        assert run(nums(10.0)) == []
        assert len(of_type(run(nums(15.0)), IssueType.GROWTH_RECONCILE)) == 1

    def test_cagr_without_span_skipped(self):
        nums = [num("$1,000m", 1000, key="revenue", period="fy22"), num("$1,210m", 1210, key="revenue", period="fy24"),
                pct("15.0%", 15.0, key="revenue cagr", period="fy24", label="Revenue CAGR")]
        assert run(nums) == []


# --------------------------------------------------------------------------- cross slide
class TestCrossSlide:
    def test_precision_difference_consistent(self):
        nums = [num("$1.2bn", 1.2, key="revenue", scale="bn", decimals=1, slide=1),
                num("$1,234m", 1234, key="revenue", slide=2)]
        assert of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES) == []

    def test_outlier_flagged_once(self):
        nums = [num("$540m", 540, key="revenue", slide=1), num("$540m", 540, key="revenue", slide=2),
                num("$512m", 512, key="revenue", slide=3)]
        issues = of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES)
        assert len(issues) == 1 and issues[0].slide_number == 3
        assert {r["slide_number"] for r in issues[0].related} == {1, 2}
        assert "$540m" in issues[0].message and "$512m" in issues[0].message

    def test_model_verified_value_is_reference(self):
        a = num("$540m", 540, key="revenue", slide=1)
        b = num("$512m", 512, key="revenue", slide=2)
        mv = ModelValue("S", "A1", 512e6)
        issues = of_type(run([a, b], matches=[Match(b, mv, "matched")]), IssueType.INCONSISTENT_ACROSS_SLIDES)
        assert len(issues) == 1 and issues[0].slide_number == 1

    def test_percent_vs_currency_never_compared(self):
        nums = [num("$540m", 540, key="revenue", slide=1), pct("54%", 54, key="revenue", slide=2)]
        assert of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES) == []

    def test_unnamed_skipped_and_different_periods(self):
        nums = [num("$5m", 5, key="unnamed", slide=1), num("$9m", 9, key="unnamed", slide=2),
                num("$5m", 5, key="revenue", period="fy23", slide=1), num("$9m", 9, key="revenue", slide=2)]
        assert of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES) == []

    def test_qualifiers_separate_groups(self):
        nums = [num("$540m", 540, key="ebitda", slide=1, qualifiers=["adjusted"]),
                num("$500m", 500, key="ebitda", slide=2)]
        assert of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES) == []

    def test_named_entities_separate_groups(self):
        """'Segment 1 revenue' and 'Segment 2 revenue' are different facts; 'Project Atlas' in a title too."""
        nums = [num("$100m", 100, key="revenue", period="", slide=1, context="Segment 1 revenue of $100m"),
                num("$102m", 102, key="revenue", period="", slide=2, context="Segment 2 revenue of $102m"),
                num("$80m", 80, key="ebitda", slide=3, slide_context="Project Atlas - overview"),
                num("$95m", 95, key="ebitda", slide=4, slide_context="Project Borealis - overview")]
        assert of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES) == []

    def test_periodless_per_entity_values_not_compared(self):
        """Every slide shows a different period-less 'EBITDA' (one per portfolio company): no consensus, no flag."""
        nums = [num(f"${v}m", v, key="ebitda", period="", slide=s) for s, v in ((1, 20), (2, 23), (3, 25), (4, 31))]
        assert of_type(run(nums), IssueType.INCONSISTENT_ACROSS_SLIDES) == []


# --------------------------------------------------------------------------- units / periods
class TestUnitsPeriods:
    def test_fy_cy_conflict(self):
        nums = [num("$10m", 10, key="net income", period="fy24", slide=1),
                num("$10m", 10, key="net income", period="fy24", slide=2),
                num("$10m", 10, key="net income", period="cy24", slide=3)]
        issues = of_type(run(nums), IssueType.UNIT_PERIOD_MISMATCH)
        assert len(issues) == 1 and "'net income' uses inconsistent period label 'cy24'" in issues[0].message
        assert issues[0].slide_number == 3

    def test_ltm_ntm_identical_values(self):
        nums = [num("$10m", 10, key="ebitda", period="ltm", slide=1), num("$10m", 10, key="ebitda", period="ntm", slide=2)]
        assert len(of_type(run(nums), IssueType.UNIT_PERIOD_MISMATCH)) == 1
        nums[1] = num("$12m", 12, key="ebitda", period="ntm", slide=2)
        assert run(nums) == [] or of_type(run(nums), IssueType.UNIT_PERIOD_MISMATCH) == []

    def test_scale_style_low(self):
        nums = [num("$1.2bn", 1.2, key="revenue", scale="bn", decimals=1, slide=1),
                num("$1,200m", 1200, key="revenue", slide=2), num("$1,200m", 1200, key="revenue", slide=3)]
        issues = of_type(run(nums), IssueType.UNIT_PERIOD_MISMATCH)
        assert len(issues) == 1 and "'bn' here but 'm' elsewhere" in issues[0].message
        assert finalize_issues(issues, CFG)[0].severity == Severity.LOW


# --------------------------------------------------------------------------- footnotes
class TestFootnotes:
    def slide_nums(self):
        return [num("$10m", 10, key="revenue", period="fy24"), num("$12m", 12, key="ebitda", period="fy24")]

    def test_filed_year_not_flagged(self):
        fn = [{"slide_number": 1, "text": "Source: Company 10-K filed March 2025"}]
        assert of_type(run(self.slide_nums(), footnotes=fn), IssueType.UNIT_PERIOD_MISMATCH) == []

    def test_data_period_conflict_flagged(self):
        fn = [{"slide_number": 1, "text": "Source: Company filings, Q3 2025."}]
        issues = of_type(run(self.slide_nums(), footnotes=fn), IssueType.UNIT_PERIOD_MISMATCH)
        assert len(issues) == 1 and "'2025'" in issues[0].message and "fy24" in issues[0].message

    def test_matching_year_ok_and_slide_sources(self):
        s = SlideInfo(1, sources=["Source: Company filings, FY2024 figures"])
        assert of_type(run(self.slide_nums(), slides=[s]), IssueType.UNIT_PERIOD_MISMATCH) == []
        s = SlideInfo(1, sources=["FY2023 figures"])
        assert len(of_type(run(self.slide_nums(), slides=[s]), IssueType.UNIT_PERIOD_MISMATCH)) == 1

    def test_as_of_dates_differ(self):
        slides = [SlideInfo(1, as_of_dates=["2025-03-31"]), SlideInfo(2, as_of_dates=["2025-06-30"])]
        issues = of_type(run([], slides=slides), IssueType.FOOTNOTE)
        assert len(issues) == 1 and "2025-03-31" in issues[0].message and "2025-06-30" in issues[0].message
        slides = [SlideInfo(1, as_of_dates=["2025-03-31"]), SlideInfo(2, as_of_dates=["2025-03-31"])]
        assert run([], slides=slides) == []


# --------------------------------------------------------------------------- build_issues
def cmp(**kw) -> Comparison:
    base = dict(ok=False, dimension="currency", deck_value=2.5e8, model_value=2.4e8, tolerance=5e5,
                difference=1e7)
    base.update(kw)
    return Comparison(**base)


def build(m: Match, slides=None) -> list[Issue]:
    col = IssueCollector(CFG)
    build_issues([m], CFG, col, slides)
    return col.issues


def model(**kw) -> ModelValue:
    base = dict(sheet_name="IS", cell_address="B5", value=240.0, unit="currency", scale="m", workbook="m.xlsx",
                row_label="Revenue", col_label="FY24")
    base.update(kw)
    return ModelValue(**base)


class TestBuildIssues:
    d = lambda self: num("$250m", 250, key="revenue")  # noqa: E731

    def test_mismatch(self):
        i = build(Match(self.d(), model(), "mismatch", comparison=cmp(), confidence=0.9, method="label"))
        assert [x.issue_type for x in i] == [IssueType.MISMATCH]
        x = i[0]
        assert x.expected_value == "$240m" and x.suggestion == "$240m" and "IS!B5" in x.source_cell
        assert x.deck_value == "$250m" and x.check == "model_match" and x.deck_uid and x.char_end == 5
        assert x.details["method"] == "label" and x.model_label == "Revenue / FY24"

    def test_sign_and_scale(self):
        assert build(Match(self.d(), model(), "mismatch", comparison=cmp(sign_flip=True)))[0].issue_type == IssueType.SIGN_ERROR
        assert build(Match(self.d(), model(), "mismatch", comparison=cmp(scale_slip="1000x")))[0].issue_type == IssueType.SCALE_ERROR

    def test_source_error(self):
        mv = model(is_error=True, error_text="#DIV/0!", value=float("nan"))
        i = build(Match(self.d(), mv, "mismatch", comparison=None))
        assert i[0].issue_type == IssueType.SOURCE_ERROR and "#DIV/0!" in i[0].message

    def test_period_note(self):
        i = build(Match(self.d(), model(), "mismatch", comparison=cmp(), note="value equals FY23 cell IS!A5"))
        assert {x.issue_type for x in i} == {IssueType.MISMATCH, IssueType.PERIOD_AMBIGUOUS}

    def test_stale(self):
        old = model(cell_address="C5", value=250.0)
        i = build(Match(self.d(), model(), "stale", comparison=cmp(), old_model=old))
        assert len(i) == 1 and i[0].issue_type == IssueType.STALE
        assert "previous model" in i[0].message and i[0].related[0]["source_cell"].endswith("IS!C5")
        assert i[0].expected_value == "$240m"

    def test_ambiguous(self):
        c1, c2 = model(cell_address="B5"), model(cell_address="B9", value=260.0)
        i = build(Match(self.d(), None, "ambiguous", candidates=[c1, c2]))
        assert i[0].issue_type == IssueType.AMBIGUOUS and len(i[0].details["candidates"]) == 2

    def test_unmatched(self):
        assert build(Match(self.d(), None, "unmatched"))[0].issue_type == IssueType.UNMATCHED

    def test_rounding_and_truncation(self):
        c = cmp(ok=True, difference=-1e7, rounding_rel=0.08)
        i = build(Match(self.d(), model(), "matched", comparison=c))
        assert i[0].issue_type == IssueType.ROUNDING
        c = cmp(ok=True, difference=0, rounding_rel=0.001, truncated=True)
        i = build(Match(self.d(), model(), "matched", comparison=c))
        assert "truncated" in i[0].message
        assert build(Match(self.d(), model(), "matched", comparison=cmp(ok=True, rounding_rel=0.001))) == []

    def test_nothing_for_value_only_or_skipped(self):
        assert build(Match(self.d(), model(), "matched_value_only", comparison=cmp(ok=True))) == []
        assert build(Match(self.d(), None, "skipped")) == []


# --------------------------------------------------------------------------- severity / finalize
class TestSeverity:
    def mk(self, t, key="capex", **kw) -> Issue:
        return Issue(1, 1, "x", "y", "", t, Severity.LOW, "m", metric_key=key, **kw)

    def test_key_metric_token_matching(self):
        assert is_key_metric("adjusted ebitda", CFG) and is_key_metric("revenue", CFG)
        assert not is_key_metric("revenue", Config(key_metrics=["ev"]))
        assert is_key_metric("ev ebitda", Config(key_metrics=["ev"]))
        assert not is_key_metric("ebitda", Config(key_metrics=["ebit"]))
        assert is_key_metric("ebit", Config(key_metrics=["ebit"]))
        assert not is_key_metric("capex", CFG)

    def test_mismatch_levels(self):
        assert assign_severity(self.mk(IssueType.MISMATCH, "capex", details={"relative_difference": 0.01}), CFG) == Severity.MEDIUM
        assert assign_severity(self.mk(IssueType.MISMATCH, "capex", details={"relative_difference": 0.2}), CFG) == Severity.HIGH
        assert assign_severity(self.mk(IssueType.MISMATCH, "revenue"), CFG) == Severity.HIGH

    def test_other_types(self):
        assert assign_severity(self.mk(IssueType.UNMATCHED, "revenue"), CFG) == Severity.MEDIUM
        assert assign_severity(self.mk(IssueType.UNMATCHED, "capex"), CFG) == Severity.LOW
        assert assign_severity(self.mk(IssueType.AMBIGUOUS), CFG) == Severity.MEDIUM
        assert assign_severity(self.mk(IssueType.ROUNDING, "revenue"), CFG) == Severity.LOW
        assert assign_severity(self.mk(IssueType.PCT_RECONCILE, "ebitda margin"), CFG) == Severity.MEDIUM
        assert assign_severity(self.mk(IssueType.FOOTNOTE), CFG) == Severity.LOW

    def test_downgrade(self):
        assert assign_severity(self.mk(IssueType.MISMATCH, "revenue", confidence=0.4), CFG) == Severity.MEDIUM
        s = [SlideInfo(1, hidden=True)]
        assert assign_severity(self.mk(IssueType.MISMATCH, "revenue"), CFG, s) == Severity.MEDIUM

    def test_severity_for(self):
        assert severity_for(IssueType.MISMATCH, "revenue", CFG) == Severity.HIGH
        assert severity_for(IssueType.MISMATCH, "capex", CFG) == Severity.MEDIUM


class TestFinalize:
    def test_merge_fold_sort_renumber(self):
        d1 = num("$540m", 540, key="revenue", slide=3)
        d2 = num("$300m", 300, key="capex", slide=1)
        col = IssueCollector(CFG)
        build_issues([Match(d1, model(), "mismatch", comparison=cmp(), confidence=0.9),
                      Match(d2, None, "unmatched")], CFG, col)
        # cross-slide flag on the same number as the model mismatch
        col.add(3, "$540m", "$512m", "", IssueType.INCONSISTENT_ACROSS_SLIDES, "inconsistent",
                metric_key="revenue", shape_id=d1.shape_id, deck_uid=d1.uid)
        out = finalize_issues(col.issues, CFG)
        assert [i.id for i in out] == [1, 2]
        assert len(out) == 2
        assert out[0].issue_type == IssueType.MISMATCH and out[0].slide_number == 3
        assert out[0].related and out[0].related[-1]["issue_type"] == "inconsistent_across_slides"
        assert out[0].severity == Severity.HIGH and out[1].severity == Severity.LOW

    def test_stable_order_by_slide(self):
        col = IssueCollector(CFG)
        for s in (5, 2, 9):
            col.add(s, "1", "", "", IssueType.UNMATCHED, "u", metric_key="capex", deck_uid=f"x{s}")
        assert [i.slide_number for i in finalize_issues(col.issues, CFG)] == [2, 5, 9]


# --------------------------------------------------------------------------- coverage
def test_compute_coverage():
    a = num("$1m", 1, slide=1, key="a")
    b = num("$2m", 2, slide=1, key="b", source=Source.TABLE)
    c = num("$3m", 3, slide=2, key="c", source=Source.CHART)
    e = num("$4m", 4, slide=2, key="e")
    ms = [Match(a, model(), "matched"), Match(b, model(), "matched_value_only"), Match(c, model(), "mismatch"),
          Match(e, None, "unmatched")]
    skipped = DeckNumber(1, "s", Source.TEXT_BOX, "2024", 2024, skip_reason="year")
    ext = DeckExtraction(skipped=[skipped, skipped], slides=[SlideInfo(1, "Intro"), SlideInfo(2, "Fin"),
                                                             SlideInfo(3, "Empty")])
    cov = compute_coverage(ms, ext)
    assert (cov["checkable"], cov["matched"], cov["matched_value_only"], cov["mismatch"], cov["unmatched"]) == (4, 1, 1, 1, 1)
    assert cov["verified"] == 1 and cov["verified_pct"] == 25.0
    assert cov["skipped"] == {"year": 2}
    assert cov["by_source"]["table"] == {"checkable": 1, "verified": 0}
    assert cov["by_source"]["chart"] == {"checkable": 1, "verified": 0}
    ps = {p["slide_number"]: p for p in cov["per_slide"]}
    assert ps[1]["checkable"] == 2 and ps[1]["verified"] == 1 and ps[2]["verified"] == 0 and ps[3]["checkable"] == 0
    assert ps[1]["title"] == "Intro" and ps[1]["issues"] == 0


def test_helpers():
    assert values_close(100, 101, 0.02) and not values_close(100, 110, 0.02)
    assert canonical_of(num("$1.2bn", 1.2, scale="bn"), CFG) == ("currency", pytest.approx(1.2e9))
    assert canonical_of(num("150bps", 150, unit="bps", scale=""), CFG) == ("percent", pytest.approx(1.5))
