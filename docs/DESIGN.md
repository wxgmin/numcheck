# NumCheck — Design & Engineering Spec

This document is the source of truth for how NumCheck works and why. It lists the
real-world hurdles of checking investment-banking pitchbooks against Excel models,
the solution or fallback for each, and the contracts between modules.

## 0. Principles (non-negotiable)

1. **Every numeric comparison is deterministic code.** The LLM may only choose
   between candidate cells by *label semantics*. It never receives numbers.
2. **Display precision is the tolerance.** A displayed number stands for an
   interval: `75%` = [74.5, 75.5], `$1.2bn` = [1.15bn, 1.25bn]. Never use a
   relative fudge factor that lets `7%` match `10%`.
3. **Unknown is a wildcard, not a value.** A missing unit/scale/period widens
   what *can* match; it never forces a mismatch. Inferences are recorded and
   lower confidence.
4. **Never fail silently.** Anything that reduces coverage (uncalculated formulas,
   images, unreadable charts, LLM failure, encrypted files, skipped sheets) becomes
   a `ReviewWarning` shown prominently. Coverage % is a first-class output.
5. **Prefer "can't verify" over a false alarm, and a false alarm over a false pass**
   — but make every flag explain itself (expected value, source cell, tolerance,
   reason, suggestion in the deck's own format).
6. **One malformed shape/sheet never aborts a review.** Catch per element, record
   a warning, continue.
7. **Confidential data stays local by default.** LLM off by default; no network
   calls; uploads deleted after use; no external fonts/CDNs in the UI.

## 1. Hurdle catalogue (situation → solution → fallback)

### 1.1 Numbers in the deck

| Situation | Solution | Fallback |
|---|---|---|
| Negatives: `(45)`, `($45m)`, `(5.2%)`, `-$5m`, `−5%` (U+2212), en-dash minus | Scanner handles paren pairs around any part, unicode minus; dash glued to preceding digit = range separator | Sign mismatch classified as `sign_error` |
| Ranges `$1.2–1.5bn`, `10-12%`, `5.0x–6.0x` | Endpoints linked, units shared, `range_role` low/high | Matcher prefers cells labelled low/min vs high/max |
| Approximations `c.$1bn`, `~`, `approx.`; bounds `>$500m`, `up to` | `comparator` field; `~` widens to `approx_relative`; `>`/`<` are inequality checks | — |
| Footnote markers `Revenue(1)`, `$1.2bn(2)`, superscripts | Skipped with `skip_reason="footnote_marker"` | — |
| Years `2024`, `2024A`, `FY22-24`, dates `30-Sep-24`, `9/30/2024`, ordinals | Skipped (`year`/`date`/`period`/`ordinal`), used as period context | — |
| Scale words `bn/b/B/billion/m/mm/mn/M/k/thousand/tn`, `000s` | Canonical scales `k/m/bn/t`; word boundary required (`10 months` ≠ `$10m`) | — |
| Currencies `$ US$ A$ C$ £ € ¥ CHF EUR USD` | `currency` code; conflicting known currencies never match | Missing currency = wildcard |
| Counts `1,200 employees`, `1.2bn shares` | `dimension="count"` | — |
| Per-share values in "$m except per share" decks | Banner `per_share_exception`; EPS/DPS/per share/price labels keep raw scale | Try both scales |
| Units stated once in title/subtitle/table header/footnote `($ in millions)` | Slide-level `UnitHint` inherited by unit-less numbers on that slide; table header hints per row/column | Compare digits against model's own display scale (`inferred`) |
| `x` inside words (`Capex`, `Tax`) or `m` inside words (`Premium`) | Token-level unit detection only | — |
| Percent points vs bps vs % change (`+150bps`, `3.2pp`) | bps → percent points canonical; pp = percent points | — |
| Rounding: model 1,234.56 shown as `$1.2bn` / `1,235` / `$1,234.6m` | Display-precision interval | `rounding_mode=round_or_truncate` for truncating teams |
| Valid but coarse rounding (`$1bn` for 1.4bn) | Matches, plus LOW `rounding` issue above `coarse_rounding_threshold` | — |
| Same number shown with different precision on different slides | Cross-slide check compares *intervals* (overlap = consistent) | — |
| Numbers split across runs/paragraphs, text in groups, placeholders, SmartArt | Paragraph-level text; recursive group walk; placeholders included; SmartArt text read from `dgm` parts when present | Warning `smartart_not_read` |
| Slide number / date / footer placeholders | Excluded by placeholder type | — |
| Hidden slides | Extracted, flagged `SlideInfo.hidden`; issues on hidden slides downgraded | — |
| Numbers inside images/screenshots/pasted Excel pictures | Counted per slide → warning `images_not_checked` | Optional OCR (LibreOffice → PDF → per-page render → Tesseract), confidence 0.7, deduped against native text |
| Speaker notes | Ignored by default (`read_speaker_notes`) | — |
| Text-box prose "Revenue grew 9% to $1.2bn in FY24, with EBITDA of $300m" | Label = nearest noun phrase before each number, bounded by previous number / punctuation; period from the clause | Value-only verification (unique ≥3 significant digits) |
| Tables: multi-row headers, merged cells, row labels not in col 0, transposed tables (periods as rows), blank spacer rows/cols, units column | Header detection (period-like rows/cols), label column detection, merged-cell propagation, unit column detection | Table-block alignment against the model (see 1.4) |
| Charts: bar/column/line/area/pie/doughnut/radar/stacked/combo; XY (`xVal/yVal`), bubble (`bubbleSize`) | Read every series type; categories from `cat` (str/num/multi-level); format from `numCache/formatCode` and data-label `numFmt` | Series-vector alignment against model rows/cols |
| Chart shows rounded data labels but cache holds exact values | Compare cache value with tolerance from the **displayed** label format | — |
| Charts with external/linked data, or no cache | Warning `chart_no_cache` | Embedded workbook (`chart.part.chart_workbook`) values |
| Encrypted / password-protected pptx or xlsx | Detect OLE CFB signature → friendly `NumCheckInputError` | `--password` with optional `msoffcrypto-tool` |
| Wrong file types: `.ppt`, `.pptm`, `.potx`, `.xls`, `.xlsb`, `.xlsm`, `.csv` | Content sniffing (zip + `[Content_Types].xml`), `.xlsm`/`.pptm` supported, `.xls`/`.xlsb`/`.ppt` → clear message how to convert | LibreOffice conversion if available |
| Very large decks (200+ slides) | Linear extraction; per-slide try/except; progress callback | — |

### 1.2 Numbers in the model

| Situation | Solution | Fallback |
|---|---|---|
| Layout: title rows, units row, blank row, header row with **numeric years** (2023, 2024, `2025E`), blank spacer columns, units column, indented labels | Per-sheet structure detection: header row = row with most period-like cells; label column = left-most text column feeding data; walk past blanks; headers excluded from values | Per-cell nearest-label walk |
| Merged header cells | Propagate merged anchor value to the merged range | Read-only mode → warning |
| Block structure (Income Statement / Balance Sheet / company A / company B) | `section_label` = nearest bold/standalone text above the block | — |
| Multiple companies side by side (comps, merger models) | Column group headers become part of `col_label` | — |
| Units: number format (`0.0%`, `0.0x`, `$#,##0`, `#,##0,,` divisors, `"bn"` literals), units column (`$m`, `%`, `x`), row label `(%)`, sheet banner `($ in millions)` | Per-cell resolution order: format → unit column → row/col label tokens → nearest banner above the block | Unknown (wildcard) |
| Percent stored as 25 vs 0.25 | `pct_basis` from format (`%` → ratio) or magnitude | Both interpretations tried, `inferred` |
| Formulas without cached values (openpyxl/pandas-written, never opened in Excel) | Count per sheet → warning `uncalculated_formulas` with fix hint | LibreOffice headless recalculation when installed, then a built-in deterministic evaluator (arithmetic, refs/ranges incl. cross-sheet, SUM/AVERAGE/MIN/MAX/COUNT/PRODUCT/ABS/ROUND*/IF/IFERROR/AND/OR/NOT); anything else stays missing |
| Error cells `#N/A #DIV/0! #REF! #VALUE! #NAME? #NUM!` | Extracted as `is_error=True`; a deck number mapped to one → `source_error` issue | — |
| Dates stored as serials, booleans, text-numbers (`'1,234`) | Dates/booleans skipped; numeric strings parsed and flagged | — |
| Hidden sheets/rows/cols, backup/old/scratch sheets | Extracted with `hidden` / `sheet_priority<1`; tie-break penalty | `exclude_sheets` |
| Duplicate values across sheets (outputs copied to a "Summary" tab) | Equal values are not ambiguous — any of them confirms | — |
| Named ranges (workbook- and sheet-scoped, multi-cell) | Single cells mapped; ranges expanded | — |
| External links | Cached values used; warning if links exist | — |
| Huge workbooks (100k–1M+ cells, 50–200MB) | Streaming read-only when above `large_workbook_cells`; label walks computed per row/col once; matching via inverted index | Sheet include/exclude filters |
| `.xlsm` macros, data tables, pivot caches | Values read; pivots/data tables are just cells | — |
| Multiple models (operating model + DCF + comps) | `workbook` field on every value; references are `[file] Sheet!A1` | — |

### 1.3 Labels & periods

| Situation | Solution |
|---|---|
| Synonyms: Sales/Revenue/Turnover, Operating income/EBIT, PAT/Net income, TEV/EV | Configurable abbreviation/synonym map, applied longest-first on whole tokens |
| Qualifiers that change meaning: Adjusted vs reported, pro forma, diluted vs basic, organic, LTM vs FY, low/high, target vs acquirer, company names | Qualifier conflict = hard penalty; company/section names are discriminating tokens |
| Generic tokens (`total`, `the`) dominating Jaccard | IDF-weighted token similarity computed over the model's label corpus |
| Period spellings FY24 / FY2024 / 2024A / 2024E / CY24 / Q3'24 / 3Q24 / 1H24 / LTM Sep-24 / 9M24 | `numcheck.periods` canonical keys; FY vs bare year compatible; FY vs CY compatible with lower affinity |
| Deck number without a period | Inherit slide period hint, then deck-wide `base_period` (config) or dominant period; if the only matching value belongs to a different period → `period_ambiguous` |
| Wrong-period number (FY23 value under an FY24 header) | Period filter excludes incompatible cells; mismatch vs the right period, with a note when the value equals another period's value |
| Sensitivity tables / football fields (numeric row & column headers) | Table-block alignment |
| Same label, different values in several cells | If exactly one candidate matches → matched (confidence reduced); none match & all equal → mismatch; else ambiguous → LLM (labels only) → ambiguous issue listing candidates |

### 1.4 Matching strategies (in order)

1. **Named range** exact/near-exact label → strongest evidence.
2. **Label match** with inverted index, IDF-weighted similarity, qualifier
   conflicts, period affinity, unit compatibility, sheet priority.
3. **Context coherence**: numbers from the same slide table/chart tend to come
   from the same sheet/row/column block; boost candidates near the block already
   chosen for sibling numbers.
4. **Table-block alignment**: for a deck table with ≥4 numbers, find the model
   rectangle (either orientation) where most cells match. Cells inside an
   aligned block that disagree are high-confidence mismatches even with poor labels.
5. **Series alignment** for chart series vs model rows/columns.
6. **Value-only verification**: unique value with ≥3 significant digits in a
   compatible dimension → `matched_value_only` (verifies, never flags).
7. **LLM** for remaining label ties (labels, headers, sheet names only — no values).

### 1.5 Checks

| Check | Rules |
|---|---|
| Model match | Issue types: `mismatch`, `sign_error`, `scale_error`, `stale`, `source_error`, `period_ambiguous`, `rounding`, `ambiguous`, `unmatched` |
| Cross-slide consistency | Group by (metric key, period, dimension, qualifiers); intervals overlap = consistent; reference = model-verified value, else majority; one merged issue per number |
| Sums (tables) | Blocks between subtotals; header rows excluded; `less:`/negative rows subtract; tolerance = Σ component half-ulps + total half-ulp; vertical and horizontal totals; `% of total` columns and pies sum to 100% |
| Margins / ratios | Exact metric tokens (EBIT ≠ EBITDA); same period; **interval arithmetic** on displayed numerator/denominator vs displayed ratio; covers margins, multiples (EV/EBITDA), premiums, per-share, tax rate |
| Growth / CAGR | Prior period = `previous_period`; CAGR uses `parse_span` and n years; interval arithmetic |
| Units / periods | FY vs CY mixed for the same metric/year; LTM vs NTM mixing; scale style differences are LOW and only when values agree |
| Footnotes / sources | As-of dates inconsistent across slides; footnote period statements that contradict the slide's own numbers |
| Staleness | Deck mismatches new model but matches the corresponding old cell (same address or same labels+period) → replaces the mismatch |

Severity: base by type; raised for key metrics and large relative differences
(>5%); lowered for low confidence, inferred units, hidden slides, value-only
evidence; style issues LOW.

### 1.6 LLM layer

* Providers: off | openai | anthropic | openai-compatible | ollama | codex.
* Payload: item id, deck label/context words, slide title, candidate cell refs with
  sheet, row/col labels, section label, period — **no numeric values**.
* Batched (`llm_batch_size`), timeout, retries, response validated (choice must be a
  candidate ref or empty), results cached per run.
* Optional redaction of configured strings before sending.
* Any failure → `ReviewWarning("llm_failed", ...)`; the run continues deterministically.
* Codex: prompt via stdin, `--output-last-message <tmpfile>`, never argv.
* Anthropic default model `claude-opus-5-5` (override via config), structured JSON via
  `output_config.format` json_schema when supported by the installed SDK, refusal
  stop reason handled.

### 1.7 Web app & reports

* Async jobs with progress stages; JSON errors everywhere (400/413/415/422/500);
  uploads validated by content; per-run temp dir deleted after a TTL / on demand /
  at shutdown; download whitelist; `numcheck.toml` honoured; tolerance bounded;
  no external network/fonts.
* Reports: HTML (self-contained, printable), Excel (Issues + full tick-and-tie
  Coverage log + Warnings + Summary; formula-injection-safe), JSON (complete),
  annotated deck (precise run highlighting, numbered markers, per-slide speaker
  notes listing issues, paginated summary slides sized to the deck).

## 2. Module contracts

See `numcheck/models.py` (data contracts) and `numcheck/matching/normalizer.py`
(`scan_numbers`, `parse_unit_banner`, `compare`, `deck_interval`, `format_like`,
`display_tolerance`), `numcheck/periods.py`, `numcheck/numfmt.py`.

Pipeline (`numcheck/runner.py`):

```
extract_deck_full(deck, config)            -> DeckExtraction
extract_model_full(path, config) per model -> ModelExtraction
match_deck_to_model(numbers, values, config, old_values=..., slides=..., warnings=...) -> list[Match]
build_issues(matches, deck_extraction, config) + run_checks(...) -> list[Issue]   (numcheck.checks)
compute_coverage(matches, deck_extraction)  -> dict
write reports (html/xlsx/json/annotated pptx) -> ReviewResult
```
