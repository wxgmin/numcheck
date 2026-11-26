"""Pluggable LLM inference layer (fuzzy label disambiguation only; never sees numbers)."""

from numcheck.llm.factory import PROVIDERS, get_provider, resolve_ambiguous

__all__ = ["PROVIDERS", "get_provider", "resolve_ambiguous"]
