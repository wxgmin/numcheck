"""End-to-end regressions for the original and visual demo cases."""

import tempfile
from pathlib import Path

from numcheck.config import load_config
from numcheck.demo import build_demo
from numcheck.demo.build_deck import build_deck
from numcheck.demo.build_model import build_model
from numcheck.runner import run_review


def _has(issues, slide, issue_type, value_fragment=None):
    """True if the finding exists as an issue, or folded into a primary issue's ``related``.

    Findings that repeat a model mismatch on the same number (e.g. cross-slide
    inconsistency of a wrong EBITDA) are folded into that mismatch so each number
    surfaces once; they remain part of the result.
    """
    for i in issues:
        if i.slide_number != slide:
            continue
        if value_fragment is not None and value_fragment not in i.deck_value:
            continue
        if i.issue_type.value == issue_type:
            return True
        if any(r.get("issue_type") == issue_type for r in i.related):
            return True
    return False


def test_original_demo_catches_all_planted_errors():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "samples"
        out.mkdir()
        paths = {key: str(out / name) for key, name in (
            ("deck", "deck.pptx"), ("model", "model.xlsx"), ("model_old", "model_old.xlsx")
        )}
        build_deck(paths["deck"])
        build_model(paths["model"])
        build_model(paths["model_old"], implied_ev=5200.0)

        cfg = load_config(None)
        cfg.llm = "off"
        result = run_review(
            paths["deck"],
            [paths["model"]],
            paths["model_old"],
            str(out / "report"),
            annotate=False,
            config=cfg,
        )
        issues = result["issues"]

    # 1. Hardcoded revenue mismatch.
    assert _has(issues, 2, "mismatch", "$2,450m")
    # 2. Sum error in revenue build.
    assert _has(issues, 5, "sum_error", "$2,450m")
    # 3. EBITDA inconsistent across slides (and wrong vs model).
    assert _has(issues, 3, "inconsistent_across_slides", "$540m")
    assert _has(issues, 3, "mismatch", "$540m")
    # 4. EBITDA margin reconcile.
    assert _has(issues, 6, "pct_reconcile", "22.4%")
    # 5. Growth reconcile.
    assert _has(issues, 6, "growth_reconcile", "8.5%")
    # 6. EV unit inconsistency ($bn vs $m).
    assert _has(issues, 7, "unit_period_mismatch", "$5.4bn")
    # 7. FY24 vs CY24 period inconsistency.
    assert _has(issues, 8, "unit_period_mismatch", "$385m")
    # 8. Stale implied EV (matches old model, not new).
    assert _has(issues, 4, "stale", "$5.2bn")
    # 9. Unmatched TAM.
    assert _has(issues, 9, "unmatched", "$48bn")
    # 10. Footnote period drift.
    assert _has(issues, 10, "unit_period_mismatch", "2025")

    # The rounding control ($6.2bn vs $6,234m) must NOT be flagged.
    assert not any("$6.2bn" in i.deck_value for i in issues)
    # No ambiguity should remain with the deterministic matcher.
    assert not any(i.issue_type.value == "ambiguous" for i in issues)


def test_keystone_visual_demo_traces_every_planted_discrepancy(tmp_path):
    paths = build_demo(tmp_path / "keystone")
    cfg = load_config(None)
    cfg.llm = "off"
    cfg.require_label_evidence = True
    result = run_review(
        paths["deck"], [paths["model"]], paths["model_old"], str(tmp_path / "report"),
        config=cfg, annotate=True,
    )
    issues = result["issues"]
    assert {
        (issue.slide_number, issue.issue_type.value, issue.deck_value, issue.expected_value,
         issue.source_cell.partition("] ")[2])
        for issue in issues if issue.severity.value in {"high", "medium"}
    } == {
        (2, "mismatch", "2.8x", "2.5x", "Cap!C15"),
        (2, "mismatch", "11.6%", "9.6%", "Segments!F11"),
        (3, "mismatch", "1,056.6", "1,065.6", "Segments!E10"),
        (3, "stale", "275.0", "269.9", "Adj EBITDA!G11"),
        (4, "mismatch", "23.6%", "21.6%", "Segments!F21"),
        (4, "mismatch", "$348.1m", "$384.1m", "Segments!F8"),
        (5, "mismatch", "4.6", "6.4", "Adj EBITDA!F9"),
        (6, "mismatch", "13.9x", "12.9x", "Comps!K9"),
        (8, "mismatch", "2,854", "2,754", "DCF!D39"),
        (9, "mismatch", "3,740", "3,470", "Valuation!G7"),
        (10, "mismatch", "724.0", "742.0", "Cap!C10"),
    }
    stale = next(issue for issue in issues if issue.issue_type.value == "stale")
    assert stale.details["old_cell"] == "[Keystone_Model_v3_prior.xlsx] Adj EBITDA!G11"

    verified_sources = {(m.deck.slide_number, m.deck.raw_text, m.model.sheet_name, m.model.cell_address)
                        for m in result.matches if m.status == "matched" and m.model is not None}
    assert {
        (1, "$1,168m", "Segments", "F10"),
        (1, "$253.0m", "Adj EBITDA", "F11"),
        (1, "21.7%", "Adj EBITDA", "F12"),
        (8, "709.8", "DCF", "C23"),
        (8, "2,973.3", "DCF", "C26"),
        (8, "(623.5)", "DCF", "C27"),
        (8, "$27.78", "DCF", "C30"),
        (10, "2.9x", "Cap", "C14"),
    } <= verified_sources
    assert {m.model.cell_address for m in result.matches if m.deck.slide_number == 7 and m.deck.source.value == "table"
            and m.deck.unit == "multiple" and m.status == "matched"} == {f"F{row}" for row in (5, 6, 7, 8, 9, 10, 12)}
