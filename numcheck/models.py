"""Shared data models for NumCheck."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Source(str, Enum):
    TEXT_BOX = "text_box"
    TABLE = "table"
    CHART = "chart"
    OCR = "ocr"


class IssueType(str, Enum):
    MISMATCH = "mismatch"
    INCONSISTENT_ACROSS_SLIDES = "inconsistent_across_slides"
    SUM_ERROR = "sum_error"
    PCT_RECONCILE = "pct_reconcile"
    GROWTH_RECONCILE = "growth_reconcile"
    UNIT_PERIOD_MISMATCH = "unit_period_mismatch"
    STALE = "stale"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class DeckNumber:
    slide_number: int
    shape_id: str
    source: Source
    raw_text: str
    value: float
    unit: str = ""       # dimension: "", "currency", "percent", "multiple", "bps"
    scale: str = ""      # "", "k", "m", "mm", "bn", "b", "t"
    period: str = ""     # "fy24", "cy24", "ltm", "ntm", "q1", ""
    label: str = ""
    context: str = ""
    chart_series_name: str = ""
    confidence: float = 1.0
    significant: int = 1
    table_row: int = -1
    table_col: int = -1
    metric_key: str = ""


@dataclass
class ModelValue:
    sheet_name: str
    cell_address: str
    value: float
    row_label: str = ""
    col_label: str = ""
    named_range: str = ""
    formula: str = ""
    unit: str = ""       # dimension: "", "currency", "percent", "multiple", "bps"
    scale: str = ""      # inferred from headers
    period: str = ""
    metric_key: str = ""


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


def format_model_value(mv: "ModelValue") -> str:
    """Human-readable model value for reports, respecting its dimension/scale."""
    if mv is None:
        return ""
    if mv.unit == "percent":
        return f"{mv.value * 100:.2f}%"
    if mv.unit == "multiple":
        return f"{mv.value:.2f}x"
    if mv.unit == "currency":
        suffix = {"bn": "bn", "mm": "mm", "m": "m", "k": "k", "b": "bn", "t": "t"}.get(mv.scale, "")
        return f"${mv.value:,.0f}{suffix}"
    if mv.value == int(mv.value):
        return f"{int(mv.value):,}"
    return f"{mv.value:,.2f}"
