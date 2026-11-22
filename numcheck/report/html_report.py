"""Self-contained, printable HTML report (no external assets, everything escaped)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment

from numcheck import __version__
from numcheck.models import Issue, ReviewResult
from numcheck.report import (
    STATUS_LABELS,
    as_result,
    coverage_pct,
    match_rows,
    sort_issues,
    summarize,
    type_label,
)

_ENV = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
_ENV.filters["type_label"] = type_label

TEMPLATE = _ENV.from_string(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NumCheck review - {{ deck }}</title>
<style>
:root{--bg:#F6F5F1;--surface:#fff;--ink:#15181C;--ink2:#5B6169;--line:#DCD9D0;--accent:#1F3A5F;
--high:#9B2C2C;--high-bg:#F6E3E1;--med:#7A5200;--med-bg:#F6EBCF;--low:#475569;--low-bg:#E8EBEF;--ok:#2F6B3F;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Inter","Segoe UI",-apple-system,"Helvetica Neue",Arial,sans-serif}
.num,td.n,.mono{font-family:ui-monospace,"SF Mono","Cascadia Mono",Consolas,monospace;font-variant-numeric:tabular-nums}
h1,h2{font-family:"Iowan Old Style","Source Serif Pro",Georgia,serif;font-weight:600;margin:0}
header{border-bottom:1px solid var(--line);background:var(--surface);padding:20px 32px}
header h1{font-size:24px;letter-spacing:.01em}
.meta{color:var(--ink2);font-size:13px;margin-top:4px}
main{max-width:1180px;margin:0 auto;padding:24px 32px 48px}
h2{font-size:18px;margin:32px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.verdict{font-size:16px;font-weight:600;margin:0 0 16px}
.kpis{display:flex;flex-wrap:wrap;gap:0;border:1px solid var(--line);background:var(--surface)}
.kpi{padding:12px 20px;border-right:1px solid var(--line);min-width:130px}
.kpi:last-child{border-right:0}
.kpi .v{font-size:24px;font-weight:600}
.kpi .l{color:var(--ink2);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.bar{height:4px;background:var(--line);margin-top:6px;width:120px}
.bar>span{display:block;height:100%;background:var(--accent)}
.warn{border:1px solid var(--line);border-left:3px solid var(--med);background:var(--surface);padding:8px 12px;margin:8px 0}
.warn .h{color:var(--ink2);font-size:13px}
table{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--line)}
th,td{padding:6px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink2);background:var(--bg)}
td.n,th.n{text-align:right}
.sev{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;padding:1px 6px;border:1px solid currentColor;border-radius:3px}
.sev.high{color:var(--high);background:var(--high-bg)}
.sev.medium{color:var(--med);background:var(--med-bg)}
.sev.low{color:var(--low);background:var(--low-bg)}
.slide{margin:20px 0;break-inside:avoid-page}
.slide h3{font-size:15px;margin:0 0 6px}
.issue{border:1px solid var(--line);background:var(--surface);padding:10px 14px;margin:0 0 8px;break-inside:avoid}
.issue .top{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.issue .ty{font-weight:600}
.issue p{margin:6px 0}
.grid{display:grid;grid-template-columns:auto 1fr;gap:2px 16px;font-size:13px}
.grid dt{color:var(--ink2)}.grid dd{margin:0}
.pill{font-size:12px;padding:0 6px;border:1px solid var(--line);border-radius:3px}
.pill.mismatch{color:var(--high)}.pill.stale,.pill.ambiguous{color:var(--med)}
details{margin-top:24px}summary{cursor:pointer;font-family:Georgia,serif;font-size:18px;font-weight:600}
footer{color:var(--ink2);font-size:12px;margin-top:40px}
@media print{body{background:#fff;font-size:11px}header{padding:0 0 8px}main{padding:12px 0;max-width:none}
details{display:block}.issue,tr{break-inside:avoid}a{color:inherit;text-decoration:none}}
</style>
</head>
<body>
<header>
<h1>NumCheck review</h1>
<div class="meta">Deck <strong>{{ deck }}</strong>{% if models %} &middot; Models {{ models|join(", ") }}{% endif %}{% if model_old %} &middot; Previous model {{ model_old }}{% endif %}</div>
<div class="meta">Generated {{ generated }} &middot; run time {{ "%.1f"|format(elapsed) }} s &middot; NumCheck {{ version }}</div>
</header>
<main>
<h2>Summary</h2>
<p class="verdict">{{ verdict }}</p>
<div class="kpis">
<div class="kpi"><div class="v num">{{ total }}</div><div class="l">Issues</div></div>
{% for sev in ("high", "medium", "low") %}
<div class="kpi"><div class="v num">{{ by_sev.get(sev, 0) }}</div><div class="l">{{ sev|capitalize }}</div></div>
{% endfor %}
<div class="kpi"><div class="v num">{% if pct is not none %}{{ "%.1f"|format(pct) }}%{% else %}n/a{% endif %}</div><div class="l">Verified</div>
{% if pct is not none %}<div class="bar"><span class="w" style="width:{{ [pct, 100]|min }}%"></span></div>{% endif %}</div>
<div class="kpi"><div class="v num">{{ checkable if checkable is not none else "n/a" }}</div><div class="l">Figures reviewed</div></div>
</div>

{% if warnings %}
<h2>Warnings</h2>
{% for w in warnings %}
<div class="warn"><strong>{{ w.code }}</strong>{% if w.file %} &middot; {{ w.file }}{% endif %}{% if w.slide_number %} &middot; slide {{ w.slide_number }}{% endif %}
<div class="h">{{ w.message }}</div></div>
{% endfor %}
{% endif %}

<h2>Issues</h2>
{% if not groups %}<p>No issues. Every checked number reconciles.</p>{% endif %}
{% for slide_number, title, items in groups %}
<section class="slide">
<h3>Slide {{ slide_number }}{% if title %} &middot; {{ title }}{% endif %}</h3>
{% for i in items %}
<article class="issue">
<div class="top"><span class="sev {{ i.severity.value }}">{{ i.severity.value|upper }}</span>
<span class="ty">{{ i.issue_type.value|type_label }}</span>
<span class="mono">#{{ i.id }}</span>{% if i.deck_label %}<span>{{ i.deck_label }}</span>{% endif %}</div>
<p>{{ i.message }}</p>
<dl class="grid">
<dt>Deck</dt><dd class="mono">{{ i.deck_value }}</dd>
{% if i.expected_value %}<dt>Expected</dt><dd class="mono">{{ i.expected_value }}</dd>{% endif %}
{% if i.suggestion %}<dt>Suggestion</dt><dd class="mono">Change to {{ i.suggestion }}</dd>{% endif %}
{% if i.source_cell %}<dt>Source cell</dt><dd class="mono">{{ i.source_cell }}</dd>{% endif %}
{% if i.difference is not none %}<dt>Difference</dt><dd class="mono">{% if i.difference_text %}{{ i.difference_text }}{% else %}{{ "%.4g"|format(i.difference) }}{% endif %}{% if i.tolerance_text %} (tolerance {{ i.tolerance_text }}){% elif i.tolerance is not none %} (tolerance &plusmn;{{ "%.4g"|format(i.tolerance) }}){% endif %}</dd>{% endif %}
<dt>Confidence</dt><dd class="mono">{{ "%.0f"|format(i.confidence * 100) }}%</dd>
</dl>
</article>
{% endfor %}
</section>
{% endfor %}

<details>
<summary>Coverage log (tick &amp; tie) &middot; {{ rows|length }} numbers</summary>
<table>
<thead><tr><th class="n">Slide</th><th>Deck</th><th>Label</th><th>Status</th><th>Source cell</th><th class="n">Model value</th><th>Method</th><th class="n">Conf.</th></tr></thead>
<tbody>
{% for r in rows %}
<tr><td class="n">{{ r.slide_number }}</td><td class="mono">{{ r.raw_text }}</td><td>{{ r.label }}</td>
<td><span class="pill {{ r.status }}">{{ statuses.get(r.status, r.status) }}</span></td>
<td class="mono">{{ r.source_cell }}</td><td class="n">{{ r.model_value }}</td><td>{{ r.method }}</td>
<td class="n">{{ "%.0f"|format((r.confidence or 0) * 100) }}%</td></tr>
{% endfor %}
</tbody></table>
</details>
<footer>Generated locally by NumCheck. Numeric comparison is deterministic; no values leave this machine.</footer>
</main>
</body>
</html>
"""
)


def _verdict(issues: list[Issue]) -> str:
    if not issues:
        return "Clean: every checked number reconciles."
    s = summarize(issues)["by_severity"]
    parts = [f"{s[k]} {k}" for k in ("high", "medium", "low") if s.get(k)]
    return f"{len(issues)} issues · " + " · ".join(parts)


def _groups(res: ReviewResult) -> list[tuple[int, str, list[Issue]]]:
    titles = {s.slide_number: s.title for s in res.slides}
    by_slide: dict[int, list[Issue]] = {}
    for i in sort_issues(res.issues):
        by_slide.setdefault(i.slide_number, []).append(i)
    return [(n, titles.get(n, ""), items) for n, items in sorted(by_slide.items())]


def render_html(result: "ReviewResult | list[Issue]") -> str:
    res = as_result(result)
    inp = res.inputs or {}
    sev = summarize(res.issues)["by_severity"]
    return TEMPLATE.render(
        deck=inp.get("deck") or "deck",
        models=list(inp.get("models") or []),
        model_old=inp.get("model_old") or "",
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        elapsed=res.elapsed or 0.0,
        version=__version__,
        verdict=_verdict(res.issues),
        total=len(res.issues),
        by_sev=sev,
        pct=coverage_pct(res),
        checkable=(res.coverage or {}).get("checkable"),
        warnings=res.warnings,
        groups=_groups(res),
        rows=match_rows(res),
        statuses=STATUS_LABELS,
    )


def write_html_report(result: "ReviewResult | list[Issue]", out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(result), encoding="utf-8")
