"""Scenario harness: realistic deck + model pairs with planted errors and controls.

A scenario module defines ``build(out_dir: Path) -> Scenario``. The harness runs
the full pipeline and scores the result:

* every ``Expect`` must be found (as an issue, or folded into an issue's ``related``)
* every ``Control`` (a correct number) must NOT be the deck value of any issue,
  except issue types listed in ``Control.allow``
* coverage must reach ``min_verified_pct`` and there must be no unexpected HIGH issues
  beyond the expectations (``max_extra_high``)

Builders here keep scenario code short and close to how bankers actually lay out
slides and models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font
from pptx import Presentation
from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Inches, Pt

from numcheck.config import Config, load_config
from numcheck.models import Issue, ReviewResult
from numcheck.runner import run_review

# --------------------------------------------------------------------------- #
# Expectations
# --------------------------------------------------------------------------- #


@dataclass
class Expect:
    """A planted error that must be reported."""

    slide: int
    types: tuple[str, ...]            # any of these issue types satisfies it
    value: str = ""                   # substring of the issue's deck_value ("" = any)
    note: str = ""


@dataclass
class Control:
    """A correct number that must not be flagged."""

    slide: int
    value: str                        # substring of deck_value
    allow: tuple[str, ...] = ()       # issue types tolerated on it (e.g. "unmatched")
    note: str = ""


@dataclass
class Scenario:
    name: str
    deck: str
    models: list[str]
    model_old: str | None = None
    expects: list[Expect] = field(default_factory=list)
    controls: list[Control] = field(default_factory=list)
    min_verified_pct: float = 60.0
    max_extra_high: int = 0
    config: dict[str, Any] = field(default_factory=dict)   # Config attribute overrides
    description: str = ""


@dataclass
class Score:
    scenario: Scenario
    result: ReviewResult
    missed: list[Expect]
    false_positives: list[tuple[Control, Issue]]
    extra_high: list[Issue]

    @property
    def ok(self) -> bool:
        cov = self.result.coverage.get("verified_pct", 0.0)
        return (not self.missed and not self.false_positives
                and len(self.extra_high) <= self.scenario.max_extra_high
                and cov >= self.scenario.min_verified_pct)

    def report(self) -> str:
        r = self.result
        lines = [f"== {self.scenario.name}: {'OK' if self.ok else 'FAIL'} | "
                 f"{len(r.issues)} issues | verified {r.coverage.get('verified_pct')}% "
                 f"({r.coverage.get('verified')}/{r.coverage.get('checkable')})"]
        for e in self.missed:
            lines.append(f"  MISSED  s{e.slide} {e.types} {e.value!r} {e.note}")
        for c, i in self.false_positives:
            lines.append(f"  FALSE+  s{c.slide} {c.value!r} -> {i.issue_type.value}: {i.message[:140]}")
        for i in self.extra_high[: self.scenario.max_extra_high + 10]:
            lines.append(f"  EXTRA-H s{i.slide_number} {i.issue_type.value} {i.deck_value!r}: {i.message[:140]}")
        if self.result.coverage.get("verified_pct", 0) < self.scenario.min_verified_pct:
            lines.append(f"  LOW COVERAGE (< {self.scenario.min_verified_pct}%)")
        for w in r.warnings:
            lines.append(f"  warn {w.code}: {w.message[:120]}")
        return "\n".join(lines)


def run_scenario(sc: Scenario, out_dir: Path) -> Score:
    cfg: Config = load_config(None, discover=False)
    for k, v in sc.config.items():
        setattr(cfg, k, v)
    result = run_review(sc.deck, sc.models, sc.model_old, str(out_dir / "report"), annotate=True, config=cfg)
    issues = result.issues

    def hits(slide: int, types: tuple[str, ...], value: str) -> list[Issue]:
        out = []
        for i in issues:
            if i.slide_number != slide or (value and value not in i.deck_value):
                continue
            if i.issue_type.value in types or any(r.get("issue_type") in types for r in i.related):
                out.append(i)
        return out

    missed = [e for e in sc.expects if not hits(e.slide, e.types, e.value)]
    fps: list[tuple[Control, Issue]] = []
    for c in sc.controls:
        # digit-bounded match: a control "5%" must not catch "25%" or "65%"
        pat = re.compile(r"(?<![\d.,])" + re.escape(c.value) + r"(?![\d])")
        for i in issues:
            if i.slide_number == c.slide and pat.search(i.deck_value) and i.issue_type.value not in c.allow:
                fps.append((c, i))
    expected_ids = {id(i) for e in sc.expects for i in hits(e.slide, e.types, e.value)}
    extra_high = [i for i in issues if i.severity.value == "high" and id(i) not in expected_ids]
    return Score(sc, result, missed, fps, extra_high)


# --------------------------------------------------------------------------- #
# Deck builder
# --------------------------------------------------------------------------- #


class Deck:
    """Fluent builder for realistic pitchbook slides (16:9 by default)."""

    def __init__(self, widescreen: bool = True):
        self.prs = Presentation()
        if widescreen:
            self.prs.slide_width, self.prs.slide_height = Inches(13.333), Inches(7.5)
        self.slide = None
        self._y = Inches(1.3)

    @property
    def W(self) -> Emu:
        return self.prs.slide_width

    def new_slide(self, title: str, subtitle: str = "", hidden: bool = False):
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[5])  # Title Only
        self.slide.shapes.title.text = title
        self._y = Inches(1.2)
        if subtitle:
            self.text(subtitle, size=12, height=0.4)
        if hidden:
            self.slide._element.set("show", "0")
        return self

    def text(self, body: str | list[str], size: int = 14, height: float | None = None,
             left: float = 0.5, width: float | None = None):
        paras = [body] if isinstance(body, str) else body
        h = Inches(height if height is not None else 0.35 * len(paras) + 0.2)
        w = Inches(width) if width else self.W - Inches(left + 0.5)
        tb = self.slide.shapes.add_textbox(Inches(left), self._y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        for n, p in enumerate(paras):
            para = tf.paragraphs[0] if n == 0 else tf.add_paragraph()
            para.text = p
            para.font.size = Pt(size)
        self._y += h
        return self

    def bullets(self, items: list[str], size: int = 14):
        return self.text(items, size=size)

    def table(self, rows: list[list[Any]], col_width: float = 1.4, first_col: float = 3.0,
              left: float = 0.5, merge: list[tuple[int, int, int, int]] | None = None):
        nr, nc = len(rows), len(rows[0])
        width = Inches(first_col + col_width * (nc - 1))
        h = Inches(0.32 * nr)
        gf = self.slide.shapes.add_table(nr, nc, Inches(left), self._y, width, h)
        t = gf.table
        t.columns[0].width = Inches(first_col)
        for c in range(1, nc):
            t.columns[c].width = Inches(col_width)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                cell = t.cell(r, c)
                cell.text = "" if v is None else str(v)
                for p in cell.text_frame.paragraphs:
                    p.font.size = Pt(10)
        for (r1, c1, r2, c2) in merge or []:
            t.cell(r1, c1).merge(t.cell(r2, c2))
        self._y += h + Inches(0.2)
        return self

    def chart(self, kind: str, categories: list[str], series: dict[str, list[float]],
              number_format: str | None = None, data_labels: bool = True, title: str | None = None,
              height: float = 3.2):
        kinds = {
            "column": XL_CHART_TYPE.COLUMN_CLUSTERED, "stacked": XL_CHART_TYPE.COLUMN_STACKED,
            "bar": XL_CHART_TYPE.BAR_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS,
            "pie": XL_CHART_TYPE.PIE, "doughnut": XL_CHART_TYPE.DOUGHNUT,
        }
        cd = CategoryChartData(number_format=number_format) if number_format else CategoryChartData()
        cd.categories = categories
        for name, vals in series.items():
            cd.add_series(name, vals)
        gf = self.slide.shapes.add_chart(kinds[kind], Inches(0.5), self._y, Inches(8), Inches(height), cd)
        ch = gf.chart
        if title:
            ch.has_title = True
            ch.chart_title.text_frame.text = title
        if data_labels:
            plot = ch.plots[0]
            plot.has_data_labels = True
            if number_format:
                plot.data_labels.number_format = number_format
                plot.data_labels.number_format_is_linked = False
        self._y += Inches(height + 0.1)
        return self

    def scatter(self, series: dict[str, list[tuple[float, float]]], height: float = 3.0):
        cd = XyChartData()
        for name, pts in series.items():
            s = cd.add_series(name)
            for x, y in pts:
                s.add_data_point(x, y)
        self.slide.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(0.5), self._y, Inches(7), Inches(height), cd)
        self._y += Inches(height + 0.1)
        return self

    def footnote(self, text: str):
        tb = self.slide.shapes.add_textbox(Inches(0.5), self.prs.slide_height - Inches(0.7),
                                           self.W - Inches(1), Inches(0.4))
        tb.text_frame.text = text
        tb.text_frame.paragraphs[0].font.size = Pt(8)
        return self

    def picture_placeholder(self):
        """A small image (e.g. a pasted Excel screenshot) that may hide numbers."""
        import base64
        import io

        png = base64.b64decode(
            b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        self.slide.shapes.add_picture(io.BytesIO(png), Inches(9), Inches(2), Inches(3), Inches(2))
        return self

    def save(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.save(path)
        return str(path)


# --------------------------------------------------------------------------- #
# Model builder
# --------------------------------------------------------------------------- #


class Model:
    """Builds banker-style workbooks.

    ``block()`` writes a titled block with a units banner, a header row of periods
    (as numbers or text), optional spacer column, and labelled rows. Values may be
    numbers or formulas (strings starting with "="); ``fmt`` sets number formats per
    row label.
    """

    def __init__(self):
        self.wb = openpyxl.Workbook()
        self.wb.remove(self.wb.active)

    def sheet(self, name: str, hidden: bool = False):
        ws = self.wb.create_sheet(name)
        if hidden:
            ws.sheet_state = "hidden"
        return ws

    def block(self, ws, top: int, title: str, units: str, periods: list[Any],
              rows: list[tuple[str, list[Any]]], fmt: dict[str, str] | None = None,
              spacer: bool = True, label_col: int = 1, group_header: list[tuple[str, int]] | None = None) -> int:
        """Write a block; returns the next free row. ``group_header`` = [(text, span), ...]."""
        fmt = fmt or {}
        ws.cell(top, label_col, title).font = Font(bold=True)
        if units:
            ws.cell(top + 1, label_col, units)
        first_data_col = label_col + (2 if spacer else 1)
        hdr = top + 3
        if group_header:
            col = first_data_col
            for text, span in group_header:
                ws.cell(hdr - 1, col, text)
                if span > 1:
                    ws.merge_cells(start_row=hdr - 1, start_column=col, end_row=hdr - 1, end_column=col + span - 1)
                col += span
        for j, p in enumerate(periods):
            ws.cell(hdr, first_data_col + j, p)
        r = hdr + 2
        for label, vals in rows:
            ws.cell(r, label_col, label)
            for j, v in enumerate(vals):
                if v is None:
                    continue
                c = ws.cell(r, first_data_col + j, v)
                if label in fmt:
                    c.number_format = fmt[label]
            r += 1
        return r + 1

    def name(self, name: str, ref: str):
        from openpyxl.workbook.defined_name import DefinedName

        self.wb.defined_names[name] = DefinedName(name, attr_text=ref)

    def save(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.wb.save(path)
        return str(path)
