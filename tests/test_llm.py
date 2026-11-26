"""Tests for the LLM disambiguation layer (everything mocked: no network, no subprocess)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from numcheck.config import Config
from numcheck.llm import PROVIDERS, get_provider, resolve_ambiguous
from numcheck.llm import providers as P
from numcheck.llm.factory import default_model
from numcheck.matching.llm_matcher import build_entry, mask_digits
from numcheck.models import DeckNumber, ModelValue, Source


def entry(i="0", n=2, label="Revenue"):
    return {
        "id": i, "deck_label": label, "deck_context": "Revenue reached $#,#m", "slide_title": "Summary",
        "deck_period": "fy24",
        "candidates": [{"ref": f"Sheet{k}!B2", "sheet": f"Sheet{k}", "row_label": "Revenue", "col_label": "FY24",
                        "section_label": "", "named_range": "", "period": "fy24"} for k in range(n)],
    }


class FakeAnthropic:
    def __init__(self, text='{"resolutions": {"0": "Sheet1!B2"}}', stop_reason="end_turn", reject_output_config=None):
        self.calls: list[dict] = []
        self.text, self.stop_reason, self.reject = text, stop_reason, reject_output_config
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if "output_config" in kw and self.reject is not None:
            raise self.reject
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


# --------------------------------------------------------------------------- provider selection


def test_providers_constant_and_selection():
    assert PROVIDERS == ("off", "openai", "anthropic", "openai-compatible", "ollama", "codex", "openrouter", "jev", "chatgpt")
    assert get_provider(Config(llm="off")).name == "off"
    assert get_provider(Config(llm="anthropic", llm_api_key="k")).name == "anthropic"
    assert get_provider(Config(llm="ollama")).name == "ollama"
    with pytest.raises(ValueError):
        get_provider(Config(llm="nope"))


def test_default_models():
    assert default_model("openai", Config()) == "gpt-4o-mini"
    assert default_model("anthropic", Config()) == "claude-opus-5-5"
    assert default_model("ollama", Config()) == "llama3.1"
    assert default_model("openai-compatible", Config()) == ""
    assert default_model("openai", Config(model_name="x")) == "x"


def test_off_returns_nothing_without_warnings():
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="off"), warns) == {} and warns == []


def test_unknown_provider_warns():
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="nope"), warns) == {}
    assert warns and warns[0].code == "llm_failed"


# --------------------------------------------------------------------------- payload privacy


def test_build_entry_contains_no_numeric_values():
    d = DeckNumber(3, "s1", Source.TEXT_BOX, "$1,234.5m", 1234.5, label="Revenue FY24 up 12.5%",
                   context="Revenue grew 9% to $1,234.5m in FY24 versus 1,100 last year", slide_title="FY24 results 2024")
    mv = ModelValue("Model", "B2", 987654.0, row_label="Revenue", col_label="FY24", workbook="m.xlsx")
    e = build_entry("0", d, d.label, "fy24", [(mv, "fy24")])
    blob = json.dumps(e)
    for forbidden in ("1,234", "1234", "12.5", "9%", "1,100", "987654"):
        assert forbidden not in blob
    assert "FY24" in e["deck_label"]              # periods are labels, not values
    assert e["candidates"][0]["ref"] == "[m.xlsx] Model!B2"
    assert set(e["candidates"][0]) == {"ref", "sheet", "row_label", "col_label", "section_label", "named_range",
                                       "period"}
    assert mask_digits("a 12,3.4b") == "a #,#.#b"


def test_payload_sent_to_provider_has_no_numbers(monkeypatch):
    fake = FakeAnthropic()
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    d = DeckNumber(1, "s", Source.TEXT_BOX, "$999m", 999.0, label="Revenue", context="Revenue was $999m")
    mv = ModelValue("A", "B2", 4242.0, row_label="Revenue")
    e = build_entry("0", d, d.label, "", [(mv, ""), (ModelValue("B", "B2", 31337.0, row_label="Revenue"), "")])
    resolve_ambiguous([e], Config(llm="anthropic", llm_api_key="k"))
    sent = json.dumps(fake.calls[0]["messages"])
    assert "999" not in sent and "4242" not in sent and "31337" not in sent


# --------------------------------------------------------------------------- anthropic


def test_anthropic_structured_output_call_shape(monkeypatch):
    fake = FakeAnthropic()
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    out = resolve_ambiguous([entry()], Config(llm="anthropic", llm_api_key="k"))
    assert out == {"0": "Sheet1!B2"}
    kw = fake.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["system"] == P.SYSTEM_PROMPT
    assert "temperature" not in kw and "top_p" not in kw
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    enum = fmt["schema"]["properties"]["resolutions"]["properties"]["0"]["enum"]
    assert enum == ["Sheet0!B2", "Sheet1!B2", ""]


def test_anthropic_refusal_warns(monkeypatch):
    fake = FakeAnthropic(stop_reason="refusal", text="")
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    warns = []
    out = resolve_ambiguous([entry()], Config(llm="anthropic", llm_api_key="k"), warns)
    assert out == {} and any(w.code == "llm_failed" and "refus" in w.message for w in warns)
    assert len(fake.calls) == 1   # a refusal is not retried


def test_anthropic_missing_key_warns_without_calling(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("client must not be created without a key")

    monkeypatch.setattr(P, "_anthropic_client", boom)
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="anthropic"), warns) == {}
    assert len(warns) == 1 and warns[0].code == "llm_failed" and "key" in warns[0].message.lower()


def test_anthropic_retries_without_output_config_on_type_error(monkeypatch):
    fake = FakeAnthropic(text='Here you go: ```json\n{"resolutions": {"0": "Sheet0!B2"}}\n```',
                         reject_output_config=TypeError("unexpected keyword argument 'output_config'"))
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    out = resolve_ambiguous([entry()], Config(llm="anthropic", llm_api_key="k"))
    assert out == {"0": "Sheet0!B2"}
    assert len(fake.calls) == 2 and "output_config" not in fake.calls[1]


def test_anthropic_retries_without_output_config_on_bad_request(monkeypatch):
    class BadRequest(Exception):
        status_code = 400

    fake = FakeAnthropic(reject_output_config=BadRequest("output_config.format not supported"))
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    assert resolve_ambiguous([entry()], Config(llm="anthropic", llm_api_key="k")) == {"0": "Sheet1!B2"}
    assert "output_config" not in fake.calls[-1]


def test_invalid_choice_ignored_with_warning(monkeypatch):
    fake = FakeAnthropic(text='{"resolutions": {"0": "Nowhere!Z9"}}')
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="anthropic", llm_api_key="k"), warns) == {}
    assert any(w.code == "llm_failed" for w in warns)


def test_empty_choice_is_allowed(monkeypatch):
    fake = FakeAnthropic(text='{"resolutions": {"0": ""}}')
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="anthropic", llm_api_key="k"), warns) == {}
    assert warns == []


# --------------------------------------------------------------------------- openai-compatible


class FakeOpenAI:
    def __init__(self, content='{"resolutions": {"0": "Sheet0!B2"}}'):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.content = content

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def test_openai_call_shape(monkeypatch):
    fake = FakeOpenAI()
    seen = {}

    def factory(key, base_url, timeout):
        seen.update(key=key, base_url=base_url, timeout=timeout)
        return fake

    monkeypatch.setattr(P, "_openai_client", factory)
    out = resolve_ambiguous([entry()], Config(llm="openai", llm_api_key="sk", llm_timeout=12))
    assert out == {"0": "Sheet0!B2"}
    kw = fake.calls[0]
    assert kw["model"] == "gpt-4o-mini"
    assert kw["response_format"] == {"type": "json_object"}
    assert seen["timeout"] == 12


def test_openai_missing_key_warns(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(P, "_openai_client", lambda *a: pytest.fail("no client without key"))
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="openai"), warns) == {}
    assert warns and warns[0].code == "llm_failed"


def test_openai_compatible_requires_model(monkeypatch):
    monkeypatch.setattr(P, "_openai_client", lambda *a: pytest.fail("must skip"))
    warns = []
    cfg = Config(llm="openai-compatible", llm_base_url="http://localhost:1234/v1")
    assert resolve_ambiguous([entry()], cfg, warns) == {}
    assert warns and "model" in warns[0].message


def test_ollama_defaults(monkeypatch):
    fake = FakeOpenAI()
    seen = {}
    monkeypatch.setattr(P, "_openai_client", lambda key, base_url, timeout: seen.update(base=base_url) or fake)
    resolve_ambiguous([entry()], Config(llm="ollama"))
    assert seen["base"] == "http://localhost:11434/v1" and fake.calls[0]["model"] == "llama3.1"


# --------------------------------------------------------------------------- codex


def test_codex_prompt_goes_through_stdin(monkeypatch):
    monkeypatch.setattr(P.shutil, "which", lambda name: "/usr/bin/codex")
    captured = {}

    def fake_run(args, **kw):
        captured["args"], captured["kw"] = args, kw
        out_path = args[args.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write('{"resolutions": {"0": "Sheet1!B2"}}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    out = resolve_ambiguous([entry(label="Distinctive label xyz")], Config(llm="codex"))
    assert out == {"0": "Sheet1!B2"}
    args, kw = captured["args"], captured["kw"]
    assert args[:2] == ["/usr/bin/codex", "exec"] and args[-1] == "-"
    assert "--skip-git-repo-check" in args and "--sandbox" in args and "read-only" in args
    assert "Distinctive label xyz" in kw["input"] and "Distinctive label xyz" not in " ".join(args)
    assert kw["capture_output"] is True and kw["text"] is True and kw["timeout"] > 0


def test_codex_missing_binary_warns(monkeypatch):
    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="codex"), warns) == {}
    assert warns and warns[0].code == "llm_failed" and "codex" in warns[0].message


def test_codex_can_abstain_without_a_warning(monkeypatch):
    monkeypatch.setattr(P.shutil, "which", lambda name: "codex")

    def fake_run(args, **kw):
        open(args[args.index("--output-last-message") + 1], "w").write('{"resolutions": {"0": ""}}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="codex"), warns) == {}
    assert warns == []


def test_codex_timeout_is_a_warning(monkeypatch):
    monkeypatch.setattr(P.shutil, "which", lambda name: "codex")

    def fake_run(args, **kw):
        raise subprocess.TimeoutExpired(args, kw["timeout"])

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="codex", llm_timeout=5), warns) == {}
    assert any("timed out" in w.message for w in warns)


# --------------------------------------------------------------------------- batching / redaction / failure


def test_batching_and_continue_after_failed_batch(monkeypatch):
    calls = []

    class Flaky:
        name = "openai"

        def resolve(self, entries, model, timeout=60.0):
            calls.append([e["id"] for e in entries])
            if "0" in [e["id"] for e in entries]:
                raise RuntimeError("boom")
            return {e["id"]: e["candidates"][0]["ref"] for e in entries}

    monkeypatch.setattr("numcheck.llm.factory.get_provider", lambda cfg: Flaky())
    entries = [entry(str(i)) for i in range(5)]
    warns = []
    out = resolve_ambiguous(entries, Config(llm="openai", llm_batch_size=2), warns)
    assert set(out) == {"2", "3", "4"}
    assert [c for c in calls if c != ["0", "1"]] == [["2", "3"], ["4"]]
    assert any(w.code == "llm_failed" and w.count == 2 for w in warns)


def test_failing_provider_adds_llm_failed(monkeypatch):
    class Boom:
        name = "openai"

        def resolve(self, entries, model, timeout=60.0):
            raise TimeoutError("slow")

    monkeypatch.setattr("numcheck.llm.factory.get_provider", lambda cfg: Boom())
    warns = []
    assert resolve_ambiguous([entry()], Config(llm="openai"), warns) == {}
    assert len(warns) == 1 and warns[0].code == "llm_failed" and "TimeoutError" in warns[0].message


def test_redaction_applied_and_refs_mapped_back(monkeypatch):
    fake = FakeAnthropic(text='{"resolutions": {"0": "Sheet1!B2"}}')
    monkeypatch.setattr(P, "_anthropic_client", lambda key, timeout: fake)
    e = entry(label="Project Falcon revenue")
    for c in e["candidates"]:
        c["sheet"] = "Falcon model"
    cfg = Config(llm="anthropic", llm_api_key="k", llm_redact=["falcon"])
    out = resolve_ambiguous([e], cfg)
    sent = json.dumps(fake.calls[0]["messages"]).lower()
    assert "falcon" not in sent and "redacted" in sent
    assert out == {"0": "Sheet1!B2"}
    assert "Falcon" in e["deck_label"]       # the caller's entries are not mutated


def test_redaction_of_confidential_refs_is_reversible(monkeypatch):
    sent_refs = []

    class Echo:
        name = "openai"

        def resolve(self, entries, model, timeout=60.0):
            sent_refs.extend(c["ref"] for c in entries[0]["candidates"])
            return {"0": entries[0]["candidates"][1]["ref"]}

    monkeypatch.setattr("numcheck.llm.factory.get_provider", lambda cfg: Echo())
    e = entry()
    e["candidates"][0]["ref"] = "[Falcon.xlsx] A!B2"
    e["candidates"][1]["ref"] = "[Falcon.xlsx] A!B2 "
    out = resolve_ambiguous([e], Config(llm="openai", llm_redact=["Falcon"]))
    assert all("Falcon" not in r for r in sent_refs) and len(set(sent_refs)) == 2
    assert out == {"0": "[Falcon.xlsx] A!B2 "}
