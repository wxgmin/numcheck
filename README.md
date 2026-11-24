# NumCheck

Automated "number check" for investment banking pitchbooks. NumCheck reads a
PowerPoint deck and the Excel model(s) behind it, ties every number on every
slide back to a source cell, and flags what is wrong, inconsistent or stale,
with the slide, the source cell, the expected value and a suggested fix in the
deck's own format.

It works on pasted values, typed numbers and drifted charts. It does not need
Macabacus/UpSlide links: it re-derives each number's source from labels,
periods, units and table structure.

**Every numeric comparison is deterministic code.** An optional LLM can break
ties between candidate cells by label meaning only; it never receives a number.

---

## What it checks

| Check | Example |
|---|---|
| Model tie-out | `$2,450m` on slide 2, model has `$2,415m` at `Operating!C5` |
| Sign and scale slips | `($45m)` vs `45`; `$45bn` vs `$45m` |
| Stale numbers | Deck matches the previous model version, not the current one |
| Wrong period | FY23 value shown under an FY24 header |
| Cross-slide consistency | EBITDA is `$540m` on slide 3, `$512m` everywhere else |
| Totals | Components don't sum to the total (rounding-aware); pies not summing to 100% |
| Margins, multiples, growth, CAGR | `22.4%` margin but EBITDA / revenue on the slide gives `21.2%` |
| Units and periods | FY24 vs CY24 labels for the same metric; `$bn` vs `$m` styles |
| Footnotes | Data-period footnotes that contradict the slide; differing "as of" dates |
| Source errors | Deck number mapped to a `#REF!` / `#DIV/0!` cell |

Comparisons use **display precision**: `75%` covers 74.5%-75.5%, `$1.2bn`
covers $1.15bn-$1.25bn. So `$6.2bn` against `$6,234m` passes, while `7%`
against `10%` and `6x` against `8.9x` fail. `c.`, `~`, `>` and `up to` are
honoured, and truncation can be allowed.

Every run reports **coverage**: how many deck numbers were verified, by label
or by value, and why the rest could not be. Anything that limits coverage is
shown as a warning, never silently skipped: uncalculated formulas, numbers in
pictures, charts without cached data, LLM failures, and encrypted or legacy
files.

## What it reads

- **Deck:** text boxes (including prose like "revenue grew 9% to $1.2bn in
  FY24"), tables (multi-row and merged headers, transposed layouts, units
  columns), charts (bar, line, pie, scatter, bubble and combo, from the chart
  cache with displayed precision), groups, placeholders and SmartArt. Footnotes,
  sources and "as of" dates are read separately. Slide-level unit banners such
  as "($ in millions, except per share data)" apply to unit-less numbers. OCR
  of pictures is optional.
- **Models:** banker layouts with title and units rows, blank spacer rows and
  columns, numeric year headers (`2024`, `2025E`), multi-company column
  groups, unit columns, number formats (`0.0%`, `0.0x`, `#,##0,,`, currency
  codes), named ranges, hidden and backup sheets, error cells, and multiple
  workbooks. Large workbooks are streamed.

## Install

For the portable Windows x64 build, extract the entire distribution ZIP and
launch `NumCheck.exe`. Keep the console open while you work in the browser.
The package includes the slide renderer, workbook review, OCR and an offline
sample review. The application binds only to this computer.

Source installs require Python 3.12+.

```bash
git clone https://github.com/wxgmin/numcheck && cd numcheck
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -e ".[llm]"         # or just: pip install -e .
```

Optional extras: `.[ocr]` (local OCR for embedded pictures),
`.[crypto]` (password-protected files), `.[dev]` (tests and lint). Run
`numcheck doctor` to see what is available.

## Use

### Web UI

```bash
numcheck web --open            # http://127.0.0.1:8000
```

Drop in the deck, the model(s) and optionally the previous model, then run.
The workspace has:

- **Issues:** sortable and filterable, with a detail drawer showing expected
  vs deck value, the tolerance in words, a suggested fix, provenance, and
  review actions (resolve, false positive, notes) exportable as CSV.
- **By slide:** issues grouped per slide.
- **Coverage:** the full tick-and-tie log of every deck number.
- **Source preview:** click a number in Issues or Coverage to see the original
  PowerPoint slide next to a sheet extract around the cited cell, with its saved
  value and formula. Inspect other candidate cells and the prior model when
  available. Download either original from the viewer.
- **Review handoff:** confirm a source in the viewer and export the findings,
  notes, reviewed sources, effective settings, and SHA-256 fingerprints as JSON.
  Confirmations record analyst judgment for that run alongside the calculated
  findings.
- **Warnings:** everything that limited coverage.
- **Downloads:** HTML report, Excel report, JSON, and the annotated deck.

**Review settings** contains rounding, base period, and tolerance help. For a
source-driven review, enable **Require label evidence** to leave numeric
coincidences unresolved. Review the Coverage and Warnings tabs before using
the exported reports.

NumCheck searches the current workbooks locally for all unresolved figures by
default. Change **Search for possible sources automatically** in Review settings
or run **Find possible sources for all** from the results. **Ask AI for sources**
compares candidate labels across the review. Enabling **Use AI during reviews**
always runs both steps, starting with local source search.

A figure moves to Verified only when its source is distinct, its workbook label
and period support the match, and the value reconciles at the slide's displayed
precision. Inspect also lists related workbook cells and shows how their values
compare with the slide. Results show the best available cell for each figure with
candidates and Inspect opens it by default. **Identify and record source with AI** selects and
opens the best-supported cell and records its workbook, sheet and address in
the finding and review handoff. Other possible cells remain available in
Inspect. This action can identify a source whose value differs when the label,
period and scope support it. The provider receives masked labels and a local
comparison category. Coverage and report downloads update when a source is verified.

### Calculated figures

When a rate or multiple has no dedicated model cell, NumCheck can calculate it
from labelled Excel inputs. **Inspect** shows the calculation and lets you open
each input cell.

| Metric | Workbook inputs and calculation |
|---|---|
| Year-over-year and cumulative growth | Ending value ÷ starting value − 1 |
| CAGR | (Ending value ÷ starting value) raised to 1 ÷ elapsed years, minus 1 |
| Percentage-point or basis-point change | Current rate minus prior rate |
| Gross, operating, EBITDA, net and free cash flow margins | Respective profit or cash flow ÷ revenue |
| FCF conversion and effective tax rate | FCF ÷ EBITDA or tax expense ÷ pre-tax income |
| ROE, ROA and ROIC | Net income ÷ average equity or average assets, or NOPAT ÷ average invested capital |
| Dividend payout and yield | Dividends ÷ net income or dividends per share ÷ share price |
| Valuation, leverage and interest coverage multiples | Relevant value, debt, price or EBITDA divided by its labelled base |
| Offer premium | Offer price ÷ unaffected price − 1 |
| Current and quick ratios | Current assets or quick assets ÷ current liabilities |
| MOIC | Total investment value ÷ invested capital |

Explicit annual-rate wording selects CAGR. Growth stated across a date range
without annual-rate wording uses the cumulative change. A forecast CAGR with
no start date can reconcile when the slide supplies independently verified
actual and forecast figures for the same workbook series. If that calculation
does not reconcile, the figure stays unresolved. Optional OCR can supply slide
text for these checks. AI source assistance ranks labels, and NumCheck performs
the calculations locally.

Select a provider, enter its model ID and API key if
needed, then use **Check connection**. The check sends a synthetic label pair
and exercises the selected model. OpenAI and Anthropic use platform API
keys. OpenRouter offers models from multiple vendors. Jev uses a Vercel AI
Gateway key and can abstain on low-probability choices. Ollama uses a running
local model. **Connect ChatGPT** opens OpenAI authorization for eligible
ChatGPT plans, then loads the account’s models. NumCheck retains the
connection in protected local storage and refreshes it when needed. Disconnect
removes the local session and requests revocation. The OAuth
connection is available at the app's local `127.0.0.1` address.
ChatGPT via Codex uses the installed CLI's `codex login`
session (`NUMCHECK_CODEX_BIN` can point to an executable outside PATH).
The compatible endpoint reads its URL from the server configuration.
Keys typed into the form are held in the tab and used only for the test or
review request. Cloud assistance sends candidate labels and source identifiers,
which can contain deal names. Add terms to redact or keep assistance off.

Workbook previews run locally from saved workbook data. They show a bounded
cell grid with common Excel number formats. The Windows ZIP includes LibreOffice
and PDF rendering. For a source installation, install the `preview` extra
(`pip install -e ".[llm,preview]"`) and make LibreOffice available on PATH or set
`NUMCHECK_SOFFICE` to its executable. LibreOffice may lay out fonts or charts differently from
PowerPoint. When text cannot be located uniquely, the viewer highlights its
containing cell or object when available. Files remain downloadable from the
viewer even when a preview is unavailable.

The UI works offline, uses no external assets, and deletes uploaded files
after two hours or on demand.

### Build the Windows ZIP

Use a Windows x64 Python environment. Create an administrative image from the
official LibreOffice x64 MSI with `msiexec /a`, then locate the extracted
directory containing `program`, `share` and `System64`.

```powershell
python -m pip install -e ".[llm,preview,ocr,crypto]" pyinstaller
python tools/build_windows.py --libreoffice "C:\build\LibreOffice" --output "dist\NumCheck-Windows-x64.zip"
```

### CLI

```bash
numcheck demo --out-dir samples
numcheck review samples/Project_Keystone_Board_Materials.pptx --model samples/Keystone_Model_v4.xlsx \
    --model-old samples/Keystone_Model_v3_prior.xlsx --out-dir samples/report
```

| Option | Purpose |
|---|---|
| `--model` (repeatable) | Source workbooks (`.xlsx` / `.xlsm`) |
| `--model-old` | Previous model, for staleness |
| `--base-period FY24` | Period assumed for numbers that state none |
| `--rounding round_or_truncate` | For teams that truncate instead of round |
| `--tolerance 0.001` | Extra relative slack on top of display precision |
| `--llm {off,openai,anthropic,openai-compatible,ollama,codex}` | Label tie-breaking only |
| `--ocr` | Read numbers inside pictures |
| `--password` | Encrypted files |
| `--format json` | Machine-readable output on stdout |
| `--fail-on high` | Exit code 1 when issues of that severity exist (CI) |

Exit codes: `0` ok, `1` issues at or above `--fail-on`, `2` input or usage
error (with a hint), `3` unexpected error.

Outputs in `--out-dir`:

- `report.html`: printable, self-contained.
- `report.xlsx`: Summary, Issues, the full Coverage log, and Warnings.
- `report.json`: everything, machine-readable.
- `deck_flagged.pptx`: flagged numbers highlighted, numbered markers, issues
  added to speaker notes, and summary slides at the end.

## Configuration

`numcheck init-config` writes a commented `numcheck.toml`. It is picked up
from `--config`, `$NUMCHECK_CONFIG`, or the current directory, by both the CLI
and the web UI. It covers:

- tolerance and rounding mode
- base period
- sheets to exclude or deprioritise
- key metrics
- extra synonyms
- LLM provider and model
- strings to redact before anything is sent to an LLM

## LLM providers (optional)

Off by default. When enabled, the LLM sees only labels, headers, sheet names
and periods for ambiguous candidates, never numbers, and its choice must be
one of the candidates. Failures are reported as warnings and the run stays
deterministic.

| Provider | Credentials |
|---|---|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` (default model `claude-opus-5-5`) |
| `openai-compatible` | `--llm-base-url` + `--model-name` (OpenRouter, LiteLLM, Groq, ...) |
| `ollama` | Local, `http://localhost:11434/v1` |
| `codex` | `codex login` with a ChatGPT subscription; no API key needed |

## Development

```bash
pip install -e ".[llm,dev]"
python -m pytest -q
ruff check numcheck tests
```

`docs/DESIGN.md` lists the real-world hurdles (negatives, ranges, banners,
per-share exceptions, banker layouts, uncached formulas, period spellings,
qualifier conflicts, and more), with the solution and fallback for each.

## Limitations

- Numbers inside embedded raster pictures need **Read numbers in images** or
  `--ocr`. RapidOCR runs locally and is included in the Windows download.
  Source installations can add it with `pip install -e ".[ocr]"`. OCR results are
  lower confidence.
- Workbooks saved by code without calculated values need to be opened and
  saved in Excel. NumCheck tries a LibreOffice recalculation when available.
- Legacy `.ppt` / `.xls` / `.xlsb` must be converted to `.pptx` / `.xlsx`.
- European number formatting (`1.234,5`) is not parsed yet.

## License

MIT
