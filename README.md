# NumCheck

**Automatic number checking for pitchbooks, CIMs, board decks and lender presentations.**

NumCheck reads your PowerPoint deck and the Excel model behind it, finds the source cell for every number on every slide, and tells you which numbers are wrong, stale or inconsistent. Each finding shows the slide, the model cell, the value it should be, and a suggested fix written in the deck's own format.

Everything runs on your own computer. No files leave your machine.

## Download and install (Windows)

1. Download **[NumCheck-Setup.exe](https://github.com/wxgmin/numcheck/releases/latest/download/NumCheck-Setup.exe)** (about 425 MB).
2. Double click it and follow the prompts. No administrator rights are needed.
3. Open **NumCheck** from your Desktop or the Start menu. It opens in your web browser.

That is the whole setup. The installer includes everything NumCheck needs: its own Python, the slide preview renderer (LibreOffice), and the engine that reads numbers inside pictures. Nothing else needs to be installed.

**If Windows shows "Windows protected your PC"**, click **More info**, then **Run anyway**. This appears because the installer is not yet code signed. If your firm blocks unsigned programs, ask IT to allow it.

**Prefer not to install?** Download **[NumCheck-Windows-x64.zip](https://github.com/wxgmin/numcheck/releases/latest/download/NumCheck-Windows-x64.zip)** instead, right click it, choose **Extract All**, and open `NumCheck.exe` in the extracted folder. Extract the whole ZIP first; NumCheck will not run from inside it.

To remove NumCheck, use **Settings, Apps, Installed apps, NumCheck, Uninstall**.

## Your first review

1. Open NumCheck. A small NumCheck window appears and your browser opens the app. Keep the small window open while you work; closing it stops NumCheck.
2. Drop in your **deck** (.pptx) and your **model** (.xlsx or .xlsm). You can add more than one model, and optionally the **previous version** of the model to catch stale numbers.
3. Click **Start review**. A typical deck takes under a minute.
4. Work through the **Issues** tab, most serious first. Click any number to see the original slide with the number highlighted next to the model cell it came from.
5. Download the flagged deck, the Excel report or the HTML report to share with your team.

To try it without your own files, click **Try a sample review**. It runs a sample board deck with deliberate mistakes so you can see what findings look like.

## How it works

NumCheck does what an analyst does during a manual tick and tie, in five steps.

**1. It reads every number on every slide.** Text boxes, sentences ("revenue grew 9% to $1.2bn in FY24"), tables, charts, footnotes and, if you switch it on, numbers inside pictures. For each number it records what it describes (the row or sentence label), which period it belongs to (FY24, LTM Sep 25, Q3), and its unit and scale, including slide banners such as "($ in millions, except per share data)".

**2. It reads the model the same way.** For every cell it works out the row label, the column period, the units, and the block or company the cell sits under, across all sheets, including hidden and backup tabs. It understands banker layouts: title rows, spacer columns, stacked company blocks, "% margin" rows beneath their metric, and number formats such as `0.0x` and `#,##0,,`.

**3. It matches by meaning, not by value.** A slide number is tied to a model cell only when the label, period and unit agree. A coincidence, where the right value sits in an unrelated cell, is never accepted as proof. When several cells could fit, NumCheck uses the surrounding table and slide context to choose, and if it still cannot decide it says so rather than guessing.

**4. It compares at the precision shown on the slide.** "$6.2bn" passes against $6,234m in the model, because the slide is rounded to one decimal. "7%" against 10% fails. Words such as "c.", "~", "over" and "up to" are respected.

**5. It runs the checks a reviewer would.** Beyond tying each number to the model it also checks that:

| Check | Example of what it catches |
|---|---|
| Model tie out | $2,450m on slide 2, but the model says $2,415m |
| Signs and scale | ($45m) shown where the model has +45, or $45bn instead of $45m |
| Stale numbers | The slide matches last week's model, not the current one |
| Wrong period | An FY23 value sitting under an FY24 header |
| Consistency across slides | EBITDA is $540m on slide 3 and $512m everywhere else |
| Totals and splits | Segments that do not add up to the total, or a pie that does not reach 100% |
| Margins, multiples, growth and CAGR | A 22.4% margin when EBITDA divided by revenue gives 21.2% |
| Changes | "Margin up from 12.0% to 15.0%" described as a 200bps increase |
| Footnotes and dates | A footnote period or "as of" date that contradicts the slide |
| Broken model cells | A slide number that points to a `#REF!` or `#DIV/0!` cell |

Every numeric comparison is done by fixed rules, so the same files always give the same answer. Optional AI assistance can help choose between similarly labelled cells, but it only ever sees labels, never numbers, and NumCheck works fully without it.

## What you get back

| Tab or file | What it shows |
|---|---|
| **Issues** | Every problem, ranked by severity, with the expected value and a suggested fix. Mark items resolved or false positive and add notes. |
| **By slide** | The same issues grouped slide by slide. |
| **Coverage** | The full tick and tie log: every number in the deck and the cell it was verified against, or why it could not be. |
| **Warnings** | Anything that limited the review, such as a picture that could not be read or a model saved without calculated values. Nothing is skipped silently. |
| **Flagged deck** | Your deck with problem numbers highlighted, numbered markers, the issues in the speaker notes, and summary slides at the end. |
| **Excel and HTML reports** | Shareable summaries for the team or for sign off. |

A number counts as verified only when NumCheck found its source. Numbers that are on the slide but not in the model are listed as unverified, so you know exactly what still needs a human look.

## Privacy

NumCheck runs as a private app on your own computer, reachable only from that computer. Uploaded files are deleted automatically after two hours, or immediately when you clear them. Nothing is sent anywhere unless you choose to turn on AI assistance and enter your own API key, and even then only labels are sent, never numbers. You can also list deal names to be masked before anything is sent.

## Good to know

* **Files supported:** .pptx decks and .xlsx or .xlsm models. Convert older .ppt, .xls or .xlsb files first. Password protected files are supported.
* **Models saved by code** without calculated values should be opened and saved in Excel once before review.
* **Numbers inside pictures** are read when you tick **Read numbers in images**. These readings are marked lower confidence.
* **Slide previews** take about 20 seconds the first time and are faster afterwards. The preview is drawn by LibreOffice, so fonts can look slightly different from PowerPoint.
* **European number formats** such as 1.234,5 are not read yet.
* **Windows only** for the installer. On a Mac, NumCheck can run from source; see the advanced guide.

## For developers

The command line, configuration file, AI provider setup, building the installer and running the tests are covered in the **[advanced guide](docs/ADVANCED.md)**. The design notes in [docs/DESIGN.md](docs/DESIGN.md) explain how each real world problem (negatives, ranges, unit banners, banker layouts, period spellings and more) is handled.

## License

MIT
