"""Numeric token scanning, unit inference, and deterministic value comparison.

All numeric logic lives here. The LLM is used only for label disambiguation and
never sees or compares numbers.

Key ideas
---------
* **Display-precision tolerance.** A displayed number covers an interval: "75%"
  means [74.5, 75.5]; "$1.2bn" means [1.15bn, 1.25bn]; "$1,250m" means
  [1,249.5m, 1,250.5m]. A model value matches iff it lies inside that interval
  (optionally widened for truncation, "c."/"~", or ">"/"<" comparators).
* **Unknown is a wildcard, not a dimension.** A plain "1,200" in a table whose
  units are stated elsewhere can match "$1,200m" in the model; the comparison
  records that the unit/scale was *inferred*.
* **Sign and scale awareness.** Mismatches are classified (sign flip, 1,000x
  scale slip) so reports can say *why* a number is wrong.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from numcheck.numfmt import CURRENCY_SYMBOLS, SCALE_WORDS
from numcheck.periods import looks_like_year, period_key

# --------------------------------------------------------------------------- #
# Parsed numbers
# --------------------------------------------------------------------------- #

CANONICAL_SCALES = {"": "", "k": "k", "m": "m", "mm": "m", "bn": "bn", "b": "bn", "t": "t"}

COUNT_WORDS = {
    "shares", "share", "users", "customers", "employees", "people", "units", "stores", "locations",
    "sites", "subscribers", "members", "accounts", "clients", "vehicles", "tonnes", "tons", "barrels",
    "bbl", "boe", "mw", "gw", "mwh", "gwh", "sq", "sqft", "beds", "rooms", "patients", "countries",
    "offices", "deals", "transactions", "downloads", "visits", "orders", "products", "skus", "hours",
    "days", "months", "years", "times", "x",
    "properties", "property", "assets", "buildings", "logos", "branches", "restaurants", "doors", "towers",
    "tenants", "plants", "facilities", "vessels", "aircraft", "wells", "mines", "hotels", "keys", "schools",
    "clinics", "hospitals", "brands", "markets", "programmes", "programs", "contracts", "projects", "fte", "ftes",
}


@dataclass
class ParsedNumber:
    value: float
    dimension: str            # "currency" | "percent" | "multiple" | "bps" | "count" | "none"
    scale: str                # canonical: "", "k", "m", "bn", "t"
    period: str
    raw: str
    significant: int
    decimals: int = 0
    currency: str = ""
    comparator: str = "="
    is_range: bool = False
    range_role: str = ""
    start: int = -1
    end: int = -1
    unit_explicit: bool = False   # dimension stated on the token itself
    scale_known: bool = False     # scale stated (on token or by a hint)
    skip_reason: str = ""         # "year" | "date" | "footnote_marker" | "ordinal" | ""
    unit_source: str = ""


@dataclass
class UnitHint:
    """Units stated in a banner/header, e.g. "($ in millions, except per share data)"."""

    dimension: str = "none"
    scale: str = ""
    currency: str = ""
    scale_known: bool = False
    per_share_exception: bool = False
    source: str = ""

    def is_empty(self) -> bool:
        return self.dimension == "none" and not self.scale and not self.currency and not self.scale_known


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #

# A number may follow "c." / "ca." directly ("runway of c.18 months") but never other letters or a decimal point.
_CORE_RE = re.compile(r"(?:(?<![A-Za-z0-9_.,])|(?<=\bc\.)|(?<=\bca\.)|(?<=\bC\.))"
                      r"(?:[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3}(?:\.[0-9]+)?"     # Indian lakh grouping 1,23,456
                      r"|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)")
# Words that make the following number an identifier, not a quantity ("Phase 3", "Tranche 2", "year 1").
_IDENT_BEFORE_RE = re.compile(
    r"\b(?:phase|stage|tranche|series|tier|round|class|fund|lot|unit|block|cohort|wave|step|option|scenario|"
    r"case|year|yr|quarter|month|week|day|slide|page|appendix|exhibit|note|section|table|chart|figure|no\.?|"
    r"number|#|rule\s+of|segment|division|region|plant|site|facility|building|tower|store|project|portfolio|"
    r"line|group|level|batch|vintage|sleeve|pool|deal|asset|property|campus|mine|well|field|vessel)\s*$", re.I)
# Benchmark rate before a spread: "S+275", "SOFR + 325", "L+400", "EURIBOR+350" (the number is basis points).
_SPREAD_BEFORE_RE = re.compile(
    r"\b(?:S|SOFR|TSOFR|L|LIBOR|E|EUR|EURIBOR|SONIA|SARON|TONA|BBSY|CDOR|CORRA|T|UST|Base|Prime)\s*\+\s*$")
# Asset / ticker style codes glued to letters ("CVX-101", "ABC 123", "B-2"): identifiers.
_CODE_BEFORE_RE = re.compile(r"\b[A-Z]{1,6}-$")

_CUR_ALT = (
    r"US\$|USD|A\$|AUD|C\$|CAD|NZ\$|HK\$|HKD|S\$|SGD|R\$|BRL|\$|£|GBP|€|EUR|¥|JPY|CHF|CNY|RMB|INR|₹|"
    r"SEK|NOK|DKK|ZAR|KRW|₩|MXN"
)
_PREFIX_RE = re.compile(
    r"(?P<cmp>(?<![A-Za-z])(?:c\.|ca\.|circa|approx\.?|approximately|about|around|nearly|almost|"
    r"over|more\s+than|above|exceeding|in\s+excess\s+of|at\s+least|up\s+to|at\s+most|less\s+than|under|below)\s*"
    r"|[~≈><≥≤]\s*)?"
    r"(?P<sign>[-−‒–+]\s?)?"
    r"(?P<po>\(\s?)?"
    r"(?P<cur>(?:" + _CUR_ALT + r"))?\s?"
    r"(?P<sign2>[-−‒–])?"
    r"(?P<po2>\()?$",
    re.I,
)
_SUFFIX_RE = re.compile(
    r"^(?P<pc2>\))?"
    r"(?:"
    r"(?P<pp>\s?(?:%\s?pts?|pp|ppts?|percentage\s+points?)(?![A-Za-z]))"
    r"|(?P<pct>\s?(?:%|percent(?![A-Za-z])|pct(?![A-Za-z])))"
    r"|(?P<bps>\s?(?:bps|bp|basis\s+points?)(?![A-Za-z]))"
    r"|(?P<scale>\s?(?:trillion|tn|billion|bln|bn|million|mln|mn|mm|mio|thousand|"
    r"B|b|M|m|K|k|T)(?![A-Za-z0-9]))"
    r")?"
    r"(?P<mult>\s?[x×X](?![A-Za-z0-9]))?"
    r"(?P<curs>\s?(?:€|EUR|USD|GBP|£)(?![A-Za-z]))?"
    r"(?P<pc>\))?",
)

_COMPARATORS = {
    "c.": "~", "ca.": "~", "circa": "~", "approx": "~", "approx.": "~", "approximately": "~",
    "about": "~", "around": "~", "nearly": "~", "almost": "~", "~": "~", "≈": "~",
    "over": ">", "more than": ">", "above": ">", "exceeding": ">", "in excess of": ">", ">": ">",
    "at least": ">=", "≥": ">=", "up to": "<=", "at most": "<=", "≤": "<=",
    "less than": "<", "under": "<", "below": "<", "<": "<",
}

_MONTH_RE = re.compile(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*", re.I)


def _canon_scale(word: str) -> str:
    w = word.strip()
    if w in ("B", "M", "K", "T"):
        return {"B": "bn", "M": "m", "K": "k", "T": "t"}[w]
    return SCALE_WORDS.get(w.lower(), "")


def significant_digits(num_part: str) -> int:
    """Legacy: count significant figures from the literal number string."""
    s = num_part.replace("-", "").replace(",", "")
    if "." in s:
        int_part, frac = s.split(".", 1)
        digits = int_part.lstrip("0") + frac
        if not int_part.lstrip("0"):
            digits = frac.lstrip("0")
    else:
        digits = s.lstrip("0")
    return max(1, len(digits))


def _decimals_of(num: str) -> int:
    return len(num.split(".", 1)[1]) if "." in num else 0


def scan_numbers(text: str, hint: UnitHint | None = None) -> list[ParsedNumber]:
    """Find every number in *text* with its unit, scale, sign, precision and comparator.

    Numbers that should not be checked (years, dates, ordinals, footnote markers)
    are returned with ``skip_reason`` set so callers can count/ignore them.
    """
    out: list[ParsedNumber] = []
    if not text:
        return out
    n = len(text)
    for m in _CORE_RE.finditer(text):
        num = m.group(0)
        cs, ce = m.span()
        nxt = text[ce:ce + 1]
        if nxt.isdigit():
            continue  # malformed grouping like "1,2345"

        # ---- suffix ----
        sm = _SUFFIX_RE.match(text[ce:ce + 24])
        suffix = sm.group(0) if sm else ""
        end = ce + len(suffix)
        after = text[end:end + 1]

        # Minor currency units glued to the number: "389.6p" (pence), "45.2c" (cents).
        minor_unit = ""
        if (not suffix and after in ("p", "c") and not text[end + 1:end + 2].isalnum()
                and not text[cs - 1:cs].isalpha()):
            minor_unit = after
            suffix, end = after, end + 1
            after = text[end:end + 1]
        # Letters glued to the number that are not a recognised unit: 3Q24, 1H24, 10th, 5G, 2024A.
        if after and (after.isalnum() or after == "_") and not suffix:
            if looks_like_year(num + after) or re.fullmatch(r"(?:19|20)\d{2}", num):
                out.append(_skip(num + after, cs, end + 1, "year"))
            elif re.match(r"(?:st|nd|rd|th)\b", text[ce:ce + 3], re.I):
                out.append(_skip(num, cs, ce + 2, "ordinal"))
            continue

        # ---- prefix ----
        left = text[max(0, cs - 24):cs]
        pm = _PREFIX_RE.search(left)
        prefix = pm.group(0) if pm else ""
        start = cs - len(prefix)
        cmp_word = (pm.group("cmp") or "").strip().lower() if pm else ""
        cur_tok = (pm.group("cur") or "") if pm else ""
        paren_open = bool(pm and (pm.group("po") or pm.group("po2")))
        paren_close = bool(sm and (sm.group("pc") or sm.group("pc2")))

        # Dates: 30-Sep-24, Sep-24, 9/30/2024, 10:30
        before_ch = text[cs - 1:cs] if cs > 0 else ""
        if (before_ch and before_ch in "/:" and text[cs - 2:cs - 1].isdigit()) or (
            after and after in "/:" and text[end + 1:end + 2].isdigit()
        ):
            out.append(_skip(num, cs, ce, "date"))
            continue
        month_after = (after == "-" and bool(_MONTH_RE.match(text[end + 1:end + 5]))) or (
            "." not in num and len(num) <= 2 and not cur_tok and not suffix
            and bool(re.match(r"\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", text[end:end + 12], re.I)))
        month_before = bool(re.search(
            r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?[-\s']$", text[max(0, cs - 10):cs], re.I
        )) and len(num) in (2, 4) and not cur_tok and suffix.strip() in ("", ")")
        if month_after or month_before:
            out.append(_skip(num, cs, ce, "date"))
            continue
        # Period spans: "FY22-24", "2022-24" -> the trailing year is not a number.
        if re.search(r"(?:(?:fy|cy|(?:19|20)\d{2})\s?'?\d{0,2}\s?[-–]|(?<![a-z0-9])(?:q[1-4]|[1-4]q|h[12]|[12]h)\s?[-/'’]?)\s?'?$",
                     text[max(0, cs - 10):cs], re.I) and (
            len(num) in (2, 4) and "." not in num and not cur_tok and not suffix
        ):
            out.append(_skip(num, cs, ce, "period"))
            continue

        # Negative-sign disambiguation: a dash glued to a preceding digit/unit is a range separator.
        negative = False
        dash_idx = max(prefix.rfind(c) for c in "-−‒–")
        if dash_idx >= 0:
            dash_abs = start + dash_idx
            prev = text[dash_abs - 1:dash_abs] if dash_abs > 0 else ""
            glued_after = not text[dash_abs + 1:dash_abs + 2].isspace()
            if prev == "" or prev.isspace() or prev in "(:=[/":
                negative = glued_after
            if not negative:
                # dash is a range separator: do not consume it as part of this token
                start = dash_abs + 1
                while start < cs and text[start].isspace():
                    start += 1
                prefix = text[start:cs]
                paren_open = "(" in prefix
                cmp_word = ""

        # Parentheses: only meaningful as a pair.
        if paren_open and paren_close:
            negative = not negative if negative else True
        elif paren_open and not paren_close:
            # "(5.2" without close: drop the paren from the token
            idx = prefix.find("(")
            if idx >= 0:
                start = start + idx + 1
                prefix = prefix[idx + 1:]
        elif paren_close and not paren_open:
            end = end - 1 if suffix.endswith(")") else end
            suffix = suffix[:-1] if suffix.endswith(")") else suffix

        raw = text[start:end].strip()

        # Exchange-rate quotes: "$1.27/£", "€1.17 / $" are prices of a currency, not amounts.
        if cur_tok and not suffix.strip() and re.match(r"\s*/\s*(?:£|\$|€|¥|gbp|usd|eur|jpy|chf)(?![a-z])",
                                                       text[end:end + 6], re.I):
            out.append(_skip(raw, start, end, "fx_rate"))
            continue
        # Unit labels: "($000)", "(£'000)", "(000s)" state a scale, they are not zero.
        if num in ("000", "0000") and (cur_tok or paren_open or text[cs - 1:cs] == "'"):
            out.append(_skip(raw, start, end, "unit_label"))
            continue

        # Footnote markers: "(1)" glued to a word/number, or "(1)" at paragraph start followed by text.
        if paren_open and paren_close and not cur_tok and "." not in num and "," not in num and len(num) <= 2:
            if not (sm.group("pct") or sm.group("bps") or sm.group("scale") or sm.group("mult")):
                glued = start > 0 and not text[start - 1].isspace()
                at_start = text[:start].strip() == "" and text[end:].strip() != ""
                if glued or at_start:
                    out.append(_skip(raw, start, end, "footnote_marker"))
                    continue

        value = float(num.replace(",", ""))
        if negative:
            value = -value

        dimension = "none"
        scale = ""
        unit_explicit = False
        scale_known = False
        currency = ""
        unit_source = ""
        if cur_tok:
            dimension = "currency"
            currency = CURRENCY_SYMBOLS.get(cur_tok.lower(), cur_tok.upper())
            unit_explicit = True
            unit_source = "token"
        if sm:
            if sm.group("pp") or sm.group("pct"):
                dimension, unit_explicit, unit_source = "percent", True, "token"
            elif sm.group("bps"):
                dimension, unit_explicit, unit_source = "bps", True, "token"
            if sm.group("scale"):
                scale = _canon_scale(sm.group("scale"))
                scale_known = True
            if sm.group("mult") and dimension not in ("percent", "bps"):
                dimension, unit_explicit, unit_source = "multiple", True, "token"
            if sm.group("curs") and not currency:
                c = sm.group("curs").strip()
                currency = CURRENCY_SYMBOLS.get(c.lower(), c.upper())
                dimension, unit_explicit, unit_source = "currency", True, "token"
        if dimension == "none" and _SPREAD_BEFORE_RE.search(
                text[max(0, start - 12):start] + ("+" if raw.lstrip().startswith("+") else "")):
            # "S+275", "SOFR + 325", "L+400", "E+350": a floating-rate spread is quoted in basis points.
            dimension, unit_explicit, unit_source = "bps", True, "token"
        if minor_unit and dimension == "none":
            # Pence / cents are compared as raw per-unit amounts (models keep per-share data in pence/cents
            # under "except per share data in pence"); cents carry no reliable currency.
            dimension, unit_explicit, unit_source = "currency", True, "token"
            currency = "GBP" if minor_unit == "p" else ""
        if dimension == "currency" and not scale:
            scale_known = True  # "$2.45" states raw dollars

        # Years: bare 4-digit integers without units are periods, not numbers to check.
        if (dimension == "none" and not scale and not negative and "," not in num and "." not in num
                and looks_like_year(num)):
            out.append(_skip(raw, start, end, "year"))
            continue

        # Count nouns right after the number ("1,200 employees", "1.2bn shares").
        # Identifiers: "Phase 3", "Tranche 2", "year 1", "CVX-101" are names, not quantities.
        single_letter_suffix = bool(scale) and not cur_tok and len(suffix.strip()) == 1   # "Phase 2b", "Fund 3a"
        if (((dimension == "none" and not scale) or single_letter_suffix) and "," not in num and "." not in num
                and not negative
                and (_IDENT_BEFORE_RE.search(text[max(0, start - 14):start])
                     or _CODE_BEFORE_RE.search(text[max(0, start - 8):start]))):
            out.append(_skip(raw, start, end, "identifier"))
            continue

        # Count nouns right after the number ("1,200 employees", "1.2bn shares", "20-year PPA", "18-month runway").
        tail_word = re.match(r"[\s-]*([A-Za-z]+)", text[end:end + 20])
        if dimension == "none" and tail_word and tail_word.group(1).lower() in COUNT_WORDS | {
                "year", "month", "week", "day", "yr", "mw", "gw", "gwh", "mwh", "kw", "kwh"}:
            dimension, unit_explicit, unit_source = "count", True, "token"

        if dimension == "none" and scale:
            dimension = "currency"  # "1.2bn" in this domain is money unless a count noun follows
            unit_source = "inferred"

        # Inherit from the hint when the token itself is silent.
        if hint is not None and not hint.is_empty():
            if dimension == "none" and hint.dimension != "none":
                dimension, unit_source = hint.dimension, hint.source or "hint"
            if dimension in ("currency", "none") and not scale and hint.scale and not minor_unit and not (
                dimension == "currency" and cur_tok and not hint.scale_known
            ):
                if not (dimension == "currency" and cur_tok):
                    scale, scale_known = hint.scale, True
                    unit_source = unit_source or hint.source or "hint"
            if dimension == "none" and hint.currency and hint.dimension in ("none", "currency"):
                dimension = "currency"
                unit_source = unit_source or hint.source or "hint"
            if not currency and hint.currency and dimension == "currency":
                currency = hint.currency
            if hint.scale_known and not scale and dimension in ("currency", "none"):
                scale_known = True

        comparator = _COMPARATORS.get(re.sub(r"\s+", " ", cmp_word.rstrip()), "=") if cmp_word else "="

        out.append(
            ParsedNumber(
                value=value,
                dimension=dimension,
                scale=scale,
                period="",
                raw=raw,
                significant=significant_digits(num),
                decimals=_decimals_of(num),
                currency=currency,
                comparator=comparator,
                start=start,
                end=end,
                unit_explicit=unit_explicit,
                scale_known=scale_known,
                unit_source=unit_source,
            )
        )
        _ = n
    _link_ranges(text, out)
    return out


def _skip(raw: str, start: int, end: int, reason: str) -> ParsedNumber:
    try:
        v = float(re.sub(r"[^\d.]", "", raw) or 0)
    except ValueError:
        v = 0.0
    return ParsedNumber(v, "none", "", "", raw, 1, 0, start=start, end=end, skip_reason=reason)


def _link_ranges(text: str, nums: list[ParsedNumber]) -> None:
    """'$1.2–1.5bn', '10-12%', '5.0x–6.0x': share units across range endpoints."""
    live = [p for p in nums if not p.skip_reason]
    for a, b in zip(live, live[1:]):
        between = text[a.end:b.start]
        if not re.fullmatch(r"\s*(?:-|–|—|‒|to)\s*", between, re.I):
            continue
        a.is_range = b.is_range = True
        a.range_role, b.range_role = "low", "high"
        if a.dimension in ("none", "currency") and b.dimension != "none" and not a.unit_explicit:
            a.dimension = b.dimension
            a.unit_source = "range"
        if a.dimension == b.dimension == "currency" and not a.scale and b.scale:
            a.scale, a.scale_known = b.scale, True
        if not a.currency and b.currency:
            a.currency = b.currency
        if b.dimension in ("none", "currency") and a.dimension == "currency" and not b.currency:
            b.dimension, b.currency = "currency", a.currency
        if a.comparator != "=" and b.comparator == "=":
            b.comparator = a.comparator


def find_number_tokens(text: str) -> list[tuple[str, int, int]]:
    """Legacy API: (token, start, end) for every checkable number in *text*."""
    return [(p.raw, p.start, p.end) for p in scan_numbers(text) if not p.skip_reason]


def parse_token(token: str, unit_hint: str = "") -> ParsedNumber | None:
    """Parse a single numeric token, optionally inheriting a unit hint string."""
    hint = parse_unit_banner(unit_hint) if unit_hint else None
    found = [p for p in scan_numbers(token, hint) if not p.skip_reason]
    if not found:
        return None
    return found[0]


# --------------------------------------------------------------------------- #
# Unit banners / headers
# --------------------------------------------------------------------------- #

_BANNER_SCALE_RE = re.compile(
    r"(?<![a-z])(thousands?|000s?|'000s?|millions?|mm|mn|mln|mio|billions?|bn|bln|trillions?|tn)(?![a-z])"
)
_SYMBOL_SCALE_RE = re.compile(
    r"(?:us\$|a\$|c\$|hk\$|s\$|nz\$|\$|€|£|¥|usd|eur|gbp|jpy|chf|cad|aud)\s?(k|m|mm|mn|b|bn|t|tn)(?![a-z])"
)
_IN_SCALE_RE = re.compile(r"\(\s?(k|m|mm|mn|bn|b)\s?\)|\bin\s+(k|m|mm|mn|bn|b)\b")
_MULT_HINT_RE = re.compile(r"\(\s?x\s?\)|(?<![a-z])(?:multiple|multiples)(?![a-z])|ev\s?/|/\s?ebitda|p\s?/\s?e(?![a-z])|\bx\s?(?:ebitda|revenue|sales)")


_THRESHOLD_RE = re.compile(
    r"(?:[<>≥≤]=?|\b(?:over|above|under|below|more\s+than|less\s+than|greater\s+than|at\s+least|exceeding|"
    r"in\s+excess\s+of)\s)\s*(?:us\$|\$|€|£|¥|usd|eur|gbp)?\s*\d[\d,.]*\s*(?:k|m|mm|mn|bn|b|thousand|million|"
    r"billion)?(?![a-z])", re.I)


def parse_unit_banner(text: str) -> UnitHint:
    """Interpret unit statements: '($ in millions)', '(US$m)', '€bn', '($000s)', '(%)', '(x)'.

    Only *token-level* evidence counts: the letter "x" inside "Capex" or "Tax" is
    never read as "multiple", and a stray "m" in "Premium" is never "millions".
    """
    hint = UnitHint()
    if not text:
        return hint
    t = " " + text.lower().replace(" ", " ") + " "

    # Thresholds name a bucket, not the unit: "Customers >$100k ARR", "Deals over $50m", "Loans < €1m".
    t = _THRESHOLD_RE.sub(" ", t)

    # Currency
    for sym in ("us$", "a$", "c$", "hk$", "s$", "nz$", "r$", "$", "€", "£", "¥", "₹"):
        if sym in t:
            hint.currency = CURRENCY_SYMBOLS[sym]
            break
    if not hint.currency:
        for code in re.findall(r"(?<![a-z])([a-z]{3})(?![a-z])", t):
            if code in CURRENCY_SYMBOLS and code not in ("ten",):
                hint.currency = CURRENCY_SYMBOLS[code]
                break

    # Scale: the primary statement is the leftmost one, before any "except ..." or ";"
    # clause ("$ in millions, except per share data; loans and deposits in $bn").
    primary = re.split(r"\bexcept\b|;", t, maxsplit=1)[0]
    found: list[tuple[int, str]] = []
    for rx, grp in ((_SYMBOL_SCALE_RE, 1), (_BANNER_SCALE_RE, 1), (_IN_SCALE_RE, 0)):
        m = rx.search(primary)
        if not m:
            continue
        word = (m.group(1) or (m.group(2) if rx is _IN_SCALE_RE else "")) or ""
        scale = SCALE_WORDS.get(word.strip("'"), SCALE_WORDS.get(word, ""))
        if scale:
            found.append((m.start(), scale))
    if found:
        hint.scale = min(found)[1]
    if hint.scale:
        hint.scale_known = True
    elif hint.currency and re.search(r"\(\s?(?:us\$|\$|€|£|usd|eur|gbp)\s?\)", t):
        hint.scale_known = True  # "($)" -> raw units

    hint.per_share_exception = bool(re.search(r"except\s+(?:for\s+)?(?:per[\s-]share|eps|share)", t))

    # Dimension
    # Rate words only count when they END the label ("EBITDA margin", "Tax rate (%)"),
    # never mid-phrase ("Fund capex and growth investments", "margin of safety").
    # Like the scale, the dimension comes from the primary statement: "($ in millions, except multiples)"
    # names an exception, not the unit of the block.
    p = primary
    # "Run-rate", "exit rate" name an annualised amount, not a percentage.
    rate_word = re.search(r"\b(?:margin|growth|yield|(?<!run-)(?<!run )(?<!exit )rate|cagr|penetration)\s*"
                          r"(?:\([^)]*\))?\s*$", p.strip())
    # A rate written into a name ("6.375% Senior notes", "5% preferred stock") is the instrument, not the
    # row's unit: a bare % only counts when no digit precedes it.
    if re.search(r"\(\s?%\s?\)|(?<![a-z0-9.])%(?:\s|\)|$)|\bin\s+%|\bpercent(?:age)?\b", p) or rate_word:
        if not hint.currency or re.search(r"\(\s?%\s?\)", p):
            hint.dimension = "percent"
    if re.search(r"\(\s?bps\s?\)|\bbps\b|\bbasis points\b", p):
        hint.dimension = "bps"
    if _MULT_HINT_RE.search(p):
        hint.dimension = "multiple"
    if hint.dimension == "none" and hint.currency:
        hint.dimension = "currency"
    if hint.dimension == "none" and hint.scale:
        hint.dimension = "none"  # "(in millions)" without currency: scale only
    # A per-unit price ("PPA price ($/MWh)", "Value per share", "$/boe") is stated in raw units:
    # the sheet's "$ in millions" banner must not scale it.
    if _PER_UNIT_RE.search(primary):   # not "($bn, except per share data)": that clause is an exception
        hint.scale, hint.scale_known = "", True
        if hint.dimension in ("none", "count"):
            hint.dimension = "currency" if hint.currency else hint.dimension
    # Share counts ("Diluted shares (m)", "Shares outstanding") are counts, not money.
    elif re.search(r"\b(?:shares?|share count|units outstanding)\b", t) and not re.search(r"\bper\s+share\b|/\s*share", t) \
            and hint.dimension in ("none",) and not hint.currency:
        hint.dimension = "count"
    elif re.search(r"\b(?:years|months|days|weeks)\b(?!\s+of\b)", t) and hint.dimension == "none" \
            and not hint.currency and not hint.scale:
        # "Contract years remaining", "Payback (months)", "DSO days": durations are counts, never money.
        hint.dimension, hint.scale, hint.scale_known = "count", "", True
    elif _COUNT_NOUN_RE.search(t.strip()) and hint.dimension == "none" and not hint.currency and not hint.scale:
        # "New logos", "Customers", "Headcount", "Stores": a count column, even on a "$ in thousands" sheet.
        hint.dimension, hint.scale, hint.scale_known = "count", "", True
    elif (hint.dimension == "none" and not hint.currency and _COUNT_TAIL_RE.search(t.strip())
          and not _AMOUNT_WORD_RE.search(t)):
        # "Patient visits (000s)", "Clinics", "Employees (#)", "Members (m)": counts keep their own scale.
        hint.dimension, hint.scale_known = "count", True
    elif _PHYSICAL_UNIT_RE.search(t) and hint.dimension == "none" and not hint.currency:
        # "Installed capacity (MW)", "Generation (GWh)", "Production (kboe/d)": physical quantities, not money.
        hint.dimension, hint.scale, hint.scale_known = "count", "", True
    return hint


_COUNT_NOUN_RE = re.compile(
    r"^(?:(?:new|total|active|net|paying|gross|number\s+of|no\.\s+of|#\s*(?:of)?)\s+)*"
    r"(?:logos|customers|clients|users|subscribers|employees|headcount|ftes?|stores|locations|doors|accounts|"
    r"members|patients|deals|transactions|branches|restaurants|sites|properties|units)\s*(?:\(#\))?$", re.I)
_COUNT_TAIL_RE = re.compile(
    r"\b(?:logos|customers|clients|users|subscribers|employees|headcount|ftes?|stores|locations|doors|accounts|"
    r"members|patients|visits|clinics|centers|centres|hospitals|beds|physicians|providers|states|countries|"
    r"offices|facilities|branches|restaurants|sites|properties|units|vehicles|trucks|homes|households|policies|"
    r"claims|orders|shipments|passengers|guests|students|schools|deals|transactions|gyms|studios|franchisees|"
    r"pharmacies|dealerships|warehouses|plants|mines|wells|towers|tenants|subscriptions|downloads)"
    r"(?:\s+(?:of|in|at)\s+[a-z]+(?:\s+[a-z]+)?)?\s*(?:\([^)$€£%]*\))?$", re.I)
_AMOUNT_WORD_RE = re.compile(
    r"\b(?:revenue|revenues|sales|income|ebitda|ebit|cost|costs|spend|expense|expenses|value|price|fees?|arr|mrr|"
    r"bookings|profit|margin|capex|cash|debt|per|average|avg|growth|cagr|share|%)\b|[$€£¥]|/", re.I)
_PHYSICAL_UNIT_RE = re.compile(
    r"\(\s*(?:mw|gw|kw|mwh|gwh|twh|kwh|mwp|boe|kboe|mmboe|boe/d|kboe/d|bbl|kbbl|mmbbl|bcf|mmcf|mcf|tonnes?|kt|mt|"
    r"oz|koz|moz|sq\.?\s?ft|sqm|m2|beds|rooms|keys|units|stores|sites|employees|fte|subscribers|customers|users|"
    r"km|miles|teu|vehicles|aircraft|vessels|towers|tenants|months|years|yrs|days|weeks|quarters|"
    r"#|no\.?|count)\s*\)|\b(?:mw|gw|gwh|mwh|twh)\b", re.I)


_PER_UNIT_RE = re.compile(
    r"(?:[$€£¥]|usd|eur|gbp)\s*/\s*[a-z]|\bper\s+(?:diluted\s+|basic\s+)?(?:share|unit|mwh|kwh|boe|bbl|barrel|tonne|ton|"
    r"oz|ounce|sq\.?\s?ft|square\s+foot|room|bed|subscriber|user|customer|store|employee|head)\b|"
    r"/\s*(?:share|mwh|kwh|boe|bbl|unit|sq\.?\s?ft|tonne|ton|oz|room|bed)\b", re.I)


_FX_CCY = r"(?:usd|eur|gbp|jpy|chf|cad|aud|nzd|sek|nok|dkk|hkd|sgd|cny|inr|zar|brl|mxn|krw|\$|€|£|¥)"
FX_RATE_RE = re.compile(
    r"(?<![a-z])" + _FX_CCY + r"\s*[/:]\s*" + _FX_CCY + r"(?![a-z])|\b(?:fx|exchange|conversion|translation)\s+rates?\b|"
    r"(?<![a-z])" + _FX_CCY + r"\s+per\s+" + _FX_CCY + r"(?![a-z])|\b(?:eurusd|gbpusd|usdjpy|eurgbp|usdchf|audusd|usdcad)\b",
    re.I)


_UNIT_PRICE_RE = re.compile(
    r"\b(?:(?<!purchase )(?<!transaction )(?<!deal )(?<!acquisition )(?<!sale )(?<!aggregate )(?<!total )price|"
    r"vwap|52[- ]?week\s+(?:high|low)|strike|nav\s+per)\b|"
    r"(?:\bper|/)\s*(?:visit|location|member|patient|bed|room|unit|clinic|store|site|user|subscriber|customer|"
    r"employee|head|tonne?|share|door|key|sq\.?\s?ft|mwh|kwh|boe|bbl|barrel|oz|ounce)\b", re.I)


def is_unit_price_label(text: str) -> bool:
    """"Revenue / visit ($)", "52-week high", "Offer price per share", "PPA price ($/MWh)": a per-unit price,
    stated in raw currency units whatever scale the slide or sheet banner gives its amounts."""
    t = text or ""
    return bool(_UNIT_PRICE_RE.search(t) or _PER_UNIT_RE.search(t))


def is_fx_rate_label(text: str) -> bool:
    """"Average GBP/USD", "EUR:USD exchange rate", "USD per GBP": a price ratio, never a scaled amount."""
    return bool(FX_RATE_RE.search(text or ""))


def explicit_currency_unit(text: str) -> bool:
    """True when *text* states a value unit explicitly: "($m)", "(EURm)", "USD equiv. ($m)", "€bn"."""
    t = (text or "").lower()
    return bool(re.search(r"\(\s*(?:us\$|\$|€|£|¥|usd|eur|gbp|jpy|chf)\s*(?:k|m|mm|mn|bn|b|t|000s?)?\s*\)", t)
                or re.search(r"(?:us\$|\$|€|£|¥)\s*(?:k|m|mm|mn|bn|b|t)\b", t))


def parse_unit_hint(hint: str) -> tuple[str, str]:
    """Legacy API: interpret a unit hint -> (dimension, scale)."""
    h = parse_unit_banner(hint)
    return h.dimension, h.scale


def detect_scale(text: str) -> str:
    """Legacy API: scale from unit-bearing text like '$m', '($bn)', '($ in millions)'."""
    return parse_unit_banner(text or "").scale


def extract_period(text: str) -> str:
    """Canonical period key from surrounding text, if present (see numcheck.periods)."""
    return period_key(text or "")


# --------------------------------------------------------------------------- #
# Canonicalization & comparison
# --------------------------------------------------------------------------- #


def scale_factor(scale: str, unit_scales: dict[str, float]) -> float:
    if not scale:
        return 1.0
    return unit_scales.get(scale, unit_scales.get(CANONICAL_SCALES.get(scale, scale), 1.0))


def parsed_from_deck(deck) -> ParsedNumber:
    """Reconstruct a ParsedNumber from an already-extracted DeckNumber."""
    return ParsedNumber(
        value=deck.value,
        dimension=deck.unit or "none",
        scale=CANONICAL_SCALES.get(deck.scale, deck.scale),
        period=deck.period,
        raw=deck.raw_text,
        significant=deck.significant,
        decimals=getattr(deck, "decimals", -1),
        currency=getattr(deck, "currency", ""),
        comparator=getattr(deck, "comparator", "=") or "=",
        is_range=getattr(deck, "is_range", False),
        range_role=getattr(deck, "range_role", ""),
        scale_known=getattr(deck, "scale_known", False) or bool(deck.scale),
        unit_explicit=getattr(deck, "unit_source", "") == "token",
        unit_source=getattr(deck, "unit_source", "") or "",
    )


def _as_parsed(d) -> ParsedNumber:
    return d if isinstance(d, ParsedNumber) else parsed_from_deck(d)


def canonical_deck(parsed: ParsedNumber, unit_scales: dict[str, float]) -> tuple[str, float]:
    """(dimension, canonical value). Percent in points; bps converted to percent points."""
    sf = scale_factor(parsed.scale, unit_scales)
    dim = parsed.dimension or "none"
    if dim == "bps":
        return ("percent", parsed.value / 100.0)
    if dim in ("percent", "multiple"):
        return (dim, parsed.value)
    return (dim, parsed.value * sf)


def _model_pct_points(mv) -> float:
    basis = getattr(mv, "pct_basis", "")
    if basis == "points":
        return mv.value
    if basis == "ratio":
        return mv.value * 100.0
    return mv.value * 100.0 if abs(mv.value) <= 1.5 else mv.value


def canonical_model(mv, unit_scales: dict[str, float]) -> tuple[str, float]:
    """(dimension, canonical value) for a model cell."""
    dim = mv.unit or "none"
    if dim == "percent":
        return ("percent", _model_pct_points(mv))
    if dim == "bps":
        return ("percent", mv.value / 100.0)
    if dim == "multiple":
        return ("multiple", mv.value)
    div = getattr(mv, "display_divisor", 1.0) or 1.0
    sf = scale_factor(mv.scale, unit_scales)
    if div != 1.0:
        return (dim, mv.value)  # raw cell value already in base units; divisor only affects display
    return (dim, mv.value * sf)


def display_tolerance(parsed: ParsedNumber, unit_scales: dict[str, float], scale: str | None = None) -> float:
    """Half a unit in the last displayed digit, in canonical units."""
    dec = parsed.decimals
    if dec is None or dec < 0:
        return 0.0
    ulp = 10.0 ** (-dec)
    if parsed.dimension == "bps":
        return 0.5 * ulp / 100.0
    if parsed.dimension in ("percent", "multiple"):
        return 0.5 * ulp
    sf = scale_factor(parsed.scale if scale is None else scale, unit_scales)
    return 0.5 * ulp * sf


@dataclass
class Comparison:
    ok: bool
    dimension: str
    deck_value: float          # canonical
    model_value: float         # canonical
    tolerance: float           # absolute canonical tolerance applied
    difference: float          # deck - model (canonical)
    inferred: bool = False     # an interpretation (scale/dimension/pct basis) was assumed
    interpretation: str = ""
    rounding_rel: float = 0.0  # |difference| / |model| (meaningful when ok)
    truncated: bool = False    # ok only under truncation semantics
    sign_flip: bool = False    # |deck| == |model| but opposite sign
    scale_slip: str = ""       # e.g. "1000x", "0.001x" when digits match at another scale
    currency_conflict: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def relative_difference(self) -> float:
        return abs(self.difference) / max(abs(self.model_value), 1e-12)


def dimensions_compatible(deck_dim: str, model_dim: str) -> bool:
    a = "percent" if deck_dim == "bps" else (deck_dim or "none")
    b = "percent" if model_dim == "bps" else (model_dim or "none")
    if a == "none" or b == "none":
        return True
    if {a, b} == {"count", "none"}:
        return True
    return a == b


def _deck_interpretations(p: ParsedNumber, mv, us: dict[str, float]) -> list[tuple[str, float, float, bool, str]]:
    """(dimension, canonical value, tolerance, inferred, label) candidates for the deck number."""
    out = []
    ddim = p.dimension or "none"
    mdim = mv.unit or "none"
    if ddim == "bps":
        out.append(("percent", p.value / 100.0, display_tolerance(p, us), False, ""))
        return out
    if ddim in ("percent", "multiple"):
        out.append((ddim, p.value, display_tolerance(p, us), False, ""))
        return out
    target = ddim if ddim != "none" else (mdim if mdim not in ("bps",) else "percent")
    if target in ("percent", "multiple"):
        tol = 0.5 * 10.0 ** (-p.decimals) if p.decimals >= 0 else 0.0
        out.append((target, p.value, tol, True, f"deck unit inferred as {target}"))
        if target == "percent" and abs(p.value) <= 1.0 and p.decimals >= 2:
            out.append(("percent", p.value * 100.0, tol * 100.0, True, "deck value read as ratio"))
        return out
    if p.scale:
        out.append((target, p.value * scale_factor(p.scale, us), display_tolerance(p, us), ddim == "none", ""))
        return out
    out.append((target, p.value, display_tolerance(p, us, ""), ddim == "none", ""))
    mscale = mv.scale or ""
    if not p.scale_known and mscale:
        out.append((target, p.value * scale_factor(mscale, us), display_tolerance(p, us, mscale), True,
                    f"deck scale inferred from model ({mscale})"))
    return out


def _model_interpretations(mv, p: ParsedNumber, us: dict[str, float]) -> list[tuple[float, bool, str]]:
    mdim = mv.unit or "none"
    if getattr(mv, "is_error", False) or (isinstance(mv.value, float) and math.isnan(mv.value)):
        return []
    if mdim == "percent":
        basis = getattr(mv, "pct_basis", "")
        if basis:
            return [(_model_pct_points(mv), False, "")]
        return [(_model_pct_points(mv), True, "model percent basis inferred")]
    if mdim == "bps":
        return [(mv.value / 100.0, False, "")]
    if mdim == "multiple":
        return [(mv.value, False, "")]
    div = getattr(mv, "display_divisor", 1.0) or 1.0
    if div != 1.0:
        return [(mv.value, False, "")]
    if mdim == "none" and p.dimension in ("percent", "bps"):
        # Unit-unknown model cell vs a deck percent: 0.65 (General format) may be the ratio behind "65%".
        out = [(mv.value, True, "model value read as percent points")]
        if abs(mv.value) <= 1.5:
            out.insert(0, (mv.value * 100.0, True, "model value read as a ratio"))
        return out
    out = [(mv.value * scale_factor(mv.scale, us), False, "")]
    if not mv.scale and not getattr(mv, "scale_known", False) and p.scale:
        out.append((mv.value * scale_factor(p.scale, us), True, f"model scale inferred from deck ({p.scale})"))
    return out


def compare(deck, mv, config) -> Comparison | None:
    """Deterministically compare a deck number against a model cell.

    Returns ``None`` when the two cannot refer to the same quantity (different
    known dimensions, e.g. "%" vs "$", or conflicting currencies).
    """
    p = _as_parsed(deck)
    us = config.unit_scales
    if not dimensions_compatible(p.dimension, mv.unit) and not (
            # "14.0m primary": money only by inference from the bare "m"; the model may hold a share count
            p.dimension == "currency" and mv.unit == "count" and p.unit_source == "inferred"):
        return None
    if p.currency and getattr(mv, "currency", "") and p.currency != mv.currency:
        return None
    if p.skip_reason:
        return None

    rel_slack = float(getattr(config, "relative_tolerance", 0.0) or 0.0)
    abs_slack = float(getattr(config, "absolute_tolerance", 0.0) or 0.0)
    truncate_ok = getattr(config, "rounding_mode", "round") == "round_or_truncate"
    approx_rel = float(getattr(config, "approx_relative", 0.05) or 0.05)

    best: Comparison | None = None
    best_key: tuple | None = None
    for ddim, dval, dtol, d_inf, d_lab in _deck_interpretations(p, mv, us):
        for mval, m_inf, m_lab in _model_interpretations(mv, p, us):
            tol = dtol + rel_slack * abs(mval) + abs_slack
            if p.decimals is not None and p.decimals < 0:
                tol = max(tol, 1e-9 * max(abs(mval), 1.0))
            tol = max(tol, 1e-9 * max(abs(mval), abs(dval), 1e-12))
            if p.comparator == "~":
                tol = max(tol, approx_rel * abs(dval))
            # Binary floats make exact half-unit boundaries fail ("25.4%" vs 0.2545 gives a diff of
            # 0.0500000000000007 against a 0.05 tolerance): allow a relative epsilon on top.
            tol += 1e-9 * max(abs(mval), abs(dval), 1.0)
            diff = dval - mval
            truncated = False
            if p.comparator in (">", ">="):
                ok = mval >= dval - tol
            elif p.comparator in ("<", "<="):
                ok = mval <= dval + tol
            else:
                ok = abs(diff) <= tol
                if not ok and truncate_ok:
                    # truncation toward zero: true value in [d, d + ulp) for positive numbers
                    if dval >= 0 and dval - tol <= mval <= dval + 2 * tol:
                        ok, truncated = True, True
                    elif dval < 0 and dval - 2 * tol <= mval <= dval + tol:
                        ok, truncated = True, True
            inferred = d_inf or m_inf
            interp = "; ".join(x for x in (d_lab, m_lab) if x)
            c = Comparison(
                ok=ok, dimension=ddim if ddim != "none" else (mv.unit or "none"),
                deck_value=dval, model_value=mval, tolerance=tol, difference=diff,
                inferred=inferred, interpretation=interp,
                rounding_rel=abs(diff) / max(abs(mval), 1e-12), truncated=truncated,
            )
            # A reading that matches prefers stated units; when nothing matches, the closest reading (often the
            # inferred scale) is the one to report.
            key = (not ok, inferred if ok else False, abs(diff) / max(abs(mval), abs(dval), 1e-12))
            if best_key is None or key < best_key:
                best, best_key = c, key
    if best is None:
        return None
    if not best.ok:
        _classify_mismatch(best)
        if best.sign_flip and not isinstance(deck, ParsedNumber) and sign_by_convention(deck, mv):
            best.ok, best.sign_flip = True, False
            best.difference = abs(best.deck_value) - abs(best.model_value)
            best.rounding_rel = abs(best.difference) / max(abs(best.model_value), 1e-12)
            best.notes = ["sign differs only by presentation convention (costs / magnitudes)"]
            best.interpretation = "; ".join(x for x in (best.interpretation, "sign convention") if x)
    return best


# Rows whose sign is a presentation choice: models store costs positive (or negative) and decks show them
# in parentheses (or not). Changes, growth, margins and returns are never sign-agnostic.
_COST_LABEL_RE = re.compile(
    r"\b(?:costs?|expenses?|expenditures?|opex|capex|maintenance|d&a|depreciation|amorti[sz]ation|"
    r"interest(?!\s+(?:income|rate|cover|coverage|earned))|taxes|tax\s+(?:paid|expense|charge)|dividends?\s+paid|fees?|"
    r"deductions?|"
    r"outflows?|uses|repayments?|purchases?|acquisitions?|write[- ]?(?:downs?|offs?)|impairments?|"
    r"provisions?|stock[- ]based\s+comp\w*|sbc|cogs|sg&a|sga|r&d)\b", re.I)
_DEDUCTION_RE = re.compile(r"(?:^|[^a-z])(?:less|minus)\s*:", re.I)   # "Less: cash", "Minus: fees"
_SIGNED_METRIC_RE = re.compile(
    r"\b(?:growth|change|chg|var(?:iance)?|margin|return|irr|yield|delta|movement|net|profit|income|"
    r"result|ebitda|ebit|earnings|cash\s+flow|fcf|surplus|deficit|gain)\b", re.I)


def sign_by_convention(deck, mv) -> bool:
    """True when a pure sign difference is a presentation convention, not an error.

    * Cost-like rows (opex, capex, ground lease costs, taxes paid) in currency: shown as (58.3) in the deck,
      stored as 58.3 in the model, or vice versa.
    * Prose magnitudes: "including £72.4m of favourable development", "a net loss of $12m" state an
      unsigned amount; the words carry the direction.
    """
    unit = getattr(deck, "unit", "none")
    quals = getattr(deck, "qualifiers", []) or []
    if "valence" in quals:
        return True     # "improved by 120bps": the words give the direction, the number its size
    if unit not in ("currency", "none", "count"):
        return False
    label = f"{getattr(deck, 'label', '')} {getattr(mv, 'row_label', '')}"
    if "change" in quals:
        return False
    if _DEDUCTION_RE.search(label) and deck.value < 0 < getattr(mv, "value", 0):
        return True     # a "Less:" row shows a positive model amount in brackets, as a deduction
    if _COST_LABEL_RE.search(label) and not _SIGNED_METRIC_RE.search(getattr(mv, "row_label", "") or ""):
        return True
    raw = getattr(deck, "raw_text", "") or ""
    src = getattr(getattr(deck, "source", None), "value", getattr(deck, "source", ""))
    if src in ("text_box", "notes") and deck.value > 0 and not re.search(r"[-−–+(]", raw):
        return True
    return False


def _classify_mismatch(c: Comparison) -> None:
    d, m, tol = c.deck_value, c.model_value, c.tolerance
    if d != 0 and m != 0 and (d < 0) != (m < 0) and abs(abs(d) - abs(m)) <= tol:
        c.sign_flip = True
        c.notes.append("sign differs from model")
        return
    ks = (1e3, 1e6, 1e-3, 1e-6) + ((100.0, 0.01) if c.dimension == "percent" else ())
    for k in ks:
        if m != 0 and abs(d * k - m) <= max(tol * k, 1e-9 * abs(m)):
            c.scale_slip = f"{k:g}x"
            if c.dimension == "percent" and k in (100.0, 0.01):
                c.notes.append("digits match the model 100x apart: bps vs % confusion")
            else:
                c.notes.append(f"digits match the model at a {k:g}x scale")
            return


def values_match(
    deck_canonical: tuple[str, float],
    model_canonical: tuple[str, float],
    parsed: ParsedNumber,
    base_relative: float = 0.0,
    absolute: float = 0.0,
) -> bool:
    """Legacy API: compare two canonical values using display-precision tolerance."""
    d_dim, d_val = deck_canonical
    m_dim, m_val = model_canonical
    if d_dim != m_dim and "none" not in (d_dim, m_dim):
        return False
    if parsed.decimals is None or parsed.decimals < 0:
        tol = 1e-9 * max(abs(m_val), 1.0)
    else:
        # scale factor implied by the deck's canonical vs displayed value
        sf = abs(d_val / parsed.value) if parsed.value else 1.0
        if parsed.dimension == "bps":
            sf = 0.01
        tol = 0.5 * 10.0 ** (-parsed.decimals) * sf
    tol = max(tol, 1e-9 * max(abs(m_val), 1e-12)) + base_relative * abs(m_val) + absolute
    return abs(d_val - m_val) <= tol


def relative_tolerance(parsed: ParsedNumber, base_relative: float) -> float:
    """Legacy helper: relative tolerance implied by display precision (for scoring only)."""
    if parsed.value == 0 or parsed.decimals is None or parsed.decimals < 0:
        return max(base_relative, 1e-9)
    return max(base_relative, 0.5 * 10.0 ** (-parsed.decimals) / abs(parsed.value))


def deck_interval(deck, config) -> tuple[str, float, float]:
    """(dimension, low, high) canonical interval a displayed deck number stands for."""
    p = _as_parsed(deck)
    dim, val = canonical_deck(p, config.unit_scales)
    tol = display_tolerance(p, config.unit_scales)
    if p.comparator == "~":
        tol = max(tol, float(getattr(config, "approx_relative", 0.05)) * abs(val))
    tol += 1e-9 * max(abs(val), 1.0)   # float epsilon at exact half-unit boundaries
    if getattr(config, "rounding_mode", "round") == "round_or_truncate":
        return (dim, val - tol, val + 2 * tol) if val >= 0 else (dim, val - 2 * tol, val + tol)
    return dim, val - tol, val + tol


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

_NUM_IN_RAW = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+")


def format_like(deck, canonical_value: float, config) -> str:
    """Render *canonical_value* in the deck number's own display style.

    "$2,450m" with canonical 2.415e9 -> "$2,415m"; "22.4%" with 21.2 -> "21.2%";
    "(45)" with -38 -> "(38)". Used for actionable suggestions.
    """
    p = _as_parsed(deck)
    raw = p.raw or ""
    disp = to_display_units(p, canonical_value, config)
    dec = max(p.decimals, 0) if p.decimals is not None else 0
    m = _NUM_IN_RAW.search(raw)
    if not m:
        return format_amount(disp, dec)
    use_commas = "," in m.group(0) or abs(disp) >= 10000
    body = f"{abs(disp):,.{dec}f}" if use_commas else f"{abs(disp):.{dec}f}"
    prefix, suffix = raw[: m.start()], raw[m.end():]
    paren_style = "(" in prefix and ")" in suffix
    prefix_clean = prefix.replace("(", "").replace("-", "").replace("−", "").replace("–", "")
    suffix_clean = suffix.replace(")", "")
    if disp < 0:
        if paren_style or (p.value < 0 and "(" in raw):
            return f"({prefix_clean}{body}{suffix_clean})"
        return f"-{prefix_clean}{body}{suffix_clean}"
    return f"{prefix_clean}{body}{suffix_clean}"


def implied_display_scale(p: ParsedNumber, canonical_value: float, config) -> str:
    """Scale the deck number is really shown in.

    Unit-less deck numbers ("1,250" in a chart whose units live in the title) have no
    scale on the token; when the comparison inferred one, pick the scale that puts the
    canonical value at the same magnitude as the displayed digits.
    """
    if p.scale or p.dimension in ("percent", "multiple", "bps") or p.scale_known:
        return p.scale
    if not p.value or not canonical_value:
        return ""
    target = math.log10(abs(p.value))
    best, best_gap = "", abs(math.log10(abs(canonical_value)) - target)
    for s in ("k", "m", "bn", "t"):
        sf = scale_factor(s, config.unit_scales)
        gap = abs(math.log10(abs(canonical_value) / sf) - target)
        if gap < best_gap - 1e-9:
            best, best_gap = s, gap
    return best if best_gap < 1.0 else ""


def to_display_units(deck, canonical_value: float, config) -> float:
    """Convert a canonical value into the deck number's displayed units."""
    p = _as_parsed(deck)
    if p.dimension == "bps":
        return canonical_value * 100.0
    if p.dimension in ("percent", "multiple"):
        return canonical_value
    return canonical_value / scale_factor(implied_display_scale(p, canonical_value, config), config.unit_scales)


def format_tolerance(deck, tolerance: float, canonical_ref: float, config) -> str:
    """'±0.05%' / '±$0.05bn' / '±0.5' — tolerance in the deck number's own units."""
    p = _as_parsed(deck)
    if p.dimension == "bps":
        tol, suffix = tolerance * 100.0, "bps"
    elif p.dimension in ("percent", "multiple"):
        tol, suffix = tolerance, "%" if p.dimension == "percent" else "x"
    else:
        s = implied_display_scale(p, canonical_ref, config)
        tol = tolerance / scale_factor(s, config.unit_scales)
        suffix = {"k": "k", "m": "m", "bn": "bn", "t": "tn"}.get(s, "")
    dec = max((p.decimals if p.decimals is not None and p.decimals >= 0 else 0) + 1, 1)
    prefix = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(p.currency, "$" if p.dimension == "currency" and "$" in (p.raw or "") else "")
    return f"±{prefix}{tol:,.{dec}f}{suffix}"


def format_difference(deck, difference: float, canonical_ref: float, config) -> str:
    """Signed difference in the deck number's units: '+$35m', '-0.6bn', '+1.2pts', '+0.5x'."""
    p = _as_parsed(deck)
    sign = "+" if difference >= 0 else "-"
    mag = abs(difference)
    if p.dimension in ("percent", "bps"):
        return f"{sign}{mag:,.{max(p.decimals, 1) if p.decimals >= 0 else 2}f}pts"
    if p.dimension == "multiple":
        return f"{sign}{mag:,.{max(p.decimals, 1) if p.decimals >= 0 else 2}f}x"
    s = implied_display_scale(p, canonical_ref, config)
    disp = mag / scale_factor(s, config.unit_scales)
    suffix = {"k": "k", "m": "m", "bn": "bn", "t": "tn"}.get(s, "")
    dec = max(p.decimals, 0) if p.decimals is not None and p.decimals >= 0 else 2
    if disp and disp < 10 ** (-dec):
        dec = min(dec + 2, 6)
    prefix = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(p.currency, "")
    return f"{sign}{prefix}{disp:,.{dec}f}{suffix}"


def format_amount(value: float, digits: int = 1) -> str:
    """Human-readable formatting for reports."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.{digits}f}"


def format_canonical(dimension: str, value: float, currency: str = "") -> str:
    """Readable canonical value (used when no deck style is available)."""
    if dimension == "percent":
        return f"{value:.2f}%"
    if dimension == "multiple":
        return f"{value:.2f}x"
    if dimension == "currency":
        sym = {"EUR": "€", "GBP": "£", "JPY": "¥"}.get(currency, "$")
        a = abs(value)
        sign = "-" if value < 0 else ""
        for div, suf in ((1e12, "tn"), (1e9, "bn"), (1e6, "m"), (1e3, "k")):
            if a >= div:
                return f"{sign}{sym}{a / div:,.2f}{suf}"
        return f"{sign}{sym}{a:,.2f}"
    return format_amount(value, 2)
