"""Built-in fallback calculator for formulas with no stored result."""
from numcheck.extraction.formula_eval import evaluate_uncached
from numcheck.extraction.xlsx_structure import SheetGrid


def _grids(**sheets):
    out = {}
    for name, cells in sheets.items():
        g = SheetGrid(name=name.replace("_", " "))
        for addr, v in cells.items():
            col = ord(addr[0]) - 64
            key = (int(addr[1:]), col)
            if isinstance(v, str) and v.startswith("="):
                g.formulas[key] = v
            else:
                g.cells[key] = v
        out[g.name] = g
    return out


def _missing(g):
    return [k for k in g.formulas if k not in g.cells]


def _run(**sheets):
    grids = _grids(**sheets)
    n = evaluate_uncached(grids, _missing)
    return n, grids


def test_arithmetic_precedence_and_functions():
    n, g = _run(S={"A1": 100.0, "A2": 20.0, "A3": "=A1*1.25-A2/4+2^3",
                   "A4": "=SUM(A1:A2)/2", "A5": "=ROUND(A1/3,1)", "A6": "=IF(A1>A2,A1,A2)",
                   "A7": "=-A2^2", "A8": "=A2%", "A9": "=AVERAGE(A1:A2,60)"})
    c = g["S"].cells
    assert n == 7
    assert c[(3, 1)] == 100 * 1.25 - 5 + 8
    assert c[(4, 1)] == 60
    assert c[(5, 1)] == 33.3
    assert c[(6, 1)] == 100
    assert c[(7, 1)] == 400          # Excel: unary minus binds tighter than ^
    assert c[(8, 1)] == 0.2
    assert c[(9, 1)] == 60


def test_chains_cross_sheet_and_quoted_names():
    n, g = _run(IS={"B1": 50.0, "B2": "=B1*2"},
                Sum_up={"A1": "='IS'!B2+IS!B1", "A2": "=A1+1"})
    assert n == 3
    assert g["Sum up"].cells[(2, 1)] == 151


def test_errors_cycles_and_unsupported_stay_unevaluated():
    n, g = _run(S={"A1": 0.0, "A2": "=5/A1", "A3": "=A4", "A4": "=A3+1", "A5": "=XNPV(A1,A2)",
                   "A6": "=Other!A1", "A7": "=[1]Book!A1", "A8": "=IFERROR(5/A1,7)", "A9": "=A2+1"})
    c = g["S"].cells
    assert n == 1
    assert c[(8, 1)] == 7
    for r in (2, 3, 4, 5, 6, 7, 9):
        assert (r, 1) not in c


def test_text_results_are_not_numbers():
    n, g = _run(S={"A1": "x", "A2": '="FY"&2024', "A3": "=A1*2"})
    assert n == 0
