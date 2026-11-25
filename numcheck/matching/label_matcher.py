"""Label normalization and decomposition.

Why this exists: deck labels ("Net sales FY2024 ($m)") and model labels
("Revenue_FY24") are written by different people.  We reduce both to the same
canonical token vocabulary so that the matching engine can compare *meaning*:

* abbreviations/synonyms are expanded in ONE regex pass, longest key first, so
  ``net sales`` -> ``revenue`` wins over ``sales`` -> ``revenue`` and expansions
  never cascade into each other;
* period spellings collapse to the canonical keys of :mod:`numcheck.periods`
  and are pulled out of the metric tokens (the period is matched separately);
* unit noise (``$``, ``m``, ``bn``, ``%``, ``x``, ``in millions``) and pure
  numbers are dropped;
* qualifiers that change meaning (``adjusted``, ``diluted`` ...) are split off
  so a conflict between them can be penalised hard instead of being one more
  token in a Jaccard score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from numcheck.config import Config
from numcheck.models import ModelValue
from numcheck.periods import find_periods

STOPWORDS = {
    "total", "subtotal", "sum", "of", "the", "a", "an", "and",
    "for", "at", "as", "in", "to", "on", "by", "per",
    "less", "plus", "add", "minus",   # sign markers: "Less: cash", "Plus: D&A"
}

UNIT_TOKENS = {
    "m", "mm", "mn", "bn", "bln", "k", "b", "t", "x",
    "usd", "eur", "gbp", "jpy", "chf", "cad", "aud",
    "in", "million", "millions", "thousand", "thousands", "billion", "billions", "mio",
    "pct", "percent", "except", "data",
}

# Qualifiers whose absence on the deck side is still a (weaker) signal and whose
# presence on the model side only is a strong one.
STRICT_QUALIFIERS = frozenset({"adjusted", "pro forma", "diluted", "ltm", "ntm", "low", "high",
                               "same store", "same property", "organic", "like for like"})

# Asset / tranche identifiers keep their number: "CVX-101" -> "cvx101", "Phase 3" -> "phase3".
_CODE_JOIN_RE = re.compile(r"\b([A-Za-z]{1,8})-(\d{1,4}[A-Za-z]?)\b")
_ORDINAL_JOIN_RE = re.compile(
    r"\b(phase|stage|tranche|series|fund|class|tier|round|lot|block|segment|division|region|plant|site|facility|"
    r"building|tower|store|project|portfolio|line|vintage|sleeve|pool|campus|mine|well|vessel|cohort|wave|"
    r"option|scenario)\s+(\d{1,3}[a-z]?|[ivx]{1,4})\b", re.I)

CONFLICT_PAIRS: tuple[tuple[str, str], ...] = (
    ("adjusted", "reported"), ("adjusted", "gaap"), ("non gaap", "gaap"), ("non gaap", "reported"),
    ("diluted", "basic"), ("low", "high"), ("min", "max"),
    ("pro forma", "standalone"), ("pro forma", "reported"),
    ("levered", "unlevered"), ("pre tax", "post tax"), ("pre synergies", "post synergies"),
    ("target", "acquirer"), ("target", "buyer"), ("seller", "buyer"),
    ("constant currency", "reported"), ("normalized", "reported"), ("underlying", "reported"),
)

# Words that are clearly "metric" vocabulary; anything else that is capitalised in
# the source text is treated as an entity (company/segment name) token.
FINANCE_VOCAB = frozenset("""
revenue income net gross operating profit margin ebitda ebit ebt eps earnings cash flow free capital
expenditures expenses expense cost goods sold depreciation amortization interest tax taxes debt equity
value enterprise market capitalization shares share price offer premium purchase leverage multiple growth
cagr dividend dividends assets liabilities working book tangible return roic roe roa irr moic wacc
terminal rate discount sga research development marketing sales general administrative ratio
ebitdar capex opex fcf nwc backlog margin yield payout coverage turnover ordinary exceptional
""".split())

def _stem(t: str) -> str:
    """Conservative plural folding so "expenses"/"expense", "taxes"/"tax", "revenues"/"revenue" agree.

    Applied identically to deck and model labels, so it only has to be consistent,
    not linguistically perfect.  Tokens with digits (period keys) are untouched.
    """
    if len(t) <= 3 or any(c.isdigit() for c in t):
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith("sses") or t.endswith("xes"):
        return t[:-2]
    if t.endswith(("ss", "us", "is", "ics")):
        return t
    if t.endswith("s"):
        return t[:-1]
    return t


FINANCE_STEMS = frozenset(_stem(w) for w in FINANCE_VOCAB) | FINANCE_VOCAB

_PERIOD_TOKEN = re.compile(r"^(?:(?:fy|cy|y)\d{2}(?:q[1-4]|h[12]|m[369]|mo\d{2})?|ltm|ntm|q[1-4])$")
_NUM_SUFFIX = re.compile(r"^\d+s$")
_RATIO_RE = re.compile(r"[a-z0-9)]\s*/\s*[a-z0-9(]")
_PROPER_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")


@dataclass(frozen=True)
class LabelParts:
    """A label split into the pieces the matcher scores separately."""

    metric: tuple[str, ...] = ()          # vocabulary tokens (revenue, ebitda ...)
    entity: tuple[str, ...] = ()          # company / segment style tokens
    qualifiers: frozenset[str] = frozenset()
    period: str = ""                      # canonical period key of the first period found
    ratio: bool = False                   # label contained "A / B"

    @property
    def tokens(self) -> frozenset[str]:
        """All content tokens (metric + entity)."""
        return frozenset(self.metric) | frozenset(self.entity)


# --------------------------------------------------------------------------- #
# Lexicon (compiled once per Config)
# --------------------------------------------------------------------------- #

def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


class _Lexicon:
    def __init__(self, config: Config):
        self.sig = _signature(config)
        abbr: dict[str, str] = {}
        for k, v in config.abbreviations.items():
            nk = " ".join(str(k).lower().split())
            if nk:
                abbr[nk] = str(v).lower()
        self.abbr = abbr
        keys = sorted(abbr, key=len, reverse=True)
        self.abbr_re = None
        if keys:
            alts = "|".join(r"\s+".join(re.escape(w) for w in k.split()) for k in keys)
            self.abbr_re = re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])")
        quals = {tuple(_words(q)) for q in config.qualifiers}
        self.quals = sorted((q for q in quals if q), key=len, reverse=True)
        self.cache: dict[str, tuple[list[str], bool]] = {}
        self.parts_cache: dict[str, LabelParts] = {}


_LEX: dict[int, _Lexicon] = {}


def _signature(config: Config) -> tuple:
    return (id(config.abbreviations), len(config.abbreviations), id(config.qualifiers), len(config.qualifiers))


def _lexicon(config: Config) -> _Lexicon:
    lex = _LEX.get(id(config))
    sig = _signature(config)
    if lex is None or lex.sig != sig:
        if len(_LEX) > 64:
            _LEX.clear()
        lex = _Lexicon(config)
        _LEX[id(config)] = lex
    return lex


def _replace_periods(s: str) -> str:
    found = find_periods(s)
    if not found:
        return s
    out: list[str] = []
    last = 0
    for p, a, b in found:
        out.append(s[last:a])
        out.append(f" {p.key} ")
        last = b
    out.append(s[last:])
    return "".join(out)


def _tokens(text: str, lex: _Lexicon) -> tuple[list[str], bool]:
    """Ordered normalized tokens (stopwords and period keys included) and a ratio flag."""
    hit = lex.cache.get(text)
    if hit is not None:
        return hit
    s = (text or "").lower().replace("_", " ").replace("’", "'")
    s = _replace_periods(s)
    if lex.abbr_re is not None:
        s = lex.abbr_re.sub(lambda m: " " + lex.abbr[" ".join(m.group(0).split())] + " ", s)
    ratio = bool(_RATIO_RE.search(s)) and "n/a" not in s
    s = s.replace("&", " and ")
    out: list[str] = []
    for t in re.findall(r"[a-z0-9]+", s):
        if t in UNIT_TOKENS or t.isdigit() or _NUM_SUFFIX.match(t):
            continue
        out.append(_stem(t))
    if ratio:
        out.append("ratio")
    res = (out, ratio)
    if len(lex.cache) < 500_000:
        lex.cache[text] = res
    return res


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def normalize_label(text: str, config: Config) -> str:
    """Lowercase, expand synonyms, canonicalise periods, drop unit/number noise."""
    toks, _ = _tokens(text, _lexicon(config))
    return " ".join(toks)


def metric_key(text: str, config: Config) -> str:
    """Canonical metric name (periods/numbers/units/stopwords removed, de-duplicated).

    Qualifiers are kept on purpose: "adjusted ebitda" is not "ebitda".
    """
    toks, _ = _tokens(text, _lexicon(config))
    seen: set[str] = set()
    out: list[str] = []
    for t in toks:
        if t in STOPWORDS or _PERIOD_TOKEN.match(t) or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return " ".join(out)


def split_label(text: str, config: Config) -> LabelParts:
    """Split a label into metric tokens, qualifiers, period and entity tokens."""
    lex = _lexicon(config)
    hit = lex.parts_cache.get(text)
    if hit is not None:
        return hit
    key = text
    text = _ORDINAL_JOIN_RE.sub(lambda m: m.group(1) + m.group(2), _CODE_JOIN_RE.sub(r"\1\2", text or ""))
    toks, ratio = _tokens(text, lex)
    period = ""
    content: list[str] = []
    for t in toks:
        if _PERIOD_TOKEN.match(t):
            period = period or t
        elif t not in STOPWORDS:
            content.append(t)
    quals: set[str] = set()
    rest: list[str] = []
    i = 0
    while i < len(content):
        for q in lex.quals:
            n = len(q)
            if tuple(content[i:i + n]) == q:
                quals.add(" ".join(q))
                i += n
                break
        else:
            rest.append(content[i])
            i += 1
    proper = {_stem(w.lower()) for w in _PROPER_RE.findall((text or "").replace("_", " "))}
    metric: list[str] = []
    entity: list[str] = []
    seen: set[str] = set()
    for t in rest:
        if t in seen:
            continue
        seen.add(t)
        if t not in FINANCE_STEMS and t in proper:
            entity.append(t)
        else:
            metric.append(t)
    parts = LabelParts(tuple(metric), tuple(entity), frozenset(quals), period, ratio)
    if len(lex.parts_cache) < 500_000:
        lex.parts_cache[key] = parts
    return parts


def normalize_qualifiers(items: list[str], config: Config) -> frozenset[str]:
    """Canonicalise extractor-provided qualifier strings ("Adj." -> "adjusted")."""
    out: set[str] = set()
    for it in items or []:
        p = split_label(it, config)
        out |= p.qualifiers
        if not p.qualifiers:
            # a bare word the extractor flagged that is not in config.qualifiers
            out |= set(p.metric)
    return frozenset(out)


@lru_cache(maxsize=4096)
def _conflict(a: frozenset, b: frozenset) -> bool:
    for x, y in CONFLICT_PAIRS:
        if (x in a and y in b) or (y in a and x in b):
            return True
    return False


@lru_cache(maxsize=65536)
def qualifier_factor(deck: frozenset, cand: frozenset) -> float:
    """Multiplier (<=1) for how well a candidate's qualifiers fit the deck's.

    Conflicts (adjusted vs reported, diluted vs basic, low vs high ...) and
    candidate-only strict qualifiers (adjusted, pro forma, diluted, low, high)
    are strongly penalised; softer differences cost a little.
    """
    if deck == cand:
        return 1.0
    f = 1.0
    if _conflict(deck, cand):
        f *= 0.3
    for q in cand - deck:
        if q in STRICT_QUALIFIERS and not _conflict(deck, frozenset({q})):
            f *= 0.5
        elif q not in STRICT_QUALIFIERS:
            f *= 0.92
    for q in deck - cand:
        f *= 0.85 if q in STRICT_QUALIFIERS else 0.92
    return max(f, 0.05)


# --------------------------------------------------------------------------- #
# Legacy pairwise score (kept for callers/tests that score one cell at a time)
# --------------------------------------------------------------------------- #

def _jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def label_score(deck_norm: str, mv: ModelValue, config: Config) -> float:
    """Best token-Jaccard similarity between a normalized deck label and a model value's labels.

    The matching engine uses IDF-weighted scoring over an inverted index
    (:mod:`numcheck.matching.index`); this unweighted form remains for one-off use.
    """
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
