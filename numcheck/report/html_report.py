"""Self-contained HTML report."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment

from numcheck.models import Issue
from numcheck.report import SEVERITY_COLORS, summarize

_ENV = Environment(autoescape=True)

TEMPLATE = _ENV.from_string(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NumCheck Review Report</title>
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 0; background:#f6f7f9; color:#1c1e21; }
header { background:#1F3864; color:#fff; padding:20px 32px; }
header h1 { margin:0 0 6px; font-size:22px; }
header p { margin:0; opacity:.85; font-size:14px; }
main { padding:24px 32px; max-width:1280px; margin:0 auto; }
.cards { display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }
.card { background:#fff; border:1px solid #e4e6eb; border-radius:8px; padding:16px 20px; min-width:150px; }
.card .num { font-size:28px; font-weight:700; }
.card.high .num { color:#d9534f; }
.card.medium .num { color:#f0ad4e; }
.card.low .num { color:#5bc0de; }
.controls { display:flex; gap:12px; margin-bottom:16px; }
.controls input, .controls select { padding:8px 10px; border:1px solid #ccc; border-radius:6px; font-size:14px; }
table { width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.08); }
th, td { text-align:left; padding:10px 12px; border-bottom:1px solid #eef0f2; font-size:13px; vertical-align:top; }
th { background:#f0f2f5; position:sticky; top:0; }
tr:hover td { background:#fafbfc; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px; font-weight:600; }
.badge.high { background:#fdecea; color:#b02a27; }
.badge.medium { background:#fdf3e0; color:#9a6b1f; }
.badge.low { background:#e8f4f8; color:#1c6f8a; }
.mono { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; }
.slide { color:#555; font-weight:600; }
</style>
</head>
<body>
<header>
  <h1>NumCheck Review Report</h1>
  <p>{{ summary.total }} issue(s) found across the deck</p>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="num">{{ summary.total }}</div><div>Total</div></div>
    {% for sev in ["high", "medium", "low"] %}
    <div class="card {{ sev }}"><div class="num">{{ summary.by_severity.get(sev, 0) }}</div><div>{{ sev|capitalize }}</div></div>
    {% endfor %}
  </div>
  <div class="controls">
    <input id="q" type="text" placeholder="Filter by metric, value, cell, message...">
    <select id="sev"><option value="">All severities</option><option>high</option><option>medium</option><option>low</option></select>
  </div>
  <table id="issues">
    <thead>
      <tr>
        <th>#</th><th>Slide</th><th>Type</th><th>Severity</th><th>Metric</th>
        <th>Deck Value</th><th>Expected</th><th>Source Cell</th><th>Message</th>
      </tr>
    </thead>
    <tbody>
      {% for i in issues %}
      <tr data-sev="{{ i.severity.value }}" data-text="{{ (i.metric_key + ' ' + i.deck_value + ' ' + i.expected_value + ' ' + i.source_cell + ' ' + i.message)|lower }}">
        <td>{{ i.id }}</td>
        <td class="slide">{{ i.slide_number }}</td>
        <td>{{ i.issue_type.value }}</td>
        <td><span class="badge {{ i.severity.value }}">{{ i.severity.value }}</span></td>
        <td>{{ i.metric_key }}</td>
        <td class="mono">{{ i.deck_value }}</td>
        <td class="mono">{{ i.expected_value }}</td>
        <td class="mono">{{ i.source_cell }}</td>
        <td>{{ i.message }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</main>
<script>
const q = document.getElementById('q'), sev = document.getElementById('sev');
function filter() {
  const term = q.value.toLowerCase(), s = sev.value;
  document.querySelectorAll('#issues tbody tr').forEach(tr => {
    const okText = tr.dataset.text.includes(term);
    const okSev = !s || tr.dataset.sev === s;
    tr.style.display = (okText && okSev) ? '' : 'none';
  });
}
q.addEventListener('input', filter);
sev.addEventListener('change', filter);
</script>
</body>
</html>"""
)


def write_html_report(issues: list[Issue], out_path: str | Path) -> None:
    html = TEMPLATE.render(summary=summarize(issues), issues=issues, sev=SEVERITY_COLORS)
    Path(out_path).write_text(html, encoding="utf-8")
