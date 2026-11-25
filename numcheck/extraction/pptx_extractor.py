"""Extract checkable numbers from a PowerPoint deck.

Walks every slide (text frames, tables, charts, SmartArt, groups, placeholders),
records slide metadata (title, unit banner, period, footnotes, pictures) and
returns a :class:`~numcheck.models.DeckExtraction`.

Design notes
------------
* Everything is deterministic; no network, no LLM.
* One malformed shape never aborts a run: failures are caught per shape and
  surfaced as :class:`ReviewWarning` objects.
* Labels are built *before* periods are computed (the old extractor computed the
  period from the bare cell text, so table cells never received one).
"""

from __future__ import annotations

import re
import zipfile  # noqa: F401  (kept for parity with fileio users)
from dataclasses import dataclass, field, replace
from datetime import date

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.shapes.group import GroupShape

from numcheck.config import Config
from numcheck.extraction import fileio
from numcheck.extraction.chart_extractor import PER_SHARE_RE, extract_chart_numbers, merge_unit_hints
from numcheck.extraction.xlsx_structure import _is_block_title
from numcheck.matching.normalizer import (
    ParsedNumber,
    UnitHint,
    explicit_currency_unit,
    is_fx_rate_label,
    is_unit_price_label,
    parse_unit_banner,
    scan_numbers,
)
from numcheck.models import (
    DeckExtraction,
    DeckNumber,
    NumCheckInputError,
    ReviewWarning,
    SlideInfo,
    Source,
)
from numcheck.periods import find_periods, header_date_period

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CHARTEX_NS = "http://schemas.microsoft.com/office/drawing/2014/chartex"
_DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_SKIP_PLACEHOLDERS = {PP_PLACEHOLDER.SLIDE_NUMBER, PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.FOOTER}
_TITLE_PLACEHOLDERS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}

# --------------------------------------------------------------------------- #
# Text helpers: labels, clauses, periods, qualifiers
# --------------------------------------------------------------------------- #

_FILLER = {
    "of", "to", "was", "at", "by", "grew", "increased", "decreased", "reached", "approximately", "c", "ca",
    "in", "with", "and", "a", "an", "the", "is", "were", "totalled", "totaled", "from", "for", "on", "up",
    "down", "than", "about", "around", "roughly", "approx", "rose", "fell", "declined", "stood", "came",
    "amounted", "reaching", "are", "be", "as", "or", "vs", "versus", "its", "our", "their", "has", "had",
    "have", "grow", "growing", "increase", "decrease", "gain", "gained", "rising", "falling", "equal",
    "equals", "over", "under", "near", "nearly", "almost", "circa", "~",
    "expanded", "expanding", "contracted", "contracting", "improved", "widened", "narrowed", "dropped",
    "including", "excluding", "undrawn", "implies", "implying", "representing", "reflecting", "since",
}
# Words that end an "after the number" noun phrase ("9.0% WACC and 2.5% ...").
_AFTER_STOP = _FILLER | {
    "compared", "which", "that", "while", "but", "because", "after", "before", "during", "across", "between",
    "through", "driven", "equating", "equivalent", "when", "where", "yielding", "resulting", "assuming",
    "also",
    # verbs and adverbs that start a predicate ("Restructuring of $6.4m relates to ...")
    "relates", "relate", "related", "relating", "reflects", "reflect", "represents", "represent", "comprises",
    "comprise", "consists", "consist", "includes", "include", "excludes", "accounts", "arises", "arose", "stems",
    "comes", "attributable", "due", "primarily", "mainly", "largely", "mostly", "principally", "will", "would",
    "could", "should", "may", "might", "can", "remains", "remained", "continues", "continued", "owing", "following",
}
_AFTER_LEAD = {"of", "the", "a", "an", "c", "ca", "approximately", "approx", "about", "around", "circa"}
# Interior words after which a label restarts ("Acquisition of Target at an enterprise value of $4.4bn").
_BOUNDARY_RE = re.compile(
    r"\b(?:at|with|implies|implying|suggests|represents|representing|reflects|assumes|yields|values)\b", re.I)
_MONTH_PAT = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
              r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_LEAD = r"(?:(?:as\s+(?:of|at)|at|on|since|until|from|by)\s+)?"
_DATE_TEXT_RE = re.compile(
    _DATE_LEAD + r"(?:(?:\d{1,2}(?:st|nd|rd|th)?[-\s./]+)?\b" + _MONTH_PAT + r"[a-z]*\.?[-\s./,']*(?:\d{4}|\d{2})\b"
    r"|\b" + _MONTH_PAT + r"[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?"
    r"|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b)", re.I)
_YOY_RE = re.compile(
    r"year[\s-]+(?:over|on)[\s-]+year|\by[\s-]?o[\s-]?y\b|\byoy\b|\by/y\b|per\s+annum|\bp\.a\.|\bannually\b",
    re.I,
)
# Hard clause boundaries: semicolon, newline, bullets, sentence end before a capital.
_HARD_RE = re.compile(r"[;\n•▪●◦►]|(?<=[A-Za-z0-9)%])\.\s+(?=[A-Z(])")
_MAX_LABEL_WORDS = 8


def _norm_token(tok: str) -> str:
    return tok.lower().strip(".,;:()[]{}\"'“”‘’")


def _clean_label(segment: str, *, keep_head: bool = False) -> str:
    """Strip filler words/punctuation at both edges and cap at ~8 words."""
    toks = segment.split()
    while toks and (_norm_token(toks[0]) in _FILLER or not re.search(r"[A-Za-z0-9]", toks[0])):
        toks.pop(0)
    while toks and (_norm_token(toks[-1]) in _FILLER or not re.search(r"[A-Za-z0-9]", toks[-1])):
        toks.pop()
    if not toks:
        return ""
    toks = toks[:_MAX_LABEL_WORDS] if keep_head else toks[-_MAX_LABEL_WORDS:]
    return re.sub(r"^[\s,:;\-–—|]+|[\s,:;\-–—|]+$", "", " ".join(toks))


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace spans with spaces (same length, so offsets stay valid)."""
    if not spans:
        return text
    chars = list(text)
    for s, e in spans:
        for i in range(max(s, 0), min(e, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    s, e = 0, len(text)
    for m in _HARD_RE.finditer(text):
        if m.group(0).startswith(".") and _ABBREV_BEFORE_DOT.search(text[:m.start()]):
            continue   # "Adj. EBITDA", "Inc. Revenue": an abbreviation, not a sentence end
        if m.end() <= start:
            s = m.end()
        elif m.start() >= end:
            e = m.start()
            break
    return s, e


def _period_spans(text: str) -> list[tuple[int, int, str]]:
    return [(s, e, p.key) for p, s, e in find_periods(text)]


_SPAN_SEP_RE = re.compile(r"\s*(?:[-–—‒]|to|through|thru|and)\s*", re.I)
_PAREN_SPAN_RE = re.compile(r"\([^()]*\)")


def _dist(s: int, e: int, start: int, end: int) -> int:
    return 0 if (s < end and e > start) else (s - end if s >= end else start - e)


def _choose_period(text: str, c_start: int, c_end: int, n: ParsedNumber) -> tuple[str, bool]:
    """Period for a prose number as ``(key, span_only)``.

    * periods that are endpoints of a span ("2022A-2026E", "from FY23 to FY26", "between 2024 and 2025E")
      are ignored, unless the number is itself a range endpoint (low -> span start, high -> span end);
    * periods inside a parenthetical aside never apply to numbers outside it;
    * ``span_only`` is True when the clause's only periods are span endpoints (the caller must then NOT
      fall back to the slide period hint, which would invent a period for e.g. a CAGR).
    """
    toks = [(s + c_start, e + c_start, key) for s, e, key in _period_spans(text[c_start:c_end])]
    if not toks:
        return "", False
    parens = [(m.start() + c_start, m.end() + c_start) for m in _PAREN_SPAN_RE.finditer(text[c_start:c_end])]
    own = next((pr for pr in parens if pr[0] <= n.start < pr[1]), None)
    if own is None:
        toks = [t for t in toks if not any(a <= t[0] < b for a, b in parens)]
    else:
        inside = [t for t in toks if own[0] <= t[0] < own[1]]
        toks = inside or toks
    spans: list[tuple[tuple[int, int, str], tuple[int, int, str]]] = []
    used: set[int] = set()
    for i in range(len(toks) - 1):
        if i in used:
            continue
        a, b = toks[i], toks[i + 1]
        between = text[a[1]:b[0]]
        m = _SPAN_SEP_RE.fullmatch(between)
        if not m:
            continue
        word = between.strip().lower()
        if word == "and" and not re.search(r"\bbetween\s*$", text[max(c_start, a[0] - 12):a[0]], re.I):
            continue
        spans.append((a, b))
        used.update((i, i + 1))
    singles = [t for i, t in enumerate(toks) if i not in used]
    if n.range_role and spans:
        a, b = min(spans, key=lambda ab: _dist(ab[0][0], ab[1][1], n.start, n.end))
        return (a[2] if n.range_role == "low" else b[2]), False
    best, best_d = "", 10 ** 9
    has_before = any(s < n.start for s, _e, _k in singles)
    for s, e, key in singles:
        d = _dist(s, e, n.start, n.end)
        if s >= n.end and re.fullmatch(r"\s*(?:[a-z%]+\s+){0,2}(?:in|for|during|at|over)\s+(?:the\s+)?",
                                       text[n.end:s], re.I):
            d = -1   # "31.0 months in FY24": the period attached right after the number owns it
        elif s < n.start and re.search(r"\bfrom\s+$", text[max(0, n.start - 8):n.start], re.I):
            d += 1000   # "... in FY25 from 31.0": a "from" value belongs to the earlier period, not the one before
        elif s >= n.end and re.search(r"\b(?:before|after|until|till|prior\s+to|beyond|post|through|since)\s+$",
                                      text[max(n.end, s - 12):s], re.I):
            continue    # "$742m with no maturities before 2029": a bound on something else, not this figure's date
        elif s >= n.end and has_before and re.search(r"\d", re.sub(r"\([^)]*\)", "", text[n.end:s])):
            continue    # "28.6%, rising to 28.7% in 2026E": a later figure sits between, the period is its own
        if d < best_d:
            best, best_d = key, d
    if not best and spans:
        return "", True
    return best, False


def _nearest_period(text: str, c_start: int, c_end: int, start: int, end: int) -> str:
    """Period key nearest to [start, end) inside the clause [c_start, c_end) (legacy helper)."""
    best, best_d = "", 10 ** 9
    for s, e, key in _period_spans(text[c_start:c_end]):
        d = _dist(s + c_start, e + c_start, start, end)
        if d < best_d:
            best, best_d = key, d
    return best


def _terms_in(text: str, terms: list[str]) -> list[str]:
    found = []
    for t in terms:
        pat = r"(?<![A-Za-z])" + re.escape(t).replace(r"\ ", r"[\s-]+") + r"(?![A-Za-z])"
        if re.search(pat, text, re.I):
            found.append(t)
    return found


def _mask_text(text: str, removed: list[tuple[int, int]]) -> str:
    """Text with skipped numbers, periods, dates and "year-over-year" phrases blanked (same length)."""
    spans = list(removed) + [(a, b) for a, b, _k in _period_spans(text)]
    spans += [(m.start(), m.end()) for m in _DATE_TEXT_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in _YOY_RE.finditer(text)]
    return _blank_spans(text, spans)


def _before_phrase(seg: str, has_prev: bool) -> str:
    parts = re.split(r"[:()]" + ("|," if has_prev else ""), seg)
    for part in reversed(parts):
        for m in reversed(list(_BOUNDARY_RE.finditer(part))):
            if _clean_label(part[m.end():]):
                part = part[m.end():]
                break
        label = _clean_label(part)
        if label:
            return label
    return ""


_VALUE_BASE_RE = re.compile(r"\b(?:enterprise value|equity value|purchase price|offer price|share price|ev|tev|"
                            r"market cap(?:italization)?|price)\b", re.I)
_FOOTNOTE_MARK_RE = re.compile(r"\(\s*\d{1,2}\s*\)|[¹²³⁴⁵⁶⁷⁸⁹⁰]+|\*+$")
_GENERIC_RATIO_WORDS = {"margin", "growth", "growth rate", "multiple", "yield", "conversion", "cagr",
                        "a margin", "margins", "penetration", "share", "improvement", "an improvement",
                        "deterioration", "a deterioration", "increase", "an increase", "decrease", "a decrease",
                        "reduction", "a reduction", "decline", "a decline", "expansion", "contraction"}
_UNIT_NOUNS = {"years", "year", "months", "month", "days", "weeks", "quarters", "times", "turns", "basis", "points",
                "bps", "pts", "units", "shares"}
_NEG_INTRO_WORDS = {"of", "was", "is", "were", "are", "at", "to", "by", "be", "been", "from", "reached", "totalled",
                    "totaled", "stood", "showed", "recorded", "posted", "reported", "generated", "=", "-", "–",
                    "loss", "outflow", "deficit", "burn"}
_VALENCE_RE = re.compile(r"\b(?:improv\w*|deteriorat\w*|better|worse|weaken\w*|strengthen\w*)\b", re.I)

_ABBREV_BEFORE_DOT = re.compile(
    r"(?:\b(?:adj|approx|est|avg|inc|corp|co|ltd|no|vs|excl|incl|cons|op|pf|dil|wtd|yr|yrs|mgmt|govt|int'?l)|"
    r"\b[A-Za-z])$", re.I)


def _after_phrase(masked: str, start: int, limit: int) -> tuple[str, int]:
    """Noun phrase directly after a number ("9.0% WACC and" -> "WACC"); ``(phrase, end offset)``."""
    if limit <= start:
        return "", 0
    seg = masked[start:limit]
    for cut in re.finditer(r"[,.;:()\n]|\s[-–—]\s", seg):
        if cut.group(0) == "." and _ABBREV_BEFORE_DOT.search(seg[:cut.start()]):
            continue   # "Adj. EBITDA", "approx. 5x", "Inc." are not sentence ends
        seg = seg[:cut.start()]
        break
    words: list[str] = []
    end = 0
    for m in re.finditer(r"\S+", seg):
        norm = _norm_token(m.group(0))
        if not norm or not re.search(r"[a-z0-9]", norm):
            break
        if not words and norm in _AFTER_LEAD:
            continue
        rest = masked[start + m.end():start + m.end() + 24]   # may run past the next number (the threshold)
        tm = re.match(r"\s*[$€£]?\s*\d[\d,.]*\s*(?:k|m|mm|bn|b)?\b(?:\s+[A-Z]{2,6}\b)?", rest)
        if words and norm in ("above", "over", "exceeding", ">") and tm:
            # "1,486 customers above $100k ARR": the threshold is part of the metric name
            words.append(m.group(0) + tm.group(0))
            end = start + m.end() + tm.end()
            break
        if norm == "of" and len(words) == 1 and words[0].lower() in _UNIT_NOUNS:
            continue   # "8.6 years of contract life remaining": the unit noun is followed by the metric
        if norm in _AFTER_STOP or len(words) >= 5:
            break
        words.append(m.group(0).strip(".,;:"))
        end = start + m.end()
    else:
        # the threshold number itself ("above $100k") starts the next token, so it lies past *limit*
        tm = re.match(r"\s*(?:above|over|exceeding|>)\s*[$€£]?\s*\d[\d,.]*\s*(?:k|m|mm|bn|b)?\b(?:\s+[A-Z]{2,6}\b)?",
                      masked[limit:limit + 32], re.I)
        if words and tm and not masked[end:limit].strip():
            words.append(tm.group(0).strip())
            end = limit + tm.end()
    return " ".join(words), end


def _label_for(text: str, masked: str, p: ParsedNumber, prev: "tuple[int, str, int] | None", next_start: int,
               clause: tuple[int, int]) -> tuple[str, int]:
    """Label of a prose number plus the offset up to which the text was consumed by it.

    Candidates: the phrase BEFORE the number (since the previous number/clause boundary) and the noun
    phrase AFTER it ("9.0% WACC", "$1.2bn of revenue").  The after phrase wins when the before phrase is
    empty/filler (or was already eaten by the previous number's after phrase) and is appended when the
    before phrase is very short ("Proposed offer" + "per share").  *prev* is (end offset, label,
    consumed-up-to offset) of the previous checkable number; when nothing describes this number but
    connective filler ("grew 9% year-over-year to $1.2bn") the previous label is inherited.
    """
    c_start, c_end = clause
    has_prev = prev is not None and prev[0] >= c_start
    seg_start = max(prev[0], prev[2]) if has_prev and prev else c_start
    before = _before_phrase(masked[seg_start:p.start], has_prev)
    after, after_end = _after_phrase(masked, p.end, min(next_start, c_end))
    before, after = _FOOTNOTE_MARK_RE.sub("", before).strip(), _FOOTNOTE_MARK_RE.sub("", after).strip()
    seg_text = masked[seg_start:p.start]
    # "Unfunded commitments of EUR 140.0m (20.0% of commitments)", "$19.3m, or 7.6% of Adjusted EBITDA": a share
    # written right after an amount is that amount as a % of the named base, not the base itself.
    if (p.dimension == "percent" and has_prev and prev and prev[1] and after
            and re.search(r"(?:\(|,\s*or|\bor|\brepresenting|\bi\.e\.,?)\s*$", masked[max(0, p.start - 16):p.start], re.I)
            and re.match(r"\s*of\s", masked[p.end:p.end + 5], re.I)):
        return f"{_FOOTNOTE_MARK_RE.sub('', prev[1]).strip()} as % of {after}", after_end
    # "... of site lease revenue, with 8.6 years of contract life remaining": nothing but filler after the
    # last comma, so the words before it describe the previous number; this one is named after it.
    if (after and p.dimension != "multiple" and "," in seg_text
            and not _before_phrase(seg_text.rsplit(",", 1)[1], has_prev)):
        return after, after_end
    # "EUR 524.0m invested cost and a gross MOIC of 1.59x": the label is the last coordinated noun phrase
    before = re.split(r"\s+and\s+(?:an?|the)\s+", before)[-1].strip()
    # "Norvik Logistics fully realised at 1.91x gross MOIC": subject + the metric named after the number
    if (before and after and re.search(r"\bat\s*[(~]?\s*$", masked[seg_start:p.start], re.I)
            and after.lower() not in before.lower()):
        return f"{before} {after}", after_end
    # "Adj. EBITDA of $116.9bn at a margin of 25.4%": a bare ratio word refers back to the metric
    # named just before ("EBITDA margin"), not to every margin in the model.
    if has_prev and prev and prev[1] and before and before.lower() in _GENERIC_RATIO_WORDS:
        return f"{_FOOTNOTE_MARK_RE.sub('', prev[1]).strip()} {before}", 0
    # "enterprise value of $4.4bn, 11.0x LTM Adj. EBITDA": a multiple written right after a value is that
    # value over the named metric (EV / EBITDA), not the metric itself.
    if (p.dimension == "multiple" and not before and after and "/" not in after and has_prev and prev
            and _VALUE_BASE_RE.search(prev[1] or "")):
        base = _VALUE_BASE_RE.search(prev[1]).group(0)
        return f"{base} / {after}", after_end
    if not before:
        if after:
            return after, after_end
        if has_prev and prev and prev[1]:
            return prev[1], 0
        return "", 0
    if after and len(before.split()) <= 2 and after.lower() not in before.lower():
        return f"{before} {after}", after_end
    return before, 0



# --------------------------------------------------------------------------- #
# Per-slide context
# --------------------------------------------------------------------------- #


@dataclass
class _SlideCtx:
    number: int
    config: Config
    height: int = 0
    title: str = ""
    context_text: str = ""
    hint: UnitHint = field(default_factory=UnitHint)
    hint_text: str = ""
    period_hint: str = ""
    numbers: list[DeckNumber] = field(default_factory=list)
    skipped: list[DeckNumber] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    as_of: list[str] = field(default_factory=list)
    image_count: int = 0
    warnings: list[ReviewWarning] = field(default_factory=list)


def _make_number(ctx: _SlideCtx, p: ParsedNumber, *, source: Source, shape_id: str, para_idx: int,
                 label: str, period: str, context: str, uid: str, skip_reason: str = "") -> DeckNumber:
    return DeckNumber(
        slide_number=ctx.number, shape_id=shape_id, source=source, raw_text=p.raw, value=p.value,
        unit=p.dimension, scale=p.scale, period=period, label=label, context=context,
        significant=p.significant, decimals=p.decimals, currency=p.currency, comparator=p.comparator,
        is_range=p.is_range, range_role=p.range_role, unit_source=p.unit_source or "",
        scale_known=p.scale_known, slide_title=ctx.title, slide_context=ctx.context_text or ctx.title,
        paragraph_index=para_idx,
        char_start=p.start, char_end=p.end, skip_reason=skip_reason or p.skip_reason, uid=uid,
    )


_ENTITY_QUALIFIERS = {"target", "acquirer", "buyer", "seller"}
_DECLINE_RE = re.compile(
    r"(?:\bdeclin(?:ed|es|ing|e)|\bdecreas(?:ed|es|ing|e)|\bfell|\bfall(?:s|ing)?|\bdrop(?:ped|s|ping)?|\bdown"
    r"|\bcontract(?:ed|s|ing|ion)|\bshr[ua]nk|\breduc(?:ed|tion)|\bdipped|\bslipped)"
    r"(?:\s+(?:by|of|approximately|about|roughly|around|c\.?|~|nearly))*\s*$", re.I)
_CHANGE_BEFORE_RE = re.compile(
    r"(?:\bup|\bdown|\bexpand(?:ed|s|ing)?|\bcontract(?:ed|s|ing)?|\bincreas(?:ed|es|ing|e)|\bdecreas(?:ed|es|ing|e)"
    r"|\bimprov(?:ed|es|ing|ement)|\bwiden(?:ed|s|ing)?|\bnarrow(?:ed|s|ing)?|\brose|\bfell|\bdeclin(?:ed|es|ing|e)"
    r"|\bgain(?:ed)?|\bdrop(?:ped)?|\bgrew|\bcompress(?:ed|ion)|\bwas)"
    r"(?:\s+(?:by|of|approximately|about|roughly|around|c\.?|~|nearly))*\s*$", re.I)
_CHANGE_AFTER_RE = re.compile(
    r"^\s*(?:of\s+)?(?:improvement|expansion|contraction|compression|decline|decrease|increase|pick-?up|uplift|"
    r"change|delta|widening|narrowing|gain|dilution|accretion|swing)\b", re.I)
_PP_RE = re.compile(r"\b(?:pp|ppt|ppts|pts|points?)\b|\bpercentage\s+points?", re.I)
_CHANGE_HDR_RE = re.compile(
    r"change|\bchg\b|[Δ∆]|\bdelta\b|\bvar(?:iance)?\b|\bq\s?o\s?q\b|\by\s?o\s?y\b|\bq/q\b|\by/y\b|"
    r"\bvs\.?(?=\s|$)|\bversus\b|expansion|growth\s*\(bps\)|bps\s+change", re.I)


def _fix_range_units(parsed: list[ParsedNumber]) -> None:
    """Range endpoints share units: a partner's explicit unit beats a slide/table hint.

    "10-12%" -> both percent (a hint's scale must not leak onto the low end); "$1.2-1.5bn" -> both bn;
    "+120bps to 24.5%" is not a range (two different explicit units).
    """
    live = [p for p in parsed if not p.skip_reason]
    for a, b in zip(live, live[1:]):
        if not (a.is_range and b.is_range and a.range_role == "low" and b.range_role == "high"):
            continue
        if a.unit_explicit and b.unit_explicit and a.dimension != b.dimension:
            a.is_range = b.is_range = False
            a.range_role = b.range_role = ""
            continue
        for x, y in ((a, b), (b, a)):
            if x.unit_explicit or not y.unit_explicit:
                continue
            if y.dimension in ("percent", "multiple", "bps"):
                x.dimension, x.scale, x.scale_known, x.currency = y.dimension, "", True, ""
                x.unit_source = "range"
            elif y.dimension == "currency" and re.search(r"[A-Za-z]", y.raw) and x.dimension in ("currency", "none"):
                has_letters = re.search(r"[A-Za-z]", re.sub(r"\bc\.", "", x.raw))
                if y.scale and not has_letters:
                    x.scale, x.scale_known = y.scale, True


def _apply_delta_semantics(dn: DeckNumber, text: str, n: ParsedNumber) -> None:
    """Decline wording negates percent/bps values; change wording adds the "change" qualifier."""
    if dn.unit not in ("percent", "bps"):
        return
    pre = text[max(0, n.start - 40):n.start]
    raw = n.raw.strip()
    positive = dn.value > 0 and not raw.startswith(("(", "-", "−", "–"))
    after = text[n.end:n.end + 30]
    if positive and _VALENCE_RE.search(pre[-25:]):
        # "an improvement of 70bps" in a combined ratio is -70bps: the direction depends on the metric, so the
        # magnitude is what the text asserts.
        _add_qualifier(dn, "valence")
    elif positive and _DECLINE_RE.search(pre):
        dn.value = -dn.value
        _add_qualifier(dn, "decline")
    is_pp = dn.unit == "percent" and bool(_PP_RE.search(after[:12]))
    # "S + 325bps", "SOFR + 350", "E+400bps", "L + 275": a credit spread over a benchmark is a level.
    spread = bool(re.search(r"(?:\b(?:s|sofr|l|libor|e|euribor|sonia|t|ust|treasuries|benchmark|base\s+rate|"
                            r"prime|ois|mid[- ]?swaps?|ms|swaps?)\s*)$", text[max(0, n.start - 20):n.start].rstrip("+ ").rstrip(),
                            re.I)) and raw.startswith("+")
    if spread:
        return
    if dn.unit == "bps" or is_pp:
        signed = raw.lstrip("(").startswith(("+", "-", "−"))
        if signed or _CHANGE_BEFORE_RE.search(pre) or _CHANGE_AFTER_RE.search(after):
            _add_qualifier(dn, "change")
    elif raw.startswith("+") or (raw.startswith(("-", "−")) and re.search(
            r"change|vs\b|yoy|qoq|y/y|q/q|growth|Δ", text, re.I)):
        _add_qualifier(dn, "change")


def _add_qualifier(dn: DeckNumber, q: str) -> None:
    if q not in dn.qualifiers:
        dn.qualifiers.append(q)


def scan_paragraph(
    ctx: _SlideCtx, text: str, hint: UnitHint | None, *, source: Source, shape_id: str, para_idx: int,
    uid_prefix: str | None = None, extra_label: str = "", fixed_period: str | None = None,
) -> tuple[list[DeckNumber], list[DeckNumber]]:
    """Scan one paragraph of prose -> (checkable numbers, skipped numbers)."""
    parsed = scan_numbers(text, hint)
    if not parsed:
        return [], []
    parsed.sort(key=lambda n: n.start)
    _fix_range_units(parsed)
    removed = [(n.start, n.end) for n in parsed if n.skip_reason]
    checkable = [n for n in parsed if not n.skip_reason]
    masked = _mask_text(text, removed)
    context = text.strip()
    uid_prefix = uid_prefix or f"s{ctx.number}-{shape_id}-p{para_idx}"
    out: list[DeckNumber] = []
    skipped: list[DeckNumber] = []
    prev: tuple[int, str, int] | None = None
    for n in parsed:
        if n.skip_reason:
            skipped.append(_make_number(ctx, n, source=source, shape_id=shape_id, para_idx=para_idx, label="",
                                        period="", context=context, uid=f"{uid_prefix}-{n.start}"))
    for i, n in enumerate(checkable):
        c_start, c_end = _clause_bounds(text, n.start, n.end)
        nxt = checkable[i + 1].start if i + 1 < len(checkable) else len(text)
        label, consumed = _label_for(text, masked, n, prev, nxt, (c_start, c_end))
        prev = (n.end, label, consumed)
        span_only = False
        if fixed_period is not None:
            period = fixed_period
        else:
            period, span_only = _choose_period(text, c_start, c_end, n)
        dn = _make_number(ctx, n, source=source, shape_id=shape_id, para_idx=para_idx,
                          label=(f"{extra_label} {label}".strip() if extra_label else label),
                          period=period or ("" if span_only else ctx.period_hint), context=context,
                          uid=f"{uid_prefix}-{n.start}")
        clause_text = text[c_start:c_end]
        if source in (Source.TEXT_BOX, Source.NOTES) and dn.value < 0 and n.raw.startswith("(") \
                and n.raw.endswith(")"):
            # "14.0m primary ($420.0m)", "80.0% (EUR 560.0m)": a bracketed aside restating an amount, not an
            # accounting negative ("EBITDA of ($5.2m)" keeps its sign: a verb/preposition introduces it).
            prev_word = re.search(r"(\S+)\s*$", text[:n.start])
            pw = prev_word.group(1).lower().strip(",:;") if prev_word else ""
            if pw and pw not in _NEG_INTRO_WORDS:
                dn.value = -dn.value
        if hint is not None and hint.per_share_exception and PER_SHARE_RE.search(label):
            if hint.scale and n.unit_source == hint.source:
                dn.scale, dn.scale_known = "", True
        if is_fx_rate_label(label) and n.unit_source != "token":
            dn.unit, dn.scale, dn.scale_known, dn.currency = "none", "", True, ""
        dn.qualifiers = _terms_in(label, ctx.config.qualifiers) or [
            q for q in _terms_in(clause_text, ctx.config.qualifiers) if q not in _ENTITY_QUALIFIERS]
        _apply_delta_semantics(dn, text, n)
        out.append(dn)
    for low, high in zip(out, out[1:]):
        if low.range_role == "low" and high.range_role == "high":
            low.label = low.label or high.label
            high.label = high.label or low.label
    # footnote markers directly after a number: "$1.2bn(2)" -> footnote_refs ["2"]
    for sk in skipped:
        if sk.skip_reason != "footnote_marker":
            continue
        digits = re.sub(r"\D", "", sk.raw_text)
        target = next((d for d in out if 0 <= sk.char_start - d.char_end <= 1), None)
        if target is None:
            target = next((d for d in out if d.char_start >= sk.char_end), None)
        if target is not None and digits:
            target.footnote_refs.append(digits)
    return out, skipped


def numbers_from_text(ctx: _SlideCtx, text: str, **kw):
    """Convenience wrapper used by OCR: scan a line with the slide hint."""
    return scan_paragraph(ctx, text, kw.pop("hint", ctx.hint), **kw)


# --------------------------------------------------------------------------- #
# Footnotes, sources, as-of dates
# --------------------------------------------------------------------------- #

_STRONG_FOOT_RE = re.compile(
    r"^\s*(?:sources?\s*[:\-–—]|notes?\s*(?:[:\-–—]|\(?\d)|memo\s*:|\(\*\))", re.I)
_LOOSE_SOURCE_RE = re.compile(r"^\s*sources?\s+\S", re.I)
_WEAK_FOOT_RE = re.compile(r"^\s*(?:\(\d{1,2}\)|\d{1,2}[.)]\s|[*†‡§]+)")
_SOURCE_LINE_RE = re.compile(r"\bsources?\s*[:\-–—]\s*(.+)$", re.I | re.S)
_ASOF_RE = re.compile(r"\bas\s+(of|at)\s+", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_PATTERNS = [
    ("iso", re.compile(r"^(\d{4})-(\d{2})-(\d{2})")),
    ("dmy_text", re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[-\s./]+" + _MON + r"[-\s./,']*(\d{4}|\d{2})(?!\d)", re.I)),
    ("mdy_text", re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)", re.I)),
    ("num", re.compile(r"^(\d{1,2})([/.])(\d{1,2})\2(\d{4}|\d{2})(?!\d)")),
    ("my_text", re.compile(_MON + r"[-\s./,'’]*(\d{4}|\d{2})(?!\d)", re.I)),
]


def _full_year(y: str) -> int:
    n = int(y)
    return n if len(y) == 4 else (2000 + n if n < 70 else 1900 + n)


def _safe_date(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def parse_as_of_dates(text: str) -> list[str]:
    """ISO dates following "as of" / "as at" phrases.

    Numeric ambiguity (09/10/24): "as at" is read day-first (British usage),
    "as of" month-first (US usage); an unambiguous part (>12) always wins.
    """
    out: list[str] = []
    for m in _ASOF_RE.finditer(text or ""):
        tail = text[m.end(): m.end() + 40]
        iso = ""
        for kind, pat in _DATE_PATTERNS:
            mm = pat.match(tail.lstrip())
            if not mm:
                continue
            g = mm.groups()
            if kind == "iso":
                iso = _safe_date(int(g[0]), int(g[1]), int(g[2]))
            elif kind == "dmy_text":
                iso = _safe_date(_full_year(g[2]), _MONTHS[g[1][:3].lower()], int(g[0]))
            elif kind == "mdy_text":
                iso = _safe_date(_full_year(g[2]), _MONTHS[g[0][:3].lower()], int(g[1]))
            elif kind == "num":
                a, b, y = int(g[0]), int(g[2]), _full_year(g[3])
                day_first = g[1] == "." or b > 12 < a or (a <= 12 and b <= 12 and m.group(1).lower() == "at")
                if a > 12 and b <= 12:
                    day_first = True
                elif b > 12 and a <= 12:
                    day_first = False
                iso = _safe_date(y, b, a) if day_first else _safe_date(y, a, b)
            elif kind == "my_text":
                iso = _safe_date(_full_year(g[1]), _MONTHS[g[0][:3].lower()], 1)
            if iso:
                break
        if iso and iso not in out:
            out.append(iso)
    return out


def _record_footnote(ctx: _SlideCtx, text: str) -> None:
    text = text.strip()
    if not text:
        return
    ctx.footnotes.append(text)
    sm = _SOURCE_LINE_RE.search(text)
    if sm:
        src = re.sub(r"\s+", " ", sm.group(1)).strip()
        if src:
            ctx.sources.append(src)
    for iso in parse_as_of_dates(text):
        if iso not in ctx.as_of:
            ctx.as_of.append(iso)


def _max_font_pt(shape) -> float | None:
    sizes: list[float] = []
    try:
        for para in shape.text_frame.paragraphs:
            if para.font.size is not None:
                sizes.append(para.font.size.pt)
            for run in para.runs:
                if run.font.size is not None:
                    sizes.append(run.font.size.pt)
    except Exception:  # noqa: BLE001
        return None
    return max(sizes) if sizes else None


def _text_kind(text: str, top: int, height: int, font_pt: float | None) -> str:
    """"footnote" | "mixed" | "body" for a text shape (see module docs / DESIGN.md 1.1)."""
    low_on_slide = height > 0 and top > 0.60 * height
    bottom = height > 0 and top > 0.85 * height
    small = font_pt is None or font_pt <= 10
    if _STRONG_FOOT_RE.match(text):
        return "footnote"
    if bottom and _LOOSE_SOURCE_RE.match(text):
        return "footnote"
    if _WEAK_FOOT_RE.match(text) and (low_on_slide or (font_pt is not None and font_pt <= 10)):
        return "footnote"
    if bottom and small and text.strip():
        return "footnote"
    return "body"


# --------------------------------------------------------------------------- #
# Shape walking
# --------------------------------------------------------------------------- #


def _placeholder_type(shape):
    try:
        if shape.is_placeholder:
            return shape.placeholder_format.type
    except Exception:  # noqa: BLE001
        return None
    return None


def _iter_shapes(shapes, top_override: int | None = None):
    """Yield ``(shape, top_emu)`` recursively; group children inherit the group's top."""
    for shape in shapes:
        try:
            top = top_override if top_override is not None else (shape.top or 0)
        except Exception:  # noqa: BLE001
            top = top_override or 0
        if isinstance(shape, GroupShape):
            try:
                yield from _iter_shapes(shape.shapes, top)
            except Exception:  # noqa: BLE001
                continue
        else:
            yield shape, top


def _is_picture(shape) -> bool:
    try:
        st = shape.shape_type
    except Exception:  # noqa: BLE001
        st = None
    if st in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.LINKED_PICTURE if hasattr(MSO_SHAPE_TYPE, "LINKED_PICTURE")
              else MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.EMBEDDED_OLE_OBJECT, MSO_SHAPE_TYPE.LINKED_OLE_OBJECT):
        return True
    try:
        from pptx.shapes.placeholder import PlaceholderPicture

        return isinstance(shape, PlaceholderPicture)
    except Exception:  # noqa: BLE001
        return False


def _is_smartart(shape) -> bool:
    try:
        for gd in shape._element.iter(f"{{{_A_NS}}}graphicData"):
            if "diagram" in (gd.get("uri") or ""):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _smartart_paragraphs(slide, shape) -> list[str]:
    """Text of the SmartArt data model (``dgm`` part), one string per paragraph."""
    rel = next(iter(shape._element.iter(f"{{{_DGM_NS}}}relIds")))
    part = slide.part.related_part(rel.get(f"{{{_R_NS}}}dm"))
    root = etree.fromstring(part.blob)
    out = []
    for p in root.iter(f"{{{_A_NS}}}p"):
        text = "".join(t.text or "" for t in p.iter(f"{{{_A_NS}}}t")).strip()
        if text:
            out.append(text)
    return out


def _paragraph_texts(text_frame) -> list[str]:
    return [p.text.replace("\x0b", "\n") for p in text_frame.paragraphs]


# --------------------------------------------------------------------------- #
# Slide pre-pass: title, banner, period hint
# --------------------------------------------------------------------------- #


@dataclass
class _TextItem:
    shape: object
    top: int
    left: int
    bottom: int
    text: str
    ph_type: object
    kind: str


def _has_checkable_numbers(text: str) -> bool:
    return any(not n.skip_reason for n in scan_numbers(text))


def _banner_hint(text: str) -> UnitHint | None:
    h = parse_unit_banner(text)
    return h if (h.scale_known or h.currency) else None


_SCOPED_BANNER_RE = re.compile(
    r"^\(?\s*(?!(?:all\s+)?(?:amounts?|figures?|values?|numbers?|data|financials?|currency|dollars?|euros?|pounds?|"
    r"usd|eur|gbp|chf|jpy|us\$|\$|€|£)\b)[A-Za-z][A-Za-z&/\- ]{2,40}?\s+(?:in|are\s+in|shown\s+in)\s+"
    r"(?:thousands|millions|billions|000s|'000s?|units)\b[^)]*\)?\s*$", re.I)


def _choose_slide_hint(ctx: _SlideCtx, items: list[_TextItem], title_item: _TextItem | None) -> None:
    ordered: list[_TextItem] = []
    if title_item is not None:
        ordered.append(title_item)
    ordered += [i for i in items if i.ph_type == PP_PLACEHOLDER.SUBTITLE and i is not title_item]
    rest = sorted((i for i in items if i not in ordered), key=lambda i: (i.top, i.left))
    ordered += rest
    for it in ordered:
        if it.kind == "footnote" or len(it.text) > 140 or _has_checkable_numbers(it.text):
            continue
        if _SCOPED_BANNER_RE.match(it.text.strip()):
            continue   # "(Members in thousands)": the unit of one metric, not of every figure on the slide
        h = _banner_hint(it.text)
        if h is not None:
            ctx.hint = replace(h, source="slide")
            ctx.hint_text = it.text.strip()
            return
    for it in items:  # last resort: a banner inside a footnote ("Note: $ in millions")
        if it.kind == "footnote":
            h = _banner_hint(it.text)
            if h is not None and h.scale_known:
                ctx.hint = replace(h, source="slide")
                ctx.hint_text = it.text.strip()
                return


def _choose_title(items: list[_TextItem]) -> _TextItem | None:
    for it in items:
        if it.ph_type in _TITLE_PLACEHOLDERS and it.text.strip():
            return it
    shorts = [i for i in items if i.kind != "footnote" and 0 < len(i.text.strip()) <= 120
              and i.ph_type not in _SKIP_PLACEHOLDERS]
    return min(shorts, key=lambda i: (i.top, i.left)) if shorts else None


_AS_OF_RE = re.compile(r"\b(?:as\s+(?:of|at)|market\s+data\s+as\s+of|priced\s+as\s+of|dated)\b[^,;)\]]*", re.I)


def _period_hint(texts: list[str]) -> str:
    # "(as of 30-Sep-2024)" / "as at Sep-24" date the data; they do not declare a reporting period.
    keys = {p.key for t in texts for p, _s, _e in find_periods(_AS_OF_RE.sub(" ", t))}
    return next(iter(keys)) if len(keys) == 1 else ""


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

_NA_RE = re.compile(r"^\s*(?:n\.?\s?m\.?|n\.?\s?a\.?|n/a|nil|tbd|[-–—−]+|\*+)\s*$", re.I)


_CELL_UNIT_WORDS = {"million", "millions", "billion", "billions", "thousand", "thousands", "bps", "times", "usd",
                    "eur", "gbp", "ltm", "ntm", "per", "share", "est", "act", "approx"}


def _real_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z]{3,}", _DATE_TEXT_RE.sub(" ", text))
            if w.lower() not in _CELL_UNIT_WORDS and not re.fullmatch(_MONTH_PAT, w, re.I)]


def _is_named_figure(text: str) -> bool:
    """"Revolving credit facility ($250m)", "6.375% Senior notes": a name that carries a figure."""
    return len(_real_words(text)) >= 2 and any(not n.skip_reason for n in scan_numbers(text))


def _classify_text(text: str) -> str:
    t = text.strip()
    if not t:
        return "empty"
    if _NA_RE.match(t):
        return "na"
    parsed = scan_numbers(t)
    if any(not n.skip_reason for n in parsed):
        # A name that carries a figure is a label, not a value cell
        return "text" if len(_real_words(t)) >= 2 else "num"
    if find_periods(t) or any(n.skip_reason in ("year", "period", "date") for n in parsed):
        return "period"
    return "text"


def _unit_cell_hint(text: str) -> UnitHint | None:
    """Hint from a units-column cell: "$m", "%", "x", "bps", "USD bn"."""
    t = text.strip().lower()
    if not t or len(t) > 14:
        return None
    if t in ("x", "×"):
        return UnitHint(dimension="multiple", scale_known=True)
    if t in ("%", "(%)"):
        return UnitHint(dimension="percent", scale_known=True)
    h = parse_unit_banner(t)
    if h.is_empty():
        from numcheck.numfmt import SCALE_WORDS

        sc = SCALE_WORDS.get(t.strip("()' "), "")
        if sc:
            return UnitHint(scale=sc, scale_known=True)
        return None
    return h


@dataclass
class _Grid:
    texts: list[list[str]]
    origin: list[list[bool]]


def _read_grid(table) -> _Grid:
    nr, nc = len(table.rows), len(table.columns)
    texts = [[""] * nc for _ in range(nr)]
    origin = [[True] * nc for _ in range(nr)]
    for r in range(nr):
        for c in range(nc):
            cell = table.cell(r, c)
            if cell.is_spanned:
                continue
            txt = cell.text
            if cell.is_merge_origin:
                for dr in range(cell.span_height):
                    for dc in range(cell.span_width):
                        if r + dr < nr and c + dc < nc:
                            texts[r + dr][c + dc] = txt
                            origin[r + dr][c + dc] = dr == 0 and dc == 0
            else:
                texts[r][c] = txt
    return _Grid(texts, origin)


def _row_kinds(kinds_row: list[str]) -> tuple[int, int, int, int]:
    n_num = sum(k == "num" for k in kinds_row)
    n_per = sum(k == "period" for k in kinds_row)
    n_text = sum(k == "text" for k in kinds_row)
    return n_num, n_per, n_text, n_num + n_per + n_text


def _is_data_row(kinds_row: list[str]) -> bool:
    n_num, n_per, _t, _n = _row_kinds(kinds_row)
    return n_num >= 1 and n_num >= n_per


def _header_rows(kinds: list[list[str]]) -> int:
    """Leading non-data rows (row 0 always when it is not data) up to 4."""
    if not kinds or _is_data_row(kinds[0]):
        return 0
    n = 1
    while n < min(len(kinds) - 1, 4):
        row = kinds[n]
        if _is_data_row(row):
            break
        _nn, n_per, _nt, total = _row_kinds(row)
        if total >= 2 or n_per >= 1:
            n += 1
        else:
            break
    if n >= len(kinds):
        n = len(kinds) - 1
    return n


def _dedupe_join(parts: list[str]) -> list[str]:
    out: list[str] = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if p and (not out or out[-1] != p):
            out.append(p)
    return out


def _pure_period(part: str) -> bool:
    spans = find_periods(part)
    if not spans:
        return False
    rest = part
    for _p, s, e in sorted(spans, key=lambda t: -t[1]):
        rest = rest[:s] + rest[e:]
    return not re.search(r"[A-Za-z0-9]{2,}", rest)


_PAREN_RE = re.compile(r"\(([^)]*)\)")


def _label_text(parts: list[str]) -> str:
    """Label from header/row parts: drop pure-period parts and unit parentheticals."""
    kept = []
    for part in parts:
        if _pure_period(part):
            continue
        part = _PAREN_RE.sub(lambda m: " " if (not parse_unit_banner(m.group(0)).is_empty()
                                                or re.fullmatch(r"\s*(?:x|%|bps)\s*", m.group(1), re.I)
                                                or re.fullmatch(r"\s*\d{1,2}\s*", m.group(1))) else m.group(0), part)
        part = re.sub(r"\s+", " ", part).strip()
        if part:
            kept.append(part)
    return " ".join(kept)


def _header_period(parts: list[str]) -> tuple[str, bool]:
    """(period key, ambiguous) from header parts; several distinct periods are ambiguous."""
    text = " ".join(parts)
    for part in parts:
        hq = header_date_period(part)
        if hq:
            return hq, False   # "30-Jun-25" column of a balance / fund report = Q2 2025
    found = find_periods(text)
    parens = [(m.start(), m.end()) for m in re.finditer(r"\([^)]*\)", text)]
    # "Q1 FY25 (Apr-24)": the bracketed month only explains the fiscal quarter
    found = [f for f in found if not any(a <= f[1] < b for a, b in parens)] or found
    keys = {p.key for p, _s, _e in found}
    if len(keys) >= 2:
        # "FY24 Q3" style combos resolve to a single key already; distinct keys mean a span/comparison.
        return "", True
    return (found[0][0].key if found else ""), False


def _caption_hint(items: list[_TextItem], table_top: int, exclude) -> UnitHint | None:
    """Banner text box sitting directly above the table (per-table caption)."""
    best: tuple[int, UnitHint] | None = None
    for it in items:
        if it is exclude or it.kind == "footnote" or it.ph_type in _TITLE_PLACEHOLDERS \
                or it.ph_type == PP_PLACEHOLDER.SUBTITLE or len(it.text) > 140:
            continue
        gap = table_top - it.bottom
        if -91440 <= gap <= 640080 and not _has_checkable_numbers(it.text):
            h = _banner_hint(it.text)
            if h is not None and (best is None or gap < best[0]):
                best = (gap, h)
    return replace(best[1], source="table") if best else None


def _extract_table(ctx: _SlideCtx, shape, items: list[_TextItem], title_item) -> None:
    table = shape.table
    shape_id = str(shape.shape_id)
    grid = _read_grid(table)
    nr, nc = len(grid.texts), len(grid.texts[0]) if grid.texts else 0
    if not nr or not nc:
        return
    kinds = [[_classify_text(grid.texts[r][c]) if grid.origin[r][c] else "empty" for c in range(nc)]
             for r in range(nr)]
    kinds_full = [[_classify_text(grid.texts[r][c]) for c in range(nc)] for r in range(nr)]
    n_hdr = _header_rows(kinds)
    body = list(range(n_hdr, nr))

    # --- columns: data / text / units / label ----------------------------------
    col_counts = []
    for c in range(nc):
        cnt = {"num": 0, "text": 0, "period": 0}
        for r in body:
            k = kinds[r][c]
            if k in cnt:
                cnt[k] += 1
        col_counts.append(cnt)

    def is_data_col(c: int) -> bool:
        cn = col_counts[c]
        tot = cn["num"] + cn["text"] + cn["period"]
        return cn["num"] >= 1 and cn["num"] >= 0.4 * tot

    data_cols = [c for c in range(nc) if is_data_col(c)]
    axis_col = _axis_column(grid, body, data_cols, kinds)
    if axis_col is not None:
        # Sensitivity grid: the first column's monotonic values (10.0x, 11.0x, 12.0x / 8.0%, 8.5% ...) are
        # axis labels for the grid cells, not claims to check.
        data_cols = [c for c in data_cols if c != axis_col]
        for r in body:
            if kinds[r][axis_col] == "num":
                kinds[r][axis_col] = "text"
        col_counts[axis_col] = {"num": 0, "text": len(body), "period": 0}
    first_data = data_cols[0] if data_cols else nc
    text_cols = [c for c in range(nc) if c not in data_cols and (col_counts[c]["text"] + col_counts[c]["period"]) > 0]
    left_text = [c for c in text_cols if c < first_data]
    label_cols: list[int] = []
    units_col = -1
    cand = left_text or text_cols[:1]
    for i, c in enumerate(cand):
        cells = [grid.texts[r][c] for r in body if kinds[r][c] in ("text", "period")]
        unit_like = cells and sum(_unit_cell_hint(t) is not None for t in cells) >= 0.5 * len(cells)
        if i > 0 and unit_like and units_col < 0:
            units_col = c
        else:
            label_cols.append(c)
    if not label_cols and cand:
        label_cols = [cand[0]]

    # --- orientation ------------------------------------------------------------
    hdr_periods = sum(kinds_full[r][c] == "period" for r in range(n_hdr) for c in range(nc) if c not in label_cols)
    transposed = False
    if label_cols and hdr_periods <= 1:
        lc = label_cols[0]
        cells = [kinds[r][lc] for r in body if kinds[r][lc] not in ("empty", "na")]
        if cells and sum(k == "period" for k in cells) >= 0.6 * len(cells):
            transposed = True

    # --- header parts per column -------------------------------------------------
    col_parts = [_dedupe_join([grid.texts[r][c] for r in range(n_hdr)]) for c in range(nc)]

    # --- hints --------------------------------------------------------------------
    corner_hints = []
    for r in range(n_hdr):
        for c in range(nc):
            if c in label_cols or (c == 0 and not data_cols):
                h = _banner_hint(grid.texts[r][c])
                if h is not None:
                    corner_hints.append(replace(h, source="table"))
    caption = _caption_hint(items, shape.top or 0, title_item)
    table_hint = merge_unit_hints(([caption] if caption else []) + corner_hints)
    col_hints: dict[int, UnitHint | None] = {}
    for c in range(nc):
        if c in label_cols or c == units_col:
            continue
        h = parse_unit_banner(" ".join(col_parts[c])) if col_parts[c] else UnitHint()
        col_hints[c] = replace(h, source="table") if not h.is_empty() else None

    # --- header / label-cell numbers go to ``skipped`` ---------------------------
    def record_skipped(r: int, c: int, reason: str) -> None:
        if not grid.origin[r][c]:
            return
        for pi, para in enumerate(table.cell(r, c).text_frame.paragraphs):
            for n in scan_numbers(para.text):
                ctx.skipped.append(_make_number(
                    ctx, n, source=Source.TABLE, shape_id=shape_id, para_idx=pi, label="", period="",
                    context=para.text.strip(), uid=f"s{ctx.number}-{shape_id}-r{r}c{c}-p{pi}-{n.start}",
                    skip_reason=n.skip_reason or reason))
                ctx.skipped[-1].table_row, ctx.skipped[-1].table_col = r, c

    for r in range(n_hdr):
        for c in range(nc):
            record_skipped(r, c, "table_header")

    def label_figures(r: int, c: int, row_label: str) -> None:
        """A figure written into a row label ("Revolving credit facility ($250m)", "6.375% Senior notes") is a
        claim about the instrument: check it like prose when it carries an explicit unit, outside the grid."""
        if not grid.origin[r][c] or c == axis_col or not _is_named_figure(grid.texts[r][c]):
            record_skipped(r, c, "table_label")    # axis values and bare numbers label the grid
            return
        cell = table.cell(r, c)
        for pi, para in enumerate(cell.text_frame.paragraphs):
            text = para.text.replace("\x0b", "\n")
            nums, skipped = scan_paragraph(
                ctx, text, ctx.hint, source=Source.TABLE, shape_id=shape_id, para_idx=pi,
                uid_prefix=f"s{ctx.number}-{shape_id}-r{r}c{c}-p{pi}")
            for dn in nums:
                dn.table_row, dn.table_col = r, c
                dn.label_cell = True
                dn.row_label = row_label
                dn.context = cell.text.strip()
                if dn.raw_text.startswith("(") and dn.raw_text.endswith(")"):
                    dn.value = abs(dn.value)            # "($250m)" in a name is an aside, not a negative
                if dn.unit_source != "token" or dn.unit == "none":
                    dn.skip_reason = "table_label"      # "Tier 1", "Phase 2", "Fund III": names, not figures
                    skipped.append(dn)
            for sk in skipped:
                sk.table_row, sk.table_col = r, c
            ctx.numbers.extend(dn for dn in nums if not dn.skip_reason)
            ctx.skipped.extend(skipped)

    # --- dependent rows ("  % margin" under "Adj. EBITDA") inherit their parent row's label ----------------
    parents: dict[int, str] = {}
    parent = ""
    for r in body:
        own = " ".join(_dedupe_join([grid.texts[r][c] for c in label_cols])).strip()
        if not own:
            parent = "" if all(k in ("empty",) for k in kinds[r]) else parent
            continue
        raw_first = next((grid.texts[r][c] for c in label_cols if grid.texts[r][c].strip()), "")
        if _is_dependent_row(own, raw_first):
            if parent:
                parents[r] = parent
        else:
            parent = own

    # --- stacked blocks: a body row repeating the period header ("Castellane | 2024A | 2025E ...") starts a
    # new company / segment block; rows below it (and the first block, under the top-left header) carry its name.
    blocks: dict[int, str] = {}
    repeat_rows = [r for r in body if label_cols and grid.texts[r][label_cols[0]].strip()
                   and sum(kinds_full[r][c] == "period" for c in range(nc) if c not in label_cols) >= 2
                   and all(kinds_full[r][c] in ("period", "empty") for c in range(nc) if c not in label_cols)]
    top_left = " ".join(_dedupe_join([grid.texts[r0][c] for r0 in range(n_hdr) for c in label_cols])).strip()
    if not repeat_rows and _is_block_title(top_left) and not transposed:
        blocks = {r: top_left for r in body}   # "Brenner | 2024A | 2025E": the table is about Brenner
    if repeat_rows:
        current = top_left
        for r in body:
            if r in repeat_rows:
                current = grid.texts[r][label_cols[0]].strip()
                continue
            if current:
                blocks[r] = current

    # --- body ---------------------------------------------------------------------
    for r in body:
        row_parts = _dedupe_join([grid.texts[r][c] for c in label_cols])
        if r in parents:
            row_parts = [parents[r], *row_parts]

        row_label = " ".join(row_parts)
        # A number inside the row label ("5.250% Senior Secured Notes", "7.0x leverage covenant") describes
        # the instrument, not the unit of the values in the row: strip it before reading unit hints.
        # "(000s)" / "('000)" is a unit statement, not a figure: keep it.
        hint_text = re.sub(r"(?<!\()(?<!\(')[$€£]?\d+(?:[.,]\d+)?\s*(?:%|x|bps)?(?!0*s?'?\s*\))", " ", row_label) \
            if row_label else ""
        row_hint = parse_unit_banner(hint_text) if hint_text.strip() else UnitHint()
        row_hints = [replace(row_hint, source="table")] if not row_hint.is_empty() else []
        if units_col >= 0:
            uh = _unit_cell_hint(grid.texts[r][units_col])
            if uh is not None:
                row_hints.insert(0, replace(uh, source="table"))
        for c in range(nc):
            if c in label_cols:
                label_figures(r, c, row_label)
                continue
            if c == units_col or not grid.origin[r][c] or kinds[r][c] in ("empty", "na", "text", "period"):
                if kinds[r][c] in ("text", "period") and c not in label_cols:
                    record_skipped(r, c, "table_label")
                continue
            col_label = " ".join(col_parts[c])
            # Side-by-side blocks ("Sources | $m | Uses | $m"): a text column between data columns
            # labels the numbers to its right.
            mid = [tc for tc in text_cols if tc not in label_cols and tc != units_col
                   and (not label_cols or tc > max(label_cols)) and tc < c and _wordy_column(grid, body, tc, kinds)]
            if mid and grid.texts[r][mid[-1]].strip():
                block_parts = _dedupe_join([grid.texts[r][mid[-1]]])
                row_parts_c, row_label_c = block_parts, " ".join(block_parts)
            else:
                row_parts_c, row_label_c = row_parts, row_label
            row_parts, row_label, _saved = row_parts_c, row_label_c, (row_parts, row_label)
            ch = col_hints.get(c)
            if (ch is not None and ch.currency and explicit_currency_unit(col_label)
                    and any(h.currency and h.currency != ch.currency for h in row_hints)):
                # Column "USD equiv. ($m)" states the value currency; "Senior EUR tranche" names the instrument.
                cell_hint = merge_unit_hints([ch] + row_hints + [table_hint, ctx.hint])
            else:
                cell_hint = merge_unit_hints(row_hints + [ch, table_hint, ctx.hint])
            if cell_hint.per_share_exception and PER_SHARE_RE.search(f"{row_label} {col_label}"):
                cell_hint = replace(cell_hint, scale="", scale_known=True)
            elif (cell_hint.dimension == "currency" and is_unit_price_label(row_label)
                  and not parse_unit_banner(row_label).scale):   # "Revenue / location ($m)" states its scale
                # "Revenue / visit ($)", "52-week high", "Offer price per share": a price in raw units, even on a
                # "$ in millions" slide
                cell_hint = replace(cell_hint, scale="", scale_known=True)
            if is_fx_rate_label(row_label):
                cell_hint = UnitHint(dimension="none", scale="", scale_known=True)
            if transposed:
                period, amb = _header_period(row_parts)
                if not period and not amb:
                    period, amb = _header_period(col_parts[c])
                label = " ".join(x for x in (_label_text(col_parts[c]), _label_text(row_parts)) if x)
            else:
                period, amb = _header_period(col_parts[c])
                if not period and not amb:
                    period, amb = _header_period(row_parts)
                label = " ".join(x for x in (_label_text(row_parts), _label_text(col_parts[c])) if x)
            if not period and not amb:
                period = ctx.period_hint
            cell = table.cell(r, c)
            for pi, para in enumerate(cell.text_frame.paragraphs):
                text = para.text.replace("\x0b", "\n")
                nums, skipped = scan_paragraph(
                    ctx, text, cell_hint, source=Source.TABLE, shape_id=shape_id, para_idx=pi,
                    uid_prefix=f"s{ctx.number}-{shape_id}-r{r}c{c}-p{pi}", fixed_period=period)
                for dn in nums:
                    dn.label = label or dn.label
                    dn.row_label, dn.col_label = row_label, col_label
                    dn.block = blocks.get(r, "")
                    dn.table_row, dn.table_col = r, c
                    dn.context = cell.text.strip()
                    if not dn.period:
                        dn.period = period
                    dn.qualifiers = _terms_in(f"{row_label} {col_label}", ctx.config.qualifiers) or dn.qualifiers
                for sk in skipped:
                    sk.table_row, sk.table_col = r, c
                ctx.numbers.extend(nums)
                ctx.skipped.extend(skipped)
            row_parts, row_label = _saved


_DEPENDENT_RE = re.compile(
    r"^\s*(?:\(?%\)?\s*(?:of\b|margin\b|growth\b|change\b|chg\b)|as\s+an?\s+%|as\s+%|"
    r"(?:y/?o?/?y|yoy|annual|organic)?\s*(?:growth|margin|change|chg\.?)\s*(?:\(%\)|%)?\s*$)", re.I)


def _is_dependent_row(label: str, raw: str) -> bool:
    """"% margin", "  % growth", "% of total", "Margin (%)", "y/y growth": a row about the row above it."""
    return bool(label) and bool(_DEPENDENT_RE.match(label))


def _wordy_column(grid, body, c, kinds) -> bool:
    """A text column of real labels (≥2 letters words, not dates/periods/codes): a block label column."""
    cells = [grid.texts[r][c].strip() for r in body if kinds[r][c] in ("text", "period") and grid.texts[r][c].strip()]
    if len(cells) < 2:
        return False
    periodish = sum(kinds[r][c] == "period" for r in body)
    wordy = sum(len(re.findall(r"[A-Za-z]{3,}", t)) >= 1 and not re.fullmatch(r"[A-Za-z]{3}[-\s']?\d{2,4}", t)
                for t in cells)
    return periodish < 0.3 * len(cells) and wordy >= 0.7 * len(cells)


def _axis_column(grid, body, data_cols, kinds) -> int | None:
    """Index of a sensitivity-grid axis column (first data column of monotonic, same-format numbers)."""
    if len(data_cols) < 3 or len(body) < 3:
        return None
    c = data_cols[0]
    if c != 0 and any(kinds[r][cc] in ("text", "period") for r in body for cc in range(c)):
        return None
    vals, suffixes = [], set()
    for r in body:
        if kinds[r][c] != "num":
            return None
        nums = [n for n in scan_numbers(grid.texts[r][c]) if not n.skip_reason]
        if len(nums) != 1:
            return None
        vals.append(nums[0].value)
        suffixes.add((nums[0].dimension, nums[0].decimals))
    if len(suffixes) != 1 or len(set(vals)) != len(vals):
        return None
    inc = all(b > a for a, b in zip(vals, vals[1:]))
    dec = all(b < a for a, b in zip(vals, vals[1:]))
    if not (inc or dec):
        return None
    # Evenly spaced steps are the hallmark of an axis (10.0x/11.0x/12.0x, 8.0%/8.5%/9.0%).
    steps = [round(b - a, 9) for a, b in zip(vals, vals[1:])]
    return c if max(steps) - min(steps) <= 1e-6 * max(1.0, max(abs(s) for s in steps)) else None


# --------------------------------------------------------------------------- #
# Slide processing
# --------------------------------------------------------------------------- #


def _process_slide(slide_idx: int, slide, prs_height: int, config: Config) -> tuple[_SlideCtx, SlideInfo]:
    ctx = _SlideCtx(number=slide_idx, config=config, height=prs_height)
    hidden = slide._element.get("show") == "0"
    modern_charts = sum(1 for _ in slide._element.iter(f"{{{_CHARTEX_NS}}}chart"))
    if modern_charts:
        ctx.warnings.append(ReviewWarning(
            "extended_chart_not_checked",
            f"Slide {slide_idx} contains {modern_charts} extended PowerPoint chart(s) whose data was not checked. "
            "Verify those chart values in PowerPoint or add a table with the values.",
            slide_number=slide_idx, count=modern_charts,
        ))

    items: list[_TextItem] = []
    frames: list[tuple[object, int]] = []
    for shape, top in _iter_shapes(slide.shapes):
        try:
            ph = _placeholder_type(shape)
            if ph in _SKIP_PLACEHOLDERS:
                continue
            if _is_picture(shape):
                ctx.image_count += 1
                continue
            if getattr(shape, "has_table", False) or getattr(shape, "has_chart", False) or _is_smartart(shape):
                frames.append((shape, top))
                continue
            if getattr(shape, "has_text_frame", False):
                paras = _paragraph_texts(shape.text_frame)
                text = "\n".join(paras).strip()
                if not text:
                    continue
                kind = "body" if ph in _TITLE_PLACEHOLDERS else _text_kind(text, top, prs_height, _max_font_pt(shape))
                left = shape.left or 0
                items.append(_TextItem(shape, top, left, top + (shape.height or 0), text, ph, kind))
        except Exception as exc:  # noqa: BLE001
            ctx.warnings.append(ReviewWarning("shape_error", f"Could not read a shape on slide {slide_idx} ({exc}).",
                                              slide_number=slide_idx, count=1))

    title_item = _choose_title(items)
    ctx.title = title_item.text.split("\n")[0].strip() if title_item else ""
    _choose_slide_hint(ctx, items, title_item)
    sub = next((i.text for i in items if i.ph_type == PP_PLACEHOLDER.SUBTITLE), "")
    if not sub and title_item is not None:
        # First short text right under the title acts as the subtitle ("Project Atlas - group overview").
        near = title_item.bottom + int(0.12 * (prs_height or 6858000))
        below = sorted((i for i in items if i is not title_item and title_item.top <= i.top <= near
                        and i.kind not in ("footnote", "mixed")
                        and len(i.text) <= 120 and "\n" not in i.text.strip()), key=lambda i: i.top)
        sub = below[0].text.strip() if below else ""
    ctx.period_hint = _period_hint([t for t in (ctx.title, sub) if t])
    ctx.context_text = " - ".join(t for t in (ctx.title, sub) if t)

    # text frames
    for it in items:
        shape = it.shape
        try:
            _extract_text_item(ctx, it, items)
        except Exception as exc:  # noqa: BLE001
            ctx.warnings.append(ReviewWarning("shape_error", f"Could not read text on slide {slide_idx} ({exc}).",
                                              slide_number=slide_idx, count=1))
        _ = shape

    # tables, charts, SmartArt
    for shape, _top in frames:
        try:
            if getattr(shape, "has_table", False):
                _extract_table(ctx, shape, items, title_item)
            elif getattr(shape, "has_chart", False):
                nums = extract_chart_numbers(slide_idx, shape, ctx.hint, ctx.warnings, ctx.title)
                for dn in nums:
                    if not dn.period:
                        dn.period = ctx.period_hint
                ctx.numbers.extend(nums)
            else:
                _extract_smartart(ctx, slide, shape)
        except Exception as exc:  # noqa: BLE001
            ctx.warnings.append(ReviewWarning("shape_error", f"Could not read a table/chart on slide {slide_idx} "
                                              f"({exc}).", slide_number=slide_idx, count=1))

    _resolve_short_callouts(ctx, items, slide.shapes)
    _label_chart_overlays(ctx, items, frames)

    if config.read_speaker_notes:
        _extract_notes(ctx, slide)

    info = SlideInfo(
        slide_number=slide_idx, title=ctx.title, unit_hint_text=ctx.hint_text, period_hint=ctx.period_hint,
        image_count=ctx.image_count, hidden=hidden, footnotes=ctx.footnotes, as_of_dates=ctx.as_of,
        sources=ctx.sources, number_count=len(ctx.numbers),
    )
    _ = hidden
    return ctx, info


_PROJECTION_PREFIX = re.compile(r"^(?:management|the company|the model|we)\s+(?:projects?|forecasts?|expects?)\s+", re.I)
_GUIDANCE_RE = re.compile(
    r"\b(target(?:s|ed|ing)?|guidance|guid(?:e|es|ed|ing)|outlook|expects?|expected|aims?|ambitions?|"
    r"medium[- ]term|long[- ]term|aspiration(?:al)?|goal|plans? to|forecast(?:s)?|projected to)\b", re.I)


def _callout_caption(shape) -> str:
    """Use the accessibility title, or a single text-only sibling in a KPI group."""
    properties = shape._element.find(".//" + qn("p:cNvPr"))
    title = properties.get("title", "").strip() if properties is not None else ""
    if title:
        return title
    if shape._element.getparent().tag != qn("p:grpSp"):
        return ""
    peers = [peer.text_frame.text.strip() for peer in shape._parent
             if peer.shape_id != shape.shape_id and getattr(peer, "has_text_frame", False)
             and peer.text_frame.text.strip()]
    if len(peers) == 1:
        caption = peers[0]
        if (len(caption) <= 120 and not _STRONG_FOOT_RE.match(caption)
                and re.search(r"[^\W\d_]", caption) and not _has_checkable_numbers(caption)):
            return caption
    return ""


def _caption_text(text: str) -> str:
    text = text.strip()
    if (not text or len(text) > 120 or "\n" in text or _STRONG_FOOT_RE.match(text)
            or _has_checkable_numbers(text) or not _label_text([text]) or (_banner_hint(text) and not PER_SHARE_RE.search(text))
            or re.fullmatch(r"yoy|year.over.year|qoq|cagr|growth|margin|(?:value|price) per share", text, re.I)
            or not re.search(r"[^\W\d_]", text)):
        return ""
    return text


def _separate_caption(item: _TextItem, items: list[_TextItem]) -> str:
    """Pair visually aligned top-level callouts without looking at their values."""
    parent = item.shape._element.getparent()
    if parent.tag == qn("p:grpSp"):
        return ""
    width = item.shape.width or 0
    if not width or item.shape.rotation:
        return ""
    above, below = [], []
    for peer in items:
        if (peer is item or peer.kind == "footnote" or peer.ph_type in _TITLE_PLACEHOLDERS
                or peer.shape._element.getparent() is not parent or peer.shape.rotation):
            continue
        peer_width = peer.shape.width or 0
        overlap = min(item.left + width, peer.left + peer_width) - max(item.left, peer.left)
        if not (width * .7 <= peer_width <= width * 1.5 and overlap >= .8 * min(width, peer_width)):
            continue
        # KPI tiles often overlap the number and caption boxes a little or space them a little apart.
        slack = -max(item.bottom - item.top, 0) // 2
        if slack <= peer.top - item.bottom <= 228600 and peer.top > item.top:
            candidates = below
        elif slack <= item.top - peer.bottom <= 228600 and peer.top < item.top:
            candidates = above
        else:
            continue
        caption = _caption_text(peer.text)
        if caption:
            candidates.append(caption)
    captions = below or above
    if len(captions) == 1:
        return captions[0]
    if captions:
        return ""
    return _row_label_left(item, items)


def _label_chart_overlays(ctx: _SlideCtx, items: list[_TextItem], frames) -> None:
    """A figure typed on top of a chart ("$1,168m" in a doughnut's hole) is about that chart.

    Only unlabelled text-box numbers whose box centre lies inside a chart frame are touched; the label is
    the chart's title, else its single series name.
    """
    charts = []
    for shape, _top in frames:
        if not getattr(shape, "has_chart", False):
            continue
        try:
            chart = shape.chart
            name = chart.chart_title.text_frame.text.strip() if chart.has_title else ""
            if not name:
                series = [s.name for p in chart.plots for s in p.series if s.name]
                name = series[0] if len(set(series)) == 1 else ""
            if name and not _has_checkable_numbers(name):
                charts.append((shape.left or 0, shape.top or 0, (shape.left or 0) + (shape.width or 0),
                               (shape.top or 0) + (shape.height or 0), name))
        except Exception:  # noqa: BLE001 - a chart without a readable title simply gives no label
            continue
    if not charts:
        return
    boxes = {str(it.shape.shape_id): it for it in items}
    for dn in ctx.numbers:
        if dn.label or dn.source != Source.TEXT_BOX or dn.shape_id not in boxes:
            continue
        it = boxes[dn.shape_id]
        cx = it.left + (it.shape.width or 0) // 2
        cy = (it.top + it.bottom) // 2
        inside = [c for c in charts if c[0] <= cx <= c[2] and c[1] <= cy <= c[3]]
        if len(inside) == 1:
            dn.label = _label_text([inside[0][4]])
            dn.context = f"{inside[0][4]}: {dn.context or dn.raw_text}"


def _heading_above(item: _TextItem, items: list[_TextItem]) -> str:
    """Single caption-like box stacked directly above *item* with the same left edge and similar width."""
    parent = item.shape._element.getparent()
    width = item.shape.width or 0
    if not width:
        return ""
    found = []
    for peer in items:
        if (peer is item or peer.kind == "footnote" or peer.ph_type in _TITLE_PLACEHOLDERS
                or peer.shape._element.getparent() is not parent or peer.shape.rotation):
            continue
        peer_width = peer.shape.width or 0
        if abs(peer.left - item.left) > 91440 or not (width * .6 <= peer_width <= width * 1.6):
            continue
        if -(peer.bottom - peer.top) // 2 <= item.top - peer.bottom <= 182880 and peer.top < item.top:
            caption = _caption_text(peer.text)
            if caption:
                found.append(caption)
    return found[0] if len(found) == 1 else ""


def _row_label_left(item: _TextItem, items: list[_TextItem]) -> str:
    """A lone figure placed on a row whose name sits to its left ("Trading comparables ... 2,834").

    Football fields and hand-built tables put the row name in a text box at the left edge and the figure
    further right on the same band. The nearest text-only box to the left whose vertical extent covers
    the figure's middle is that row's name.
    """
    parent = item.shape._element.getparent()
    mid = (item.top + item.bottom) // 2
    best, best_gap = "", None
    for peer in items:
        if (peer is item or peer.kind == "footnote" or peer.ph_type in _TITLE_PLACEHOLDERS
                or peer.shape._element.getparent() is not parent or peer.shape.rotation):
            continue
        peer_right = peer.left + (peer.shape.width or 0)
        if peer_right > item.left + 45720 or not (peer.top <= mid <= peer.bottom):
            continue
        caption = _caption_text(peer.text.splitlines()[0] if peer.text else "")
        if not caption:
            continue
        gap = item.left - peer_right
        if best_gap is None or gap < best_gap:
            best, best_gap = caption, gap
    return best


def _extract_text_item(ctx: _SlideCtx, it: _TextItem, items: list[_TextItem]) -> None:
    shape = it.shape
    shape_id = str(shape.shape_id)
    paras = _paragraph_texts(shape.text_frame)
    lines = [line.strip() for text in paras for line in text.splitlines() if line.strip()]
    last_period = ""   # bullets inherit the most recent explicit period of the same text box
    for pi, text in enumerate(paras):
        if not text.strip():
            continue
        if it.kind == "footnote" or (_STRONG_FOOT_RE.match(text) and it.ph_type not in _TITLE_PLACEHOLDERS):
            _record_footnote(ctx, text)
            continue
        nums, skipped = scan_paragraph(ctx, text, ctx.hint, source=Source.TEXT_BOX, shape_id=shape_id, para_idx=pi)
        guidance = bool(_GUIDANCE_RE.search(text))
        range_only = (len(nums) == 2 and nums[0].range_role == "low" and nums[1].range_role == "high"
                      and not text[:nums[0].char_start].strip() and not text[nums[1].char_end:].strip())
        for dn in nums:
            start = text.rfind("\n", 0, dn.char_start) + 1
            end = text.find("\n", dn.char_end)
            end = len(text) if end < 0 else end
            display_line = text[start:end].strip()
            if not dn.label and (range_only or not (text[start:dn.char_start] + text[dn.char_end:end]).strip()):
                caption = ""
                if len(lines) == 2:
                    other = lines[1] if lines[0] == display_line else lines[0]
                    caption = _caption_text(other)
                elif len(lines) == 1:
                    caption = _callout_caption(shape) or _separate_caption(it, items)
                if caption:
                    dn.label = _label_text([caption])
                    dn.period = _period_hint([caption]) or dn.period
                    dn.context = f"{caption}: {text.strip() if range_only else dn.raw_text}"
                    if ctx.hint and ctx.hint.per_share_exception and dn.unit_source == ctx.hint.source and PER_SHARE_RE.search(caption):
                        dn.scale, dn.scale_known = "", True
            # A heading in the same text box can distinguish otherwise identical metrics.
            if dn.is_range and dn.label and len(lines) == 2 and display_line == lines[1]:
                heading = _caption_text(lines[0])
                if heading and heading.casefold() not in dn.label.casefold():
                    dn.label = _label_text([heading, dn.label])
            elif dn.is_range and dn.label and len(lines) == 1:
                # "Trading comparables" in its own box stacked on "10.5x-12.5x FY25E Adj. EBITDA" (football fields)
                heading = _heading_above(it, items)
                if heading and heading.casefold() not in dn.label.casefold():
                    dn.label = _label_text([heading, dn.label])
            projected_label = _PROJECTION_PREFIX.sub("", dn.label)
            if projected_label != dn.label and projected_label and dn.period:
                dn.label = projected_label
                if "guidance" not in dn.qualifiers:
                    dn.qualifiers.append("guidance")
            if guidance and "guidance" not in dn.qualifiers:
                dn.qualifiers.append("guidance")
            if dn.period:
                # A comparative aside "(FY2023A: 3.6%)" does not set the period for the bullets that follow.
                pre = text[:max(dn.char_start, 0)]
                aside = pre.count("(") > pre.count(")") or bool(re.search(
                    r"\b(?:from|vs\.?|versus|compared\s+(?:to|with)|against|prior\s+year)\s*[$€£]?\s*$",
                    pre[-24:], re.I))   # "... from 31.0 months in FY24": the comparison period
                if dn.comparator == "=" and not dn.is_range and not aside:
                    last_period = dn.period
            elif last_period and dn.comparator == "=" and not dn.is_range:
                dn.period = last_period
        ctx.numbers.extend(nums)
        ctx.skipped.extend(skipped)


_BARE_STATISTIC = re.compile(r"median|mean|average", re.I)
_YOY_CAPTION = re.compile(r"yoy|y/y|year[- ]over[- ]year", re.I)


def _contains_callout(container, shape) -> bool:
    return (container.left <= shape.left and container.top <= shape.top
            and shape.left + shape.width <= container.left + container.width
            and shape.top + shape.height <= container.top + container.height)


def _resolve_short_callouts(ctx: _SlideCtx, items: list[_TextItem], shapes) -> None:
    """Resolve shorthand only when slide structure identifies the missing metric."""
    series = {(n.chart_series_name, n.period) for n in ctx.numbers
              if n.source == Source.CHART and n.unit == "multiple" and n.chart_series_name}
    boxes = {str(item.shape.shape_id): item for item in items}
    badges = [n for n in ctx.numbers if n.source == Source.TEXT_BOX and n.unit == "percent" and not n.label
              and n.shape_id in boxes and any(_YOY_CAPTION.fullmatch(line.strip())
                                            for line in boxes[n.shape_id].text.splitlines())]
    if badges:
        amounts = [n for n in ctx.numbers if n.source == Source.TEXT_BOX and n.unit == "currency"
                   and n.shape_id in boxes and not n.is_range and n.comparator == "="]
        containers = [shape for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE
                      and shape.has_text_frame and not shape.text_frame.text.strip() and not shape.rotation]
        for badge in badges:
            box = boxes[badge.shape_id].shape
            possible = set()
            for card in containers:
                if box._element.getparent() is not card._element.getparent() or not _contains_callout(card, box):
                    continue
                inside = [n for n in amounts if boxes[n.shape_id].shape._element.getparent() is card._element.getparent()
                          and _contains_callout(card, boxes[n.shape_id].shape)]
                if len(inside) > 1:
                    continue
                if inside and inside[0].label and inside[0].period:
                    possible.add((inside[0].label, inside[0].period))
            if len(possible) == 1:
                label, period = possible.pop()
                badge.label, badge.period = f"{label} growth", period

    if len(series) == 1:
        metric, period = next(iter(series))
        if metric.casefold() not in ctx.context_text.casefold():
            return  # the slide must independently state this is the featured metric
        for n in ctx.numbers:
            if (n.source == Source.TEXT_BOX and n.unit == "multiple" and _BARE_STATISTIC.fullmatch(n.label)
                    and (not n.period or n.period == period)):
                n.label = f"{n.label} {metric}"
                n.period = n.period or period


def _extract_smartart(ctx: _SlideCtx, slide, shape) -> None:
    try:
        paras = _smartart_paragraphs(slide, shape)
    except Exception as exc:  # noqa: BLE001
        ctx.warnings.append(ReviewWarning(
            "smartart_not_read", f"SmartArt on slide {ctx.number} could not be read ({exc}); its text was not checked.",
            slide_number=ctx.number, count=1))
        return
    shape_id = str(shape.shape_id)
    for pi, text in enumerate(paras):
        nums, skipped = scan_paragraph(ctx, text, ctx.hint, source=Source.TEXT_BOX, shape_id=shape_id, para_idx=pi)
        ctx.numbers.extend(nums)
        ctx.skipped.extend(skipped)


def _extract_notes(ctx: _SlideCtx, slide) -> None:
    try:
        if not slide.has_notes_slide:
            return
        tf = slide.notes_slide.notes_text_frame
        if tf is None:
            return
        for pi, text in enumerate(_paragraph_texts(tf)):
            if text.strip():
                nums, skipped = scan_paragraph(ctx, text, ctx.hint, source=Source.NOTES, shape_id="notes",
                                               para_idx=pi)
                ctx.numbers.extend(nums)
                ctx.skipped.extend(skipped)
    except Exception as exc:  # noqa: BLE001
        ctx.warnings.append(ReviewWarning("shape_error", f"Could not read speaker notes on slide {ctx.number} ({exc}).",
                                          slide_number=ctx.number, count=1))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def extract_deck_full(path: str, config: Config | None = None) -> DeckExtraction:
    """Extract everything NumCheck needs from a deck.

    Raises :class:`NumCheckInputError` for unreadable/encrypted/wrong-type files.
    """
    config = config or Config()
    real = fileio.sniff_office_file(path, "pptx", config.password, config.max_file_mb)
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    try:
        prs = Presentation(real)
    except NumCheckInputError:
        raise
    except Exception as exc:  # noqa: BLE001 - python-pptx raises many types on corrupt packages
        raise NumCheckInputError(
            f"{name} could not be opened as a PowerPoint deck ({type(exc).__name__}: {exc}).", file=name,
            hint="Open and re-save the deck in PowerPoint (File > Save As > .pptx), then try again.") from exc

    result = DeckExtraction()
    height = int(prs.slide_height or 0)
    for idx, slide in enumerate(prs.slides, start=1):
        try:
            ctx, info = _process_slide(idx, slide, height, config)
        except Exception as exc:  # noqa: BLE001
            result.slides.append(SlideInfo(slide_number=idx))
            result.warnings.append(ReviewWarning(
                "slide_error", f"Slide {idx} could not be read ({exc}); it was not checked.",
                file=name, slide_number=idx, count=1))
            continue
        result.numbers.extend(ctx.numbers)
        result.skipped.extend(ctx.skipped)
        result.slides.append(info)
        result.warnings.extend(ctx.warnings)
        for text in ctx.footnotes:
            result.footnotes.append({"slide_number": idx, "text": text})

    ocr_ok = False
    if config.ocr:
        from numcheck.extraction.ocr_extractor import extract_deck_ocr

        before = len(result.warnings)
        result.numbers.extend(extract_deck_ocr(real, existing=result.numbers, warnings=result.warnings, config=config))
        ocr_ok = not any(w.code == "ocr_unavailable" for w in result.warnings[before:])
        counts: dict[int, int] = {}
        for dn in result.numbers:
            if dn.source == Source.OCR:
                counts[dn.slide_number] = counts.get(dn.slide_number, 0) + 1
        for s in result.slides:
            s.number_count += counts.get(s.slide_number, 0)

    pic_slides = [s.slide_number for s in result.slides if s.image_count]
    if pic_slides and not ocr_ok:
        total = sum(s.image_count for s in result.slides)
        result.warnings.append(ReviewWarning(
            "images_not_checked",
            f"{total} picture(s) on slide(s) {', '.join(map(str, pic_slides))} may contain numbers that were not "
            "checked. Enable local OCR or verify "
            "them manually.", file=name, count=total))
    for w in result.warnings:
        if not w.file:
            w.file = name
    return result


def extract_deck(path: str, config: Config | None = None) -> list[DeckNumber]:
    """Compatibility wrapper: the checkable numbers only."""
    return extract_deck_full(path, config).numbers


def extract_footnotes(path: str) -> list[dict]:
    """Compatibility wrapper: ``{"slide_number", "text"}`` for every footnote."""
    return extract_deck_full(path).footnotes
