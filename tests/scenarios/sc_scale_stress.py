"""Scale and formatting stress: 40-slide deck vs a 30-sheet model (~65k numeric cells).

Model: 29 segment sheets + a "Group" sheet. Every sheet has three blocks (Annual 2020-2025E, Quarterly
Q1-20..Q4-24, Monthly Jan-20..Dec-24) of 25 identical row labels ("Revenue", "EBITDA", ...), so every label
repeats ~90 times; only the sheet name / block title (segment name) tells them apart. All figures are
internally consistent (stored in integer tenths, sums foot exactly).

Deck: exec summary prose, a group table, 29 segment slides, outlook ranges, league tables, market data
with n.m., quarterly / monthly tables, parentheses negatives, footnote markers "Revenue(1)", "c.", ">",
dates "as of 30-Sep-2024", years in prose.

The scenario times the full review itself (prints TIMING) and fails hard if it takes 60 s or more.
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from numcheck.config import load_config
from numcheck.runner import run_review
from tests.scenarios.harness import Control, Deck, Expect, Model, Scenario

SEGMENTS = [
    "Aerospace", "Automotive", "Chemicals", "Consumer Durables", "Construction", "Defense", "Electronics",
    "Energy Services", "Food Processing", "Healthcare Devices", "Industrial Automation", "Logistics", "Marine",
    "Medical Supplies", "Mining Equipment", "Packaging", "Pharma Services", "Rail", "Renewables",
    "Retail Systems", "Semiconductors", "Shipping", "Steel", "Telecom Equipment", "Textiles",
    "Utilities Equipment", "Water Treatment", "Wind Components", "Agri Machinery",
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
YEARS = [2020, 2021, 2022, 2023, 2024]
ROWS = [
    "Revenue", "Cost of sales", "Gross profit", "Gross margin", "Operating expenses", "EBITDA", "EBITDA margin",
    "D&A", "EBIT", "Interest expense", "Pre-tax profit", "Tax", "Net income", "Capex", "Working capital change",
    "Free cash flow", "R&D spend", "Marketing spend", "Order intake", "Backlog", "Headcount (#)",
    "Volume (000 units)", "Avg. price ($)", "Cash", "Net debt",
]
PCT_ROWS = {"Gross margin", "EBITDA margin"}
STOCK_ROWS = {"Backlog", "Headcount (#)", "Cash", "Net debt"}
FLOW_ROWS = [r for r in ROWS if r not in PCT_ROWS and r not in STOCK_ROWS and r != "Avg. price ($)"]
SEASON = [0.92, 0.90, 1.00, 1.02, 1.04, 1.06, 0.96, 0.94, 1.04, 1.08, 1.06, 0.98]


def derive(b: dict[str, int]) -> dict:
    """All rows from base lines (integer tenths); ratios are floats."""
    d = dict(b)
    d["Gross profit"] = b["Revenue"] - b["Cost of sales"]
    d["EBITDA"] = d["Gross profit"] - b["Operating expenses"]
    d["EBIT"] = d["EBITDA"] - b["D&A"]
    d["Pre-tax profit"] = d["EBIT"] - b["Interest expense"]
    return d


def finish(d: dict) -> dict:
    d["Net income"] = d["Pre-tax profit"] - d["Tax"]
    d["Free cash flow"] = d["EBITDA"] - d["Tax"] - d["Capex"] - d["Working capital change"] - d["Interest expense"]
    d["Gross margin"] = round(d["Gross profit"] / d["Revenue"], 4)
    d["EBITDA margin"] = round(d["EBITDA"] / d["Revenue"], 4)
    vol = max(d["Volume (000 units)"], 1)
    d["Avg. price ($)"] = round(d["Revenue"] / 10 * 1e6 / (vol / 10 * 1e3), 1)
    return d


def month_row(rnd: random.Random, p: dict, t: int) -> dict:
    """One month of base lines (ints, tenths of $m)."""
    rev_f = p["base"] / 12 * (1 + p["g"]) ** (t / 12) * SEASON[t % 12] * (1 + rnd.uniform(-0.03, 0.03))
    rev = int(round(rev_f * 10))
    r = lambda ratio, noise=0.04: int(round(rev * ratio * (1 + rnd.uniform(-noise, noise))))  # noqa: E731
    b = {
        "Revenue": rev, "Cost of sales": r(p["cos"]), "Operating expenses": r(p["opex"]),
        "D&A": int(round(p["base"] * 0.03 / 12 * 10)), "Interest expense": int(round(p["base"] * 0.008 / 12 * 10)),
        "Capex": r(p["capex"], 0.1), "Working capital change": r(0.01, 0.5), "R&D spend": r(0.03), "Marketing spend": r(0.02),
        "Order intake": r(1.05, 0.08), "Volume (000 units)": max(1, r(0.4, 0.05)),
        "Backlog": r(4.0, 0.03), "Headcount (#)": max(1, r(0.02, 0.02)) * 10, "Cash": r(0.3, 0.1), "Net debt": r(0.8, 0.05),
    }
    d = derive(b)
    d["Tax"] = max(0, int(round(d["Pre-tax profit"] * 0.25)))
    return finish(d)


def aggregate(months: list[dict]) -> dict:
    out: dict = {}
    for k in FLOW_ROWS:
        out[k] = sum(m[k] for m in months)
    for k in STOCK_ROWS:
        out[k] = months[-1][k]
    out["Gross margin"] = round(out["Gross profit"] / out["Revenue"], 4)
    out["EBITDA margin"] = round(out["EBITDA"] / out["Revenue"], 4)
    vol = max(out["Volume (000 units)"], 1)
    out["Avg. price ($)"] = round(out["Revenue"] / 10 * 1e6 / (vol / 10 * 1e3), 1)
    return out


def forecast(a24: dict, g: float) -> dict:
    b = {k: int(round(a24[k] * (1 + g))) for k in
         ["Revenue", "Cost of sales", "Operating expenses", "D&A", "Interest expense", "Capex", "Working capital change",
          "R&D spend", "Marketing spend", "Order intake", "Volume (000 units)", "Backlog", "Headcount (#)", "Cash", "Net debt"]}
    d = derive(b)
    d["Tax"] = max(0, int(round(d["Pre-tax profit"] * 0.25)))
    return finish(d)


def series(label: str, vals: list[dict]) -> list:
    if label in PCT_ROWS:
        return [v[label] for v in vals]
    if label == "Avg. price ($)":
        return [v[label] for v in vals]
    return [v[label] / 10 for v in vals]


def build_entity(rnd: random.Random, p: dict):
    months = [month_row(rnd, p, t) for t in range(60)]
    quarters = [aggregate(months[i:i + 3]) for i in range(0, 60, 3)]
    annual = [aggregate(months[i:i + 12]) for i in range(0, 60, 12)]
    annual.append(forecast(annual[-1], p["g"]))
    return months, quarters, annual


def sum_entities(ents: list):
    """Group = sum of segments (additive rows), ratios recomputed."""
    def comb(lists):
        out = []
        for i in range(len(lists[0])):
            row = {k: sum(l[i][k] for l in lists) for k in FLOW_ROWS + list(STOCK_ROWS)}
            row["Gross margin"] = round(row["Gross profit"] / row["Revenue"], 4)
            row["EBITDA margin"] = round(row["EBITDA"] / row["Revenue"], 4)
            vol = max(row["Volume (000 units)"], 1)
            row["Avg. price ($)"] = round(row["Revenue"] / 10 * 1e6 / (vol / 10 * 1e3), 1)
            out.append(row)
        return out
    return tuple(comb([e[i] for e in ents]) for i in range(3))


def write_sheet(m: Model, name: str, ent) -> None:
    months, quarters, annual = ent
    ws = m.sheet(name)
    fmt = {r: ("0.0%" if r in PCT_ROWS else "#,##0.0") for r in ROWS}
    fmt["Headcount (#)"] = "#,##0"
    q_hdr = [f"Q{q}-{y % 100}" for y in YEARS for q in range(1, 5)]
    m_hdr = [f"{mo}-{y % 100}" for y in YEARS for mo in MONTHS]
    nxt = m.block(ws, 1, f"{name} - Annual", "($ in millions)", YEARS + ["2025E"],
                  [(r, series(r, annual)) for r in ROWS], fmt=fmt)
    nxt = m.block(ws, nxt + 1, f"{name} - Quarterly", "($ in millions)", q_hdr,
                  [(r, series(r, quarters)) for r in ROWS], fmt=fmt)
    m.block(ws, nxt + 1, f"{name} - Monthly", "($ in millions)", m_hdr,
            [(r, series(r, months)) for r in ROWS], fmt=fmt)


def f1(v10: int) -> str:
    return f"{v10 / 10:,.1f}"


def neg(v10: int) -> str:
    return f"({abs(v10) / 10:,.1f})" if v10 < 0 else f1(v10)


def pct(r: float) -> str:
    return f"{r * 100:.1f}%"


def transpose_digits(s: str) -> str:
    raw = s.replace(",", "")
    ip, dp = raw.split(".")
    for i in range(len(ip) - 1, 0, -1):
        if ip[i] != ip[i - 1]:
            ip = ip[:i - 1] + ip[i] + ip[i - 1] + ip[i + 1:]
            break
    return f"{int(ip):,}.{dp}"


def build(out: Path) -> Scenario:
    rnd = random.Random(20241119)
    params = {}
    for s in SEGMENTS:
        params[s] = {"base": rnd.uniform(150, 2400), "g": rnd.uniform(0.03, 0.09), "cos": rnd.uniform(0.55, 0.65),
                     "opex": rnd.uniform(0.12, 0.18), "capex": 0.38 if s == "Renewables" else rnd.uniform(0.05, 0.08)}
    ents = {s: build_entity(rnd, params[s]) for s in SEGMENTS}
    group = sum_entities([ents[s] for s in SEGMENTS])

    m = Model()
    for s in SEGMENTS:
        write_sheet(m, s, ents[s])
    write_sheet(m, "Group", group)
    ncells = sum(1 for ws in m.wb.worksheets for row in ws.iter_rows() for c in row
                 if isinstance(c.value, (int, float)))
    model = m.save(out / "segment_model.xlsx")
    print(f"[scale_stress] model: {len(m.wb.worksheets)} sheets, {ncells} numeric cells")

    A = {s: ents[s][2] for s in SEGMENTS}      # annual lists, index 0..4 = 2020..2024, 5 = 2025E
    GA = group[2]
    GQ = group[1]
    IDX = {2022: 2, 2023: 3, 2024: 4}

    planted: dict[int, list[str]] = {}
    expects: list[Expect] = []
    controls: list[Control] = []

    def plant(slide: int, text: str, types: tuple[str, ...], note: str):
        planted.setdefault(slide, []).append(text)
        expects.append(Expect(slide, types, text, note))

    def ctl(slide: int, text: str, allow: tuple[str, ...] = ()):
        if any(text in p or p in text for p in planted.get(slide, [])):
            return
        controls.append(Control(slide, text, allow))

    d = Deck()
    # ---------------------------------------------------------------- slide 1
    g24, g23 = GA[4], GA[3]
    growth = g24["Revenue"] / g23["Revenue"] - 1
    d.new_slide("Executive summary", "Project Atlas - group overview (as of 30-Sep-2024)")
    e5 = f"c.${g24['EBITDA'] / 10 / 100:.1f}bn"          # correct would be /1000
    bad_e5 = f"${g24['EBITDA'] / 10 / 100:,.1f}bn"          # wrong: 10x
    d.bullets([
        f"Group revenue(1) of ${g24['Revenue'] / 10 / 1000:.1f}bn in 2024, up {growth * 100:.1f}% on 2023",
        f"Adj. EBITDA(2) of {bad_e5} at a margin of {pct(g24['EBITDA margin'])} (>20% in each year since 2022)",
        "Management targets 2025E revenue of €1.2–1.5bn from new platforms, at an EBITDA margin of 10–12%",
        "Net leverage n.m. on a pro forma basis; see segment pages for 2019 acquisitions",
    ])
    plant(1, bad_e5, ("scale_error", "mismatch"), "group EBITDA 10x too large")
    ctl(1, f"${g24['Revenue'] / 10 / 1000:.1f}bn")
    ctl(1, f"{growth * 100:.1f}%", allow=("unmatched",))
    ctl(1, pct(g24["EBITDA margin"]))
    ctl(1, "€1.2", allow=("unmatched",))
    ctl(1, "10", allow=("unmatched",))

    # ---------------------------------------------------------------- slide 2 group table
    d.new_slide("Group financial summary", "($ in millions)")
    rows = [["", "2022A", "2023A", "2024A", "2025E"]]
    spec = [("Revenue", "Revenue", f1), ("EBITDA", "EBITDA", f1), ("EBITDA margin", "EBITDA margin", pct),
            ("Net income", "Net income", f1), ("Free cash flow", "Free cash flow", f1)]
    for lab, key, fn in spec:
        row = [lab]
        for i in (2, 3, 4, 5):
            v = GA[i][key]
            row.append(fn(v) if fn is not f1 else f1(v))
        rows.append(row)
    ni_ok = f1(GA[4]["Net income"])
    rows[4][3] = f"({ni_ok})"                                                  # ERROR: sign flipped
    d.table(rows)
    d.footnote("Source: Company filings. Figures as of 30-Sep-2024.")
    plant(2, f"({ni_ok})", ("sign_error", "mismatch"), "group net income sign flipped")
    for r_i, (lab, key, fn) in enumerate(spec, start=1):
        for c_i, i in enumerate((2, 3, 4, 5), start=1):
            txt = rows[r_i][c_i]
            if (r_i, c_i) == (4, 3):
                continue
            ctl(2, txt)

    # ---------------------------------------------------------------- slides 3..31 segments
    seg_slide = {}
    for n, s in enumerate(SEGMENTS):
        sl = 3 + n
        seg_slide[s] = sl
        a = A[s]
        d.new_slide(f"{s} - financial summary", "($ in millions)")
        marker = "(1)" if n % 4 == 0 else ""
        rows = [["", "2022A", "2023A", "2024A"]]
        cells = {}
        for lab, key, fn in [(f"Revenue{marker}", "Revenue", f1), ("Gross profit", "Gross profit", f1),
                             ("EBITDA", "EBITDA", f1), ("EBITDA margin", "EBITDA margin", pct),
                             ("Free cash flow", "Free cash flow", neg)]:
            row = [lab]
            for y in (2022, 2023, 2024):
                v = a[IDX[y]][key]
                row.append(fn(v) if key != "EBITDA margin" else pct(v))
            rows.append(row)
            cells[key] = row[1:]
        # planted errors on specific slides
        if n == 0:                                                       # E1 transposed digits
            bad = transpose_digits(cells["Revenue"][2])
            rows[1][3] = bad
            plant(sl, bad, ("mismatch",), f"{s} revenue 2024 digits transposed")
            cells["Revenue"][2] = None
        if s == "Chemicals":                                             # E2 wrong segment, same label "Revenue"
            other = A["Consumer Durables"][4]["Revenue"]
            rows[1][3] = f1(other)
            plant(sl, f1(other), ("mismatch",), "Revenue of the NEXT segment (wrong sheet, same label)")
            cells["Revenue"][2] = None
        if s == "Shipping":                                              # E3 margin does not reconcile
            bad = pct(a[4]["EBITDA margin"] + 0.02)
            rows[4][3] = bad
            plant(sl, bad, ("mismatch", "pct_reconcile"), "EBITDA margin +2pts vs model")
            cells["EBITDA margin"][2] = None
        if s == "Renewables":                                            # E8 negative FCF shown positive
            assert a[4]["Free cash flow"] < 0, "scenario assumption: Renewables FCF 2024 negative"
            bad = f1(abs(a[4]["Free cash flow"]))
            rows[5][3] = bad
            plant(sl, bad, ("sign_error", "mismatch"), "negative FCF shown without parentheses")
            cells["Free cash flow"][2] = None
        d.table(rows)
        rev22, rev23 = a[2]["Revenue"], a[3]["Revenue"]
        gr = (rev23 / rev22 - 1) * 100
        if s != "Chemicals" and s != "Aerospace":
            d.bullets([f"Revenue grew {gr:.1f}% in 2023 (>{max(int(gr) - 3, 0)}% p.a. since 2022)"])
            ctl(sl, f"{gr:.1f}%", allow=("unmatched",))
        d.footnote("Source: Company data as of 30-Sep-2024." + (" (1) Includes intersegment sales." if marker else ""))
        for key, vals in cells.items():
            for txt in vals:
                if txt:
                    ctl(sl, txt)

    # ---------------------------------------------------------------- 32 outlook
    d.new_slide("Outlook and guidance", "2025E management guidance")
    d.bullets([
        "2025E group revenue growth of 5–7% (2024: " + f"{growth * 100:.1f}%)",
        "Capex of c.$1.8–2.1bn in 2025E, including >$400m for Renewables",
        "In 2023 the group completed 3 acquisitions; since 2019 leverage has fallen",
        "Medium-term EBITDA margin target of 24–26% by 2027",
    ])
    for t in ("5–7%", "$1.8–2.1bn", "24–26%"):
        ctl(32, t, allow=("unmatched",))
    ctl(32, f"{growth * 100:.1f}%", allow=("unmatched",))
    ctl(32, ">$400m", allow=("unmatched",))

    # ---------------------------------------------------------------- 33 league table (segment name as row label)
    top = sorted(SEGMENTS, key=lambda s: -A[s][4]["Revenue"])[:10]
    d.new_slide("Segment league table: 2024 revenue", "($ in millions)")
    rows = [["Segment", "2024A revenue"]]
    for i, s in enumerate(top):
        val = f1(A[s][4]["Revenue"])
        if i == 3:
            val = f1(A[s][3]["Revenue"])                                  # E6 stale: 2023 value under 2024
            plant(33, val, ("mismatch", "period_ambiguous", "stale"), f"{s}: 2023 revenue shown as 2024")
        rows.append([s, val])
    d.table(rows, first_col=3.5)
    d.footnote("Source: Company data as of 30-Sep-2024.")
    for r in rows[1:]:
        ctl(33, r[1])

    # ---------------------------------------------------------------- 34 market data with n.m.
    d.new_slide("Market data and trading multiples", "Share prices as of 14-Nov-2024")
    d.table([
        ["Peer", "EV / EBITDA 2024", "EV / EBITDA 2025E", "P / E 2025E"],
        ["Peer A", "11.2x", "10.1x", "17.4x"],
        ["Peer B", "9.8x", "n.m.", "n.m."],
        ["Peer C", "–", "12.0x", "19.3x"],
        ["Median", "10.5x", "11.1x", "18.4x"],
    ])
    for t in ("11.2x", "10.1x", "17.4x", "9.8x", "12.0x", "19.3x", "10.5x", "11.1x", "18.4x"):
        ctl(34, t, allow=("unmatched",))

    # ---------------------------------------------------------------- 35 group quarterly (E7)
    d.new_slide("Group quarterly performance 2024", "($ in millions)")
    q24 = GQ[16:20]
    rows = [["", "Q1-24", "Q2-24", "Q3-24", "Q4-24"]]
    rev_row = ["Revenue"] + [f1(q["Revenue"]) for q in q24]
    rev_row[3] = f1(q24[1]["Revenue"])                                   # E7: Q2 value pasted into Q3
    plant(35, rev_row[3], ("mismatch",), "Q3-24 revenue = Q2 value")
    rows.append(rev_row)
    rows.append(["EBITDA"] + [f1(q["EBITDA"]) for q in q24])
    rows.append(["EBITDA margin"] + [pct(q["EBITDA margin"]) for q in q24])
    d.table(rows)
    for i, q in enumerate(q24):
        if i != 2:
            ctl(35, f1(q["Revenue"]))
        ctl(35, f1(q["EBITDA"]))
        ctl(35, pct(q["EBITDA margin"]))

    # ---------------------------------------------------------------- 36 monthly
    am = ents["Aerospace"][0][48:54]
    d.new_slide("Aerospace monthly revenue, H1 2024", "($ in millions)")
    rows = [[""] + [f"{mo}-24" for mo in MONTHS[:6]], ["Revenue"] + [f1(x["Revenue"]) for x in am],
            ["EBITDA"] + [f1(x["EBITDA"]) for x in am]]
    d.table(rows, col_width=1.1)
    for x in am:
        ctl(36, f1(x["Revenue"]))
        ctl(36, f1(x["EBITDA"]))

    # ---------------------------------------------------------------- 37 margin league
    topm = sorted(SEGMENTS, key=lambda s: -A[s][4]["EBITDA margin"])[:8]
    d.new_slide("EBITDA margin by segment, 2024")
    rows = [["Segment", "EBITDA margin 2024A"]] + [[s, pct(A[s][4]["EBITDA margin"])] for s in topm]
    d.table(rows, first_col=3.5)
    for r in rows[1:]:
        ctl(37, r[1])

    # ---------------------------------------------------------------- 38 group cash flow w/ negatives
    d.new_slide("Group cash flow", "($ in millions)")
    rows = [["", "2023A", "2024A"],
            ["EBITDA", f1(GA[3]["EBITDA"]), f1(GA[4]["EBITDA"])],
            ["Less: capex", neg(-GA[3]["Capex"]), neg(-GA[4]["Capex"])],
            ["Less: change in working capital", neg(-GA[3]["Working capital change"]), neg(-GA[4]["Working capital change"])],
            ["Free cash flow", f1(GA[3]["Free cash flow"]), f1(GA[4]["Free cash flow"])]]
    d.table(rows, first_col=3.8)
    ctl(38, rows[1][1]); ctl(38, rows[1][2])
    ctl(38, rows[4][1]); ctl(38, rows[4][2])

    # ---------------------------------------------------------------- 39 Renewables quarterly FCF (parens)
    rq = ents["Renewables"][1][12:16]
    d.new_slide("Renewables quarterly free cash flow, 2023", "($ in millions)")
    rows = [["", "Q1-23", "Q2-23", "Q3-23", "Q4-23"], ["Free cash flow"] + [neg(q["Free cash flow"]) for q in rq],
            ["Capex"] + [f1(q["Capex"]) for q in rq]]
    d.table(rows)
    for q in rq:
        ctl(39, neg(q["Free cash flow"]))
        ctl(39, f1(q["Capex"]))

    # ---------------------------------------------------------------- 40 notes
    d.new_slide("Important notes")
    d.bullets([
        "All figures as of 30-Sep-2024 unless otherwise stated; fiscal years ended 31 December 2022, 2023 and 2024.",
        "Segment definitions changed in 2021; 2020 figures are not comparable.",
        "n.m. = not meaningful; c. = circa.",
    ])
    deck = d.save(out / "deck.pptx")

    # ---------------------------------------------------------------- timing of a full review
    cfg = load_config(None, discover=False)
    t0 = time.perf_counter()
    res = run_review(deck, [model], None, str(out / "timing_report"), annotate=True, config=cfg)
    dt = time.perf_counter() - t0
    print(f"[scale_stress] TIMING full review: {dt:.1f}s  ({len(res.issues)} issues, "
          f"{res.coverage.get('verified_pct')}% verified, {res.coverage.get('checkable')} checkable numbers, "
          f"{len(m.wb.worksheets)} sheets, {ncells} model cells)")
    if dt >= 60:
        raise RuntimeError(f"review took {dt:.1f}s (>= 60s budget)")

    return Scenario(
        name="scale_stress", deck=deck, models=[model],
        description="40 slides x 30 sheets x ~65k cells with repeated labels; timing printed at build",
        expects=expects, controls=controls, min_verified_pct=85.0, max_extra_high=2,
    )
