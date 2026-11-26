"""Report writers: HTML, Excel, JSON and the annotated deck."""

from __future__ import annotations

import json

import pytest
from openpyxl import load_workbook
from pptx import Presentation
from pptx.util import Emu, Inches

from numcheck.models import (
    DeckNumber,
    Issue,
    IssueType,
    Match,
    ModelValue,
    ReviewResult,
    ReviewWarning,
    Severity,
    SlideInfo,
    Source,
)
from numcheck.report.annotate_pptx import ISSUES_PER_SUMMARY_SLIDE, annotate_deck
from numcheck.report.excel_report import write_excel_report
from numcheck.report.html_report import render_html, write_html_report
from numcheck.report.json_report import write_json_report


def _issue(i: int, slide: int = 1, sev=Severity.HIGH, typ=IssueType.MISMATCH, **kw) -> Issue:
    base = dict(
        id=i, slide_number=slide, deck_value="22.4%", expected_value="21.2%", source_cell="[model.xlsx] IS!D12",
        issue_type=typ, severity=sev, message=f"EBITDA margin shows 22.4% but the model has 21.20% ({i})",
        metric_key="ebitda margin", shape_id="2", confidence=0.93, deck_label="EBITDA margin",
        model_label="EBITDA margin", suggestion="21.2%", difference=1.2, tolerance=0.05, check="model_match",
        related=[{"slide_number": 3, "deck_value": "21.2%"}],
    )
    base.update(kw)
    return Issue(**base)


def make_result(deck_text: str = "EBITDA margin") -> ReviewResult:
    def dn(slide, raw, label, uid):
        return DeckNumber(slide_number=slide, shape_id="2", source=Source.TEXT_BOX, raw_text=raw, value=1.0,
                          label=label, uid=uid)

    mv = ModelValue(sheet_name="IS", cell_address="D12", value=0.212, unit="percent", pct_basis="ratio",
                    workbook="model.xlsx")
    matches = [
        Match(dn(1, "$2,450m", "Revenue FY24", "u1"), mv, "matched", confidence=0.95, method="label"),
        Match(dn(1, "22.4%", deck_text, "u2"), mv, "mismatch", confidence=0.9, method="label"),
        Match(dn(2, "300", "Capex", "u3"), None, "matched_value_only", confidence=0.6, method="value_only"),
        Match(dn(2, "5.0x", "Leverage", "u4"), mv, "stale", confidence=0.8, method="label"),
        Match(dn(3, "12", "Headcount", "u5"), None, "ambiguous", confidence=0.3),
        Match(dn(3, "7%", "Churn", "u6"), None, "unmatched"),
    ]
    issues = [
        _issue(1, 1, Severity.HIGH),
        _issue(2, 2, Severity.MEDIUM, IssueType.STALE, message="Matches the previous model, not the new one"),
        _issue(3, 3, Severity.LOW, IssueType.ROUNDING, difference=None, tolerance=None, suggestion=""),
    ]
    return ReviewResult(
        issues=issues, matches=matches,
        warnings=[
            ReviewWarning("uncalculated_formulas", "model.xlsx: 12 formulas have no cached value. "
                          "Open and save the workbook in Excel, then re-run.", file="model.xlsx", count=12),
            ReviewWarning("images_not_checked", "Slide 4 has 2 images that were not checked.", slide_number=4),
        ],
        slides=[SlideInfo(1, "Summary"), SlideInfo(2, "Financials"), SlideInfo(3, "Valuation")],
        coverage={"checkable": 40, "matched": 30, "matched_value_only": 3, "mismatch": 4, "stale": 1,
                  "ambiguous": 1, "unmatched": 1, "verified": 33, "verified_pct": 82.5, "skipped": {"year": 5},
                  "by_source": {"text_box": 30, "table": 10},
                  "per_slide": [{"slide_number": 1, "title": "Summary", "checkable": 5, "verified": 4, "issues": 1}]},
        elapsed=3.2, deck_numbers=45, model_values=900,
        inputs={"deck": "Project Atlas.pptx", "models": ["model.xlsx"]},
        settings={"llm": "off", "rounding_mode": "round"},
    )


# --------------------------------------------------------------------------- #
# HTML / Excel / JSON
# --------------------------------------------------------------------------- #

def test_html_escapes_deck_text(tmp_path):
    res = make_result()
    res.issues[0].message = 'Label "<script>alert(1)</script>" differs'
    res.issues[0].deck_label = "<script>alert(2)</script>"
    res.matches[1].deck.label = "<script>alert(3)</script>"
    res.inputs["deck"] = "<b>deck</b>.pptx"
    out = tmp_path / "r.html"
    write_html_report(res, out)
    html = out.read_text(encoding="utf-8")
    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;deck" in html
    assert "gradient" not in html.lower()
    assert "http://" not in html and "https://" not in html
    assert "82.5%" in html and "uncalculated_formulas" in html


def test_html_accepts_plain_issue_list_and_clean_verdict():
    assert "Clean" in render_html([])
    assert "1 issues" in render_html([_issue(1)])


def test_excel_formula_injection_is_text(tmp_path):
    res = make_result()
    evil = '=HYPERLINK("http://evil","click")'
    res.issues[0].message = evil
    res.issues[0].deck_label = "+SUM(A1:A2)"
    res.matches[0].deck.label = "@cmd"
    res.warnings[0].message = "-2+3"
    out = tmp_path / "r.xlsx"
    write_excel_report(res, out)
    wb = load_workbook(out, data_only=False)
    assert wb.sheetnames == ["Summary", "Issues", "Coverage", "Warnings"]
    issues = wb["Issues"]
    cells = [c for row in issues.iter_rows() for c in row]
    hit = [c for c in cells if c.value == evil]
    assert hit and all(c.data_type == "s" for c in hit)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                assert c.data_type != "f", f"formula in {ws.title}!{c.coordinate}"
    assert issues.freeze_panes == "A2"
    assert issues.auto_filter.ref
    assert wb["Coverage"].max_row == 1 + len(res.matches)


def test_excel_accepts_issue_list(tmp_path):
    write_excel_report([_issue(1)], tmp_path / "x.xlsx")
    assert load_workbook(tmp_path / "x.xlsx")["Issues"].max_row == 2


def test_json_round_trip(tmp_path):
    res = make_result()
    out = tmp_path / "r.json"
    write_json_report(res, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] >= 2
    assert data["inputs"]["deck"] == "Project Atlas.pptx"
    assert data["coverage"]["verified_pct"] == 82.5
    assert len(data["issues"]) == 3 and len(data["matches"]) == 6
    first = data["issues"][0]
    for key in ("id", "suggestion", "related", "difference", "tolerance", "details", "char_start", "check"):
        assert key in first
    assert data["warnings"][0]["code"] == "uncalculated_formulas"
    assert {m["status"] for m in data["matches"]} >= {"matched", "mismatch", "stale", "ambiguous", "unmatched"}
    row = next(m for m in data["matches"] if m["status"] == "mismatch")
    assert row["source_cell"] == "[model.xlsx] IS!D12" and row["model_value"] == "21.20%"


def test_json_handles_nan(tmp_path):
    res = make_result()
    res.issues[0].difference = float("nan")
    write_json_report(res, tmp_path / "n.json")
    assert json.loads((tmp_path / "n.json").read_text())["issues"][0]["difference"] is None


# --------------------------------------------------------------------------- #
# Annotated deck
# --------------------------------------------------------------------------- #

def _deck(path, widescreen=False):
    prs = Presentation()
    if widescreen:
        prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    blank = prs.slide_layouts[6]
    s1 = prs.slides.add_slide(blank)
    tb = s1.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    p = tb.text_frame.paragraphs[0]
    for text in ("Revenue of ", "$2,450m", " and EBITDA of $300m"):
        p.add_run().text = text
    s1.notes_slide.notes_text_frame.text = "Existing speaker note"
    s2 = prs.slides.add_slide(blank)
    gf = s2.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(5), Inches(1))
    for r in range(2):
        for c in range(2):
            gf.table.cell(r, c).text_frame.paragraphs[0].add_run().text = "10.0%"
    prs.save(path)
    return tb.shape_id, gf.shape_id


def _runs(par):
    return [(r.text, r.font.bold, r.font.underline) for r in par.runs]


@pytest.mark.parametrize("wide", [False, True])
def test_annotate_targets_only_the_run(tmp_path, wide):
    src, out = tmp_path / "in.pptx", tmp_path / "out.pptx"
    tb_id, tbl_id = _deck(src, widescreen=wide)
    issues = [
        _issue(1, 1, shape_id=str(tb_id), deck_value="$2,450m", paragraph_index=0, char_start=11, char_end=18),
        _issue(2, 2, Severity.MEDIUM, shape_id=str(tbl_id), deck_value="10.0%", table_row=1, table_col=1),
        _issue(3, 2, Severity.LOW, shape_id="9999", deck_value=""),
    ]
    failures = annotate_deck(str(src), str(out), ReviewResult(issues=issues))
    assert isinstance(failures, list)
    prs = Presentation(out)
    assert (prs.slide_width, prs.slide_height) == ((12192000, 6858000) if wide else (9144000, 6858000))

    s1 = prs.slides[0]
    tb = next(s for s in s1.shapes if s.shape_id == tb_id)
    runs = _runs(tb.text_frame.paragraphs[0])
    assert runs[0][1:] == (None, None) and runs[2][1:] == (None, None)
    assert runs[1][1:] == (True, True)
    names = [s.name for s in s1.shapes]
    assert "NumCheck marker 1" in names
    marker = next(s for s in s1.shapes if s.name == "NumCheck marker 1")
    assert 0 <= marker.left and marker.left + marker.width <= prs.slide_width
    assert marker.text_frame.text == "1"

    notes = s1.notes_slide.notes_text_frame.text
    assert "Existing speaker note" in notes and "#1" in notes

    tbl = next(s for s in prs.slides[1].shapes if s.has_table).table
    assert tbl.cell(1, 1).text_frame.paragraphs[0].runs[0].font.bold is True
    assert tbl.cell(0, 0).text_frame.paragraphs[0].runs[0].font.bold is None
    # Empty deck_value highlights nothing; unknown shape still gets a corner marker.
    assert "NumCheck marker 3" in [s.name for s in prs.slides[1].shapes]
    assert len(prs.slides) == 3   # two original slides + one summary
    summary = prs.slides[2]
    assert len(summary.placeholders) == 0
    for shp in summary.shapes:
        assert shp.left + shp.width <= prs.slide_width + 1 and shp.top + shp.height <= prs.slide_height + 1


def test_annotate_paginates_summary_and_falls_back_to_value(tmp_path):
    src, out = tmp_path / "in.pptx", tmp_path / "out.pptx"
    tb_id, _ = _deck(src)
    n = ISSUES_PER_SUMMARY_SLIDE * 2 + 1
    issues = [_issue(i, 1, shape_id=str(tb_id), deck_value="$2,450m") for i in range(1, n + 1)]
    annotate_deck(str(src), str(out), issues)
    prs = Presentation(out)
    assert len(prs.slides) == 2 + 3
    tb = next(s for s in prs.slides[0].shapes if s.shape_id == tb_id)
    runs = _runs(tb.text_frame.paragraphs[0])
    assert runs[1][1] is True and runs[0][1] is None


def test_annotate_never_crashes_on_bad_slide(tmp_path):
    src, out = tmp_path / "in.pptx", tmp_path / "out.pptx"
    _deck(src)
    failures = annotate_deck(str(src), str(out), [_issue(1, 99)])
    assert any("99" in f for f in failures)
    assert out.exists()
