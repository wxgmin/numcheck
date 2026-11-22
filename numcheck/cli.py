"""Command-line interface.

Exit codes: 0 = success (no issues at/above --fail-on), 1 = issues at/above
--fail-on, 2 = usage or input-file error, 3 = unexpected internal error.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from numcheck import __version__
from numcheck.config import load_config
from numcheck.llm import PROVIDERS
from numcheck.models import NumCheckInputError

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except NumCheckInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 2
    except ValueError as exc:  # bad config values
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - surface a friendly message, details with --verbose
        logging.getLogger("numcheck").debug("unexpected error", exc_info=True)
        print(f"error: unexpected failure: {exc} (re-run with --verbose for details)", file=sys.stderr)
        return 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="numcheck",
        description="Check a PowerPoint pitchbook against its Excel model(s) for number errors.",
    )
    parser.add_argument("--version", action="version", version=f"numcheck {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("review", help="Review a deck against one or more models")
    r.add_argument("deck", help="Path to the .pptx pitchbook")
    r.add_argument("--model", action="append", required=True, dest="models",
                   help="Source .xlsx/.xlsm model (repeat for several workbooks)")
    r.add_argument("--model-old", dest="model_old", help="Previous model version, for staleness checks")
    r.add_argument("--out-dir", default="report", help="Output directory (default: report)")
    r.add_argument("--annotate", dest="annotate", action="store_true", default=True,
                   help="Write an annotated copy of the deck (default)")
    r.add_argument("--no-annotate", dest="annotate", action="store_false")
    r.add_argument("--config", default=None, help="Path to numcheck.toml (default: $NUMCHECK_CONFIG or ./numcheck.toml)")
    r.add_argument("--llm", choices=PROVIDERS, default=None,
                   help="LLM for ambiguous label matches only (receives labels, never numbers)")
    r.add_argument("--model-name", default=None, help="LLM model name (provider default if omitted)")
    r.add_argument("--llm-base-url", default=None, help="Base URL for openai-compatible / ollama")
    r.add_argument("--llm-api-key", default=None, help="API key override (prefer environment variables)")
    r.add_argument("--tolerance", type=float, default=None,
                   help="Extra relative tolerance on top of display precision, 0-0.1 (default 0)")
    r.add_argument("--rounding", choices=("round", "round_or_truncate"), default=None,
                   help="How the deck rounds numbers (default: round)")
    r.add_argument("--base-period", default=None,
                   help="Default period for numbers without one, e.g. FY24")
    r.add_argument("--ocr", action="store_true", help="Also read numbers inside pictures (needs LibreOffice + Tesseract)")
    r.add_argument("--password", default=None, help="Password for encrypted files (needs msoffcrypto-tool)")
    r.add_argument("--format", choices=("text", "json"), default="text", help="Console output format")
    r.add_argument("--fail-on", choices=("high", "medium", "low"), default=None,
                   help="Exit with code 1 when any issue at or above this severity is found (for CI)")
    r.add_argument("-q", "--quiet", action="store_true", help="Only print the summary line")
    r.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    r.set_defaults(func=_cmd_review)

    d = sub.add_parser("demo", help="Generate a demo model + deck with planted errors")
    d.add_argument("--out-dir", default="samples")
    d.set_defaults(func=_cmd_demo)

    i = sub.add_parser("init-config", help="Write a starter numcheck.toml")
    i.add_argument("--out", default="numcheck.toml")
    i.add_argument("--force", action="store_true", help="Overwrite an existing file")
    i.set_defaults(func=_cmd_init_config)

    w = sub.add_parser("web", help="Launch the local web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--config", default=None, help="Path to numcheck.toml")
    w.add_argument("--open", action="store_true", help="Open the UI in the default browser")
    w.add_argument("-v", "--verbose", action="store_true")
    w.set_defaults(func=_cmd_web)

    doc = sub.add_parser("doctor", help="Check optional dependencies and configuration")
    doc.add_argument("--config", default=None)
    doc.set_defaults(func=_cmd_doctor)
    return parser


# --------------------------------------------------------------------------- #


def _cmd_review(args) -> int:
    if args.config and not Path(args.config).is_file():
        raise NumCheckInputError(f"config file not found: {args.config}")
    cfg = load_config(args.config)
    if args.tolerance is not None:
        if not 0 <= args.tolerance <= 0.1:
            raise ValueError("--tolerance must be between 0 and 0.1")
        cfg.relative_tolerance = args.tolerance
    if args.rounding:
        cfg.rounding_mode = args.rounding
    if args.base_period:
        cfg.base_period = args.base_period.strip().lower()
    if args.model_name is not None:
        cfg.model_name = args.model_name
    if args.llm is not None:
        cfg.llm = args.llm
    if args.llm_base_url is not None:
        cfg.llm_base_url = args.llm_base_url
    if args.llm_api_key is not None:
        cfg.llm_api_key = args.llm_api_key
    if args.password:
        cfg.password = args.password
    fail_on = args.fail_on or cfg.fail_on or None

    for path, kind in [(args.deck, "deck")] + [(m, "model") for m in args.models] + (
        [(args.model_old, "previous model")] if args.model_old else []
    ):
        if Path(path).is_dir():
            raise NumCheckInputError(f"{kind} path is a folder, not a file: {path}")
        if not Path(path).is_file():
            raise NumCheckInputError(f"{kind} file not found: {path}")

    from numcheck.runner import run_review

    def progress(stage: str, fraction: float, message: str) -> None:
        if not args.quiet and args.format == "text" and sys.stderr.isatty():
            print(f"\r  {int(fraction * 100):3d}%  {message[:70]:<70}", end="", file=sys.stderr, flush=True)

    result = run_review(args.deck, args.models, args.model_old, args.out_dir, args.annotate, cfg,
                        args.ocr, progress=progress)
    if sys.stderr.isatty() and not args.quiet and args.format == "text":
        print("\r" + " " * 80 + "\r", end="", file=sys.stderr)

    if args.format == "json":
        from numcheck.report.json_report import build_json

        print(json.dumps(build_json(result), indent=2, ensure_ascii=False))
    else:
        _print_summary(result, quiet=args.quiet)

    if fail_on:
        limit = SEVERITY_RANK[fail_on]
        if any(SEVERITY_RANK.get(i.severity.value, 9) <= limit for i in result.issues):
            return 1
    return 0


def _print_summary(result, quiet: bool = False) -> None:
    from numcheck.report import summarize

    s = summarize(result.issues)
    sev = s.get("by_severity", {})
    cov = result.coverage or {}
    print(f"NumCheck: {s['total']} issue(s) - high {sev.get('high', 0)}, medium {sev.get('medium', 0)}, "
          f"low {sev.get('low', 0)} | verified {cov.get('verified', 0)}/{cov.get('checkable', 0)} "
          f"numbers ({cov.get('verified_pct', 0)}%) | {result.elapsed:.1f}s")
    if quiet:
        return
    for w in result.warnings:
        print(f"  warning: {w.message}")
    if result.issues:
        print()
        width = max(len(i.deck_value) for i in result.issues[:40])
        for i in result.issues[:40]:
            exp = f" -> {i.suggestion or i.expected_value}" if (i.suggestion or i.expected_value) else ""
            src = f"  [{i.source_cell}]" if i.source_cell else ""
            print(f"  {i.severity.value.upper():<6} slide {i.slide_number:<3} {i.issue_type.value:<26} "
                  f"{i.deck_value:<{width}}{exp}{src}")
        if len(result.issues) > 40:
            print(f"  ... and {len(result.issues) - 40} more (see the HTML/Excel report)")
    print()
    for key, label in (("html", "HTML report"), ("xlsx", "Excel report"), ("json", "JSON"), ("deck", "Annotated deck")):
        if key in result.files:
            print(f"  {label:<15} {result.files[key]}")


def _cmd_demo(args) -> int:
    from numcheck.demo import build_demo

    paths = build_demo(args.out_dir)
    print("Demo files written:")
    for k, v in paths.items():
        print(f"  {k:<10} {v}")
    print(f"\nTry: numcheck review {paths['deck']} --model {paths['model']} --model-old {paths['model_old']}")
    return 0


def _cmd_init_config(args) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        raise NumCheckInputError(f"{out} already exists (use --force to overwrite)")
    from importlib import resources

    try:
        text = resources.files("numcheck").joinpath("default_config.toml").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        text = "[tolerance]\nrounding_mode = \"round\"\n\n[llm]\nprovider = \"off\"\n"
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def _cmd_web(args) -> int:
    from numcheck.web.app import create_app

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: binding to {args.host} exposes the UI (and uploaded client files) to your "
              "network without authentication.", file=sys.stderr)
    app = create_app(args.config)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print(f"NumCheck web UI: {url}  (Ctrl+C to stop)")
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


def _cmd_doctor(args) -> int:
    from numcheck.extraction.fileio import soffice_binary

    cfg = load_config(args.config)
    ok = "ok  "
    no = "--  "

    def line(flag: bool, text: str) -> None:
        print(f"  {ok if flag else no}{text}")

    print(f"numcheck {__version__} on Python {sys.version.split()[0]}")
    print(f"config: {cfg.source_path or 'built-in defaults'}")
    print("\ncore")
    for mod in ("pptx", "openpyxl", "lxml", "flask", "jinja2", "rapidfuzz"):
        line(importlib.util.find_spec(mod) is not None, mod)
    print("\noptional")
    line(soffice_binary() is not None, "LibreOffice (OCR rendering, formula recalculation)")
    line(shutil.which("tesseract") is not None, "Tesseract binary (OCR)")
    line(importlib.util.find_spec("pytesseract") is not None, "pytesseract (OCR)")
    line(shutil.which("pdftoppm") is not None or importlib.util.find_spec("fitz") is not None,
         "pdftoppm or PyMuPDF (OCR page rendering)")
    line(importlib.util.find_spec("msoffcrypto") is not None, "msoffcrypto-tool (password-protected files)")
    print("\nLLM providers (labels only, never numbers)")
    print(f"  configured: {cfg.llm}")
    line(bool(os.environ.get("OPENAI_API_KEY") or cfg.llm_api_key), "OpenAI key present")
    line(bool(os.environ.get("ANTHROPIC_API_KEY") or cfg.llm_api_key), "Anthropic key present")
    line(shutil.which("codex") is not None, "codex CLI on PATH")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
