"""Deterministic label matching with token-Jaccard similarity + abbreviation expansion."""

from __future__ import annotations

import re

from numcheck.config import Config
from numcheck.models import ModelValue

STOPWORDS = {
    "total", "subtotal", "sum", "of", "the", "a", "an", "and",
    "for", "at", "as", "in", "to", "on", "by", "per",
}

UNIT_TOKENS = {"m", "mm", "bn", "k", "b", "t"}

_PERIOD_PAT = re.compile(r"\b(?:fy\s?\d{2,4}|cy\s?\d{2,4}|ltm|ntm|q[1-4](?:\s?\d{2,4})?)\b", re.IGNORECASE)
_NUMBER_PAT = re.compile(r"\b\d+(?:\.\d+)?\b")
_PERIOD_YEAR_RE = re.compile(r"\b(fy|cy)\s?(\d{2}|\d{4})\b", re.IGNORECASE)


def _expand_text(text: str, config: Config) -> str:
    """Expand abbreviations as whole phrases/tokens (supports multi-word keys)."""
    s = text
    for key, value in config.abbreviations.items():
        s = re.sub(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", value, s, flags=re.IGNORECASE)
    return s


def _normalize_periods(s: str) -> str:
    """Normalize period spelling (FY2024 -> fy24) while KEEPING the period token."""
    def repl(m: re.Match) -> str:
        tag = m.group(1).lower()
        year = m.group(2)
        return f"{tag}{year[-2:]}"

    return _PERIOD_YEAR_RE.sub(repl, s)


def normalize_label(text: str, config: Config) -> str:
    """Lowercase, expand abbreviations, normalize periods, drop unit/number noise."""
    s = (text or "").lower()
    s = _expand_text(s, config)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = _normalize_periods(s)
    tokens = [t for t in s.split() if t not in UNIT_TOKENS and not t.isdigit()]
    return " ".join(tokens)


def metric_key(text: str, config: Config) -> str:
    """Canonical metric name (period/numbers/units stripped) for grouping."""
    s = (text or "").lower()
    s = _expand_text(s, config)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = _PERIOD_PAT.sub(" ", s)
    s = _NUMBER_PAT.sub(" ", s)
    tokens = [t for t in s.split() if t not in STOPWORDS and t not in UNIT_TOKENS]
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out)


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def label_score(deck_norm: str, mv: ModelValue, config: Config) -> float:
    """Best token-Jaccard similarity between a deck label and a model value's labels."""
    d = deck_norm
    if not d:
        return 0.0
    best = 0.0
    for c in (mv.named_range, mv.row_label, mv.col_label, f"{mv.row_label} {mv.col_label}"):
        cn = normalize_label(c, config)
        if not cn:
            continue
        best = max(best, _jaccard(d, cn))
    return best
