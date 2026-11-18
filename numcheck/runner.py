"""End-to-end review orchestration."""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from numcheck.checks import IssueCollector, severity_for
from numcheck.checks.arithmetic import check_growth, check_percentages, check_sums
from numcheck.checks.consistency import check_cross_slide
from numcheck.checks.staleness import check_staleness
from numcheck.checks.units_periods import check_units_periods
from numcheck.config import Config, load_config
from numcheck.extraction.ocr_extractor import extract_deck_ocr
from numcheck.extraction.pptx_extractor import extract_deck, extract_footnotes
from numcheck.extraction.xlsx_extractor import extract_model
from numcheck.matching.pipeline import match_deck_to_model
from numcheck.models import Issue, IssueType, ModelValue, format_model_value
from numcheck.report.annotate_pptx import annotate_deck
from numcheck.report.excel_report import write_excel_report
from numcheck.report.html_report import write_html_report
from numcheck.report.json_report import write_json_report


def run_review(
    deck_path: str,
    model_paths: list[str],
    model_old_path: str | None = None,
    out_dir: str = "report",
    annotate: bool = True,
    config: Config | None = None,
    ocr: bool = False,
) -> dict:
    config = config or load_config()
    start = time.time()

    deck_numbers = extract_deck(deck_path)
    if ocr:
        deck_numbers.extend(extract_deck_ocr(deck_path))
    footnotes = extract_footnotes(deck_path)

    model_values: list[ModelValue] = []
    for p in model_paths:
        model_values.extend(extract_model(p))
    old_model_values = extract_model(model_old_path) if model_old_path else []

    matches = match_deck_to_model(deck_numbers, model_values, config)
    collector = IssueCollector(config)

    for m in matches:
        if m.status == "mismatch":
            collector.add(
                m.deck.slide_number,
                m.deck.raw_text,
                format_model_value(m.model),
                f"{m.model.sheet_name}!{m.model.cell_address}" if m.model else "",
                IssueType.MISMATCH,
                f"'{m.deck.label or m.deck.metric_key}' is {m.deck.raw_text} but model has {format_model_value(m.model)}",
                m.deck.metric_key,
                m.deck.shape_id,
            )
        elif m.status == "unmatched":
            collector.add(
                m.deck.slide_number,
                m.deck.raw_text,
                "",
                "",
                IssueType.UNMATCHED,
                f"No source found for '{m.deck.label or m.deck.metric_key}'",
                m.deck.metric_key,
                m.deck.shape_id,
            )
        elif m.status == "ambiguous":
            collector.add(
                m.deck.slide_number,
                m.deck.raw_text,
                "",
                "",
                IssueType.AMBIGUOUS,
                f"Ambiguous match for '{m.deck.label or m.deck.metric_key}'",
                m.deck.metric_key,
                m.deck.shape_id,
            )

    check_cross_slide(deck_numbers, config, collector)
    check_sums(deck_numbers, config, collector)
    check_percentages(deck_numbers, config, collector)
    check_growth(deck_numbers, config, collector)
    check_units_periods(deck_numbers, config, collector)
    check_staleness(deck_numbers, model_values, old_model_values, config, collector)
    _check_footnote_period(footnotes, deck_numbers, collector)

    issues = _dedupe(collector.issues)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_html_report(issues, out / "report.html")
    write_excel_report(issues, out / "report.xlsx")
    write_json_report(issues, out / "report.json")
    if annotate:
        annotate_deck(deck_path, str(out / "deck_flagged.pptx"), issues)

    elapsed = time.time() - start
    return {
        "issues": issues,
        "elapsed": elapsed,
        "deck_numbers": len(deck_numbers),
        "model_values": len(model_values),
    }


def _check_footnote_period(footnotes: list[dict], deck_numbers, collector: IssueCollector) -> None:
    if not footnotes:
        return
    periods = Counter(d.period for d in deck_numbers if d.period)
    if not periods:
        return
    dominant = periods.most_common(1)[0][0]
    dom_year = _period_year(dominant)
    for fn in footnotes:
        text = fn["text"]
        years = re.findall(r"\b(20\d{2})\b", text)
        conflict = None
        if dom_year:
            for y in years:
                if int(y) != dom_year:
                    conflict = y
                    break
        if conflict:
            collector.add(
                fn["slide_number"],
                text,
                "",
                "",
                IssueType.UNIT_PERIOD_MISMATCH,
                f"Footnote mentions '{conflict}' which conflicts with deck period '{dominant}'",
            )


def _period_year(period: str) -> int | None:
    m = re.match(r"(?:fy|cy)(\d{2})", period or "")
    if m:
        yy = int(m.group(1))
        return 2000 + yy
    return None


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple] = set()
    out: list[Issue] = []
    for i in issues:
        key = (i.slide_number, i.issue_type.value, i.deck_value, i.message)
        if key in seen:
            continue
        seen.add(key)
        i.id = len(out) + 1
        out.append(i)
    return out
