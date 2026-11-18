"""Configuration loading (defaults + optional numcheck.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_ABBREVIATIONS: dict[str, str] = {
    "rev": "revenue",
    "ebitda": "ebitda",
    "ebit": "ebit",
    "ni": "net income",
    "eps": "eps",
    "ev": "enterprise value",
    "eqv": "equity value",
    "eq val": "equity value",
    "fcf": "free cash flow",
    "gm": "gross margin",
    "cogs": "cogs",
    "opex": "opex",
    "capex": "capex",
    "nwc": "net working capital",
    "d&a": "d&a",
    "share count": "shares outstanding",
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
    "fcf",
]


@dataclass
class Config:
    relative_tolerance: float = 0.006
    absolute_tolerance: float = 0.5
    value_weight: float = 0.6
    label_weight: float = 0.4
    ambiguity_band: float = 0.03
    llm: str = "off"           # provider: off | openai | anthropic | openai-compatible | ollama
    model_name: str = "gpt-4o-mini"
    llm_base_url: str = ""
    llm_api_key: str = ""
    key_metrics: list[str] = field(default_factory=lambda: list(DEFAULT_KEY_METRICS))
    abbreviations: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ABBREVIATIONS))
    unit_scales: dict[str, float] = field(default_factory=lambda: {
        "k": 1e3,
        "m": 1e6,
        "mm": 1e6,
        "bn": 1e9,
        "b": 1e9,
        "t": 1e12,
    })


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    data: dict = {}

    if path is not None:
        p = Path(path)
        if p.exists():
            data = tomllib.loads(p.read_text(encoding="utf-8"))

    tol = data.get("tolerance", {})
    if isinstance(tol, dict):
        cfg.relative_tolerance = float(tol.get("relative", cfg.relative_tolerance))
        cfg.absolute_tolerance = float(tol.get("absolute", cfg.absolute_tolerance))

    m = data.get("matching", {})
    if isinstance(m, dict):
        cfg.value_weight = float(m.get("value_weight", cfg.value_weight))
        cfg.label_weight = float(m.get("label_weight", cfg.label_weight))
        cfg.ambiguity_band = float(m.get("ambiguity_band", cfg.ambiguity_band))
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

    # Environment variables override the file.
    env = __import__("os").environ
    cfg.llm = env.get("NUMCHECK_LLM_PROVIDER", cfg.llm)
    cfg.model_name = env.get("NUMCHECK_LLM_MODEL", cfg.model_name)
    cfg.llm_base_url = env.get("NUMCHECK_LLM_BASE_URL", cfg.llm_base_url)
    cfg.llm_api_key = env.get("NUMCHECK_LLM_API_KEY", cfg.llm_api_key)

    if "key_metrics" in data:
        cfg.key_metrics = [str(x).lower() for x in data["key_metrics"]]

    if "unit_scales" in data:
        cfg.unit_scales.update({str(k): float(v) for k, v in data["unit_scales"].items()})

    if "abbreviations" in data:
        cfg.abbreviations.update({str(k): str(v) for k, v in data["abbreviations"].items()})

    return cfg
