"""LLM provider factory and public entrypoint."""

from __future__ import annotations

import os

from numcheck.config import Config
from numcheck.llm.providers import AnthropicProvider, CodexProvider, NoopProvider, OpenAICompatProvider

PROVIDERS = ("off", "openai", "anthropic", "openai-compatible", "ollama", "codex")


def get_provider(config: Config):
    p = (config.llm or "off").strip().lower()
    if p in ("off", "none", ""):
        return NoopProvider()

    if p == "openai":
        key = config.llm_api_key or os.environ.get("OPENAI_API_KEY", "")
        return OpenAICompatProvider(api_key=key)

    if p in ("openai-compatible", "custom", "openrouter"):
        key = (
            config.llm_api_key
            or os.environ.get("OPENAI_API_KEY", "")
            or os.environ.get("OPENROUTER_API_KEY", "")
        )
        return OpenAICompatProvider(api_key=key, base_url=config.llm_base_url or None)

    if p == "ollama":
        return OpenAICompatProvider(
            api_key="ollama",
            base_url=config.llm_base_url or "http://localhost:11434/v1",
        )

    if p == "codex":
        return CodexProvider()

    if p in ("anthropic", "claude"):
        key = config.llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        return AnthropicProvider(api_key=key)

    raise ValueError(f"Unknown LLM provider '{p}'. Choose from: {', '.join(PROVIDERS)}")


def resolve_ambiguous(entries: list[dict], config: Config) -> dict[str, str]:
    """Resolve ambiguous deck->cell mappings with the configured provider.

    Returns {entry_id: cell_address}; empty dict when no provider/key is available.
    """
    provider = get_provider(config)
    if isinstance(provider, NoopProvider):
        return {}
    return provider.resolve(entries, config.model_name)
