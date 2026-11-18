# NumCheck

Automated "number check" for investment banking pitchbooks. NumCheck reads a
PowerPoint deck and the Excel model(s) behind it, reconciles every number, and
produces a flagged report — the check an analyst does by hand at 2 a.m., done
deterministically in under a second.

It catches the errors that Excel-to-PowerPoint linkers (Macabacus, UpSlide) miss,
because those only work when everything was linked. Most decks contain pasted
values, typed numbers, and charts that have drifted from the source. NumCheck
does not require links — it re-derives the source of every number from labels
and values.

---

## The problem

Before a deck goes to a client, an analyst (and often an associate) prints it
and checks every number by hand:

- does it match the model,
- does the same figure agree across pages,
- are units and periods consistent,
- are footnotes and sources right,
- did anything go stale after the model was updated.

One wrong number in front of a client is a career-level mistake.

## What NumCheck does

Given `deck.pptx` and `model.xlsx`:

1. **Extracts every number from the deck** — text boxes, tables, chart data
   (from embedded chart XML), and footnotes — with slide, shape, label, unit
   (`$m`, `%`, `x`, `FY24`, `LTM`), and period.
2. **Extracts labelled values from the model** — cell values with row/column
   labels, sheet name, cell address, formula, and named ranges.
3. **Matches deck numbers to model cells** using deterministic label similarity
   (token-Jaccard with abbreviation expansion) plus value matching with
   rounding tolerance. `1,234.56` in Excel matches `$1.2bn` and `1,235` in the
   deck.
4. **Runs consistency checks** — cross-slide drift, totals that don't sum,
   percentages that don't reconcile, growth rates that don't match, and
   unit/period inconsistencies.
5. **Runs a staleness check** against a previous model version.
6. **Emits** an HTML report, an Excel report, a machine-readable JSON report,
   and an annotated copy of the deck with flagged numbers highlighted.

**An LLM is used only to resolve ambiguous label matches. Every numeric
comparison is deterministic code.**

---

## Installation

Requires Python 3.12+.

```bash
git clone <this-repo> && cd numcheck
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Optional OCR fallback (numbers baked into slide images) additionally needs
[LibreOffice](https://www.libreoffice.org/) and
[Tesseract](https://github.com/tesseract-ocr/tesseract), plus
`pip install pytesseract pillow`.

---

## Quick start

Generate a realistic demo (comps + DCF + operating model, a 12-slide deck with
10 planted errors) and review it:

```bash
python -m numcheck demo --out-dir samples

python -m numcheck review samples/deck.pptx \
  --model samples/model.xlsx \
  --model-old samples/model_old.xlsx \
  --out-dir samples/report \
  --annotate
```

Open `samples/report/report.html` (or `report.xlsx`) for the issue table and
`samples/report/deck_flagged.pptx` for the annotated deck.

## Web UI

For an interactive, no-CLI experience:

```bash
python -m numcheck web
# open http://127.0.0.1:8000
```

Upload a deck and one or more models (or hit **Load demo**), run the review in
the browser, filter the issue table, and download the reports plus the
annotated deck. The UI ships the same `codex`/`openai`/`anthropic`/
`openai-compatible`/`ollama` provider picker.

The demo deliberately plants 10 errors of different types and the tool catches
all of them with zero false positives:

| # | Planted error | Caught as |
|---|---------------|-----------|
| 1 | Exec-summary FY24 Revenue $2,450m vs model $2,415m | `mismatch` |
| 2 | Revenue build total $2,450m vs components $2,420m | `sum_error` |
| 3 | EBITDA $540m on comps slide vs $512m on DCF slide | `inconsistent_across_slides` + `mismatch` |
| 4 | EBITDA margin 22.4% vs 512/2415 = 21.2% | `pct_reconcile` |
| 5 | Revenue growth 8.5% vs (2415/2230 − 1) = 8.3% | `growth_reconcile` |
| 6 | EV "$5.4bn" on one slide, "$5,400m" on another | `unit_period_mismatch` |
| 7 | Same figure "FY24" vs "CY24" | `unit_period_mismatch` |
| 8 | DCF implied EV $5.2bn matches old model, not new $5.8bn | `stale` |
| 9 | "Total addressable market $48bn" with no source | `unmatched` |
| 10 | Footnote "Q3 2025" vs deck period FY24 | `unit_period_mismatch` |

A control number (`Market Cap $6.2bn` vs model `$6,234m`) is **not** flagged,
demonstrating the rounding tolerance.

### Before / after

**Before** — an analyst eyeballs 40 pages and hopes nothing drifted:

```
slide 2  FY24 Revenue .......... $2,450m   ← is that 2,415 or 2,450?
slide 5  Total Revenue ......... $2,450m   ← do the segments even sum?
slide 3  EBITDA ............... $540m      ← comps says 540, DCF says 512?
slide 4  Implied EV ........... $5.2bn     ← did the model get updated?
slide 9  TAM .................. $48bn      ← where did this even come from?
```

**After** — NumCheck points to the exact slide and cell:

```
mismatch   slide 2  $2,450m  expected $2,415m  source Operating!C5
sum_error  slide 5  $2,450m  expected $2,420m
mismatch   slide 3  $540m    expected $512m    source Operating!C6
stale      slide 4  $5.2bn   expected $5,800m  source DCF!B4
unmatched  slide 9  $48bn
```

---

## CLI reference

```text
python -m numcheck review DECK --model MODEL [--model MODEL ...] [options]
python -m numcheck demo --out-dir DIR
python -m numcheck init-config --out numcheck.toml
```

`review` options:

| Flag | Description |
|------|-------------|
| `--model` (repeatable) | Source `.xlsx` model(s) |
| `--model-old` | Previous model version for the staleness check |
| `--out-dir` | Output directory (default `report`) |
| `--annotate` / `--no-annotate` | Write `deck_flagged.pptx` (default on) |
| `--config` | Path to `numcheck.toml` |
| `--llm` | Provider: `off`, `openai`, `anthropic`, `openai-compatible`, `ollama` |
| `--model-name` | Model name for the LLM provider |
| `--llm-base-url` | Base URL for `openai-compatible` / `ollama` |
| `--llm-api-key` | API key override |
| `--tolerance` | Base relative tolerance (default `0.006`) |
| `--ocr` | Enable optional OCR fallback for image numbers |

---

## LLM providers (pluggable)

The LLM is an optional, swappable inference layer used **only** for fuzzy label
disambiguation. Numeric comparison never touches the LLM.

Provider configuration lives in `numcheck.toml` under `[llm]`, and every field
can be overridden with environment variables.

```toml
[llm]
provider = "openai"        # off | openai | anthropic | openai-compatible | ollama | codex
model = "gpt-4o-mini"
base_url = ""              # for openai-compatible endpoints / ollama
api_key = ""               # optional; otherwise read from the provider env var
```

| Provider | Env var | Notes |
|----------|---------|-------|
| `off` | — | Fully deterministic; ambiguous labels stay `ambiguous` |
| `openai` | `OPENAI_API_KEY` | OpenAI API |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude (requires `anthropic` in requirements) |
| `openai-compatible` | `OPENAI_API_KEY` / `NUMCHECK_LLM_API_KEY` | Any OpenAI-compatible endpoint (OpenRouter, Groq, LiteLLM, vLLM, …) |
| `ollama` | — | Local Ollama (`http://localhost:11434/v1`) |
| `codex` | — | Uses the **OpenAI Codex CLI** authenticated with your ChatGPT login (no API key) |

**Use your existing ChatGPT subscription (no API key).** ChatGPT Plus/Pro does
not expose a standard API key — that's a separate product. The supported way to
use your subscription programmatically is the Codex CLI. Install it, sign in
with your ChatGPT account, then set `provider = "codex"`:

```bash
npm install -g @openai/codex
codex login          # choose "Sign in with ChatGPT"
```

NumCheck then shells out to `codex exec` for the disambiguation step. Model
selection is controlled by the Codex CLI itself (`~/.codex/config.toml`). The
numeric checks all still run without the LLM.

Environment overrides:

```bash
export NUMCHECK_LLM_PROVIDER=openai-compatible
export NUMCHECK_LLM_BASE_URL=http://localhost:4000/v1
export NUMCHECK_LLM_MODEL=gpt-4o-mini
export NUMCHECK_LLM_API_KEY=sk-...
```

---

## Configuration

`python -m numcheck init-config` writes a starter `numcheck.toml`. The main
knobs:

- `tolerance.relative` / `tolerance.absolute` — value-matching tolerance.
  Rounding tolerance is computed per-number from its significant digits, so
  `$1.2bn` still matches `1,234,560,000`.
- `matching.value_weight` / `label_weight` / `ambiguity_band`.
- `key_metrics` — metrics treated as high severity (Revenue, EBITDA, EPS, …).
- `abbreviations` — label abbreviation expansion.
- `unit_scales` — `k`/`m`/`mm`/`bn`/`b`/`t` scale factors.

---

## Outputs

| File | Description |
|------|-------------|
| `report.html` | Self-contained, sortable/filterable issue table |
| `report.xlsx` | Styled workbook: issues + summary sheets |
| `report.json` | Machine-readable issues and summary |
| `deck_flagged.pptx` | Copy of the deck with flagged numbers highlighted |

Every issue has: slide number, deck value, expected value, source cell,
issue type, severity, and a human-readable message.

---

## Architecture

```
numcheck/
  extraction/   pptx (text/tables/charts/OCR) + xlsx (labelled cells + named ranges)
  matching/     normalizer, value matching, label matching, pipeline
  llm/          pluggable provider abstraction (off/openai/anthropic/openai-compatible/ollama)
  checks/       consistency, arithmetic, units/periods, staleness
  report/       html, excel, json, annotated deck
  demo/         realistic comps + DCF model and planted-error deck
```

Matching is **label-first**: the best label match identifies the intended source
cell, then the value is checked deterministically. Value proximity is used only
to break ties among cells that share the same label. This is what makes a wrong
number surface as a `mismatch` (with its source cell) rather than a silent
`unmatched`.

---

## Tests

```bash
python -m pytest -q
```

The integration test regenerates the demo and asserts all 10 planted errors are
caught and the rounding control is clean.

---

## Demo GIF

To record a short demo GIF, use your preferred terminal recorder, for example:

```bash
# asciinema (terminal cast -> gif)
asciinema rec demo.cast
# ... run the demo + review commands above ...
# then convert with agg:
agg demo.cast assets/demo.gif
```

Place the result at `assets/demo.gif`. A text transcript of the before/after is
included above so the demo remains useful without the GIF.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and pull requests welcome.

## License

MIT — see [LICENSE](LICENSE).
