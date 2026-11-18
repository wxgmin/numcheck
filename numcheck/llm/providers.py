"""Pluggable LLM providers for fuzzy label disambiguation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

SYSTEM_PROMPT = (
    "You are a financial data disambiguation engine. For each item, choose the single "
    "Excel cell that the deck number most likely corresponds to, based on label semantics. "
    "Return JSON only, as an object with a 'resolutions' key mapping each id to a "
    "cell_address string (use the exact cell_address provided, or an empty string if none fits)."
)


def build_payload(entries: list[dict]) -> str:
    return json.dumps({"items": entries})


def parse_resolutions(content: str, entries: list[dict]) -> dict[str, str]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {}
    resolutions = data.get("resolutions", data) if isinstance(data, dict) else {}
    if not isinstance(resolutions, dict):
        return {}
    out: dict[str, str] = {}
    for e in entries:
        key = str(e["id"])
        choice = resolutions.get(key, resolutions.get(e["id"], ""))
        out[key] = str(choice) if choice else ""
    return out


class NoopProvider:
    name = "off"

    def resolve(self, entries: list[dict], model: str) -> dict[str, str]:
        return {}


class OpenAICompatProvider:
    """Works for OpenAI, OpenRouter, Groq, LiteLLM proxies, and any OpenAI-compatible endpoint."""

    name = "openai-compatible"

    def __init__(self, api_key: str = "", base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    def resolve(self, entries: list[dict], model: str) -> dict[str, str]:
        if not self.api_key and not self.base_url:
            return {}
        try:
            from openai import OpenAI
        except ImportError:
            return {}
        client = OpenAI(api_key=self.api_key or "not-needed", base_url=self.base_url or None)
        try:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_payload(entries)},
                ],
            )
            return parse_resolutions(resp.choices[0].message.content or "", entries)
        except Exception:
            return {}


class CodexProvider:
    """Uses the OpenAI Codex CLI, authenticated with the user's ChatGPT login.

    This is the supported way to use a ChatGPT Plus/Pro subscription without an
    API key: `codex exec` runs non-interactively and returns the model response.
    Model selection is handled by the Codex CLI's own config (~/.codex/config.toml).
    """

    name = "codex"

    def __init__(self):
        self.binary = shutil.which("codex")

    def resolve(self, entries: list[dict], model: str) -> dict[str, str]:
        if not self.binary:
            return {}
        prompt = (
            SYSTEM_PROMPT
            + "\n\nItems:\n"
            + build_payload(entries)
            + "\n\nRespond with ONLY the JSON object and nothing else."
        )

        # Prefer --output-last-message; fall back to a plain run if the flag
        # is not supported by the installed Codex version.
        for args in (
            [self.binary, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--output-last-message", prompt],
            [self.binary, "exec", "--skip-git-repo-check", "--sandbox", "read-only", prompt],
        ):
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
            except Exception:
                continue
            text = (proc.stdout or "") + "\n" + (proc.stderr or "")
            parsed = parse_resolutions(_extract_json(text), entries)
            if parsed:
                return parsed
        return {}


def _extract_json(text: str) -> str:
    """Pull the first balanced {...} JSON object out of free-form output."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key

    def resolve(self, entries: list[dict], model: str) -> dict[str, str]:
        if not self.api_key:
            return {}
        try:
            from anthropic import Anthropic
        except ImportError:
            return {}
        client = Anthropic(api_key=self.api_key)
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_payload(entries)}],
            )
            text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
            return parse_resolutions(text, entries)
        except Exception:
            return {}
