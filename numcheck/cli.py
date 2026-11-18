"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from numcheck.config import load_config
from numcheck.llm import PROVIDERS
from numcheck.report import summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="numcheck",
        description="Check a PowerPoint pitchbook against Excel models for number errors.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser("review", help="Run a deck review")
    r.add_argument("deck", help="Path to the .pptx pitchbook")
    r.add_argument("--model", action="append", required=True, dest="models", help="Source .xlsx model (repeatable)")
    r.add_argument("--model-old", dest="model_old", help="Previous model version for staleness checks")
    r.add_argument("--out-dir", default="report", help="Output directory (default: report)")
    r.add_argument("--annotate", dest="annotate", action="store_true", default=True)
    r.add_argument("--no-annotate", dest="annotate", action="store_false")
    r.add_argument("--config", default=None, help="Path to numcheck.toml")
    r.add_argument("--llm", choices=PROVIDERS, default=None, help="LLM provider for fuzzy label disambiguation")
    r.add_argument("--model-name", default=None)
    r.add_argument("--llm-base-url", default=None, help="Base URL for openai-compatible / ollama")
    r.add_argument("--llm-api-key", default=None, help="API key override")
    r.add_argument("--tolerance", type=float, default=None, help="Base relative tolerance")
    r.add_argument("--ocr", action="store_true", help="Enable optional OCR fallback")

    d = sub.add_parser("demo", help="Generate a demo model + planted-error deck")
    d.add_argument("--out-dir", default="samples")

    i = sub.add_parser("init-config", help="Write a starter config")
    i.add_argument("--out", default="numcheck.toml")

    w = sub.add_parser("web", help="Launch the local web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.command == "review":
        if args.config and not Path(args.config).is_file():
            print(f"error: config file not found: {args.config}", file=sys.stderr)
            return 2
        cfg = load_config(args.config)
        if args.tolerance is not None:
            cfg.relative_tolerance = args.tolerance
        if args.model_name is not None:
            cfg.model_name = args.model_name
        if args.llm is not None:
            cfg.llm = args.llm
        if args.llm_base_url is not None:
            cfg.llm_base_url = args.llm_base_url
        if args.llm_api_key is not None:
            cfg.llm_api_key = args.llm_api_key

        if not Path(args.deck).is_file():
            print(f"error: deck file not found: {args.deck}", file=sys.stderr)
            return 2
        if not args.deck.lower().endswith(".pptx"):
            print("error: deck must be a .pptx file", file=sys.stderr)
            return 2
        for model in args.models:
            if not Path(model).is_file():
                print(f"error: model file not found: {model}", file=sys.stderr)
                return 2
            if not model.lower().endswith(".xlsx"):
                print(f"error: model must be a .xlsx file: {model}", file=sys.stderr)
                return 2
        if args.model_old and not Path(args.model_old).is_file():
            print(f"error: old model file not found: {args.model_old}", file=sys.stderr)
            return 2

        from numcheck.runner import run_review

        try:
            result = run_review(args.deck, args.models, args.model_old, args.out_dir, args.annotate, cfg, args.ocr)
        except Exception as exc:  # noqa: BLE001 - surface a friendly CLI error
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _print_summary(result)
        return 0

    if args.command == "demo":
        from numcheck.demo import build_demo

        build_demo(args.out_dir)
        return 0

    if args.command == "init-config":
        _write_default_config(args.out)
        return 0

    if args.command == "web":
        from numcheck.web.app import create_app

        app = create_app()
        print(f"NumCheck web UI → http://{args.host}:{args.port}")
        app.run(host=args.host, port=args.port, debug=False)
        return 0

    return 1


def _print_summary(result: dict) -> None:
    issues = result["issues"]
    s = summarize(issues)
    print(f"NumCheck review complete in {result['elapsed']:.2f}s")
    print(f"  deck numbers: {result['deck_numbers']}, model values: {result['model_values']}")
    print(f"  issues: {s['total']}  (high={s['by_severity'].get('high', 0)}, "
          f"medium={s['by_severity'].get('medium', 0)}, low={s['by_severity'].get('low', 0)})")
    print(f"  by type: {s['by_type']}")


def _write_default_config(path: str) -> None:
    import shutil
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent / "numcheck.toml"
    if here.exists():
        shutil.copyfile(here, path)
    else:
        Path(path).write_text("[tolerance]\nrelative = 0.006\nabsolute = 0.5\n\n[matching]\nllm = \"off\"\n", encoding="utf-8")
    print(f"Wrote {path}")
