"""Model index: labels normalized ONCE per run, inverted index, IDF weights.

Why this exists: matching 300 deck numbers against 200k model cells must never
normalize a model label inside the per-deck loop.  Everything label-related that
depends only on the model is computed here, once:

* every cell gets label parts for its named range, row label, column label,
  section label and sheet name (``split_label`` is cached per distinct string,
  so a 200k-cell sheet with 2k distinct labels costs 2k normalizations);
* ``mv.label_norm`` / ``mv.metric_key`` are filled for downstream consumers;
* an inverted index ``token -> cell indices`` and IDF weights make candidate
  generation proportional to the postings of the deck number's own tokens;
* label-overlap scores are memoised per (deck token set, label token set), so
  the many cells that share a row label are scored once.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from numcheck.config import Config
from numcheck.matching.label_matcher import (
    LabelParts,
    metric_key,
    normalize_label,
    normalize_qualifiers,
    split_label,
)
from numcheck.models import ModelValue

EMPTY: frozenset[str] = frozenset()

# Sheet / section names that carry no entity signal ("Model", "Output", "IS", "Annual" ...).
GENERIC_CONTEXT = frozenset("""
model output outputs summary is pl p l bs cf inputs input calcs calc calculation calculations annual quarterly
monthly data sheet sheets income statement balance cash flow overview analysis backup old scratch tab page
financials financial assumptions table tables schedule schedules
""".split())

# Mutually exclusive statistic words ("Mean" must never match "Median").
_STAT_GROUPS = {
    "mean": "mean", "average": "mean", "avg": "mean", "median": "median",
    "min": "min", "minimum": "min", "low": "min", "lowest": "min",
    "max": "max", "maximum": "max", "high": "max", "highest": "max",
}
_STAT_RE = re.compile(r"\b(" + "|".join(_STAT_GROUPS) + r")\b", re.I)
_CHANGE_RE = re.compile(r"change|chg|cagr|\bnet\s+(?:new|adds?|additions?|openings?)\b|Δ|delta|\bq/?o/?q\b|\by/?o/?y\b|growth|expansion|increase|improvement|"
                        r"vs\.?\s+prior", re.I)


_COVENANT_RE = re.compile(r"\b(covenant|minimum|maximum|threshold|limit|floor|target|cap)\b", re.I)
_MARKET_CAP_RE = re.compile(r"\bmarket\s+cap\b", re.I)


def covenant_words(text: str) -> frozenset[str]:
    """Covenant / threshold vocabulary in a row label ("Minimum interest cover covenant")."""
    t = _MARKET_CAP_RE.sub(" ", text or "")
    return frozenset(m.lower() for m in _COVENANT_RE.findall(t))


def stat_groups(*texts: str) -> frozenset[str]:
    """Canonical statistic groups (mean/median/min/max) named in the given texts."""
    out: set[str] = set()
    for t in texts:
        for m in _STAT_RE.findall(t or ""):
            out.add(_STAT_GROUPS[m.lower()])
    return frozenset(out)


def expresses_change(*texts: str) -> bool:
    return any(_CHANGE_RE.search(t or "") for t in texts)

# View kinds a deck label can match; "named" gets a bonus because a named range is
# the modeller's own explicit name for the cell.
VIEW_NAMED, VIEW_ROW, VIEW_ROWCOL, VIEW_ROWSEC, VIEW_COL, VIEW_CTX = (
    "named", "row", "row+col", "row+section", "col", "context",
)
DECK_ONLY_WEIGHT = 0.15   # weight of a deck-only decoration token (see DECK_DECORATION)
# Presentation words that decorate a label on a slide but rarely appear in model labels.
DECK_DECORATION = frozenset("""
base case scenario indicative range ranges illustrative approx approximately approximate estimated est current
currently denominated euro dollar dollars sterling usd eur gbp equivalent reported headline overall
summary key selected highlights snapshot implied implies implying end outstanding annual annually yearly year
of the a an record strong solid robust healthy turned positive negative total
""".split())
_CTX_FACTOR = 0.92   # sheet-name context is weaker evidence than the cell's own labels
_COL_FACTOR = 0.95


@dataclass(frozen=True)
class DeckQuery:
    """Pre-weighted deck label tokens (built once per distinct token set)."""

    tokens: frozenset[str]
    weights: dict
    total: float
    key: frozenset  # the token set itself: frozenset hashes are cached, tuples' are not


class ModelIndex:
    """Immutable-after-build view of a list of ModelValue objects."""

    def __init__(self, values: list[ModelValue], config: Config):
        self.config = config
        self.values = values
        n = len(values)
        self.n = n
        self.periods: list[str] = [""] * n
        self.quals: list[frozenset[str]] = [EMPTY] * n
        self._tok: dict[str, frozenset[str]] = {}
        self._parts: dict[str, LabelParts] = {}
        self._row_tok: list[frozenset[str]] = [EMPTY] * n
        self._col_tok: list[frozenset[str]] = [EMPTY] * n
        self._sec_tok: list[frozenset[str]] = [EMPTY] * n
        self._named_tok: list[frozenset[str]] = [EMPTY] * n
        self._sheet_tok: list[frozenset[str]] = [EMPTY] * n
        self._col_entity: list[frozenset[str]] = [EMPTY] * n
        self._ctx_ent: list[frozenset[str]] = [EMPTY] * n
        self._stats: list[frozenset[str]] = [EMPTY] * n
        self._change: list[bool] = [False] * n
        self._cov: list[frozenset[str]] = [EMPTY] * n
        self._rowcol: dict[tuple[str, str], frozenset[str]] = {}
        self._label_cache: dict[tuple[str, str, str], tuple[str, str]] = {}
        self.by_address: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.postings: dict[str, list[int]] = defaultdict(list)
        self._wcache: dict[frozenset[str], float] = {}
        self._score_cache: dict[tuple, tuple[float, str]] = {}
        self._cand_cache: dict[tuple, list[tuple[int, float]]] = {}
        self.scored_cache: dict[tuple, list[tuple[int, float, str]]] = {}   # filled by pipeline._scored_candidates
        self._value_index = None
        self._build()
        self.idf: dict[str, float] = {}
        self.max_idf = math.log(1.0 + max(n, 1))
        for tok, post in self.postings.items():
            self.idf[tok] = math.log(1.0 + n / len(post))

    # ------------------------------------------------------------------ build
    def parts(self, text: str) -> LabelParts:
        p = self._parts.get(text)
        if p is None:
            p = split_label(text, self.config)
            self._parts[text] = p
        return p

    def tokens_of(self, text: str) -> frozenset[str]:
        t = self._tok.get(text)
        if t is None:
            t = self.parts(text).tokens if text else EMPTY
            self._tok[text] = t
        return t

    def _build(self) -> None:
        post = self.postings
        qual_cache: dict[tuple, frozenset[str]] = {}
        stat_cache: dict[tuple, tuple] = {}
        for i, mv in enumerate(self.values):
            named, row, col, sec, sheet = mv.named_range, mv.row_label, mv.col_label, mv.section_label, mv.sheet_name
            pn, pr, pc, ps = self.parts(named), self.parts(row), self.parts(col), self.parts(sec)
            tn, tr, tc, ts, tsh = (self.tokens_of(named), self.tokens_of(row), self.tokens_of(col),
                                   self.tokens_of(sec), self.tokens_of(sheet))
            self._named_tok[i], self._row_tok[i], self._col_tok[i] = tn, tr, tc
            self._sec_tok[i], self._sheet_tok[i] = ts, tsh
            self._col_entity[i] = frozenset(pc.entity) if pc.entity else EMPTY
            self._ctx_ent[i] = (ts | tsh) - GENERIC_CONTEXT
            skey = (named, row, col, sec)
            sc_ = stat_cache.get(skey)
            if sc_ is None:
                sc_ = (stat_groups(named, row, col, sec), expresses_change(named, row, col, sec),
                       covenant_words(f"{named} {row}"))
                stat_cache[skey] = sc_
            self._stats[i], self._change[i], self._cov[i] = sc_
            row_period = pr.period
            if row_period and "mo" in row_period and pr.tokens:
                row_period = ""   # "Norvik Logistics Mar-2019": an entry / maturity date attribute, not the period
            self.periods[i] = mv.period or pc.period or pn.period or row_period or ps.period
            qkey = (named, row, col, sec, tuple(mv.qualifiers))
            q = qual_cache.get(qkey)
            if q is None:
                q = pn.qualifiers | pr.qualifiers | pc.qualifiers | ps.qualifiers
                if mv.qualifiers:
                    q = q | normalize_qualifiers(list(mv.qualifiers), self.config)
                qual_cache[qkey] = q
            self.quals[i] = q
            lk = (named, row, col)
            ln = self._label_cache.get(lk)
            if ln is None:
                joined = f"{named} {row} {col}"
                ln = (normalize_label(joined, self.config), metric_key(joined, self.config) or "unnamed")
                self._label_cache[lk] = ln
            mv.label_norm, mv.metric_key = ln
            self.by_address[(sheet, mv.cell_address)].append(i)
            for t in tn | tr | tc | ts | tsh:
                post[t].append(i)

    # ----------------------------------------------------------------- weights
    def weight(self, toks: frozenset[str]) -> float:
        w = self._wcache.get(toks)
        if w is None:
            idf = self.idf
            w = sum(idf.get(t, self.max_idf) for t in toks)
            self._wcache[toks] = w
        return w

    def query(self, tokens: frozenset[str]) -> DeckQuery:
        # A deck token the model never uses ("base", "case", "indicative", "euro-denominated") is deck-only
        # decoration: it must not sink an otherwise good label match, so it gets a small fixed weight.
        # Only generic decoration is discounted; a deck-only CONTENT word ("addressable" in
        # "Total Addressable Market") is real evidence the cell is something else.
        # Decoration words ("range", "implied", "indicative") are weak evidence even when some model
        # label happens to use them ("52-week trading range"): a rare word is not a meaningful one.
        weights = {t: (DECK_ONLY_WEIGHT if t in DECK_DECORATION else self.idf.get(t, self.max_idf)) for t in tokens}
        return DeckQuery(tokens, weights, sum(weights.values()), tokens)

    # -------------------------------------------------------------- candidates
    def candidates(self, q: DeckQuery) -> list[tuple[int, float]]:
        """Cells whose label tokens (all fields together) cover enough of the deck's tokens.

        The coverage floor is the exact bound implied by the scoring formula
        (score <= coverage**1.5), so no cell that could pass ``min_label_score``
        is dropped.  Results are cached per token set: table rows repeat labels.
        """
        hit = self._cand_cache.get(q.key)
        if hit is not None:
            return hit
        if q.total <= 0:
            return []
        floor = max(self.config.min_label_score - 0.08, 0.05) ** (2.0 / 3.0)
        acc: dict[int, float] = defaultdict(float)
        for t, w in q.weights.items():
            for i in self.postings.get(t, ()):
                acc[i] += w
        need = floor * q.total
        out = [(i, a / q.total) for i, a in acc.items() if a >= need]
        if len(self._cand_cache) < 20000:
            self._cand_cache[q.key] = out
        return out

    def label_score(self, q: DeckQuery, i: int) -> tuple[float, str]:
        """Best IDF-weighted overlap between the deck query and cell *i*'s label views.

        score = coverage_of_deck_tokens**1.5 * (0.6 + 0.4 * coverage_of_cell_tokens): missing a
        deck token is costly (the deck says "margin", the cell does not), extra cell
        tokens (column/section context) cost little.
        """
        best, kind = 0.0, ""
        mv = self.values[i]
        views = [(VIEW_ROW, self._row_tok[i], 1.0)]
        if self._named_tok[i]:
            views.append((VIEW_NAMED, self._named_tok[i], 1.0))
        if self._col_tok[i]:
            rc_key = (mv.row_label, mv.col_label)
            rc = self._rowcol.get(rc_key)
            if rc is None:
                rc = self._row_tok[i] | self._col_tok[i]
                self._rowcol[rc_key] = rc
            views.append((VIEW_ROWCOL, rc, 1.0))
            views.append((VIEW_COL, self._col_tok[i], _COL_FACTOR))
        if self._sec_tok[i]:
            views.append((VIEW_ROWSEC, self._row_tok[i] | self._sec_tok[i], 0.97))
        ctx = self._row_tok[i] | self._col_tok[i] | self._sec_tok[i] | self._sheet_tok[i]
        if self._sheet_tok[i] or self._sec_tok[i]:
            views.append((VIEW_CTX, ctx, _CTX_FACTOR))
        for name, vt, fac in views:
            if not vt:
                continue
            ck = (q.key, vt)
            hit = self._score_cache.get(ck)
            if hit is None:
                inter = 0.0
                for t, w in q.weights.items():
                    if t in vt:
                        inter += w
                if inter <= 0.0:
                    s = 0.0
                else:
                    cov_d = inter / q.total
                    cov_v = inter / max(self.weight(vt), 1e-9)
                    s = cov_d ** 1.5 * (0.6 + 0.4 * cov_v)
                if len(self._score_cache) < 2_000_000:
                    self._score_cache[ck] = (s, name)
                hit = (s, name)
            s = hit[0] * fac
            if s > best:
                best, kind = s, name
        return best, kind

    # --------------------------------------------------------- label helpers
    def col_entities(self, i: int) -> frozenset[str]:
        """Company/segment-style tokens of the column header (peer names in a comps table)."""
        return self._col_entity[i]

    def context_entities(self, i: int) -> frozenset[str]:
        """Sheet/section tokens that name an entity or topic (generic names such as "Model" removed)."""
        return self._ctx_ent[i]

    def stats(self, i: int) -> frozenset[str]:
        """mean/median/min/max groups named in the cell's labels."""
        return self._stats[i]

    def covenant(self, i: int) -> frozenset[str]:
        """Covenant/threshold words of the cell's row label (minimum, covenant, limit, cap ...)."""
        return self._cov[i]

    def is_change(self, i: int) -> bool:
        """True when the cell's labels say it expresses a change (change, delta, QoQ, YoY, growth ...)."""
        return self._change[i]

    def label_tokens(self, i: int) -> frozenset[str]:
        """Every label token of cell *i*: row, named range, column header and section."""
        return self._row_tok[i] | self._named_tok[i] | self._col_tok[i] | self._sec_tok[i]

    def row_tokens(self, i: int) -> frozenset[str]:
        return self._row_tok[i] | self._named_tok[i]

    def col_tokens(self, i: int) -> frozenset[str]:
        return self._col_tok[i]

    def value_index(self):
        """Sorted value index for value-only / alignment lookups (built lazily, once)."""
        if self._value_index is None:
            from numcheck.matching.value_match import ValueIndex
            self._value_index = ValueIndex(self.values, self.config)
        return self._value_index
