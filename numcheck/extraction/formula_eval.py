"""A small, deterministic fallback calculator for formulas that have no stored result.

Workbooks written by scripts (openpyxl, pandas, some exporters) often carry formulas
that Excel never calculated, so the file stores no value for them. LibreOffice is
the first choice for recalculation; when it is not installed, this module evaluates
the common banker subset itself:

* arithmetic ``+ - * / ^ %``, unary minus, parentheses, comparisons, ``&``
* references ``A1``, ``$A$1``, ``Sheet!A1``, ``'My sheet'!A1:B9``
* ``SUM AVERAGE MIN MAX COUNT PRODUCT ABS ROUND ROUNDUP ROUNDDOWN IF IFERROR AND OR NOT``

Anything else (unknown functions, external workbooks, defined names, errors,
circular references) leaves the cell unevaluated, which is the existing behaviour.
Nothing here ever raises out of :func:`evaluate_uncached`.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable

from .xlsx_structure import SheetGrid

_MAX_RANGE = 200_000
_MAX_DEPTH = 400

_TOKEN_RE = re.compile(
    r"""\s*(?:
        (?P<str>"(?:[^"]|"")*")
      | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)(?![\w(])
      | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
      | (?P<func>[A-Za-z_][\w.]*)\s*\(
      | (?P<bool>TRUE|FALSE)\b
      | (?P<op><>|<=|>=|[-+*/^&=<>%(),:])
    )""",
    re.X | re.I,
)


class _Unsupported(Exception):
    pass


class _XlError(Exception):
    """An Excel error value (#DIV/0!, #VALUE!...) - the cell stays unevaluated."""


def _col_num(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


_CELL_RE = re.compile(r"\$?([A-Za-z]{1,3})\$?(\d+)")


def _parse_ref(text: str, default_sheet: str) -> tuple[str, int, int, int, int]:
    sheet = default_sheet
    if "!" in text:
        sheet, text = text.rsplit("!", 1)
        if sheet.startswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
    parts = text.split(":")
    m1 = _CELL_RE.fullmatch(parts[0])
    m2 = _CELL_RE.fullmatch(parts[-1])
    if not m1 or not m2:
        raise _Unsupported(text)
    r1, c1 = int(m1.group(2)), _col_num(m1.group(1))
    r2, c2 = int(m2.group(2)), _col_num(m2.group(1))
    return sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


class _Range:
    __slots__ = ("values",)

    def __init__(self, values: list[Any]):
        self.values = values


def _tokenize(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    src = src.strip()
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            raise _Unsupported(src[pos:pos + 20])
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def _num(v: Any) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        if v.startswith("#"):
            raise _XlError(v)
        try:
            return float(v.replace(",", ""))
        except ValueError:
            raise _XlError("#VALUE!") from None
    raise _Unsupported(type(v).__name__)


def _flat(args: list[Any]) -> list[Any]:
    out: list[Any] = []
    for a in args:
        if isinstance(a, _Range):
            for v in a.values:
                if isinstance(v, str) and v.startswith("#"):
                    raise _XlError(v)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append(float(v))
        else:
            out.append(_num(a))
    return out


def _round(x: float, n: float, mode: str) -> float:
    k = 10 ** int(n)
    if mode == "up":
        return math.copysign(math.ceil(abs(x) * k - 1e-9) / k, x)
    if mode == "down":
        return math.copysign(math.floor(abs(x) * k + 1e-9) / k, x)
    return math.copysign(math.floor(abs(x) * k + 0.5) / k, x)


def _avg(xs: list[float]) -> float:
    if not xs:
        raise _XlError("#DIV/0!")
    return sum(xs) / len(xs)


_FUNCS: dict[str, Callable[[list[Any]], Any]] = {
    "SUM": lambda a: sum(_flat(a)),
    "AVERAGE": lambda a: _avg(_flat(a)),
    "MIN": lambda a: min(_flat(a), default=0.0),
    "MAX": lambda a: max(_flat(a), default=0.0),
    "COUNT": lambda a: float(len(_flat(a))),
    "PRODUCT": lambda a: math.prod(_flat(a)),
    "ABS": lambda a: abs(_num(a[0])),
    "ROUND": lambda a: _round(_num(a[0]), _num(a[1]) if len(a) > 1 else 0, "half"),
    "ROUNDUP": lambda a: _round(_num(a[0]), _num(a[1]) if len(a) > 1 else 0, "up"),
    "ROUNDDOWN": lambda a: _round(_num(a[0]), _num(a[1]) if len(a) > 1 else 0, "down"),
    "AND": lambda a: all(_flat(a)),
    "OR": lambda a: any(_flat(a)),
    "NOT": lambda a: not _num(a[0]),
}


class _Evaluator:
    def __init__(self, grids: dict[str, SheetGrid]):
        self.grids = grids
        self.lower = {k.lower(): k for k in grids}
        self.done: dict[tuple[str, int, int], Any] = {}
        self.active: set[tuple[str, int, int]] = set()

    # -- cell access -------------------------------------------------------- #
    def _grid(self, sheet: str) -> SheetGrid:
        name = self.lower.get(sheet.lower())
        if name is None:
            raise _Unsupported(f"sheet {sheet}")
        return self.grids[name]

    def cell(self, sheet: str, r: int, c: int) -> Any:
        g = self._grid(sheet)
        key = (r, c)
        if key in g.cells:
            return g.cells[key]
        if key in g.formulas:
            return self.formula_cell(g, key)
        return None

    def formula_cell(self, g: SheetGrid, key: tuple[int, int]) -> Any:
        k = (g.name, *key)
        if k in self.done:
            v = self.done[k]
            if isinstance(v, Exception):
                raise v
            return v
        if k in self.active or len(self.active) > _MAX_DEPTH:
            raise _Unsupported("circular reference")
        self.active.add(k)
        try:
            v = self.evaluate(g.formulas[key], g.name)
            if isinstance(v, _Range):
                raise _Unsupported("range result")
            self.done[k] = v
            return v
        except (_Unsupported, _XlError, ArithmeticError, ValueError, TypeError) as exc:
            err = exc if isinstance(exc, (_Unsupported, _XlError)) else _XlError(str(exc))
            self.done[k] = err
            raise err from None
        finally:
            self.active.discard(k)

    def range(self, sheet: str, r1: int, c1: int, r2: int, c2: int) -> _Range:
        g = self._grid(sheet)
        n = (r2 - r1 + 1) * (c2 - c1 + 1)
        if n > _MAX_RANGE:
            raise _Unsupported("range too large")
        keys = set(g.cells) | set(g.formulas)
        if n <= len(keys):
            cand = [(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1) if (r, c) in keys]
        else:
            cand = sorted(k for k in keys if r1 <= k[0] <= r2 and c1 <= k[1] <= c2)
        vals = []
        for key in cand:
            vals.append(g.cells[key] if key in g.cells else self.formula_cell(g, key))
        return _Range(vals)

    # -- parser ------------------------------------------------------------- #
    def evaluate(self, formula: str, sheet: str) -> Any:
        src = formula[1:] if formula.startswith("=") else formula
        if "[" in src or "{" in src:
            raise _Unsupported("external or array formula")
        self.toks = _tokenize(src)
        self.i = 0
        self.sheet = sheet
        v = self.expr(0)
        if self.i != len(self.toks):
            raise _Unsupported("trailing tokens")
        return v

    _BIN = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5}

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def expr(self, min_prec: int) -> Any:
        left = self.unary()
        while True:
            t = self.peek()
            if not t or t[0] != "op" or t[1] not in self._BIN:
                return left
            prec = self._BIN[t[1]]
            if prec < min_prec:
                return left
            self.i += 1
            right = self.expr(prec + (0 if t[1] == "^" else 1))
            left = self.binop(t[1], left, right)

    def unary(self) -> Any:
        t = self.peek()
        if t and t == ("op", "-"):
            self.i += 1
            return -_num(self.unary())
        if t and t == ("op", "+"):
            self.i += 1
            return self.unary()
        v = self.atom()
        while self.peek() == ("op", "%"):
            self.i += 1
            v = _num(v) / 100.0
        return v

    def atom(self) -> Any:
        t = self.peek()
        if t is None:
            raise _Unsupported("unexpected end")
        kind, text = t
        self.i += 1
        if kind == "num":
            return float(text)
        if kind == "str":
            return text[1:-1].replace('""', '"')
        if kind == "bool":
            return text.upper() == "TRUE"
        if kind == "ref":
            sheet, r1, c1, r2, c2 = _parse_ref(text, self.sheet)
            if (r1, c1) == (r2, c2) and ":" not in text:
                return self.cell(sheet, r1, c1)
            return self.range(sheet, r1, c1, r2, c2)
        if kind == "func":
            return self.call(text.upper())
        if (kind, text) == ("op", "("):
            v = self.expr(0)
            if self.peek() != ("op", ")"):
                raise _Unsupported("missing )")
            self.i += 1
            return v
        raise _Unsupported(text)

    def args(self) -> list[Callable[[], Any]]:
        """Return argument thunks (start/end token spans) so IF/IFERROR stay lazy."""
        spans: list[tuple[int, int]] = []
        depth, start = 0, self.i
        while True:
            t = self.peek()
            if t is None:
                raise _Unsupported("missing )")
            if t[0] == "func" or t == ("op", "("):
                depth += 1
            elif t == ("op", ")"):
                if depth == 0:
                    if self.i > start:
                        spans.append((start, self.i))
                    self.i += 1
                    break
                depth -= 1
            elif t == ("op", ",") and depth == 0:
                spans.append((start, self.i))
                start = self.i + 1
            self.i += 1
        toks = self.toks

        def thunk(span: tuple[int, int]) -> Callable[[], Any]:
            def run() -> Any:
                saved = (self.toks, self.i)
                self.toks, self.i = toks[span[0]:span[1]], 0
                try:
                    v = self.expr(0)
                    if self.i != len(self.toks):
                        raise _Unsupported("bad argument")
                    return v
                finally:
                    self.toks, self.i = saved
            return run

        return [thunk(s) for s in spans]

    def call(self, name: str) -> Any:
        thunks = self.args()
        if name == "IF":
            if not 2 <= len(thunks) <= 3:
                raise _Unsupported("IF arity")
            if _num(thunks[0]()):
                return thunks[1]()
            return thunks[2]() if len(thunks) == 3 else False
        if name == "IFERROR":
            if len(thunks) != 2:
                raise _Unsupported("IFERROR arity")
            try:
                v = thunks[0]()
                if isinstance(v, str) and v.startswith("#"):
                    raise _XlError(v)
                return v
            except (_XlError, ArithmeticError):
                return thunks[1]()
        fn = _FUNCS.get(name)
        if fn is None:
            raise _Unsupported(name)
        return fn([t() for t in thunks])

    @staticmethod
    def binop(op: str, a: Any, b: Any) -> Any:
        if isinstance(a, _Range) or isinstance(b, _Range):
            raise _Unsupported("range in arithmetic")
        if op == "&":
            return f"{'' if a is None else a}{'' if b is None else b}"
        if op in ("=", "<>", "<", ">", "<=", ">="):
            x, y = (_num(a), _num(b)) if not (isinstance(a, str) or isinstance(b, str)) else (str(a), str(b))
            return {"=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]
        x, y = _num(a), _num(b)
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            if y == 0:
                raise _XlError("#DIV/0!")
            return x / y
        if op == "^":
            return x ** y
        raise _Unsupported(op)


def evaluate_uncached(grids: dict[str, SheetGrid], missing: Callable[[SheetGrid], list[tuple[int, int]]]) -> int:
    """Fill numeric results of uncached formulas into ``grid.cells``; return how many were filled."""
    try:
        ev = _Evaluator(grids)
        filled = 0
        for g in grids.values():
            for key in missing(g):
                try:
                    v = ev.formula_cell(g, key)
                except Exception:  # noqa: BLE001 - unsupported/error: leave unevaluated
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    continue
                g.cells[key] = float(v)
                filled += 1
        return filled
    except Exception:  # noqa: BLE001 - never break extraction
        return 0
