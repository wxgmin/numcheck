"""Numeric token parsing and deterministic value comparison.

All numeric logic lives here. The LLM is used only for label disambiguation
and never for comparing numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from numcheck.models import ModelValue


@dataclass
class ParsedNumber:
    value: float
    dimension: str  # "currency" | "percent" | "multiple" | "bps" | "none"
    scale: str      # "", "k", "m", "mm", "bn", "b", "t"
    period: str
    raw: str
    significant: int


_SCALE_RE = re.compile(r"(?:bn|mm|m|k|b|t|B|M|K|T)$")
_SCALE_UNIT_RE = re.compile(r"(?:[$]\s*|\()\s*(bn|mm|m|k|b|t)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(
    r"(?<![\w])"
    r"[$£€]?"
    r"\s*"
    r"\(?"
    r"-?"
    r"(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
    r"\)?"
    r"\s*"
    r"(?:%|bps?)?"
    r"\s*"
    r"(?:bn|mm|m|k|b|t|B|M|K|T)?"
    r"\s*x?"
)

_PERIOD_RE = re.compile(r"\b(fy\s?(\d{2})|cy\s?(\d{2})|ltm|ntm|q([1-4]))\b", re.IGNORECASE)


def find_number_tokens(text: str) -> list[tuple[str, int, int]]:
    """Return (token, start, end) for every number-like token in *text*."""
    out: list[tuple[str, int, int]] = []
    for m in _TOKEN_RE.finditer(text):
        token = m.group(0)
        if parse_token(token) is not None:
            out.append((token, m.start(), m.end()))
    return out


def detect_scale(text: str) -> str:
    """Return a scale token from unit-bearing text like '$m' or '($bn)'."""
    m = _SCALE_UNIT_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).lower()


def parse_unit_hint(hint: str) -> tuple[str, str]:
    """Interpret a unit hint like '$m', '$bn', '%', 'x', 'bps' -> (dimension, scale)."""
    h = (hint or "").strip().lower()
    dim = "none"
    scale = ""
    if not h:
        return dim, scale
    if "%" in h:
        dim = "percent"
    elif "bps" in h:
        dim = "bps"
    elif "x" in h:
        dim = "multiple"
    elif "$" in h or "£" in h or "€" in h or "usd" in h:
        dim = "currency"
    scale = detect_scale(h) if dim == "currency" else ""
    return dim, scale


def significant_digits(num_part: str) -> int:
    """Count significant figures from the literal number string.

    Integers are treated as exact (all digits significant); only a decimal
    point marks rounding precision, e.g. "1.2" -> 2 sig figs.
    """
    s = num_part.replace("-", "").replace(",", "")
    if "." in s:
        int_part, frac = s.split(".")
        digits = int_part.lstrip("0") + frac.lstrip("0")
    else:
        digits = s.lstrip("0")
    return max(1, len(digits))


def parse_token(token: str, unit_hint: str = "") -> ParsedNumber | None:
    """Parse a single numeric token, optionally inheriting a unit hint."""
    s = token.strip()
    if not s:
        return None

    dimension = "none"
    scale = ""
    period = ""
    is_negative = False

    if "%" in s:
        dimension = "percent"
        s = s.replace("%", "")
    elif re.search(r"bps?", s, re.IGNORECASE):
        dimension = "bps"
        s = re.sub(r"bps?", "", s, flags=re.IGNORECASE)
    elif re.search(r"x\s*$", s, re.IGNORECASE):
        dimension = "multiple"
        s = re.sub(r"x\s*$", "", s, flags=re.IGNORECASE)

    m = re.match(r"^\s*([$£€])\s*", s)
    if m:
        dimension = "currency"
        s = s[m.end():]

    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1]

    m = _SCALE_RE.search(s)
    if m:
        scale = m.group(0).lower()
        s = s[: m.start()]

    num_part = s.strip().replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?|\.\d+", num_part):
        return None
    try:
        value = float(num_part)
    except ValueError:
        return None
    if is_negative:
        value = -value

    # A bare number with a scale (e.g. "1.2bn") is money in this domain.
    if dimension == "none" and scale:
        dimension = "currency"

    if dimension == "none" and not scale and unit_hint:
        hint_dim, hint_scale = parse_unit_hint(unit_hint)
        if hint_dim != "none":
            dimension = hint_dim
            scale = hint_scale

    significant = significant_digits(num_part)

    return ParsedNumber(
        value=value,
        dimension=dimension,
        scale=scale,
        period=period,
        raw=token,
        significant=significant,
    )


def extract_period(text: str) -> str:
    """Return a canonical period tag from surrounding text, if present."""
    m = _PERIOD_RE.search(text or "")
    if not m:
        return ""
    s = m.group(0).lower().replace(" ", "")
    if s.startswith("fy"):
        return f"fy{m.group(2)}"
    if s.startswith("cy"):
        return f"cy{m.group(3)}"
    if s.startswith("q"):
        return f"q{m.group(4)}"
    return s  # ltm / ntm


def parsed_from_deck(deck) -> "ParsedNumber":
    """Reconstruct a ParsedNumber from an already-extracted DeckNumber."""
    return ParsedNumber(
        value=deck.value,
        dimension=deck.unit,
        scale=deck.scale,
        period=deck.period,
        raw=deck.raw_text,
        significant=deck.significant,
    )


def scale_factor(scale: str, unit_scales: dict[str, float]) -> float:
    return unit_scales.get(scale, 1.0)


def canonical_deck(parsed: ParsedNumber, unit_scales: dict[str, float]) -> tuple[str, float]:
    """Return (dimension, base_value) for a deck number."""
    sf = scale_factor(parsed.scale, unit_scales)
    if parsed.dimension == "currency":
        return ("currency", parsed.value * sf)
    if parsed.dimension == "percent":
        return ("percent", parsed.value)
    if parsed.dimension == "multiple":
        return ("multiple", parsed.value)
    if parsed.dimension == "bps":
        return ("bps", parsed.value)
    return ("none", parsed.value * sf)


def canonical_model(mv: ModelValue, unit_scales: dict[str, float]) -> tuple[str, float]:
    """Return (dimension, base_value) for a model value."""
    sf = scale_factor(mv.scale, unit_scales)
    dim = mv.unit
    if dim == "currency":
        return ("currency", mv.value * sf)
    if dim == "percent":
        return ("percent", mv.value * 100.0)
    if dim == "multiple":
        return ("multiple", mv.value)
    if dim == "bps":
        return ("bps", mv.value)
    return ("none", mv.value * sf)


def relative_tolerance(parsed: ParsedNumber, base_relative: float) -> float:
    """Widen tolerance to account for presentation rounding."""
    sig = max(1, parsed.significant)
    half_ulp = 0.5 * (10 ** (1 - sig))
    return max(base_relative, half_ulp * 1.5)


def values_match(
    deck_canonical: tuple[str, float],
    model_canonical: tuple[str, float],
    parsed: ParsedNumber,
    base_relative: float,
    absolute: float,
) -> bool:
    """Deterministic numeric comparison with rounding tolerance."""
    d_dim, d_val = deck_canonical
    m_dim, m_val = model_canonical
    if d_dim != m_dim:
        return False
    if d_val == 0 and m_val == 0:
        return True
    rel = abs(d_val - m_val) / max(abs(m_val), abs(d_val), 1e-12)
    abs_diff = abs(d_val - m_val)
    tol = relative_tolerance(parsed, base_relative)
    if rel <= tol:
        return True
    # Absolute tolerance only applies to genuinely small magnitudes; otherwise a
    # tiny fixed epsilon would make e.g. $0.10 vs $0.60 count as equal.
    return abs_diff <= absolute and max(abs(d_val), abs(m_val)) <= absolute * 1e3


def format_amount(value: float, digits: int = 1) -> str:
    """Human-readable formatting for reports."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.{digits}f}"
