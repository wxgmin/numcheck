"""Shared data models for NumCheck.

This module is the contract between extraction, matching, checks, and reports.
Every field has a default so producers can fill what they know; consumers must
tolerate defaults (e.g. ``decimals == -1`` means "precision unknown").

Conventions
-----------
* ``unit`` / ``dimension`` is one of: ``"currency" | "percent" | "multiple" |
  "bps" | "count" | "none"``. ``"none"`` means *unknown* (a wildcard when
  matching), never "dimensionless".
* ``scale`` is one of ``"" | "k" | "m" | "mm" | "bn" | "b" | "t"``. ``""`` means
  "no scale / raw units" when ``scale_known`` is True, otherwise unknown.
* ``period`` is a canonical period key from :mod:`numcheck.periods`
  (``"fy24"``, ``"cy24"``, ``"y24"``, ``"fy24q3"``, ``"y24h1"``, ``"ltm"``,
  ``"ntm"``, ``"q3"`` …) or ``""`` when unknown.
* Percent values on the deck side are in *points* (``25%`` -> ``25.0``).
  Model percents keep the raw cell value; ``pct_basis`` says whether it is a
  ratio (0.25) or points (25).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Source(str, Enum):
    TEXT_BOX = "text_box"
    TABLE = "table"
    CHART = "chart"
    OCR = "ocr"
    NOTES = "notes"


class IssueType(str, Enum):
    MISMATCH = "mismatch"
    INCONSISTENT_ACROSS_SLIDES = "inconsistent_across_slides"
    SUM_ERROR = "sum_error"
    PCT_RECONCILE = "pct_reconcile"
    GROWTH_RECONCILE = "growth_reconcile"
    RATIO_RECONCILE = "ratio_reconcile"        # multiples, premiums, per-share, etc.
    UNIT_PERIOD_MISMATCH = "unit_period_mismatch"
    STALE = "stale"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    PERIOD_AMBIGUOUS = "period_ambiguous"      # value matches a different period than the slide implies
    SIGN_ERROR = "sign_error"                  # magnitude matches, sign differs
    SCALE_ERROR = "scale_error"                # digits match at a different scale (1,000x)
    ROUNDING = "rounding"                      # valid rounding but coarse/misleading, or truncation
    SOURCE_ERROR = "source_error"              # mapped model cell is an Excel error / uncalculated
    FOOTNOTE = "footnote"                      # source/as-of/footnote inconsistencies


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Ordered statuses a deck number can end in after matching.
MATCH_STATUSES = (
    "matched",             # label-confirmed source cell, value within tolerance
    "matched_value_only",  # no label confirmation; unique value match (low confidence verification)
    "mismatch",            # confident source cell, value outside tolerance
    "stale",               # mismatches new model but equals old model
    "ambiguous",           # several plausible source cells with different values
    "unmatched",           # no plausible source cell
    "skipped",             # deliberately not checked (year, footnote marker, slide number…)
)


@dataclass
class DeckNumber:
    slide_number: int
    shape_id: str
    source: Source
    raw_text: str                 # the token exactly as displayed, stripped of surrounding whitespace
    value: float                  # signed value in displayed units (25% -> 25.0, $1.2bn -> 1.2)
    unit: str = "none"
    scale: str = ""
    period: str = ""
    label: str = ""               # best label for matching (row+col headers, nearest phrase, series+category)
    context: str = ""             # full paragraph / cell / chart description
    chart_series_name: str = ""
    confidence: float = 1.0       # extraction confidence (OCR < 1)
    significant: int = 1          # legacy; prefer ``decimals``
    table_row: int = -1
    table_col: int = -1
    metric_key: str = ""
    # --- precision & semantics ---
    decimals: int = -1            # displayed decimal places; -1 unknown (treat as exact)
    currency: str = ""            # ISO-ish code: USD, EUR, GBP, JPY, CHF, CAD, AUD, ... or "" unknown
    comparator: str = "="         # "=", "~", ">", ">=", "<", "<="
    is_range: bool = False
    range_role: str = ""          # "low" | "high" | ""
    unit_source: str = ""         # "token" | "table" | "slide" | "series" | "chart_format" | "inferred" | ""
    scale_known: bool = False     # True when scale ("" included) was stated, not guessed
    # --- location ---
    slide_title: str = ""
    slide_context: str = ""       # title + subtitle ("Executive summary - Project Atlas group overview")
    paragraph_index: int = -1
    char_start: int = -1          # offsets of raw_text inside the paragraph/cell text
    char_end: int = -1
    row_label: str = ""           # table row header (tables) or ""
    col_label: str = ""           # table column header (tables) or chart category
    chart_type: str = ""
    series_index: int = -1
    point_index: int = -1
    footnote_refs: list[str] = field(default_factory=list)
    qualifiers: list[str] = field(default_factory=list)   # e.g. ["adjusted", "pro forma", "diluted"]
    skip_reason: str = ""         # non-empty -> extracted but should not be matched (year, marker…)
    uid: str = ""                 # stable id: f"s{slide}-{shape}-{p}-{start}" (set by extractor)


@dataclass
class ModelValue:
    sheet_name: str
    cell_address: str
    value: float
    row_label: str = ""
    col_label: str = ""
    named_range: str = ""
    formula: str = ""
    unit: str = "none"
    scale: str = ""
    period: str = ""
    metric_key: str = ""
    # --- provenance & display ---
    workbook: str = ""            # file name (basename) of the source workbook
    row: int = 0
    col: int = 0
    number_format: str = ""
    display_decimals: int = -1    # decimals shown by the cell's number format
    display_divisor: float = 1.0  # trailing-comma scaling in format: "#,##0,," -> 1e6
    pct_basis: str = ""           # "ratio" (0.25) | "points" (25) | ""
    currency: str = ""
    scale_known: bool = False
    hidden: bool = False          # hidden sheet/row/column
    sheet_priority: float = 1.0   # <1 for backup/old/scratch sheets
    section_label: str = ""       # nearest block header above (e.g. "Income Statement", company name)
    qualifiers: list[str] = field(default_factory=list)
    is_error: bool = False        # Excel error value (#N/A, #DIV/0!, #REF!, …); value is NaN
    error_text: str = ""
    label_norm: str = ""          # precomputed normalized label (filled by matching layer)
    uid: str = ""                 # f"{workbook}|{sheet}!{addr}"

    @property
    def ref(self) -> str:
        """Human-readable cell reference, prefixed by workbook when known."""
        base = f"{self.sheet_name}!{self.cell_address}"
        return f"[{self.workbook}] {base}" if self.workbook else base


@dataclass
class Issue:
    id: int
    slide_number: int
    deck_value: str
    expected_value: str
    source_cell: str
    issue_type: IssueType
    severity: Severity
    message: str
    metric_key: str = ""
    shape_id: str = ""
    # --- enrichment (all optional) ---
    confidence: float = 1.0       # how sure we are this is a real problem
    deck_label: str = ""
    model_label: str = ""
    suggestion: str = ""          # replacement text in the deck's own format, e.g. "$2,415m"
    difference: float | None = None   # deck - expected, canonical units
    tolerance: float | None = None
    difference_text: str = ""     # difference in the deck's own units, e.g. "+$35m", "+1.2pts"
    tolerance_text: str = ""      # e.g. "±$0.5m", "±0.05%"
    check: str = ""               # producing check, e.g. "model_match", "sum", "margin"
    related: list[dict[str, Any]] = field(default_factory=list)  # other slides/cells involved
    deck_uid: str = ""
    char_start: int = -1
    char_end: int = -1
    paragraph_index: int = -1
    table_row: int = -1
    table_col: int = -1
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewWarning:
    """Run-level warning that is not tied to one deck number (shown prominently)."""

    code: str                     # e.g. "uncalculated_formulas", "llm_failed", "images_not_checked"
    message: str
    file: str = ""
    slide_number: int | None = None
    sheet: str = ""
    count: int = 0


@dataclass
class Match:
    deck: DeckNumber
    model: ModelValue | None
    status: str                   # one of MATCH_STATUSES
    candidates: list[ModelValue] = field(default_factory=list)
    score: float = 0.0            # label score of the chosen candidate
    confidence: float = 0.0       # 0..1 confidence in the chosen mapping
    method: str = ""              # "label" | "named_range" | "table_align" | "series_align" | "value_only" | "llm"
    comparison: Any = None        # numcheck.matching.normalizer.Comparison
    old_model: ModelValue | None = None   # staleness: corresponding cell in previous model
    note: str = ""
    source_options: list[tuple[ModelValue, str]] | None = field(default=None, repr=False)
    source_ai_options: list[tuple[ModelValue, str]] = field(default_factory=list, repr=False)
    source_suggestion: str = ""


@dataclass
class SlideInfo:
    slide_number: int
    title: str = ""
    unit_hint_text: str = ""      # e.g. "($ in millions, except per share data)" found on the slide
    period_hint: str = ""         # dominant/declared period for the slide (canonical key)
    image_count: int = 0          # pictures that may contain numbers (not checked without OCR)
    hidden: bool = False
    footnotes: list[str] = field(default_factory=list)
    as_of_dates: list[str] = field(default_factory=list)   # ISO dates parsed from "as of ..." text
    sources: list[str] = field(default_factory=list)       # "Source: ..." lines
    number_count: int = 0


@dataclass
class SheetInfo:
    name: str
    hidden: bool = False
    priority: float = 1.0
    unit_hint_text: str = ""
    value_count: int = 0
    formula_count: int = 0
    uncached_formula_count: int = 0
    error_count: int = 0
    header_row: int = 0
    label_col: int = 0


@dataclass
class DeckExtraction:
    numbers: list[DeckNumber] = field(default_factory=list)       # checkable numbers only
    skipped: list[DeckNumber] = field(default_factory=list)       # years, markers, dates… (skip_reason set)
    slides: list[SlideInfo] = field(default_factory=list)
    footnotes: list[dict] = field(default_factory=list)           # {"slide_number", "text"}
    warnings: list[ReviewWarning] = field(default_factory=list)


@dataclass
class ModelExtraction:
    values: list[ModelValue] = field(default_factory=list)
    sheets: list[SheetInfo] = field(default_factory=list)
    warnings: list[ReviewWarning] = field(default_factory=list)
    workbook: str = ""


@dataclass
class ReviewResult:
    """Everything a review produced. Supports ``result["issues"]`` for backward compatibility."""

    issues: list[Issue] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    warnings: list[ReviewWarning] = field(default_factory=list)
    slides: list[SlideInfo] = field(default_factory=list)
    sheets: list[SheetInfo] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    elapsed: float = 0.0
    deck_numbers: int = 0
    model_values: int = 0
    files: dict[str, str] = field(default_factory=dict)    # {"html": path, "xlsx": ..., "json": ..., "deck": ...}
    inputs: dict[str, Any] = field(default_factory=dict)   # {"deck": name, "models": [...], "model_old": name}
    settings: dict[str, Any] = field(default_factory=dict) # effective config summary (no secrets)
    source_review: dict[str, int] = field(default_factory=dict)
    extraction: DeckExtraction | None = field(default=None, repr=False)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class NumCheckInputError(Exception):
    """A user-facing problem with an input file (unreadable, encrypted, wrong type)."""

    def __init__(self, message: str, file: str = "", hint: str = ""):
        super().__init__(message)
        self.file = file
        self.hint = hint


def format_model_value(mv: "ModelValue | None") -> str:
    """Human-readable model value for reports, respecting its dimension/scale."""
    if mv is None:
        return ""
    if mv.is_error:
        return mv.error_text or "#ERROR"
    v = mv.value
    if mv.unit == "percent":
        pts = v * 100 if mv.pct_basis != "points" else v
        return f"{pts:.2f}%"
    if mv.unit == "multiple":
        return f"{v:.2f}x"
    if mv.unit == "bps":
        return f"{v:,.0f}bps"
    if mv.unit == "currency":
        sym = {"EUR": "€", "GBP": "£", "JPY": "¥"}.get(mv.currency, "$")
        suffix = {"bn": "bn", "mm": "m", "m": "m", "k": "k", "b": "bn", "t": "tn"}.get(mv.scale, "")
        dec = 0 if abs(v) >= 100 or v == int(v) else (1 if abs(v) >= 10 else 2)
        if mv.display_decimals >= 0:
            dec = min(mv.display_decimals, 4)
        sign = "-" if v < 0 else ""
        return f"{sign}{sym}{abs(v):,.{dec}f}{suffix}"
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.2f}"
