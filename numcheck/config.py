"""Configuration loading (defaults + optional numcheck.toml + environment).

Resolution order (later wins): built-in defaults -> config file -> environment
variables -> explicit CLI/web overrides applied by the caller.

The config file is found at the explicit path if given, else ``$NUMCHECK_CONFIG``,
else ``./numcheck.toml``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_ABBREVIATIONS: dict[str, str] = {
    "rev": "revenue",
    "revs": "revenue",
    "sales": "revenue",
    "net sales": "revenue",
    "turnover": "revenue",
    "top line": "revenue",
    "ni": "net income",
    "net earnings": "net income",
    "pat": "net income",
    "profit after tax": "net income",
    "op income": "ebit",
    "operating income": "ebit",
    "operating profit": "ebit",
    "ev": "enterprise value",
    "tev": "enterprise value",
    "eqv": "equity value",
    "eq val": "equity value",
    "mkt cap": "market capitalization",
    "market cap": "market capitalization",
    "fcf": "free cash flow",
    "sum of pv of fcf": "aggregate present value free cash flow",
    "sum of pv of free cash flows": "aggregate present value free cash flow",
    "implied share price": "implied value per share",
    "discounted cash flow": "dcf",
    "ufcf": "unlevered free cash flow",
    "lfcf": "levered free cash flow",
    "gm": "gross margin",
    "gp": "gross profit",
    "cogs": "cost of goods sold",
    "cost of sales": "cost of goods sold",
    "sg&a": "sga",
    "sga": "sga",
    "opex": "operating expenses",
    "capex": "capital expenditures",
    "cap ex": "capital expenditures",
    "nwc": "net working capital",
    "d&a": "depreciation amortization",
    "da": "depreciation amortization",
    "share count": "shares outstanding",
    "shs": "shares",
    "dps": "dividend per share",
    "yoy": "growth",
    "y/y": "growth",
    "y-o-y": "growth",
    "year-over-year": "growth",
    "year over year": "growth",
    "cagr": "cagr",
    "adj": "adjusted",
    "adj.": "adjusted",
    "pf": "pro forma",
    "pro-forma": "pro forma",
    "proforma": "pro forma",
    "nd": "net debt",
    "lev": "leverage",
    "noi": "net operating income",
    "net operating income": "net operating income",   # longer key wins over "operating income" -> ebit
    "offer premium": "premium",
    "premium paid": "premium",
    "takeover premium": "premium",
    "acquisition premium": "premium",
    "escalation": "escalator",
    "annual escalation": "escalator",
    "gross debt": "total debt",
    "interest cover": "ebitda interest",
    "interest coverage": "ebitda interest",
    "interest coverage ratio": "ebitda interest",
    "net leverage": "net debt ebitda",
    "total leverage": "total debt ebitda",
    "gross leverage": "total debt ebitda",
    "wacc": "wacc",
    "tgr": "terminal growth rate",
    "irr": "irr",
    "moic": "moic",
    "roic": "roic",
    "roe": "roe",
    "bv": "book value",
    "tbv": "tangible book value",
    # direction words on change figures ("an improvement of 70bps" = the change)
    "improvement": "change",
    "deterioration": "change",
    "expansion": "change",
    "contraction": "change",
    # insurance
    "gwp": "gross written premium",
    "nwp": "net written premium",
    "nep": "net earned premium",
    "underwriting profit": "underwriting result",
    "underwriting income": "underwriting result",
    "pyd": "prior year reserve development",
    "prior-year development": "prior year reserve development",
    "prior year development": "prior year reserve development",
    "reserve releases": "prior year reserve development",
    # SaaS
    "nrr": "net revenue retention",
    "ndr": "net revenue retention",
    "net dollar retention": "net revenue retention",
    "grr": "gross revenue retention",
    "gross dollar retention": "gross revenue retention",
    # funds
    "capital called": "called capital",
    "drawn capital": "called capital",
    "contributed capital": "called capital",
    "paid-in capital": "called capital",
    "paid in capital": "called capital",
    "capital distributed": "distributions",
    "distributed capital": "distributions",
}

DEFAULT_KEY_METRICS: list[str] = [
    "revenue",
    "ebitda",
    "ebit",
    "eps",
    "net income",
    "enterprise value",
    "equity value",
    "wacc",
    "free cash flow",
    "offer price",
    "premium",
    "purchase price",
    "net debt",
]

# Qualifiers that change the meaning of a metric. Two labels that differ in one of
# these must never be silently treated as the same metric.
DEFAULT_QUALIFIERS: list[str] = [
    "adjusted", "reported", "pro forma", "standalone", "combined", "consolidated",
    "diluted", "basic", "organic", "constant currency", "like for like", "gaap", "ifrs",
    "non gaap", "underlying", "normalized", "recurring", "run rate", "same store", "same property",
    "same center", "comparable store",
    "levered", "unlevered", "pre tax", "post tax", "pre synergies", "post synergies",
    "low", "high", "median", "mean", "average", "min", "max", "implied", "illustrative",
    "target", "acquirer", "buyer", "seller",
]

DEFAULT_DEPRIORITIZED_SHEETS: list[str] = [
    "*old*", "*backup*", "*bak*", "*archive*", "*scratch*", "*temp*", "*tmp*", "*draft*",
    "*copy*", "*prior*", "*superseded*", "*do not use*", "*dnu*", "x_*", "z_*",
]


@dataclass
class Config:
    # --- tolerance ---
    # Comparisons use display precision ("75%" == 74.5..75.5). These add extra slack.
    relative_tolerance: float = 0.0
    absolute_tolerance: float = 0.0
    rounding_mode: str = "round"            # "round" | "round_or_truncate"
    approx_relative: float = 0.05           # slack for "c.", "~", "approximately"
    coarse_rounding_threshold: float = 0.05  # flag LOW when valid rounding hides >5% of the value
    # --- matching ---
    min_label_score: float = 0.35
    base_period: str = ""                   # e.g. "fy24": default period for numbers without one
    deprioritized_sheets: list[str] = field(default_factory=lambda: list(DEFAULT_DEPRIORITIZED_SHEETS))
    exclude_sheets: list[str] = field(default_factory=list)
    include_hidden: bool = True
    require_label_evidence: bool = False
    value_only_min_digits: int = 3          # min significant digits for value-only verification
    qualifiers: list[str] = field(default_factory=lambda: list(DEFAULT_QUALIFIERS))
    # --- LLM (label disambiguation only; never sees numbers) ---
    llm: str = "off"                        # off | openai | anthropic | openai-compatible | ollama | codex
    model_name: str = ""                    # empty -> provider default
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_timeout: float = 60.0
    llm_batch_size: int = 25
    llm_redact: list[str] = field(default_factory=list)  # substrings replaced with [REDACTED] before sending
    # --- extraction ---
    ocr: bool = False
    max_file_mb: float = 250.0
    large_workbook_cells: int = 2_000_000   # above this, stream read-only (merged cells unavailable)
    read_speaker_notes: bool = False
    # --- metrics ---
    key_metrics: list[str] = field(default_factory=lambda: list(DEFAULT_KEY_METRICS))
    abbreviations: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ABBREVIATIONS))
    unit_scales: dict[str, float] = field(default_factory=lambda: {
        "k": 1e3, "m": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9, "t": 1e12,
    })
    # --- output ---
    fail_on: str = ""                       # "" | "high" | "medium" | "low" (CLI exit code)
    password: str = ""                      # for encrypted workbooks/decks (needs msoffcrypto-tool)
    # legacy (accepted and ignored)
    value_weight: float = 0.6
    label_weight: float = 0.4
    ambiguity_band: float = 0.03
    source_path: str = ""                   # which config file was loaded


_KNOWN_SECTIONS = {"tolerance", "matching", "llm", "extraction", "output", "key_metrics", "unit_scales",
                   "abbreviations", "qualifiers"}


def find_config_path(path: str | Path | None = None) -> Path | None:
    if path:
        p = Path(path)
        return p if p.is_file() else None
    env = os.environ.get("NUMCHECK_CONFIG")
    if env and Path(env).is_file():
        return Path(env)
    local = Path.cwd() / "numcheck.toml"
    return local if local.is_file() else None


def load_config(path: str | Path | None = None, *, discover: bool = True) -> Config:
    """Load config. With ``discover`` (default) and no path, look for $NUMCHECK_CONFIG / ./numcheck.toml."""
    cfg = Config()
    data: dict = {}
    p = find_config_path(path) if (path or discover) else None
    if p is not None:
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
            cfg.source_path = str(p)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"could not read config {p}: {exc}") from exc

    for key in data:
        if key not in _KNOWN_SECTIONS:
            log.warning("numcheck config: unknown key %r ignored", key)

    tol = data.get("tolerance", {})
    if isinstance(tol, dict):
        cfg.relative_tolerance = float(tol.get("relative", cfg.relative_tolerance))
        cfg.absolute_tolerance = float(tol.get("absolute", cfg.absolute_tolerance))
        cfg.rounding_mode = str(tol.get("rounding_mode", cfg.rounding_mode))
        cfg.approx_relative = float(tol.get("approx_relative", cfg.approx_relative))
        cfg.coarse_rounding_threshold = float(tol.get("coarse_rounding_threshold", cfg.coarse_rounding_threshold))

    m = data.get("matching", {})
    if isinstance(m, dict):
        cfg.min_label_score = float(m.get("min_label_score", cfg.min_label_score))
        cfg.base_period = str(m.get("base_period", cfg.base_period)).lower()
        cfg.include_hidden = bool(m.get("include_hidden", cfg.include_hidden))
        cfg.require_label_evidence = bool(m.get("require_label_evidence", cfg.require_label_evidence))
        cfg.value_only_min_digits = int(m.get("value_only_min_digits", cfg.value_only_min_digits))
        if "deprioritized_sheets" in m:
            cfg.deprioritized_sheets = [str(x) for x in m["deprioritized_sheets"]]
        if "exclude_sheets" in m:
            cfg.exclude_sheets = [str(x) for x in m["exclude_sheets"]]
        # backward-compatible provider/model location
        if "llm" in m:
            cfg.llm = str(m["llm"])
        if "model_name" in m:
            cfg.model_name = str(m["model_name"])

    llm_cfg = data.get("llm", {})
    if isinstance(llm_cfg, dict):
        cfg.llm = str(llm_cfg.get("provider", cfg.llm))
        cfg.model_name = str(llm_cfg.get("model", cfg.model_name))
        cfg.llm_base_url = str(llm_cfg.get("base_url", cfg.llm_base_url))
        cfg.llm_api_key = str(llm_cfg.get("api_key", cfg.llm_api_key))
        cfg.llm_timeout = float(llm_cfg.get("timeout", cfg.llm_timeout))
        cfg.llm_batch_size = int(llm_cfg.get("batch_size", cfg.llm_batch_size))
        if "redact" in llm_cfg:
            cfg.llm_redact = [str(x) for x in llm_cfg["redact"]]

    ext = data.get("extraction", {})
    if isinstance(ext, dict):
        cfg.ocr = bool(ext.get("ocr", cfg.ocr))
        cfg.max_file_mb = float(ext.get("max_file_mb", cfg.max_file_mb))
        cfg.large_workbook_cells = int(ext.get("large_workbook_cells", cfg.large_workbook_cells))
        cfg.read_speaker_notes = bool(ext.get("read_speaker_notes", cfg.read_speaker_notes))

    out = data.get("output", {})
    if isinstance(out, dict):
        cfg.fail_on = str(out.get("fail_on", cfg.fail_on)).lower()

    # Environment variables override the file.
    env = os.environ
    cfg.llm = env.get("NUMCHECK_LLM_PROVIDER", cfg.llm)
    cfg.model_name = env.get("NUMCHECK_LLM_MODEL", cfg.model_name)
    cfg.llm_base_url = env.get("NUMCHECK_LLM_BASE_URL", cfg.llm_base_url)
    cfg.llm_api_key = env.get("NUMCHECK_LLM_API_KEY", cfg.llm_api_key)

    if "key_metrics" in data:
        cfg.key_metrics = [str(x).lower() for x in data["key_metrics"]]
    if "qualifiers" in data:
        cfg.qualifiers = [str(x).lower() for x in data["qualifiers"]]
    if "unit_scales" in data:
        cfg.unit_scales.update({str(k): float(v) for k, v in data["unit_scales"].items()})
    if "abbreviations" in data:
        cfg.abbreviations.update({str(k).lower(): str(v).lower() for k, v in data["abbreviations"].items()})

    validate_config(cfg)
    return cfg


def validate_config(cfg: Config) -> None:
    """Clamp obviously wrong values instead of producing nonsense results."""
    cfg.relative_tolerance = min(max(cfg.relative_tolerance, 0.0), 0.1)
    cfg.absolute_tolerance = max(cfg.absolute_tolerance, 0.0)
    cfg.approx_relative = min(max(cfg.approx_relative, 0.0), 0.5)
    if cfg.rounding_mode not in ("round", "round_or_truncate"):
        cfg.rounding_mode = "round"
    cfg.min_label_score = min(max(cfg.min_label_score, 0.05), 0.95)
    cfg.llm_batch_size = min(max(cfg.llm_batch_size, 1), 100)
