"""End-to-end demo test: all 10 planted errors are caught, control is clean."""

from numcheck.config import load_config
from numcheck.demo import build_demo
from numcheck.runner import run_review


def _has(issues, slide, issue_type, value_fragment=None):
    for i in issues:
        if i.slide_number == slide and i.issue_type.value == issue_type:
            if value_fragment is None or value_fragment in i.deck_value:
                return True
    return False


def test_demo_catches_all_planted_errors(tmp_path):
    out = tmp_path / "samples"
    paths = build_demo(out)

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
