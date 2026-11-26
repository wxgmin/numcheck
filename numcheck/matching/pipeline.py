"""Orchestrates deck -> model matching.

Strategy order (DESIGN.md 1.4): label match over an inverted index (qualifiers,
periods, units, sheet priority), context coherence between sibling numbers, table
and series alignment, value-only verification, and finally LLM disambiguation of
label ties (labels only).  Staleness against a previous model is evaluated from the
same matches, never by re-running the whole matching.

Every numeric verdict comes from :func:`numcheck.matching.normalizer.compare`.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from numcheck.config import Config
from numcheck.llm import resolve_ambiguous
from numcheck.matching.alignment import AlignResult, align_blocks
from numcheck.matching.index import (
    VIEW_NAMED,
    DeckQuery,
    ModelIndex,
    covenant_words,
    expresses_change,
    stat_groups,
)
from numcheck.matching.label_matcher import (
    STRICT_QUALIFIERS,
    LabelParts,
    metric_key,
    normalize_qualifiers,
    qualifier_factor,
    split_label,
)
from numcheck.matching.llm_matcher import MAX_CANDIDATES, build_entry
from numcheck.matching.normalizer import compare, dimensions_compatible
from numcheck.matching.source_search import display_precision, verify_range_pairs
from numcheck.matching.value_match import significant_digits_of
from numcheck.models import DeckNumber, Match, ModelValue, ReviewWarning, SlideInfo
from numcheck.periods import parse_period, period_key
from numcheck.periods import period_affinity as _period_affinity

_TOTAL_COL_RE = re.compile(r"(?:total|group|consolidated|combined|overall|firm[- ]?wide|company|all)\b", re.I)

log = logging.getLogger(__name__)


@lru_cache(maxsize=200_000)
def period_affinity(a: str, b: str) -> float | None:
    """Memoised ``periods.period_affinity``: it re-parses both keys with regexes on every call."""
    return _period_affinity(a, b)

__all__ = ["Match", "match_deck_to_model", "assign_metric_keys"]

MIN_LABEL_SCORE = 0.35          # default of Config.min_label_score (kept for importers)
BAND = 0.05                     # label scores within BAND of the best are "tied" (value decides among ties)
NEAR_BAND = 0.15                # a clear winner that mismatches yields to a near-label cell that matches
STRONG_LABEL = 0.8              # a label score this high outranks a structural alignment
WEAK_COVERAGE = 0.5             # a label-only mismatch needs the labels to share at least this much (either side)
MAX_CANDS = 300


@dataclass
class _Cand:
    idx: int
    score: float
    kind: str = ""              # which label view produced the score
    base: float = 0.0
    boost: float = 0.0          # sibling-coherence boost included in score


@dataclass
class _Deck:
    pos: int
    d: DeckNumber
    label: str
    query: DeckQuery | None
    quals: frozenset
    period: str                 # explicit (hard filter)
    soft: str                   # default period (soft preference)
    cands: list[_Cand] = field(default_factory=list)
    excluded: list[_Cand] = field(default_factory=list)   # label matches ruled out by period
    alt_query: DeckQuery | None = None                    # row label alone (tables): retry when the full label fails
    title_tokens: frozenset = frozenset()                 # slide title tokens (context disambiguation)
    stats: frozenset = frozenset()                        # mean/median/min/max words of the deck label
    cov_words: frozenset = frozenset()                    # covenant words the deck label itself uses
    change: bool = False                                  # the deck number expresses a change, not a level
    rate_words: frozenset = frozenset()                   # cagr / margin / yield: the cell must name them too
    metric_tokens: frozenset = frozenset()                # non-entity label tokens
    entities: frozenset = frozenset()                     # companies / segments the model also labels cells with
    share: bool = False                                   # the deck figure is a share ("% of total", "mix")
    change_words: bool = False                            # label or nearby words say the figure is a change
    flow: bool = False                                    # "repaid", "redeemed": a flow, never a balance cell
    block_entities: frozenset = frozenset()               # entity named by the table block heading (soft)


@dataclass
class _Label:
    """Outcome of label-based matching for one deck number."""

    status: str = "none"        # none | matched | mismatch | ambiguous
    idx: int | None = None
    comparison: object = None
    score: float = 0.0
    margin: float = 1.0
    tied: list[int] = field(default_factory=list)
    note: str = ""
    conf: float = 0.0
    method: str = "label"


# --------------------------------------------------------------------------- #
# Deck-side preparation
# --------------------------------------------------------------------------- #

def _label_text(d: DeckNumber) -> str:
    label = (d.label or "").strip()
    if not label:
        label = " ".join(x for x in (d.row_label, d.col_label, d.chart_series_name) if x).strip()
    if not label:
        label = (d.context or "")[:160]
    return label


def assign_metric_keys(deck_numbers: list[DeckNumber], model_values: list[ModelValue], config: Config) -> None:
    """Fill ``metric_key`` on deck numbers (model cells are filled by :class:`ModelIndex`)."""
    for d in deck_numbers:
        d.metric_key = metric_key(_label_text(d), config) or "unnamed"
    for mv in model_values:
        if not mv.metric_key:
            mv.metric_key = metric_key(f"{mv.named_range} {mv.row_label} {mv.col_label}", config) or "unnamed"


_METRIC_COLUMN_RE = re.compile(
    r"%|\bcagr\b|\bgrowth\b|\bmargin\b|\bshare\b|\bmix\b|\bchange\b|\by/?o?/?y\b|\bratio\b|\bmultiple\b|"
    r"\bper\b|\bx\s+\w|\w\s*/\s*\w|\bcontribution\b|\bcoverage\b|\bleverage\b|\byield\b|\bpremium\b|\bimplied\b", re.I)


_RATE_WORDS = frozenset({"cagr", "margin", "yield"})
_FLOW_DECK_TOKENS = frozenset({"repaid", "redeemed", "prepaid", "amortised", "amortized", "repurchased", "bought"})
_FLOW_TOKENS = frozenset({"repaid", "repay", "repayment", "repayments", "redeemed", "redemption", "prepaid", "prepayment",
                          "amortization", "amortisation", "amortised", "amortized", "paid", "repurchased", "repurchase",
                          "buyback", "sweep", "paydown"})
# "NAV increased by EUR 21.0m", "up 39", "net 39 new": the words right before a figure say it is a change
_CHANGE_CUE_RE = re.compile(r"\b(?:increased?|increases|decreased?|decreases|rose|risen|fell|fallen|grew|grown|"
                            r"declined?|up|down|added|adds|change[sd]?|net|movement|higher|lower)\b"
                            r"[^.;]{0,30}$", re.I)
_BARE_RATE = frozenset({"margin", "growth", "cagr", "change", "yield"})
_SUBJECT_RE = re.compile(
    r"\b((?:adj(?:usted|\.)?\s+|reported\s+|pro\s+forma\s+|organic\s+|net\s+)?"
    r"(?:ebitda|ebita|ebit|revenues?|net\s+sales|sales|gross\s+profit|operating\s+(?:income|profit)|"
    r"net\s+income|free\s+cash\s+flow|fcf|eps|noi|ffo|affo|arr|bookings|backlog))\b", re.I)


def _sentence_subject(d: DeckNumber, label: str) -> str:
    """"Adj. EBITDA increased 17.9% to $150.6m; margin expanded 76bps to 19.0%": a bare "margin" or
    "growth" takes the metric named earlier in the same sentence."""
    ctx = d.context or ""
    if not ctx or d.table_row >= 0:
        return ""
    end = d.char_start if 0 < d.char_start <= len(ctx) else len(ctx)
    pre = re.split(r"(?<=[.!?])\s+|\n", ctx[:end])[-1]
    hits = _SUBJECT_RE.findall(pre)
    return hits[-1] if hits else ""


_SHARE_RE = re.compile(r"%\s*of\b|\bshare\s+of\b|\b(?:revenue|sales|product|segment|debt|capital)\s+mix\b|"
                       r"\bmix\s*\(%\)|\bweighting\b|\bpercentage\s+of\b", re.I)


_HDR_MONTH_RE = re.compile(r"(?<![A-Za-z])(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[-\s'/]+((?:19|20)?\d{2})(?![\d])",
                           re.I)
_MONTH_NUM = {m: k for k, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"), start=1)}


def _header_periods(index: ModelIndex, i: int) -> frozenset[str]:
    """Every period the cell's column header names ("Q3 FY25 Mar-25" -> fy25q3 and y25mo03)."""
    cache = getattr(index, "_hdr_periods", None)
    if cache is None:
        cache = index._hdr_periods = {}
    hit = cache.get(i)
    if hit is None:
        from numcheck.periods import find_periods

        col = index.values[i].col_label or ""
        keys = {p.key for p, _s, _e in find_periods(col)}
        for m in _HDR_MONTH_RE.finditer(col):   # "Q3 FY25 Mar-25": the quarter-end month as well
            mon = _MONTH_NUM.get(m.group(1).lower()[:3])
            yy = int(m.group(2)) % 100
            if mon:
                keys.add(f"y{yy:02d}mo{mon:02d}")
        hit = cache[i] = frozenset(keys)
    return hit


def _is_share_cell(index: ModelIndex, i: int) -> bool:
    cache = getattr(index, "_share_cells", None)
    if cache is None:
        cache = index._share_cells = {}
    hit = cache.get(i)
    if hit is None:
        mv = index.values[i]
        hit = cache[i] = bool(_SHARE_RE.search(f"{mv.row_label} {mv.col_label}"))
    return hit


def _model_entities(index: ModelIndex) -> dict[str, int]:
    """Tokens the model uses in row/column labels of value cells, with how many cells use them.

    A company or segment named in row/column labels ("Castellane net debt", "Brenner shares") is a
    discriminating entity; a deal code name only on a cover sheet or title is not."""
    cached = getattr(index, "_entity_counts", None)
    if cached is None:
        # Count DISTINCT labels using the token: "Brenner" prefixes many rows (P/E, shares, price), while
        # "Clinics" is one metric row repeated across columns.
        labels: dict[str, set] = defaultdict(set)
        for i in range(index.n):
            mv = index.values[i]
            for t in index.row_tokens(i):
                labels[t].add((mv.row_label or "").strip().lower())
            for t in index.col_tokens(i):
                labels[t].add((mv.col_label or "").strip().lower())
            for t in index.label_tokens(i) - index.row_tokens(i) - index.col_tokens(i):
                labels[t].add((mv.row_label or "").strip().lower())   # a block title over several rows
        cached = Counter({t: len(v) for t, v in labels.items()})
        index._entity_counts = cached
    return cached


def _prepare(pos: int, d: DeckNumber, index: ModelIndex, config: Config, soft: str) -> _Deck:
    label = _label_text(d)
    parts: LabelParts = split_label(label, config)
    if parts.tokens and parts.tokens <= _BARE_RATE:
        subject = _sentence_subject(d, label)
        if subject:
            label = f"{subject} {label}"
            parts = split_label(label, config)
    if not parts.tokens:
        alt = " ".join(x for x in (d.row_label, d.col_label, d.chart_series_name, (d.context or "")[:200]) if x)
        parts2 = split_label(alt, config)
        if parts2.tokens:
            parts, label = parts2, alt
    quals = set(parts.qualifiers)
    if d.qualifiers:
        quals |= normalize_qualifiers(list(d.qualifiers), config)
    if d.range_role in ("low", "high"):
        quals.add(d.range_role)
    period = d.period or parts.period
    query = index.query(parts.tokens) if parts.tokens else None
    d.metric_key = metric_key(label, config) or "unnamed"
    st = _Deck(pos, d, label, query, frozenset(quals), period, "" if period else soft)
    if (d.row_label and d.row_label.strip() and d.row_label.strip() != label.strip()
            and not _METRIC_COLUMN_RE.search(d.col_label or "")):
        # Retrying with the row label alone is safe only when the column header is a period or a plain
        # heading; "CAGR FY22A-24A", "% of total", "x EBITDA" columns define the metric of the cell.
        rtoks = split_label(d.row_label, config).tokens
        if rtoks and (query is None or rtoks != query.tokens):
            st.alt_query = index.query(rtoks)
    title_text = d.slide_context or d.slide_title
    if title_text:
        st.title_tokens = index.tokens_of(title_text)
    text = f"{label} {d.row_label} {d.col_label}"
    st.stats = stat_groups(text)
    st.cov_words = covenant_words(text)
    st.change = "change" in {q.lower() for q in (d.qualifiers or [])}
    if not st.change and (d.unit or "none") in ("percent", "none") and "%" in (d.raw_text or ""):
        # "Net sales up 5.0%", "revenue grew 11%": a growth rate, never a level cell. Such numbers
        # match growth-labelled cells or fall through to derived verification from the model.
        from numcheck.matching.derived import _growth_intent

        st.change = _growth_intent(d) != 0
    if query is not None and "cagr" in query.tokens:
        st.change = True   # a CAGR is never a level or a share cell
    st.share = (bool(_SHARE_RE.search(f"{label} {d.row_label} {d.col_label}")) or "share" in parts.tokens
                or (d.unit == "percent" and bool(d.raw_text) and d.table_row < 0     # "15.9% of Group GWP"
                    and bool(re.search(re.escape(d.raw_text) + r"\s+of\b", d.context or ""))))
    st.metric_tokens = frozenset(parts.metric)
    st.flow = bool(query is not None and query.tokens & _FLOW_DECK_TOKENS)
    before = (d.context or "")[:d.char_start] if 0 < d.char_start <= len(d.context or "") else ""
    st.change_words = bool(expresses_change(label) or d.raw_text.startswith(("+", "-")) or _CHANGE_CUE_RE.search(before))
    st.entities = frozenset(e for e in parts.entity if _model_entities(index).get(e, 0) >= 2)
    if d.block:
        st.block_entities = frozenset(e for e in split_label(d.block, config).tokens
                                      if _model_entities(index).get(e, 0) >= 2) - frozenset(parts.tokens)
    st.rate_words = (query.tokens & _RATE_WORDS) if query is not None else frozenset()
    return st


# --------------------------------------------------------------------------- #
# Label scoring
# --------------------------------------------------------------------------- #

def _period_factor(st: _Deck, model_period: str) -> float:
    if st.period:
        aff = period_affinity(st.period, model_period)
        if aff is None:
            return 0.93
        if aff >= 1.0:
            return 1.0
        if aff >= 0.85:
            return 0.97
        if aff >= 0.7:
            return 0.9
        if aff >= 0.6:
            return 0.88
        return 0.75   # month-end vs year / quarter: a fallback only, never a tie with an exact-period cell
    if st.soft:
        aff = period_affinity(st.soft, model_period)
        if aff is None:
            return 0.97
        if _partial_period(model_period) and not _partial_period(st.soft):
            return 0.85
        if aff >= 1.0:
            return 1.0
        if aff >= 0.85:
            return 0.98
        if aff > 0.0:
            return 0.92
        return 0.82
    # No period at all: a headline figure means a full year, not a month or quarter of it.
    return 0.9 if _partial_period(model_period) else 1.0


def _partial_period(key: str) -> bool:
    """True for quarter / half / YTD / month periods (y24q3, fy24h1, y24mo09 ...)."""
    return bool(key) and bool(re.search(r"\d{2}(?:q[1-4]|h[12]|m[369]|mo\d{2})$", key))


def _score_deck(st: _Deck, index: ModelIndex, config: Config) -> None:
    """Candidate cells for one deck number with final label scores (period/unit/qualifier aware)."""
    if st.query is None:
        return
    cands, excluded = _collect(st, st.query, index, config)
    if not cands and st.alt_query is not None:
        # The full label (row + column headers, deck-only decoration) found nothing: retry with the row label alone.
        c2, e2 = _collect(st, st.alt_query, index, config)
        if c2:
            st.query = st.alt_query
            cands, excluded = c2, e2
    cands.sort(key=lambda c: (-c.score, c.idx))
    excluded.sort(key=lambda c: (-c.score, c.idx))
    st.cands = cands[:MAX_CANDS]
    st.excluded = excluded[:MAX_CANDS]


# Context tiers for slide-title disambiguation: the sheet/section names an entity the title mentions (A), carries no
# entity signal (B), or names a different entity the title never mentions (C).
_CTX_A, _CTX_B, _CTX_C = 1.0, 0.93, 0.85


def _scored_candidates(index: ModelIndex, query: DeckQuery, floor: float) -> list[tuple[int, float, str]]:
    """Label-only part of candidate scoring, cached per query: table rows and repeated
    metrics ask the same question many times, and large models make each answer costly."""
    cache = index.scored_cache
    key = (query.key, floor)
    hit = cache.get(key)
    if hit is None:
        hit = []
        for i, _cov in index.candidates(query):
            base, kind = index.label_score(query, i)
            if base >= floor:
                hit.append((i, base, kind))
        if len(cache) < 20000:
            cache[key] = hit
    return hit


def _collect(st: _Deck, query: DeckQuery, index: ModelIndex, config: Config) -> tuple[list[_Cand], list[_Cand]]:
    d = st.d
    vals = index.values
    min_s = config.min_label_score
    dunit = d.unit or "none"
    cands: list[_Cand] = []
    excluded: list[_Cand] = []
    deck_covenant = st.cov_words
    if st.stats:
        deck_covenant = deck_covenant | {"minimum", "maximum"}
    title = st.title_tokens
    # In a transaction table the row date and the multiple's reporting horizon are
    # different axes. A March transaction can have an LTM EBITDA multiple.
    horizon = index.parts(d.col_label).period if d.table_row >= 0 and "mo" in st.period else ""
    tiers: list[int] = []   # context tier of each entry of cands
    for i, base, kind in _scored_candidates(index, query, min_s * 0.9):
        mv = vals[i]
        if not dimensions_compatible(dunit, mv.unit) and not (
                dunit == "currency" and mv.unit == "count" and d.unit_source == "inferred"):
            continue
        if d.currency and mv.currency and d.currency != mv.currency:
            continue
        cell_stats = index.stats(i)
        if st.stats and cell_stats and not st.stats & cell_stats:
            continue   # Mean vs Median, Min vs Max ...: mutually exclusive statistics
        if st.rate_words and not st.rate_words <= index.label_tokens(i):
            continue   # "Physical Therapy CAGR" is not "Physical Therapy - % of total"
        if st.flow and not (index.label_tokens(i) & _FLOW_TOKENS):
            continue   # "Term Loan B repaid" is a repayment, never the tranche's balance
        if not st.share and _is_share_cell(index, i):
            continue   # a "% of total" cell is a share; "7.250%" in a Pricing column is not
        if st.change and not index.is_change(i):
            continue   # a change (bps, pp, QoQ ...) is never compared with a level cell
        if (index.quals[i] & _METRIC_DEFINING) - st.quals:
            continue   # "same-store sales growth" is a different metric from "net sales growth"
        mp = index.periods[i]
        if horizon in ("ltm", "ntm") and mp == horizon and period_key(mv.row_label) == st.period:
            mp = st.period
        if st.period and mp != st.period and st.period in _header_periods(index, i):
            mp = st.period   # "Clinics at Mar-25" and a "Q3 FY25 Mar-25" column: the header names the same date
        if st.period and period_affinity(st.period, mp) == 0.0:
            excluded.append(_Cand(i, base, kind, base))
            continue
        f = qualifier_factor(st.quals, index.quals[i]) * _period_factor(st, mp)
        if st.block_entities and not (st.block_entities & (index.label_tokens(i) | index.tokens_of(mv.sheet_name))):
            f *= 0.9   # the table block names a company / segment this cell does not (soft: rows may be totals)
        if not st.change and index.is_change(i) and not st.change_words:
            f *= 0.6   # "479 locations" is a level; "Net new locations" / "Revenue growth" are changes
        pr = mv.sheet_priority if mv.sheet_priority is not None else 1.0
        f *= 0.8 + 0.2 * min(max(pr, 0.0), 1.0)
        if mv.hidden:
            f *= 0.95
        ent = index.col_entities(i)
        if ent and not ent <= (query.tokens | title):
            f *= 0.9   # the column names a company/segment the deck label never mentions
        cov = index.covenant(i) - deck_covenant
        if cov:
            continue   # a covenant/threshold cell ("Minimum interest cover covenant") is never the metric itself
        if dunit != "none" and (mv.unit or "none") == "none":
            f *= 0.9
        elif dunit == "none" and (mv.unit or "none") != "none":
            f *= 0.97
        tier = 1
        if title:
            ctx = index.context_entities(i)
            if ctx:
                if ctx & title:
                    tier = 0
                elif not ctx & query.tokens:
                    tier = 2
        score = base * f
        if kind == VIEW_NAMED and base >= 0.9:
            score += 0.05   # the modeller's own name for the cell
        cands.append(_Cand(i, score, kind, base))
        tiers.append(tier)
    _relax_unstated_qualifiers(st, index, cands)
    if st.entities and cands:
        # "Castellane net debt / EBITDA": the model labels Castellane's own cells, so a cell that names
        # neither Castellane (nor sits on a Castellane sheet) is another company's or the pro forma figure.
        def _names_entity(c: _Cand) -> bool:
            mv = vals[c.idx]
            return bool(st.entities & (index.label_tokens(c.idx) | index.tokens_of(mv.sheet_name)))
        kept = [(c, t) for c, t in zip(cands, tiers, strict=True) if _names_entity(c)]
        if len(kept) < len(cands):
            excluded.extend(c for c in cands if not _names_entity(c))
            cands = [c for c, _ in kept]
            tiers = [t for _, t in kept]
    if cands and 0 in tiers:
        # Context only breaks ties: cells that the label score already ranks clearly lower are left alone.
        floor = max(c.score for c in cands) - BAND
        tied = [(c, t) for c, t in zip(cands, tiers, strict=True) if c.score >= floor]
        if any(t == 0 for _, t in tied):
            mult = (_CTX_A, _CTX_B, _CTX_C)
            for c, t in tied:
                c.score *= mult[t]
    cands = [c for c in cands if c.score >= min_s]
    if "dcf" in title:
        # A slide explicitly presenting a DCF can identify its own valuation metrics
        # from DCF rows rather than equally named trading-comps columns.
        scoped = [c for c in cands if query.tokens <= index.row_tokens(c.idx)
                  and "dcf" in index.tokens_of(vals[c.idx].sheet_name)]
        if scoped:
            cands = scoped
    if st.stats and any(index.stats(c.idx) & st.stats for c in cands):
        # "Median EV / NTM EBITDA": when the model has a Median row, a single peer that happens to share
        # the value is not that statistic.
        keep = [c for c in cands if index.stats(c.idx) & st.stats]
        excluded.extend(c for c in cands if not index.stats(c.idx) & st.stats)
        cands = keep
    _explicit_basis(st, index, cands, excluded)
    return cands, excluded


_METRIC_DEFINING = frozenset({"same store", "same property", "same center", "comparable store", "organic",
                              "like for like"})
_RELAXABLE = frozenset({"adjusted", "pro forma"})
_ADJUSTABLE_METRICS = frozenset({"ebitda", "ebita", "ebit", "eps", "earnings", "income", "profit", "debt", "leverage",
                                 "margin", "revenue", "sales", "ffo", "affo", "noi", "fcf", "cash", "ebitdar"})


def _relax_unstated_qualifiers(st: _Deck, index: ModelIndex, cands: list[_Cand]) -> set[int]:
    """Decks drop "Adj." freely ("net leverage 3.1x" for "Net debt / Adj. EBITDA"). An unstated
    adjusted / pro forma qualifier only argues against a cell when the model also holds a comparably
    labelled cell without it; otherwise the qualified cell is the only version of the metric."""
    relaxed: set[int] = set()
    if not cands:
        return relaxed
    for c in cands:
        extra = (index.quals[c.idx] & _RELAXABLE) - st.quals
        if not extra or (index.quals[c.idx] & STRICT_QUALIFIERS) - st.quals - _RELAXABLE:
            continue
        if st.query is None or not (st.query.tokens & index.row_tokens(c.idx) & _ADJUSTABLE_METRICS):
            continue   # the deck must name the metric that carries the qualifier ("net leverage" -> EBITDA)
        rival = any(o is not c and o.base >= c.base - 0.15 and not (index.quals[o.idx] & extra) for o in cands)
        if not rival:
            c.score *= 0.92 / (0.5 ** len(extra))   # undo the strict penalty, keep a soft one
            relaxed.add(c.idx)
    return relaxed


def _basis_of(period: str) -> str:
    return period[:2] if period[:2] in ("fy", "cy") else ""


def _explicit_basis(st: _Deck, index: ModelIndex, cands: list[_Cand], excluded: list[_Cand]) -> None:
    """Explicit FY/CY deck period: when an exact-basis cell with the same label exists, other-basis cells are
    ruled out (kept in ``excluded`` so the issue can say "value equals <ref> (<other period>)")."""
    basis = _basis_of(st.period)
    if not basis or not cands:
        return
    exact_base = [c.base for c in cands if period_affinity(st.period, index.periods[c.idx]) == 1.0]
    if not exact_base:
        return
    top = max(exact_base)
    keep: list[_Cand] = []
    for c in cands:
        mp = index.periods[c.idx]
        other = _basis_of(mp) and _basis_of(mp) != basis and period_affinity(st.period, mp) not in (None, 1.0)
        if other and c.base <= top + 0.05:
            excluded.append(c)
        else:
            keep.append(c)
    cands[:] = keep


# --------------------------------------------------------------------------- #
# Context coherence (second pass)
# --------------------------------------------------------------------------- #

def _coherence(states: list[_Deck], index: ModelIndex) -> None:
    """Boost candidates that sit where the sibling numbers of the same slide/table/chart landed.

    Numbers on one slide tend to come from one sheet; numbers of one table row/column or chart series
    tend to come from one model row/column.  Only confident siblings vote.  The boost (<= ~0.14) breaks
    ties between same-label cells without overriding a clearly better label.
    """
    vals = index.values
    by_slide: dict[int, list[_Deck]] = defaultdict(list)
    by_shape: dict[tuple, list[_Deck]] = defaultdict(list)
    for st in states:
        if st.cands:
            by_slide[st.d.slide_number].append(st)
            by_shape[(st.d.slide_number, st.d.shape_id)].append(st)

    def voters_of(members: list[_Deck]) -> dict[int, _Cand]:
        out: dict[int, _Cand] = {}
        for st in members:
            c0 = st.cands[0]
            second = st.cands[1].score if len(st.cands) > 1 else 0.0
            if c0.score >= 0.6 and c0.score - second >= BAND:
                out[st.pos] = c0
        return out

    boosts: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))   # pos -> idx -> boost

    for members in by_slide.values():
        if len(members) < 2:
            continue
        voters = voters_of(members)
        sheet_votes: Counter = Counter()
        for pos, c0 in voters.items():
            mv = vals[c0.idx]
            sheet_votes[(mv.workbook, mv.sheet_name)] += 1
        for st in members:
            own = voters.get(st.pos)
            own_sheet = (vals[own.idx].workbook, vals[own.idx].sheet_name) if own else None
            n_other = len(voters) - (1 if own else 0)
            if n_other <= 0:
                continue
            seen_sheets: dict[tuple, float] = {}
            for c in st.cands:
                mv = vals[c.idx]
                sk = (mv.workbook, mv.sheet_name)
                b = seen_sheets.get(sk)
                if b is None:
                    same = sheet_votes[sk] - (1 if own_sheet == sk else 0)
                    b = 0.02 + 0.02 * min(same, 3) if same / n_other >= 0.5 and same > 0 else 0.0
                    seen_sheets[sk] = b
                if b:
                    boosts[st.pos][c.idx] += b

    for members in by_shape.values():
        if len(members) < 2:
            continue
        voters = voters_of(members)
        if not voters:
            continue
        line_votes: dict[tuple, Counter] = defaultdict(Counter)
        for st in members:
            c0 = voters.get(st.pos)
            if c0 is None:
                continue
            mv = vals[c0.idx]
            sk = (mv.workbook, mv.sheet_name)
            for g in _groups(st.d):
                line_votes[g][(sk, "r", mv.row)] += 1
                line_votes[g][(sk, "c", mv.col)] += 1
        for st in members:
            own = voters.get(st.pos)
            own_idx = own.idx if own else -1
            for c in st.cands:
                mv = vals[c.idx]
                sk = (mv.workbook, mv.sheet_name)
                for g in _groups(st.d):
                    lv = line_votes.get(g)
                    if not lv:
                        continue
                    sub = 1 if c.idx == own_idx else 0
                    if (mv.row > 0 and lv[(sk, "r", mv.row)] - sub > 0) or (mv.col > 0 and lv[(sk, "c", mv.col)] - sub > 0):
                        boosts[st.pos][c.idx] += 0.06
                        break

    for st in states:
        b = boosts.get(st.pos)
        if b:
            for c in st.cands:
                c.boost = b.get(c.idx, 0.0)
                c.score += c.boost
            st.cands.sort(key=lambda c: (-c.score, c.idx))


def _groups(d: DeckNumber) -> list[tuple]:
    """Sibling groups whose members should land on one model row/column."""
    out: list[tuple] = []
    key = (d.slide_number, d.shape_id)
    if d.label_cell:
        return out
    if d.table_row >= 0:
        out.append((*key, "tr", d.table_row))
    if d.table_col >= 0:
        out.append((*key, "tc", d.table_col))
    if d.series_index >= 0 and d.table_row < 0:
        out.append((*key, "series", d.series_index))
    return out


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #

def _label_conf(score: float, margin: float, cmp_) -> float:
    m = min(1.0, max(margin, 0.0) / 0.3)
    c = 0.35 + 0.55 * min(score, 1.0) + 0.1 * m
    if cmp_ is not None and cmp_.inferred:
        c *= 0.9
    return max(0.05, min(c, 1.0))


def _same_value(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def _label_decision(st: _Deck, index: ModelIndex, config: Config) -> _Label:
    """Pick the label winner and judge it (clear winner / tie band rules)."""
    d, vals = st.d, index.values
    cands = st.cands
    if not cands:
        return _Label()
    valid: list[tuple[_Cand, object]] = []
    best_score = None
    for c in cands:
        if best_score is not None and c.score < best_score - BAND:
            break
        mv = vals[c.idx]
        cmp_ = None
        if not mv.is_error:
            cmp_ = compare(d, mv, config)
            if cmp_ is None:
                continue
        if best_score is None:
            best_score = c.score
        valid.append((c, cmp_))
    if not valid:
        return _Label()
    if st.period and len(valid) > 1:
        # The deck states its period ("FY2024 NOI"): cells that carry that period beat cells with no period
        # at all (a regional breakdown table) when the labels tie.
        exact = [(c, x) for c, x in valid if (period_affinity(st.period, index.periods[c.idx]) or 0.0) >= 0.85]
        if exact and len(exact) < len(valid):
            valid = exact
    best_score = valid[0][0].score
    outside = next((c.score for c in cands if c.score < best_score - BAND), 0.0)
    margin = best_score - outside
    method0 = "named_range" if valid[0][0].kind == VIEW_NAMED and valid[0][0].base >= 0.9 else "label"

    if len(valid) == 1:
        c, cmp_ = valid[0]
        if not config.require_label_evidence and cmp_ is not None and not cmp_.ok:
            near = _near_match(st, c, index, config)
            if near is not None:
                nc, ncmp = near
                return _Label("matched", nc.idx, ncmp, nc.score, margin, [c.idx, nc.idx],
                              f"best-labelled cell {vals[c.idx].ref} does not match, but similarly labelled "
                              f"{vals[nc.idx].ref} does", min(_label_conf(nc.score, 0.0, ncmp), 0.6), "label")
        if cmp_ is None:
            return _Label("mismatch", c.idx, None, c.score, margin, [c.idx], "source cell is an Excel error",
                          _label_conf(c.score, margin, None), method0)
        return _Label("matched" if cmp_.ok else "mismatch", c.idx, cmp_, c.score, margin, [c.idx], "",
                      _label_conf(c.score, margin, cmp_), method0)

    tied = [c.idx for c, _ in valid]
    oks = [(c, x) for c, x in valid if x is not None and x.ok]

    def _live(c: _Cand) -> bool:
        mv = vals[c.idx]
        return not mv.hidden and (mv.sheet_priority if mv.sheet_priority is not None else 1.0) >= 1.0

    if oks and not any(_live(c) for c, _ in oks):
        live_bad = [(c, x) for c, x in valid if _live(c) and x is not None and not x.ok]
        if live_bad:
            # The number agrees only with a hidden/backup copy while the live sheet says otherwise:
            # classic "pasted from the old tab" error.
            c, cmp_ = live_bad[0]
            src = vals[oks[0][0].idx].ref
            return _Label("mismatch", c.idx, cmp_, c.score, margin, tied,
                          f"matches hidden/backup sheet {src} only; the live model has a different value",
                          0.8, "label")
    if config.require_label_evidence and oks:
        reference_value = oks[0][1].model_value
        if any(x is None or not _same_value(reference_value, x.model_value) for _, x in valid):
            return _Label("ambiguous", None, None, best_score, margin, tied,
                          "similarly labelled source cells disagree", 0.0, "label")
    spread = 0
    if oks and len(oks) == 1:
        # Count the copies of the *same* row label (e.g. "Revenue" on 30 segment sheets); comps-table rows
        # (company names under an "EV" column) are different metrics, not copies.
        ok_lab = (vals[oks[0][0].idx].row_label or "").strip().lower()
        spread = len({(vals[c.idx].sheet_name, vals[c.idx].section_label, vals[c.idx].row) for c, _ in valid
                      if (vals[c.idx].row_label or "").strip().lower() == ok_lab})
    if oks and spread > 3 and len(oks) == 1:
        # One of many identically labelled cells (e.g. "Revenue" on 30 segment sheets) happening to
        # equal the deck value is coincidence, not verification.
        return _Label("ambiguous", None, None, best_score, margin, tied,
                      f"{len(valid)} equally labelled cells; only {vals[oks[0][0].idx].ref} matches", 0.0, "label")
    if oks and d.comparator in (">", ">=", "<", "<=") and len(oks) < len(valid):
        # "cushion above 35%" over a horizon must hold for every equally labelled period, not just one of them
        return _Label("ambiguous", None, None, best_score, margin, tied,
                      f"the bound holds for {len(oks)} of {len(valid)} equally labelled cells", 0.0, "label")
    if oks:
        c, cmp_ = min(oks, key=lambda t: (t[1].inferred, -t[0].score, abs(t[1].difference)))
        conf = min(_label_conf(c.score, 0.0, cmp_), 0.7 if len(oks) == len(valid) else 0.65)
        return _Label("matched", c.idx, cmp_, c.score, margin, tied,
                      f"{len(oks)} of {len(valid)} equally labelled cells match; used {vals[c.idx].ref}",
                      conf, "label")
    bads = [(c, x) for c, x in valid if x is not None]
    errs = [(c, x) for c, x in valid if x is None]
    if errs and not bads:
        c, _ = errs[0]
        return _Label("mismatch", c.idx, None, c.score, margin, tied, "source cell is an Excel error", 0.6, "label")
    if bads and not errs and all(_same_value(bads[0][1].model_value, x.model_value) for _, x in bads):
        c, cmp_ = bads[0]
        return _Label("mismatch", c.idx, cmp_, c.score, margin, tied,
                      f"{len(bads)} equally labelled cells share the same value", 0.6, "label")
    if bads and not errs:
        # Segment columns (Property | Motor | Liability | Group) plus the time-series sheet: when the deck names
        # no segment, it is quoting the group/total figure. If every total-like cell agrees, judge against it.
        text = f"{_label_text(d)} {d.slide_context or ''}".lower()
        totals, named_segment = [], False
        for c, x in bads:
            col = (vals[c.idx].col_label or "").strip().lower()
            if _TOTAL_COL_RE.match(col) or index.periods[c.idx] and col and parse_period(col) is not None:
                totals.append((c, x))
            elif col and re.search(r"\b" + re.escape(col) + r"\b", text):
                named_segment = True
        if (totals and len(totals) < len(bads) and not named_segment
                and all(_same_value(totals[0][1].model_value, x.model_value) for _, x in totals)):
            c, cmp_ = totals[0]
            return _Label("mismatch", c.idx, cmp_, c.score, margin, tied,
                          "no segment named: judged against the group/total figure", 0.6, "label")
    xm = re.search(r"\bx=(-?[\d.]+)", d.context or "")
    if xm and len(bads) == 2:
        # XY chart point: the x value equals one cell of the row (e.g. growth 0.081), so the y value must be
        # the other one (the multiple).
        try:
            xv = float(xm.group(1))
        except ValueError:
            xv = None
        if xv is not None:
            others = [(c, x) for c, x in bads if not _same_value(vals[c.idx].value, xv)]
            if len(others) == 1:
                c, cmp_ = others[0]
                return _Label("mismatch", c.idx, cmp_, c.score, margin, tied,
                              f"XY chart point: x value {xv:g} identifies the other cell of the row", 0.7, "label")
    return _Label("ambiguous", None, None, best_score, margin, tied, "", 0.0, "label")


def _near_match(st: _Deck, winner: _Cand, index: ModelIndex, config: Config):
    """A near-label candidate (soft qualifier differences only) whose value matches the deck number.

    Guards against false alarms such as a deck "EV" compared with the DCF's "Implied EV" when the
    comps sheet's "EV" equals it.  Strict qualifier differences (adjusted, diluted, low/high ...) are
    never bridged: those are exactly the false passes the matcher exists to prevent.
    """
    # The window ignores sibling-coherence boosts, which only break ties between same-label cells.
    lo = winner.score - winner.boost - NEAR_BAND
    for c in st.cands:
        if (st.stats or index.stats(c.idx)) and st.stats != index.stats(c.idx):
            continue   # median vs one peer, min vs max: different statistics
        if c.idx == winner.idx or c.score >= winner.score - BAND:
            continue
        if c.score - c.boost < lo:
            continue
        cmv = index.values[c.idx]
        wmv = index.values[winner.idx]
        if (cmv.hidden or (cmv.sheet_priority or 1.0) < 1.0) and not (wmv.hidden or (wmv.sheet_priority or 1.0) < 1.0):
            continue   # agreeing only with a hidden / backup copy is the error, not its excuse
        wctx, cctx = index.context_entities(winner.idx), index.context_entities(c.idx)
        if wctx != cctx and (wctx & st.title_tokens or st.entities & index.label_tokens(winner.idx)):
            continue   # the slide names the winner's segment; another segment's equal value is a copy error
        if not _keeps_winner_tokens(st, winner, c.idx, index):
            continue   # the winner shares "Talbot" with the slide; a cell without it is someone else's figure
        if qualifier_factor(st.quals, index.quals[c.idx]) < 0.9:
            continue
        mv = index.values[c.idx]
        if mv.is_error:
            continue
        x = compare(st.d, mv, config)
        if x is not None and x.ok:
            return c, x
    return _context_match(st, winner, index, config) or _periodless_match(st, winner, index, config)


def _periodless_match(st: _Deck, winner: _Cand, index: ModelIndex, config: Config):
    """The figure states no period, the slide's period picked a cell that differs, and exactly one cell with
    the very same label matches ("One-off integration costs of EUR240m" = "Total one-off integration
    costs"). The slide's period was only a guess, so the label-identical cell is the source."""
    if st.d.period or not st.soft:
        return None
    total_re = re.compile(r"^\s*total\b|\btotal\s*$", re.I)
    if total_re.search(index.values[winner.idx].row_label or ""):
        return None
    found = []
    for c in st.cands:
        if (st.stats or index.stats(c.idx)) and st.stats != index.stats(c.idx):
            continue   # median vs one peer, min vs max: different statistics
        if c.idx == winner.idx or c.base < winner.base - 1e-9:
            continue
        if not total_re.search(index.values[c.idx].row_label or ""):
            continue   # another year of the same line is a wrong-period figure, not the total
        if not _keeps_winner_tokens(st, winner, c.idx, index):
            continue
        mv = index.values[c.idx]
        if mv.is_error or qualifier_factor(st.quals, index.quals[c.idx]) < qualifier_factor(st.quals, index.quals[winner.idx]):
            continue
        x = compare(st.d, mv, config)
        if x is not None and x.ok:
            found.append((c, x))
    if len({round(x.model_value, 9) for _c, x in found}) == 1:
        return min(found, key=lambda t: (bool(index.values[t[0].idx].formula), -t[0].score))
    return None


_BASIS_RE = re.compile(
    r"\b(?:including|incl\.?|excluding|excl\.?|ex\.?|net\s+of|before|after|pro\s+forma\s+for|adjusted\s+for)\s+"
    r"(?:(?:the|full|fully|all|run[- ]rate|expected|one[- ]off|estimated|annual|total|\S+%|[$€£]?[\d.,]+\w*)\s+)*"
    r"([a-z][a-z&/-]{3,})", re.I)
_BASIS_GENERIC = {"that", "this", "these", "which", "closing", "close", "completion", "taxes", "tax"}
# Adjustments that change a figure's basis; "including undrawn RCF" restates a definition and does not count.
_ADJUSTMENT_WORD_RE = re.compile(
    r"(?:synerg|ifrs|lease|sbc|stock|share-based|one-off|non-recurring|exceptional|restructuring|savings|"
    r"cost-savings|fees|transaction|integration|minorit|nci|acquisition|divest|disposal|fx|currency|hedg|"
    r"earn-?out|pension|impairment|amorti|ppa|contingent|dilution)", re.I)


def _unshared_basis(st: _Deck, idx: int, index: ModelIndex) -> str:
    """The sentence qualifies the figure ("including synergies", "excluding IFRS 16", "net of fees") with a word
    none of the cell's labels use; returns that word ("" when there is none). Prose figures only."""
    d = st.d
    if d.table_row >= 0 or not d.context:
        return ""
    start = d.char_start if 0 <= d.char_start <= len(d.context) else 0
    a = max(d.context.rfind(". ", 0, start), d.context.rfind("; ", 0, start), d.context.rfind("\n", 0, start)) + 1
    ends = [i for i in (d.context.find(". ", start), d.context.find("; ", start), d.context.find("\n", start)) if i >= 0]
    clause = d.context[a:min(ends) if ends else len(d.context)]
    toks = index.label_tokens(idx)
    for m in _BASIS_RE.finditer(clause):
        word = m.group(1).lower()
        if word in _BASIS_GENERIC or not _ADJUSTMENT_WORD_RE.match(word):
            continue
        wt = index.tokens_of(word)
        if wt and not wt & toks:
            return word
    return ""


def _keeps_winner_tokens(st: _Deck, winner: _Cand, idx: int, index: ModelIndex) -> bool:
    """A fallback cell must carry every slide word the best-labelled cell's row shares with the slide
    ("Talbot Aerostructures NTM revenue growth": a Segments growth cell without "Talbot" is not it)."""
    if st.query is None:
        return True
    return (st.query.tokens & index.row_tokens(winner.idx)) <= index.label_tokens(idx)


def _weak_label(st: _Deck, idx: int, index: ModelIndex) -> bool:
    """True when the deck label and the cell's row label share too little to call a difference an error:
    under half of the deck's (IDF-weighted) words appear on the cell, and under half of the cell's row
    words appear on the deck ("new gross debt" vs "New Term Loan B")."""
    q = st.query
    if q is None or not q.total:
        return False
    toks = index.label_tokens(idx)
    cov_d = sum(w for t, w in q.weights.items() if t in toks) / q.total
    row = index.row_tokens(idx)
    if not row:
        return cov_d < WEAK_COVERAGE
    cov_v = sum(w for t, w in q.weights.items() if t in row) / max(index.weight(row), 1e-9)
    return cov_d < WEAK_COVERAGE and cov_v < WEAK_COVERAGE


def _context_match(st: _Deck, winner: _Cand, index: ModelIndex, config: Config):
    """The best-labelled cell disagrees, but the sentence itself names another candidate that agrees.

    "Total leverage of 5.1x including run-rate cost savings" -> "Total leverage (PF Adj. EBITDA incl. cost
    savings)" rather than "Total leverage (FY24A Adj. EBITDA)". Two or more distinctive words of the
    sentence must appear in that cell's labels and not in the winner's; only prose figures qualify.
    """
    d = st.d
    if st.query is None:
        return None
    if d.table_row >= 0:
        # Table cell: the column header can name the metric on its own ("52-week high" x "Implied offer" is
        # the offer price). Every column word must appear on the alternative cell, which the winner lacks.
        ctx = index.tokens_of(d.col_label or "") - index.tokens_of(d.row_label or "")
        need = len(ctx)
    else:
        ctx = index.tokens_of(d.context or "") - st.query.tokens
        need = 2
    if not ctx or need < 1:
        return None
    win_tokens = index.label_tokens(winner.idx)
    found = []
    for c in st.cands:
        if (st.stats or index.stats(c.idx)) and st.stats != index.stats(c.idx):
            continue   # median vs one peer, min vs max: different statistics
        if c.idx == winner.idx:
            continue
        hits = (ctx & index.label_tokens(c.idx)) - win_tokens
        if len(hits) < need or (d.table_row >= 0 and not ctx <= index.label_tokens(c.idx)):
            continue
        if not _keeps_winner_tokens(st, winner, c.idx, index):
            continue
        mv = index.values[c.idx]
        if mv.is_error:
            continue
        x = compare(d, mv, config)
        if x is not None and x.ok:
            found.append((c, x))
    if len(found) == 1:
        return found[0]
    if not found and d.table_row >= 0 and len(ctx) >= 2:
        # The column header alone ("Implied offer") may name a cell the combined label never reached.
        q2 = index.query(frozenset(ctx))
        for i, base, kind in _scored_candidates(index, q2, max(config.min_label_score, 0.6)):
            mv = index.values[i]
            if i == winner.idx or mv.is_error or not ctx <= index.label_tokens(i):
                continue
            if index.row_tokens(i) - st.query.tokens - ctx:
                continue   # the cell's row names something the slide does not ("Talbot" for a median row)
            if not _keeps_winner_tokens(st, winner, i, index):
                continue
            if (st.stats or index.stats(i)) and st.stats != index.stats(i):
                continue
            x = compare(d, mv, config)
            if x is not None and x.ok:
                found.append((_Cand(i, base * 0.9, kind, base), x))
        if found and len({round(x.model_value, 9) for _c, x in found}) == 1:
            return max(found, key=lambda t: t[0].score)
    return None


def _period_note(st: _Deck, chosen: int, index: ModelIndex, config: Config, chosen_score: float) -> str:
    """'value equals <ref> (<period>)' when the deck value belongs to another period's cell."""
    d, vals = st.d, index.values
    chosen_period = index.periods[chosen]
    pool = sorted([(c.score, c) for c in st.cands] + [(c.base, c) for c in st.excluded],
                  key=lambda t: -t[0])
    thr = 0.7 * min(chosen_score, 1.0)
    for n, (s, c) in enumerate(pool):
        if n >= 60 or s < thr:
            break
        if c.idx == chosen:
            continue
        mp = index.periods[c.idx]
        if mp == chosen_period:
            continue
        mv = vals[c.idx]
        if mv.is_error:
            continue
        x = compare(d, mv, config)
        if x is not None and x.ok:
            return f"value equals {mv.ref} ({mp or 'other period'})"
    return ""


def _value_only(st: _Deck, index: ModelIndex, config: Config) -> tuple[int, object] | None:
    d = st.d
    if significant_digits_of(d) < config.value_only_min_digits:
        return None
    if st.change:
        return None   # a change/growth rate equal to some unrelated cell proves nothing
    hits = []
    for i, c in index.value_index().hits(d):
        mv = index.values[i]
        if mv.is_error:
            continue
        if st.period and period_affinity(st.period, index.periods[i]) == 0.0:
            continue
        hits.append((i, c))
    if not hits:
        return None
    first = hits[0][1].model_value
    if any(not _same_value(first, c.model_value) for _, c in hits[1:]):
        return None
    hits.sort(key=lambda t: (-(index.values[t[0]].sheet_priority or 1.0), index.values[t[0]].hidden, t[0]))
    return hits[0]


# --------------------------------------------------------------------------- #
# Match assembly
# --------------------------------------------------------------------------- #

def _match(d, mv, status, cands, score, conf, method, cmp_, note="") -> Match:
    return Match(d, mv, status, list(cands), round(score, 4), round(conf, 4), method, cmp_, None, note)


_RANGE_END_RE = {"low": re.compile(r"\b(?:low|min(?:imum)?|bottom|floor)\b", re.I),
                 "high": re.compile(r"\b(?:high|max(?:imum)?|top|ceiling)\b", re.I)}


def _same_range_end(d: DeckNumber, mv: ModelValue) -> bool:
    """A table range endpoint compared with a model cell explicitly labelled as the same end of a range."""
    rx = _RANGE_END_RE.get(d.range_role)
    src = getattr(d.source, "value", d.source)
    return bool(rx and src == "table" and rx.search(f"{mv.row_label} {mv.col_label}"))


def _build_match(st: _Deck, lab: _Label, al: AlignResult | None, index: ModelIndex, config: Config,
                 pending: list[tuple[Match, _Deck, _Label]]) -> Match:
    d, vals = st.d, index.values
    top = [vals[c.idx] for c in st.cands[:5]]

    def from_label() -> Match:
        mv = vals[lab.idx]
        note = lab.note
        if lab.status == "mismatch" and mv is not None and not mv.is_error:
            # The issue builder keys off the exact "value equals ..." prefix, so it replaces other notes.
            note = _period_note(st, lab.idx, index, config, lab.score) or note
        cands = [vals[i] for i in lab.tied] if len(lab.tied) > 1 else top
        return _match(d, mv, lab.status, cands, lab.score, lab.conf, lab.method, lab.comparison, note)

    if d.label_cell:
        # "6.375% Senior notes": the row's other cells hold its amount, so a row-label figure is confirmed
        # only by an equal labelled cell (or later by workbook text), never judged against the row's value.
        if lab.status == "matched":
            return from_label()
        return _match(d, None, "unmatched", top, 0.0, 0.0, "", None,
                      "figure inside a table row label; no equal workbook cell or text found")
    if (al is not None and not al.ok and st.query is not None and st.cands and st.cands[0].score >= 0.6
            and st.cands[0].idx != al.idx
            and index.label_score(st.query, al.idx)[0] < max(config.min_label_score, 0.45)):
        # A partial table alignment (the slide skips or reorders model rows) put this cell on a row whose
        # label has nothing to do with the slide's ("Free cash flow" on "Cash interest"): the position is
        # not evidence. Judge the cell by its own label instead.
        al = None
    if al is not None:
        amv = vals[al.idx]
        label_ready = lab.status in ("matched", "mismatch")
        strong = label_ready and lab.score >= STRONG_LABEL and lab.idx != al.idx
        score_a = next((c.score for c in st.cands if c.idx == al.idx), 0.0)
        support_conf = 0.7 + 0.2 * al.support
        if al.ok:
            if label_ready and lab.idx == al.idx:
                m = from_label()
                m.confidence = round(max(m.confidence, min(support_conf + 0.05, 0.97)), 4)
                return m
            if strong and config.require_label_evidence and lab.status == "mismatch":
                sources = [vals[lab.idx], amv]
                return _match(d, None, "ambiguous", sources, lab.score, 0.0, "label", None,
                              "the labelled source disagrees with the table or chart alignment")
            if strong and lab.status == "matched":
                return from_label()
            return _match(d, amv, "matched", top, score_a, support_conf, al.method, al.comparison,
                          f"{al.block} aligns with {amv.sheet_name} ({al.support:.0%} of its numbers match)")
        if strong or lab.status == "matched":
            # A labelled cell that equals the figure ("Pro forma ownership" at the end of a contribution
            # series) outweighs the position the rest of the block aligned to.
            return from_label()
        if label_ready and lab.idx == al.idx:
            m = from_label()
            m.confidence = round(max(m.confidence, support_conf), 4)
            return m
        note = (f"{al.block} aligns with {amv.sheet_name} ({al.support:.0%} of its numbers match) "
                f"but this cell does not")
        if amv.is_error:
            note = "source cell is an Excel error; " + note
        return _match(d, amv, "mismatch", top, score_a, support_conf, al.method, al.comparison, note)

    if lab.status == "mismatch" and d.is_range and not _same_range_end(d, vals[lab.idx]):
        # "WACC 8.5%-9.5%", "implies 11.1x-13.4x": an endpoint of a stated range is a sensitivity bound
        # or a derived figure, not a claim that a point cell equals it. Report it as unverified. A
        # football-field TABLE range against the model's own "- high" / "- low" cell is still compared.
        mv = vals[lab.idx]
        return _match(d, None, "unmatched", [mv], lab.score, 0.0, "label", None,
                      f"range endpoint; nearest labelled cell {mv.sheet_name}!{mv.cell_address} is not the same range end")
    if lab.status == "mismatch" and st.query is not None and lab.idx is not None and _weak_label(st, lab.idx, index):
        # "new gross debt" vs "New Term Loan B": one shared word is not enough to say the figure is wrong.
        return _match(d, None, "unmatched", top, lab.score, 0.0, "label", None,
                      f"closest labelled cell {vals[lab.idx].ref} differs, but its label is only a weak match")
    if lab.status == "mismatch" and lab.idx is not None and _unshared_basis(st, lab.idx, index):
        # "Including full run-rate synergies, pro forma net leverage falls to 1.4x": the sentence states a basis
        # (incl. synergies) the cell's labels do not, so the cell is not the same figure.
        return _match(d, None, "unmatched", top, lab.score, 0.0, "label", None,
                      f"the slide states a basis ({_unshared_basis(st, lab.idx, index)}) that "
                      f"{vals[lab.idx].ref} does not")
    if (lab.status == "mismatch" and not st.metric_tokens and st.query is not None and lab.idx is not None
            and st.entities and st.query.tokens <= st.entities
            and index.row_tokens(lab.idx) - st.query.tokens):
        # "21,400 Brenner": the label names a company but no metric, so it cannot say which Brenner cell
        # the figure claims to be. Unverified, not wrong.
        return _match(d, None, "unmatched", top, lab.score, 0.0, "label", None,
                      "the label names only a company or segment, not a metric")
    if lab.status in ("matched", "mismatch"):
        return from_label()
    if lab.status == "ambiguous" and (d.is_range or d.comparator in ("~", ">", ">=", "<", "<=")):
        # Guidance ranges / approximations ("targets an EBITDA margin of 10-12%") that equal no cell are
        # forward-looking statements, not ambiguous references to existing model cells.
        return _match(d, None, "unmatched", [vals[i] for i in lab.tied], lab.score, 0.0, "label", None,
                      "range/approximation matches none of the similarly labelled cells")
    if lab.status == "ambiguous":
        cands = [vals[i] for i in lab.tied]
        m = _match(d, None, "ambiguous", cands, lab.score, 0.0, "label", None,
                   lab.note or f"{len(cands)} similarly labelled cells with different values")
        pending.append((m, st, lab))
        return m

    vo = None if config.require_label_evidence else _value_only(st, index, config)
    if vo is not None:
        i, c = vo
        return _match(d, vals[i], "matched_value_only", [vals[i]], 0.0, 0.4, "value_only", c,
                      "no label match; Excel contains a numerically compatible value")
    return _match(d, None, "unmatched", [], 0.0, 0.0, "", None, "")


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #

def _llm_enabled(config: Config) -> bool:
    return (config.llm or "off").strip().lower() not in ("off", "none", "")


def _run_llm(pending: list[tuple[Match, _Deck, _Label]], index: ModelIndex, config: Config,
             warnings: list[ReviewWarning]) -> None:
    entries = []
    for n, (m, st, lab) in enumerate(pending):
        # A truncated shortlist could exclude the actual source cell.
        if len(lab.tied) > MAX_CANDIDATES:
            continue
        cands = [(index.values[i], index.periods[i]) for i in lab.tied]
        entries.append(build_entry(str(n), st.d, st.label, st.period or st.soft, cands))
    if len(entries) < len(pending):
        warnings.append(ReviewWarning(code="llm_shortlist_limit",
                                      message="Some labels had too many candidate cells for AI assistance.",
                                      count=len(pending) - len(entries)))
    try:
        resolutions = resolve_ambiguous(entries, config, warnings)
    except Exception as exc:  # noqa: BLE001 - resolve_ambiguous should not raise; belt and braces
        warnings.append(ReviewWarning(code="llm_failed", message=f"LLM disambiguation failed: {exc}",
                                      count=len(entries)))
        return
    for n, (m, st, lab) in enumerate(pending):
        chosen = resolutions.get(str(n), "")
        if not chosen:
            continue
        mv = next((c for c in m.candidates if c.ref == chosen), None)
        if mv is None:
            continue
        cmp_ = compare(st.d, mv, config) if not mv.is_error else None
        m.model = mv
        m.method = "llm"
        m.comparison = cmp_
        m.score = lab.score
        m.confidence = 0.7 * (0.9 if cmp_ is not None and cmp_.inferred else 1.0)
        m.status = "matched" if cmp_ is not None and cmp_.ok else "mismatch"
        m.record_ai_source(mv, "used")
        m.note = ("source cell is an Excel error; " if mv.is_error else "") + \
            f"AI suggested this source from {len(m.candidates)} equally labelled cells (labels only)"


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #

def _old_key_map(old: ModelIndex) -> dict[tuple, list[int]]:
    out: dict[tuple, list[int]] = defaultdict(list)
    for i in range(old.n):
        out[(old.row_tokens(i), old.col_tokens(i), old.periods[i], old.quals[i])].append(i)
    return out


def _labels_agree(new: ModelIndex, i: int, old: ModelIndex, j: int) -> bool:
    a, b = new.row_tokens(i), old.row_tokens(j)
    if a and b and len(a & b) / len(a | b) < 0.5:
        return False
    pa, pb = new.periods[i], old.periods[j]
    return not (pa and pb and period_affinity(pa, pb) == 0.0)


def _apply_staleness(matches: list[Match], index: ModelIndex, old_values: list[ModelValue],
                     config: Config) -> None:
    """Relabel mismatches whose value equals the *corresponding old-model cell* as ``stale``."""
    targets = [m for m in matches if m.status == "mismatch" and m.model is not None and not m.model.is_error]
    if not targets or not old_values:
        return
    old = ModelIndex([mv for mv in old_values if config.include_hidden or not mv.hidden], config)
    key_map: dict[tuple, list[int]] | None = None
    new_pos = {id(mv): i for i, mv in enumerate(index.values)}
    for m in targets:
        i = new_pos.get(id(m.model))
        if i is None:
            continue
        mv = m.model
        cands = [j for j in old.by_address.get((mv.sheet_name, mv.cell_address), [])
                 if _labels_agree(index, i, old, j)]
        if not cands:
            if key_map is None:
                key_map = _old_key_map(old)
            cands = list(key_map.get((index.row_tokens(i), index.col_tokens(i), index.periods[i], index.quals[i]), []))
            cands.sort(key=lambda j: (old.values[j].sheet_name != mv.sheet_name,
                                      abs(old.values[j].row - mv.row) + abs(old.values[j].col - mv.col), j))
        for j in cands:
            ov = old.values[j]
            if ov.is_error:
                continue
            c = compare(m.deck, ov, config)
            if c is not None and c.ok:
                m.status = "stale"
                m.old_model = ov
                m.note = (m.note + "; " if m.note else "") + f"matches the previous model at {ov.ref}"
                break


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def _soft_period(d: DeckNumber, slide_hint: dict[int, str], config: Config, dominant: str) -> str:
    return slide_hint.get(d.slide_number, "") or config.base_period or dominant


def match_deck_to_model(
    deck_numbers: list[DeckNumber],
    model_values: list[ModelValue],
    config: Config,
    old_model_values: list[ModelValue] | None = None,
    slides: list[SlideInfo] | None = None,
    warnings: list[ReviewWarning] | None = None,
    model_index: ModelIndex | None = None,
) -> list[Match]:
    """Match every deck number to its model cell; one :class:`Match` per input, in order.

    ``slides`` supplies ``period_hint`` (a soft default period); ``old_model_values`` enables
    staleness detection; ``warnings`` receives run-level warnings (LLM failures, contained
    per-number errors).
    """
    if warnings is None:
        warnings = []
    slide_hint = {s.slide_number: s.period_hint for s in (slides or []) if s.period_hint}
    index = model_index or ModelIndex([mv for mv in model_values if config.include_hidden or not mv.hidden], config)
    live = [i for i, d in enumerate(deck_numbers) if not d.skip_reason]
    dominant = ""
    pc = Counter(deck_numbers[i].period for i in live if deck_numbers[i].period)
    if pc:
        dominant = pc.most_common(1)[0][0]

    states: dict[int, _Deck] = {}
    errors = 0
    for i in live:
        try:
            st = _prepare(i, deck_numbers[i], index, config, _soft_period(deck_numbers[i], slide_hint, config, dominant))
            _score_deck(st, index, config)
            states[i] = st
        except Exception:  # noqa: BLE001 - one malformed number never aborts the run
            log.exception("label scoring failed for deck number %s", i)
            errors += 1
    try:
        _coherence(list(states.values()), index)
    except Exception:  # noqa: BLE001
        log.exception("coherence pass failed")
        errors += 1

    aligned = align_blocks(deck_numbers, [i for i in live if i in states], index, config, warnings)

    results: list[Match] = []
    pending: list[tuple[Match, _Deck, _Label]] = []
    for i, d in enumerate(deck_numbers):
        if d.skip_reason:
            results.append(Match(d, None, "skipped", note=d.skip_reason))
            continue
        st = states.get(i)
        if st is None:
            results.append(Match(d, None, "unmatched", note="matching error"))
            continue
        try:
            lab = _label_decision(st, index, config)
            results.append(_build_match(st, lab, aligned.get(i), index, config, pending))
        except Exception:  # noqa: BLE001
            log.exception("matching failed for deck number %s", i)
            errors += 1
            results.append(Match(d, None, "unmatched", note="matching error"))

    if pending and _llm_enabled(config):
        _run_llm(pending, index, config, warnings)

    # Both ends of a displayed range reconciling to one low/high pair of cells is deterministic evidence,
    # so unresolved range endpoints are recovered here, not only in the optional source search.
    verify_range_pairs(results, index, display_precision(config), recover=True)

    if old_model_values:
        try:
            _apply_staleness(results, index, old_model_values, config)
        except Exception:  # noqa: BLE001
            log.exception("staleness check failed")
            warnings.append(ReviewWarning(code="staleness_failed",
                                          message="Comparison against the previous model failed; no stale numbers were detected."))
    if errors:
        warnings.append(ReviewWarning(
            code="match_error", count=errors,
            message=f"{errors} deck number(s) could not be matched because of an internal error and are reported as unmatched."))
    return results
