"""Excel / chart number-format parsing (shared by xlsx and chart extraction).

A format code tells us how a raw cell value is *displayed*:

* ``0.0%``            -> percent, value is a ratio, 1 decimal
* ``#,##0.0,,``       -> raw value displayed in millions (divisor 1e6)
* ``$#,##0.0"bn"``    -> currency, literal "bn" suffix (value already in bn)
* ``0.0"x"`` / ``0.0x`` -> multiple
* ``[$€-x-euro2] #,##0`` -> EUR currency
* ``yyyy-mm-dd``      -> date (not a number to check)

Canonical value of a cell = ``value / divisor * scale_factor(scale)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CURRENCY_SYMBOLS = {
    "$": "USD", "us$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
    "¥": "JPY", "jpy": "JPY", "chf": "CHF", "a$": "AUD", "aud": "AUD", "c$": "CAD", "cad": "CAD",
    "hk$": "HKD", "hkd": "HKD", "s$": "SGD", "sgd": "SGD", "cny": "CNY", "rmb": "CNY", "inr": "INR",
    "₹": "INR", "sek": "SEK", "nok": "NOK", "dkk": "DKK", "nz$": "NZD", "nzd": "NZD", "r$": "BRL",
    "brl": "BRL", "zar": "ZAR", "krw": "KRW", "₩": "KRW", "mxn": "MXN",
}

SCALE_WORDS = {
    "k": "k", "thousand": "k", "thousands": "k", "000s": "k", "000": "k", "'000": "k", "'000s": "k", "th": "k",
    "m": "m", "mm": "m", "mn": "m", "mln": "m", "mio": "m", "million": "m", "millions": "m",
    "b": "bn", "bn": "bn", "bln": "bn", "billion": "bn", "billions": "bn",
    "t": "t", "tn": "t", "trillion": "t", "trillions": "t",
}

DIVISOR_SCALE = {1e3: "k", 1e6: "m", 1e9: "bn", 1e12: "t"}


@dataclass
class FormatInfo:
    dimension: str = "none"   # currency | percent | multiple | bps | date | text | none
    decimals: int = -1        # displayed decimals; -1 when General/unknown
    divisor: float = 1.0      # trailing-comma scaling
    scale: str = ""           # literal scale suffix ("bn", "m", "k") or implied by divisor
    currency: str = ""
    is_general: bool = False


_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
_QUOTED_RE = re.compile(r'"([^"]*)"')


def parse_number_format(fmt: str | None) -> FormatInfo:
    info = FormatInfo()
    if not fmt:
        info.is_general = True
        return info
    f = fmt.strip()
    if f.lower() in ("general", "@"):
        info.is_general = f.lower() == "general"
        if f == "@":
            info.dimension = "text"
        return info

    section = _first_section(f)

    # Bracketed tokens: currency locale codes like [$€-407], [$USD], colors, conditions.
    for tok in _BRACKET_RE.findall(section):
        if tok.startswith("$"):
            sym = tok[1:].split("-")[0].strip().lower()
            if sym:
                info.currency = CURRENCY_SYMBOLS.get(sym, sym.upper())
                info.dimension = "currency"
    body = _BRACKET_RE.sub("", section)

    # Quoted literals and backslash-escaped characters.
    literals = " ".join(_QUOTED_RE.findall(body))
    body_noq = _QUOTED_RE.sub(" ", body)
    escaped = "".join(re.findall(r"\\(.)", body_noq))
    body_noq = re.sub(r"\\.", " ", body_noq)
    literal_text = (literals + " " + escaped).strip().lower()

    low = body_noq.lower()
    # Dates/times (outside literals). 'mm' alone could be minutes, so require y/d or h: patterns.
    if re.search(r"[yd]", low) or re.search(r"h+:|:ss|am/pm", low) or "mmm" in low:
        info.dimension = "date"
        return info

    # Percent: a % outside literals means the value is a ratio.
    if "%" in body_noq:
        info.dimension = "percent"
    elif re.search(r"\bbps\b|\bbp\b", literal_text) or "bps" in low:
        info.dimension = "bps"
    elif re.search(r"(?:^|[\s\d#?])x(?:$|\s)", literal_text) or re.search(r"[0#?]\s*[x×]", low) or "×" in literal_text:
        info.dimension = "multiple"
    elif info.dimension != "currency":
        sym = _currency_in(body_noq.lower() + " " + literal_text)
        if sym:
            info.currency = sym
            info.dimension = "currency"

    # Trailing commas after the last digit placeholder => divide by 1000 each.
    m = re.search(r"[0#?](,+)(?![0#?])", body_noq)
    if m:
        info.divisor = 1000.0 ** len(m.group(1))

    # Literal scale suffix ("m", "bn", "k", "mm").
    for w in re.findall(r"[a-z']+", literal_text):
        if w in SCALE_WORDS and w not in ("x",):
            info.scale = SCALE_WORDS[w]
            break
    if not info.scale and info.divisor in DIVISOR_SCALE:
        info.scale = DIVISOR_SCALE[info.divisor]

    # Decimals: placeholders after the decimal point in the numeric part.
    dm = re.search(r"\.([0#?]+)", body_noq)
    if dm:
        info.decimals = len(dm.group(1))
    elif re.search(r"[0#?]", body_noq):
        info.decimals = 0
    return info


def _first_section(fmt: str) -> str:
    # Split on ';' not inside quotes.
    out, buf, inq = [], "", False
    for ch in fmt:
        if ch == '"':
            inq = not inq
        if ch == ";" and not inq:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    out.append(buf)
    return out[0]


def _currency_in(text: str) -> str:
    for sym in ("us$", "a$", "c$", "hk$", "s$", "nz$", "r$", "$", "€", "£", "¥", "₹", "₩"):
        if sym in text:
            return CURRENCY_SYMBOLS[sym]
    for code in re.findall(r"[a-z]{3}", text):
        if code in CURRENCY_SYMBOLS and len(code) == 3:
            return CURRENCY_SYMBOLS[code]
    return ""


def display_value(value: float, info: FormatInfo) -> float:
    """Value as the format displays it (percent ratio -> points, divisor applied)."""
    v = value / (info.divisor or 1.0)
    if info.dimension == "percent":
        v *= 100.0
    return v
