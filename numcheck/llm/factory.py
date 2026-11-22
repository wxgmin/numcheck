"""LLM provider factory and the public ``resolve_ambiguous`` entrypoint."""

from __future__ import annotations

import copy
import os
import re

from numcheck.config import Config
from numcheck.llm.providers import (
    DEFAULT_MODELS,
    AnthropicProvider,
    CodexProvider,
    JevProvider,
    LLMError,
    NoopProvider,
    OpenAICompatProvider,
)
from numcheck.models import ReviewWarning

PROVIDERS = ("off", "openai", "anthropic", "openai-compatible", "ollama", "codex", "openrouter", "jev")


def get_provider(config: Config):
    p = (config.llm or "off").strip().lower()
    if p in ("off", "none", ""):
        return NoopProvider()

    if p == "openai":
        key = config.llm_api_key or os.environ.get("OPENAI_API_KEY", "")
        return OpenAICompatProvider(api_key=key, name="openai")

    if p == "jev":
        return JevProvider(config.llm_api_key or os.environ.get("AI_GATEWAY_API_KEY", ""))

    if p == "openrouter":
        key = config.llm_api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise LLMError("Enter an OpenRouter API key.", fatal=True)
        return OpenAICompatProvider(api_key=key, base_url="https://openrouter.ai/api/v1", name=p)

    if p in ("openai-compatible", "custom"):
        if not config.llm_base_url:
            raise LLMError("Configure a server-side base URL for the compatible endpoint.", fatal=True)
        return OpenAICompatProvider(api_key=config.llm_api_key, base_url=config.llm_base_url, name="openai-compatible")

    if p == "ollama":
        return OpenAICompatProvider(
            api_key="ollama",
            base_url=config.llm_base_url or "http://localhost:11434/v1",
            name="ollama",
        )

    if p == "codex":
        return CodexProvider()

    if p in ("anthropic", "claude"):
        key = config.llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        return AnthropicProvider(api_key=key)

    raise ValueError(f"Unknown LLM provider '{p}'. Choose from: {', '.join(PROVIDERS)}")


def default_model(provider_name: str, config: Config) -> str:
    """Configured model, else the provider default ('' for openai-compatible: it must be named)."""
    if config.model_name:
        return config.model_name
    p = (provider_name or "").lower()
    p = "anthropic" if p == "claude" else p
    return DEFAULT_MODELS.get(p, "")


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #

def _redactor(terms: list[str]):
    terms = [t for t in terms if t]
    if not terms:
        return lambda s: s
    rx = re.compile("|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True)), re.I)
    return lambda s: rx.sub("[REDACTED]", s) if isinstance(s, str) else s


def _redact_entries(entries: list[dict], terms: list[str]) -> tuple[list[dict], list[dict[str, str]]]:
    """Redacted deep copy of *entries* plus, per entry, ``{sent ref: original ref}``.

    Candidate refs can themselves contain confidential names (workbook/sheet), so they are
    redacted too and mapped back after the call; refs that collide after redaction get a
    suffix so the model can still tell them apart.
    """
    red = _redactor(terms)
    out: list[dict] = []
    maps: list[dict[str, str]] = []
    for e in entries:
        e2 = copy.deepcopy(e)
        for k, v in list(e2.items()):
            if isinstance(v, str):
                e2[k] = red(v)
        back: dict[str, str] = {}
        for i, c in enumerate(e2.get("candidates", [])):
            orig = str(e["candidates"][i].get("ref", ""))
            for k, v in list(c.items()):
                if isinstance(v, str):
                    c[k] = red(v)
            sent = str(c.get("ref", ""))
            if sent in back and back[sent] != orig:
                sent = f"{sent} [{i}]"
                c["ref"] = sent
            back[sent] = orig
        out.append(e2)
        maps.append(back)
    return out, maps


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #

def resolve_ambiguous(entries: list[dict], config: Config, warnings: list[ReviewWarning] | None = None
                      ) -> dict[str, str]:
    """Resolve ambiguous deck->cell mappings by label meaning with the configured provider.

    ``entries`` hold labels only (no numbers; see ``numcheck.matching.llm_matcher``).
    Returns ``{entry id: chosen candidate ref}`` (only non-empty, validated choices).  Never
    raises: any failure appends ``ReviewWarning(code="llm_failed")`` and the remaining
    batches still run (a fatal configuration problem such as a missing key stops early).
    """
    def warn(msg: str, count: int = 0) -> None:
        if warnings is not None:
            warnings.append(ReviewWarning(code="llm_failed", message=msg, count=count))

    if not entries:
        return {}
    try:
        provider = get_provider(config)
    except (ValueError, LLMError) as exc:
        warn(f"LLM disambiguation skipped: {exc}", len(entries))
        return {}
    if isinstance(provider, NoopProvider):
        return {}

    model = default_model(provider.name, config)
    if provider.name in ("openai-compatible", "openrouter") and not model:
        warn(f"LLM disambiguation skipped: provider '{provider.name}' requires llm.model to be set",
             len(entries))
        return {}

    sent, maps = _redact_entries(entries, config.llm_redact)
    resolved: dict[str, str] = {}
    size = max(int(config.llm_batch_size or 25), 1)
    timeout = float(config.llm_timeout or 60.0)
    for start in range(0, len(sent), size):
        batch = sent[start:start + size]
        batch_maps = maps[start:start + size]
        result: dict[str, str] | None = None
        last_err = ""
        for attempt in (1, 2):
            try:
                result = provider.resolve(batch, model, timeout)
                break
            except LLMError as exc:
                last_err = str(exc)
                if exc.fatal:
                    warn(f"LLM disambiguation ({provider.name}) unavailable: {exc}", len(sent) - start)
                    return resolved
                if "refus" in last_err or "JSON" in last_err or "resolution" in last_err:
                    break  # deterministic failures are not worth a second identical request
            except Exception as exc:  # noqa: BLE001 - network/SDK errors of any kind
                status = getattr(exc, "status_code", None)
                last_err = f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")
                if status in (400, 401, 403, 404, 429):
                    warn(f"Label assistance ({provider.name}) stopped: {last_err}. Check model, key, and quota.",
                         len(sent) - start)
                    return resolved
        if result is None:
            warn(f"LLM disambiguation ({provider.name}) failed for {len(batch)} item(s): {last_err}", len(batch))
            continue
        dropped = 0
        for e, back in zip(batch, batch_maps):
            chosen = str(result.get(str(e["id"]), "") or "")
            if not chosen:
                continue
            if chosen not in back:
                dropped += 1
                continue
            resolved[str(e["id"])] = back[chosen]
        if dropped:
            warn(f"LLM ({provider.name}) returned {dropped} choice(s) that were not candidates; ignored", dropped)
    return resolved
