"""Flask web app: a local, easy-to-use front end for NumCheck."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from numcheck.config import load_config
from numcheck.report import summarize


def _serialize_issues(issues) -> list[dict]:
    return [
        {
            "id": i.id,
            "slide_number": i.slide_number,
            "deck_value": i.deck_value,
            "expected_value": i.expected_value,
            "source_cell": i.source_cell,
            "issue_type": i.issue_type.value,
            "severity": i.severity.value,
            "message": i.message,
            "metric_key": i.metric_key,
            "shape_id": i.shape_id,
        }
        for i in issues
    ]


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024
    app.config["RUNS"] = {}
    app.config["RUN_ROOT"] = tempfile.mkdtemp(prefix="numcheck_runs_")

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.post("/api/review")
    def api_review():
        deck = request.files.get("deck")
        models = request.files.getlist("models")
        if not deck or not deck.filename or not models:
            return jsonify({"error": "A deck and at least one model are required."}), 400

        run_id = uuid.uuid4().hex
        out_dir = Path(app.config["RUN_ROOT"]) / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        deck_path = out_dir / "deck.pptx"
        deck.save(deck_path)

        model_paths: list[str] = []
        for idx, model in enumerate(models):
            if not model.filename:
                continue
            path = out_dir / f"model_{idx}.xlsx"
            model.save(path)
            model_paths.append(str(path))

        old_path = None
        model_old = request.files.get("model_old")
        if model_old and model_old.filename:
            old_path = out_dir / "model_old.xlsx"
            model_old.save(old_path)
            old_path = str(old_path)

        cfg = load_config(None)
        cfg.llm = request.form.get("llm", "off")
        if request.form.get("tolerance"):
            try:
                cfg.relative_tolerance = float(request.form["tolerance"])
            except ValueError:
                pass
        annotate = request.form.get("annotate", "1") == "1"

        from numcheck.runner import run_review

        result = run_review(str(deck_path), model_paths, old_path, str(out_dir / "report"), annotate, cfg)

        app.config["RUNS"][run_id] = out_dir
        return jsonify({
            "run_id": run_id,
            "summary": summarize(result["issues"]),
            "issues": _serialize_issues(result["issues"]),
            "elapsed": result["elapsed"],
            "deck_numbers": result["deck_numbers"],
            "model_values": result["model_values"],
        })

    @app.post("/api/demo")
    def api_demo():
        run_id = uuid.uuid4().hex
        out_dir = Path(app.config["RUN_ROOT"]) / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        from numcheck.demo import build_demo

        paths = build_demo(out_dir / "demo")
        cfg = load_config(None)
        cfg.llm = "off"

        from numcheck.runner import run_review

        result = run_review(
            paths["deck"],
            [paths["model"]],
            paths["model_old"],
            str(out_dir / "report"),
            annotate=True,
            config=cfg,
        )

        app.config["RUNS"][run_id] = out_dir
        return jsonify({
            "run_id": run_id,
            "summary": summarize(result["issues"]),
            "issues": _serialize_issues(result["issues"]),
            "elapsed": result["elapsed"],
            "deck_numbers": result["deck_numbers"],
            "model_values": result["model_values"],
        })

    @app.get("/api/download/<run_id>/<filename>")
    def api_download(run_id: str, filename: str):
        out_dir = app.config["RUNS"].get(run_id)
        if not out_dir:
            abort(404)
        # Guard against path traversal.
        if "/" in filename or ".." in filename:
            abort(404)
        path = out_dir / "report" / filename
        if not path.exists():
            abort(404)
        return send_file(path, as_attachment=True)

    return app
