"""Canonical period parsing shared by every layer.

Canonical keys (``Period.key``):

=================  ==========================================
``fy24``           fiscal year 2024 (FY24, FY2024, FY'24, FY24A/E)
``cy24``           calendar year 2024 (CY24, CY2024E)
``y24``            bare year, basis unknown (2024, 2024A, 2025E, '24)
``fy24q3``         quarter of a fiscal year (Q3 FY24, 3QFY24)
``y24q3``          quarter, basis unknown (Q3'24, 3Q24, Q3 2024)
``y24h1``          half year (1H24, H1 2024, 1H FY24 -> fy24h1)
``y24m9``          nine/six months YTD (9M24, 6M 2024)
``ltm`` / ``ntm``  trailing / forward twelve months (anchor kept separately)
``q3``             quarter without a year (legacy)
=================  ==========================================

Status (actual/estimate/projected/budget) is kept in ``Period.status`` but is
NOT part of the key: ``FY24E`` and ``FY2024`` compare equal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

YEAR_MIN, YEAR_MAX = 1990, 2069


@dataclass(frozen=True)
class Period:
    basis: str          # "fy" | "cy" | "y" (unknown) | "ltm" | "ntm" | "q" (quarter w/o year)
    year: int | None    # four-digit year
    part: str = ""      # "" | "q1".."q4" | "h1" | "h2" | "m3" | "m6" | "m9"
    status: str = ""    # "a" | "e" | "p" | "b" | "f" | ""
    anchor: str = ""    # for ltm/ntm: "2024-09" when known

    @property
    def key(self) -> str:
        if self.basis in ("ltm", "ntm"):
            return self.basis
        if self.basis == "q":
            return self.part
        yy = f"{self.year % 100:02d}" if self.year is not None else ""
        return f"{self.basis}{yy}{self.part}"

    @property
    def is_full_year(self) -> bool:
        return self.basis in ("fy", "cy", "y") and not self.part


def _yy_to_year(s: str) -> int | None:
    s = s.strip("'’ ")
    if not s.isdigit():
        return None
    if len(s) == 4:
        y = int(s)
    elif len(s) == 2:
        y = 2000 + int(s) if int(s) < 70 else 1900 + int(s)
    else:
        return None
    return y if YEAR_MIN <= y <= YEAR_MAX else None


_Y = r"(?:'|’)?(\d{4}|\d{2})"          # year: 2024, 24, '24
_ST = r"([aepbf])?"                    # status suffix
_B = r"(?<![A-Za-z0-9])"               # left boundary
_E = r"(?![A-Za-z0-9])"                # right boundary

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # LTM / NTM with optional anchor: "LTM Sep-24", "LTM 30-Sep-2024", "NTM"
    (re.compile(_B + r"(ltm|ttm|ntm)(?:\s*(?:\d{1,2}[-\s])?([a-z]{3,4})[-\s'’]?(\d{2,4})[aepbf]?)?" + _E, re.I), "lxm"),
    # Quarter with FY: "Q3 FY24", "3QFY24", "Q3FY2024"
    (re.compile(_B + r"(?:q([1-4])|([1-4])q)\s*'?fy\s*" + _Y + _ST + _E, re.I), "qfy"),
    # FY with quarter after: "FY24 Q3"
    (re.compile(_B + r"fy\s*" + _Y + r"\s*q([1-4])" + _E, re.I), "fyq"),
    # Half with FY: "1H FY24", "H1 FY2024"
    (re.compile(_B + r"(?:([12])h|h([12]))\s*'?fy\s*" + _Y + _ST + _E, re.I), "hfy"),
    # FY / CY year: "FY24", "FY2024A", "FY'24", "CY2025E", "FYE 2024"
    (re.compile(_B + r"(fy|cy)e?\s*" + _Y + _ST + _E, re.I), "fycy"),
    # Quarter + year: "Q3'24", "Q3 2024", "3Q24", "3Q 2024", "Q3-24", "Q3/2024"
    (re.compile(_B + r"(?:q([1-4])|([1-4])q)[\s\-/]*" + _Y + _ST + _E, re.I), "qy"),
    # Half + year: "1H24", "H1 2024", "2H'25E", "H1-24"
    (re.compile(_B + r"(?:([12])h|h([12]))[\s\-/]*" + _Y + _ST + _E, re.I), "hy"),
    # Month + year: "Jan-24", "Sep 2024", "Sept. '24" (monthly model columns)
    # A full date ("30-Sep-2024", "30 September 2024") is a point in time, not a monthly period.
    (re.compile(r"(?<!\d[-\s/.])(?<!\d)" + _B + r"(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?[\s\-/]*"
                + _Y + _ST + _E + r"(?!,?\s*(?:19|20)\d{2})", re.I), "mon"),   # "January 31, 2025" is a date
    # As-of dates: "March 31, 2025", "31 March 2025", "Dec. 31, 2024" -> the month the date falls in
    (re.compile(_B + r"(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+"
                r"((?:19|20)\d{2})" + _E, re.I), "datemo"),
    (re.compile(_B + r"\d{1,2}(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?,?\s+"
                r"((?:19|20)\d{2})" + _E, re.I), "datemo"),
    # YTD months: "9M24", "6M 2024", "9M'24"
    (re.compile(_B + r"([369])m\s*" + _Y + _ST + _E, re.I), "my"),
    # Bare 4-digit year with optional status: "2024", "2024A", "2025E"
    (re.compile(_B + r"((?:19|20)\d{2})" + _ST + _E, re.I), "year"),
    # Apostrophe year: "'24", "’24E" (only with apostrophe to avoid plain numbers)
    (re.compile(r"(?<![A-Za-z0-9])['’](\d{2})" + _ST + _E, re.I), "apos"),
    # Bare quarter: "Q3"
    (re.compile(_B + r"q([1-4])" + _E, re.I), "q"),
]


def _from_match(kind: str, m: re.Match) -> Period | None:
    g = m.groups()
    if kind == "lxm":
        basis = "ltm" if g[0].lower() in ("ltm", "ttm") else "ntm"
        anchor = ""
        if g[1] and g[2]:
            mon = _MONTHS.get(g[1].lower()[:4]) or _MONTHS.get(g[1].lower()[:3])
            yr = _yy_to_year(g[2])
            if mon and yr:
                anchor = f"{yr}-{mon:02d}"
        return Period(basis, None, anchor=anchor)
    if kind == "qfy":
        q = g[0] or g[1]
        y = _yy_to_year(g[2])
        return Period("fy", y, f"q{q}", (g[3] or "").lower()) if y else None
    if kind == "fyq":
        y = _yy_to_year(g[0])
        return Period("fy", y, f"q{g[1]}") if y else None
    if kind == "hfy":
        h = g[0] or g[1]
        y = _yy_to_year(g[2])
        return Period("fy", y, f"h{h}", (g[3] or "").lower()) if y else None
    if kind == "fycy":
        y = _yy_to_year(g[1])
        return Period(g[0].lower(), y, "", (g[2] or "").lower()) if y else None
    if kind == "qy":
        q = g[0] or g[1]
        y = _yy_to_year(g[2])
        return Period("y", y, f"q{q}", (g[3] or "").lower()) if y else None
    if kind == "hy":
        h = g[0] or g[1]
        y = _yy_to_year(g[2])
        return Period("y", y, f"h{h}", (g[3] or "").lower()) if y else None
    if kind == "mon":
        mon = _MONTHS.get(g[0].lower()[:4]) or _MONTHS.get(g[0].lower()[:3])
        y = _yy_to_year(g[1])
        text = m.group(0)
        if (re.match(r"(?i)(?:january|february|march|april|june|july|august|september|october|november|december)\s+\d{1,2}$",
                     text) and int(g[1]) <= 31):
            return None   # "fiscal year ending June 30": a day of the month, not June 2030
        if y and mon and g[2]:
            # "Dec-22A", "Jun-25E": an actual / estimate column named by its period end is a fiscal year
            # ending that month (FYE Dec-22 = FY22), not a monthly figure.
            return Period("fy", y, "", g[2].lower())
        return Period("y", y, f"mo{mon:02d}") if (y and mon) else None
    if kind == "datemo":
        mon = _MONTHS.get(g[0].lower()[:4]) or _MONTHS.get(g[0].lower()[:3])
        y = _yy_to_year(g[1])
        return Period("y", y, f"mo{mon:02d}") if (y and mon) else None
    if kind == "my":
        y = _yy_to_year(g[1])
        return Period("y", y, f"m{g[0]}", (g[2] or "").lower()) if y else None
    if kind == "year":
        y = _yy_to_year(g[0])
        return Period("y", y, "", (g[1] or "").lower()) if y else None
    if kind == "apos":
        y = _yy_to_year(g[0])
        return Period("y", y, "", (g[1] or "").lower()) if y else None
    if kind == "q":
        return Period("q", None, f"q{g[0]}")
    return None


_SPAN_END_RE = re.compile(r"\s*[-–—]\s*'?(\d{2})([AaEePpFfBb])?\b")


def find_periods(text: str) -> list[tuple[Period, int, int]]:
    """All non-overlapping periods in *text* as (Period, start, end), in order."""
    if not text:
        return []
    taken: list[tuple[int, int]] = []
    out: list[tuple[Period, int, int]] = []
    for pat, kind in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            p = _from_match(kind, m)
            if p is None:
                continue
            taken.append((s, e))
            out.append((p, s, e))
    # "FY22A-24A", "FY22–24E", "CY23-25": the span's end inherits the start's basis
    for p, s, e in list(out):
        if p.basis not in ("fy", "cy") or p.part or p.year is None:
            continue
        m = _SPAN_END_RE.match(text, e)
        if not m or any(m.start(1) < te and m.end() > ts for ts, te in taken):
            continue
        y = _yy_to_year(m.group(1))
        # "FY2024-25" names one fiscal year (UK / India style); a span skips years or marks its end "24A"
        if y and (y > p.year + 1 or (y == p.year + 1 and m.group(2))):
            taken.append((m.start(1), m.end()))
            out.append((Period(p.basis, y, "", (m.group(2) or "").lower()), m.start(1), m.end()))
    out.sort(key=lambda t: t[1])
    return out


def parse_period(text: str) -> Period | None:
    """First period found in *text* (most specific pattern wins on overlap)."""
    found = find_periods(text)
    return found[0][0] if found else None


_MONTH_END = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def header_date_period(text: str) -> str:
    """Quarter key for a column header that is only a quarter-end date ("30-Jun-25", "31/12/2024",
    "2025-06-30", "June 30, 2025"): balance-sheet / fund-report columns are as-of a quarter end.
    Prose dates ("as at 30 June 2025") are *not* periods; this is for bare header cells only."""
    t = (text or "").strip().lower().rstrip(".")
    day = mon = year = None
    m = re.fullmatch(r"(\d{1,2})[-\s/.]([a-z]{3,9})\.?[-\s/.,']*(\d{2}|\d{4})", t)
    if m:
        day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2)[:4]) or _MONTHS.get(m.group(2)[:3]), m.group(3)
    m = m or re.fullmatch(r"([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", t)
    if m and mon is None and not m.group(1).isdigit():
        day, mon, year = int(m.group(2)), _MONTHS.get(m.group(1)[:4]) or _MONTHS.get(m.group(1)[:3]), m.group(3)
    m2 = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ t]00:00(?::00)?)?", t)
    if m2:
        year, mon, day = m2.group(1), int(m2.group(2)), int(m2.group(3))
    m3 = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m3:
        a, b = int(m3.group(1)), int(m3.group(2))
        day, mon = (a, b) if a > 12 else (b, a) if b > 12 else (None, None)
        year = m3.group(3)
    if not (day and mon and year) or mon not in (3, 6, 9, 12) or day < _MONTH_END[mon] - 1:
        return ""
    y = _yy_to_year(str(year))
    return f"y{y % 100:02d}q{mon // 3}" if y else ""


def header_period_key(text: str) -> str:
    """Period of a (possibly multi-level) column header.

    "Fiscal 2025 (Feb-24 to Jan-25) Q4 FY25" -> the most specific period outside brackets (``fy25q4``),
    not the first year mentioned in the group header above it.
    """
    if not text:
        return ""
    found = find_periods(text)
    if not found:
        return ""
    parens = [(m.start(), m.end()) for m in re.finditer(r"\([^)]*\)", text)]
    outside = [f for f in found if not any(a <= f[1] < b for a, b in parens)] or found
    with_part = [f for f in outside if f[0].part and f[0].year]
    if with_part:
        return with_part[-1][0].key
    if any(f[0].part for f in outside):
        return period_key(text)      # "2024 Q1": the combined reading carries the year
    fiscal = [f for f in outside if f[0].basis in ("fy", "cy")]
    return (fiscal[-1] if fiscal else outside[0])[0].key


def period_key(text: str) -> str:
    p = parse_period(text)
    return p.key if p else ""


def parse_key(key: str) -> Period | None:
    """Inverse of ``Period.key`` (status/anchor are lost)."""
    if not key:
        return None
    if key in ("ltm", "ntm"):
        return Period(key, None)
    m = re.fullmatch(r"q([1-4])", key)
    if m:
        return Period("q", None, key)
    m = re.fullmatch(r"(fy|cy|y)(\d{2})((?:q[1-4])|(?:h[12])|(?:m[369])|(?:mo\d{2}))?", key)
    if not m:
        return None
    return Period(m.group(1), _yy_to_year(m.group(2)), m.group(3) or "")


def period_affinity(a: str, b: str) -> float | None:
    """How compatible two period keys are.

    Returns ``None`` when either side is unknown (neutral), ``1.0`` for an exact
    match, ``0.85`` when one side has unknown basis and the year/part agree
    (``fy24`` vs ``y24``), ``0.6`` for FY vs CY of the same year/part, and
    ``0.0`` for incompatible periods (different year, quarter vs full year,
    LTM vs FY…). Callers should treat ``0.0`` as a hard exclusion.
    """
    if not a or not b:
        return None
    if a == b:
        return 1.0
    pa, pb = parse_key(a), parse_key(b)
    if pa is None or pb is None:
        return 0.0
    if pa.basis == "q" or pb.basis == "q":
        # bare quarter vs year-quarter: same quarter -> compatible
        qa = pa.part if pa.part.startswith("q") else ""
        qb = pb.part if pb.part.startswith("q") else ""
        return 0.7 if qa and qa == qb else 0.0
    if pa.basis in ("ltm", "ntm") or pb.basis in ("ltm", "ntm"):
        # "LTM" vs a "30-Sep-25" column: the LTM figure is reported as of a quarter end.
        other = pb if pa.basis in ("ltm", "ntm") else pa
        if (pa.basis == "ltm" or pb.basis == "ltm") and other.part.startswith("q") and other.year:
            return 0.5
        return 0.0
    if pa.year is not None and pa.year == pb.year and pa.part != pb.part:
        ma = pa.part if pa.part.startswith("mo") else ""
        mb = pb.part if pb.part.startswith("mo") else ""
        if ma or mb:
            other = pb if ma else pa
            month = int((ma or mb)[2:])
            if not other.part:
                return 0.5      # "as of March 31, 2025" vs a FY25 / "Mar-25A" year-end column
            if other.part.startswith("q") and other.basis in ("y", "cy") and month == 3 * int(other.part[1]):
                return 0.5      # Mar-25 ends calendar Q1 2025 (a balance date, or one month of a flow)
            # a fiscal quarter (FYE unknown) is matched only when its header names the month (see pipeline)
    if pa.year != pb.year or pa.part != pb.part:
        return 0.0
    if "y" in (pa.basis, pb.basis):
        return 0.85
    return 0.6  # fy vs cy, same year


def periods_compatible(a: str, b: str) -> bool:
    aff = period_affinity(a, b)
    return aff is None or aff > 0.0


def sort_key(key: str) -> tuple:
    """Chronological sort key for period keys (unknowns last)."""
    p = parse_key(key)
    if p is None or p.year is None:
        return (9999, 99, key)
    part_order = {"": 50, "h1": 20, "h2": 40, "q1": 10, "q2": 20, "q3": 30, "q4": 40,
                  "m3": 10, "m6": 20, "m9": 30}
    if p.part.startswith("mo"):
        return (p.year, int(p.part[2:]) * 3.5, key)
    return (p.year, part_order.get(p.part, 60), key)


def previous_quarter(key: str) -> str:
    """The sequential prior quarter (y24q1 -> y23q4); "" for non-quarter keys. Used for QoQ."""
    p = parse_key(key)
    if p is None or p.year is None or not p.part.startswith("q"):
        return ""
    q = int(p.part[1])
    return Period(p.basis, p.year - 1, "q4").key if q == 1 else Period(p.basis, p.year, f"q{q - 1}").key


def previous_period(key: str) -> str:
    """The comparable prior period (fy24 -> fy23, y24q3 -> y23q3)."""
    p = parse_key(key)
    if p is None or p.year is None:
        return ""
    return Period(p.basis, p.year - 1, p.part).key


_SPAN_YEAR = r"((?:(?:fy|cy)\s*)?['’]?(?:\d{4}|\d{2})[aepbf]?)"
_SPAN_END = r"(?![\w%]|\.\d)"
_SPAN_RE = re.compile(rf"(?<![\w.$]){_SPAN_YEAR}\s*(?:-|–|—|‑|to\b|through\b)\s*{_SPAN_YEAR}{_SPAN_END}", re.I)
_BETWEEN_RE = re.compile(rf"\bbetween\s+{_SPAN_YEAR}\s+and\s+{_SPAN_YEAR}{_SPAN_END}", re.I)


def parse_span(text: str) -> tuple[str, str, int] | None:
    """Parse a period span like "FY22-FY24", "2021–2024E", "'21-'24", "between 2022 and 2024".

    Returns ``(start_key, end_key, n_years)`` or ``None``. Used for CAGR.
    """
    text = text or ""
    partial = [(start, end) for p, start, end in find_periods(text) if not p.is_full_year]
    found: set[tuple[str, str, int]] = set()
    for match in (*_BETWEEN_RE.finditer(text), *_SPAN_RE.finditer(text)):
        if any(match.start(group) < end and start < match.end(group)
               for group in (1, 2) for start, end in partial):
            continue  # A year fragment inside a quarterly or monthly period is not an annual endpoint.
        a_txt, b_txt = match.groups()
        if not any(re.search(r"\d{4}|fy|cy|['’]", token, re.I) for token in (a_txt, b_txt)):
            continue  # "10 to 20" is not a year span.
        pa = parse_period(re.sub(r"^(?=\d{2}[aepbf]?$)", "'", a_txt, flags=re.I))
        pb = parse_period(re.sub(r"^(?=\d{2}[aepbf]?$)", "'", b_txt, flags=re.I))
        if not pa or not pb or not pa.is_full_year or not pb.is_full_year:
            continue
        if pb.year <= pa.year:
            continue
        basis_a = pa.basis if pa.basis != "y" else pb.basis
        basis_b = pb.basis if pb.basis != "y" else pa.basis
        if basis_a != basis_b:
            continue
        start = Period(basis_a, pa.year).key
        end = Period(basis_b, pb.year).key
        found.add((start, end, pb.year - pa.year))
    return next(iter(found)) if len(found) == 1 else None


def looks_like_year(token: str) -> bool:
    """True for bare 4-digit years (and with A/E suffix) that should not be checked as numbers."""
    t = token.strip().rstrip("aepbfAEPBF")
    return bool(re.fullmatch(r"(?:19|20)\d{2}", t)) and YEAR_MIN <= int(t) <= YEAR_MAX
