"""Flask web app: a local, hardened front end for NumCheck.

Reviews run as asynchronous jobs (``POST /api/jobs`` then poll ``GET /api/jobs/<id>``).
Uploads are saved under fixed safe names inside a per-job temp directory; only a
whitelist of generated files can be downloaded; temp data is purged after a TTL,
on demand, and at shutdown. No API key is ever logged or returned.
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from numcheck import __version__
from numcheck.config import Config, load_config
from numcheck.demo import ASSET_DIR, DEMO_FILES, build_demo
from numcheck.extraction import fileio
from numcheck.llm.factory import PROVIDERS, default_model, resolve_ambiguous
from numcheck.models import NumCheckInputError, ReviewResult, ReviewWarning
from numcheck.report import MATCH_ROW_CAP, result_to_dict
from numcheck.web.evidence import PreviewUnavailable, slide_preview, workbook_preview

log = logging.getLogger(__name__)

DOWNLOADS = {
    "report.html": ("html", "HTML report"),
    "report.xlsx": ("xlsx", "Excel report"),
    "report.json": ("json", "JSON"),
    "deck_flagged.pptx": ("deck", "Annotated deck"),
}
DEFAULT_MAX_UPLOAD_MB = 250
DEFAULT_TTL_SECONDS = 2 * 60 * 60
ROUNDING_MODES = ("round", "round_or_truncate")
DECK_EXTS = (".pptx", ".pptm")
MODEL_EXTS = (".xlsx", ".xlsm")
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
GENERIC_ERROR = "The review failed unexpectedly. Details were written to the server log."
PROVIDER_LABELS = {
    "off": "Off (deterministic only)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "openai-compatible": "OpenAI-compatible endpoint",
    "ollama": "Ollama (local)",
    "codex": "ChatGPT via Codex",
    "openrouter": "OpenRouter",
    "jev": "Jev via Vercel AI Gateway (experimental)",
}
LOCAL_PROVIDERS = ("off", "ollama")


class RequestError(Exception):
    """Validation problem reported to the client as JSON."""

    def __init__(self, message: str, status: int = 400, hint: str = "", file: str = ""):
        super().__init__(message)
        self.message, self.status, self.hint, self.file = message, status, hint, file


@dataclass
class Job:
    id: str
    dir: Path
    settings: dict[str, Any]
    names: dict[str, Any]
    paths: dict[str, Any]
    demo: bool = False
    status: str = "queued"           # queued | running | done | error
    stage: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    error: dict[str, Any] | None = None
    result: ReviewResult | None = None
    payload: dict[str, Any] | None = None
    created: float = field(default_factory=time.time)
    finished: float = 0.0
    last_access: float = field(default_factory=time.time)
    future: Future | None = None
    cfg: Config | None = None
    slide_previews: dict[tuple, dict] = field(default_factory=dict, repr=False)
    preview_lock: Any = field(default_factory=threading.Lock, repr=False)


class JobManager:
    """Thread-safe registry plus executor for review jobs."""

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS, workers: int = 2):
        self.root = Path(tempfile.mkdtemp(prefix="numcheck_jobs_"))
        self.ttl = ttl
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="numcheck-job")
        atexit.register(self.shutdown)

    # -- registry ---------------------------------------------------------- #
    def new_dir(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex
        d = self.root / job_id
        d.mkdir(parents=True)
        return job_id, d

    def add(self, job: Job) -> None:
        with self.lock:
            self.jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job:
                job.last_access = time.time()
            return job

    def update(self, job: Job, **fields: Any) -> None:
        with self.lock:
            for k, v in fields.items():
                setattr(job, k, v)

    def purge(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.pop(job_id, None)
        if job is None:
            return False
        shutil.rmtree(job.dir, ignore_errors=True)
        return True

    def cleanup(self) -> None:
        now = time.time()
        with self.lock:
            stale = [j.id for j in self.jobs.values()
                     if j.status in ("done", "error") and now - j.last_access >= self.ttl]
        for job_id in stale:
            self.purge(job_id)

    def shutdown(self) -> None:
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.root, ignore_errors=True)

    # -- execution --------------------------------------------------------- #
    def submit(self, job: Job) -> None:
        job.future = self.executor.submit(self.execute, job)

    def execute(self, job: Job) -> None:
        """Run the review for *job* (called from a worker thread or inline)."""
        self.update(job, status="running", stage="validate", progress=0.02, message="Validating files")

        def progress(stage: str, fraction: float, message: str = "") -> None:
            self.update(job, stage=stage, progress=max(0.0, min(float(fraction), 1.0)), message=message or stage)

        try:
            from numcheck.runner import run_review  # lazy: tests monkeypatch it

            paths = dict(job.paths)
            if job.demo:
                built = build_demo(job.dir / "demo")
                paths = {"deck": built["deck"], "models": [built["model"]], "model_old": built.get("model_old")}
                self.update(job, paths=paths, names={"deck": Path(built["deck"]).name,
                                        "models": [Path(built["model"]).name],
                                        "model_old": Path(built["model_old"]).name})
            result = run_review(
                paths["deck"], list(paths["models"]), paths.get("model_old"),
                out_dir=str(job.dir / "report"), annotate=bool(job.settings.get("annotate", True)),
                config=job.cfg, ocr=bool(job.settings.get("ocr", False)), progress=progress,
                input_names=getattr(job, "names", None) or None,
            )
            payload = build_payload(job, result)
            self.update(job, result=result, payload=payload, status="done", stage="done", progress=1.0,
                        message="Review complete", finished=time.time())
        except NumCheckInputError as exc:
            self.update(job, status="error", finished=time.time(), message=str(exc),
                        error={"message": str(exc), "hint": exc.hint, "file": exc.file})
        except Exception:  # noqa: BLE001 - never leak internals to the client
            log.exception("review job %s failed", job.id)
            self.update(job, status="error", finished=time.time(), message=GENERIC_ERROR,
                        error={"message": GENERIC_ERROR, "hint": "", "file": ""})
        finally:
            if job.cfg is not None:
                job.cfg.llm_api_key = ""


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #

def _safe_name(name: str) -> str:
    return Path((name or "").replace("\\", "/")).name[:200]


def _download_path(job: Job, name: str) -> Path | None:
    """Resolve a whitelisted download to a file inside the job directory."""
    if name not in DOWNLOADS:
        return None
    candidate = job.dir / "report" / name
    if candidate.is_file():
        return candidate
    key = DOWNLOADS[name][0]
    recorded = (job.result.files if job.result else {}).get(key)
    if recorded:
        p = Path(recorded)
        try:
            if p.name == name and p.is_file() and job.dir.resolve() in p.resolve().parents:
                return p
        except OSError:
            return None
    return None


def build_payload(job: Job, result: ReviewResult) -> dict[str, Any]:
    data = result_to_dict(result, match_cap=MATCH_ROW_CAP)
    data["job_id"] = job.id
    data["inputs"] = {**data.get("inputs", {}), **{k: v for k, v in job.names.items() if v}}
    data["settings"] = {**job.settings, **data.get("settings", {})}
    data["downloads"] = [
        {"name": n, "label": label, "url": f"/api/jobs/{job.id}/download/{n}"}
        for n, (_k, label) in DOWNLOADS.items() if _download_path(_ResultHolder(job, result), n)
    ]
    if job.demo:
        data["originals"] = [
            {"name": name, "label": label, "url": f"/api/demo/files/{name}"}
            for name, label in DEMO_FILES.values()
        ]
    return data


class _ResultHolder:
    """Lets _download_path see a not-yet-stored result."""

    def __init__(self, job: Job, result: ReviewResult):
        self.dir, self.result = job.dir, result


# --------------------------------------------------------------------------- #
# Request parsing
# --------------------------------------------------------------------------- #

def _truthy(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "on", "yes")


def _load_cfg(config_path: str | None) -> Config:
    try:
        return load_config(config_path)
    except ValueError as exc:
        log.warning("config could not be read: %s", exc)
        raise RequestError("The configuration file could not be read.", 422,
                           hint="Check numcheck.toml for syntax errors.") from exc


def apply_provider_overrides(cfg: Config, form) -> None:
    provider = (form.get("llm") or cfg.llm or "off").strip()
    if provider not in PROVIDERS:
        raise RequestError("Choose a supported label matching provider.", 422)
    if provider != cfg.llm:
        cfg.model_name = cfg.llm_api_key = ""
        if provider != "openai-compatible":
            cfg.llm_base_url = ""
    cfg.llm = provider
    if provider == "off":
        return
    model = (form.get("llm_model") or "").strip()
    api_key = (form.get("llm_api_key") or "").strip()
    redactions = (form.get("llm_redact") or "").strip()
    if len(model) > 200 or len(api_key) > 4096 or len(redactions) > 2000:
        raise RequestError("One of the label matching settings is too long.", 422)
    if model:
        cfg.model_name = model
    if api_key and provider in ("openai", "anthropic", "openrouter", "openai-compatible", "jev"):
        cfg.llm_api_key = api_key
    if redactions:
        cfg.llm_redact.extend(term.strip() for term in redactions.splitlines() if term.strip())
    if provider in ("openrouter", "openai-compatible") and not cfg.model_name:
        raise RequestError("Enter the model ID shown in your provider account.", 422)
    info = next(p for p in provider_info(cfg) if p["id"] == provider)
    if not info["available"]:
        raise RequestError(info["reason"], 422)


def apply_form_overrides(cfg: Config, form) -> dict[str, Any]:
    """Validate form fields, apply them to *cfg*, and return the effective settings."""
    apply_provider_overrides(cfg, form)
    llm = cfg.llm
    raw_tol = (form.get("tolerance") or "").strip()
    tol = cfg.relative_tolerance
    if raw_tol:
        try:
            tol = float(raw_tol)
        except ValueError as exc:
            raise RequestError("Tolerance must be a number.", 422, hint="Use a fraction such as 0.005.") from exc
        if not 0 <= tol <= 0.1:
            raise RequestError("Tolerance must be between 0 and 0.1.", 422, hint="0.1 equals 10 percent.")
    mode = (form.get("rounding_mode") or cfg.rounding_mode).strip()
    if mode not in ROUNDING_MODES:
        raise RequestError(f"Unknown rounding mode '{mode}'.", 422, hint="Use round or round_or_truncate.")
    base = form.get("base_period")
    base = cfg.base_period if base is None else base.strip()
    if len(base) > 12:
        raise RequestError("Base period is limited to 12 characters.", 422, hint="For example FY24 or LTM.")
    annotate = _truthy(form.get("annotate"), True)
    ocr = _truthy(form.get("ocr"), cfg.ocr)

    cfg.llm, cfg.relative_tolerance, cfg.rounding_mode = llm, tol, mode
    cfg.base_period, cfg.ocr = base.lower(), ocr
    cfg.require_label_evidence = _truthy(form.get("require_label_evidence"), cfg.require_label_evidence)
    return {"llm": llm, "tolerance": tol, "rounding_mode": mode, "base_period": base,
            "annotate": annotate, "ocr": ocr, "require_label_evidence": cfg.require_label_evidence,
            "llm_model": default_model(llm, cfg)}


def _save_upload(storage, dest: Path, kind: str, exts: tuple[str, ...], max_mb: float) -> str:
    """Validate extension and content, save under *dest*; returns the display name."""
    display = _safe_name(storage.filename or "")
    if not display.lower().endswith(exts):
        raise RequestError(
            f"{display or 'File'} is not a supported file type.", 415, file=display,
            hint="Upload " + " or ".join(exts) + (" (PowerPoint)." if kind == "pptx" else " (Excel)."))
    storage.save(dest)
    try:
        fileio.sniff_office_file(dest, kind, max_mb=max_mb)
    except NumCheckInputError as exc:
        dest.unlink(missing_ok=True)
        raise RequestError(str(exc), 422, hint=exc.hint, file=display or exc.file) from exc
    return display


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


def create_job_from_request(mgr: JobManager, config_path: str | None, demo: bool = False) -> Job:
    cfg = _load_cfg(config_path)
    job_id, job_dir = mgr.new_dir()
    try:
        if demo:
            settings = {"llm": "off", "tolerance": cfg.relative_tolerance, "rounding_mode": cfg.rounding_mode,
                        "base_period": cfg.base_period, "annotate": True, "ocr": False, "require_label_evidence": True}
            cfg.require_label_evidence = True
            cfg.llm, cfg.ocr = "off", False
            job = Job(job_id, job_dir, settings, {}, {}, demo=True, cfg=cfg)
        else:
            settings = apply_form_overrides(cfg, request.form)
            deck = request.files.get("deck")
            models = [m for m in request.files.getlist("models") if m and m.filename]
            if not deck or not deck.filename:
                raise RequestError("A deck (.pptx) is required.", 400, hint="Add the pitchbook to the Deck slot.")
            if not models:
                raise RequestError("At least one model (.xlsx) is required.", 400,
                                   hint="Add the Excel model to the Models slot.")
            mb = cfg.max_file_mb or DEFAULT_MAX_UPLOAD_MB
            names: dict[str, Any] = {"models": []}
            paths: dict[str, Any] = {"models": []}
            deck_path = job_dir / f"deck{_suffix(deck.filename) if _suffix(deck.filename) in DECK_EXTS else '.pptx'}"
            names["deck"] = _save_upload(deck, deck_path, "pptx", DECK_EXTS, mb)
            paths["deck"] = str(deck_path)
            for idx, m in enumerate(models):
                p = job_dir / f"model_{idx}{_suffix(m.filename) if _suffix(m.filename) in MODEL_EXTS else '.xlsx'}"
                names["models"].append(_save_upload(m, p, "xlsx", MODEL_EXTS, mb))
                paths["models"].append(str(p))
            old = request.files.get("model_old")
            if old and old.filename:
                p = job_dir / f"model_old{_suffix(old.filename) if _suffix(old.filename) in MODEL_EXTS else '.xlsx'}"
                names["model_old"] = _save_upload(old, p, "xlsx", MODEL_EXTS, mb)
                paths["model_old"] = str(p)
            job = Job(job_id, job_dir, settings, names, paths, cfg=cfg)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    mgr.add(job)
    return job


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #

def _ocr_available() -> bool:
    if not fileio.soffice_binary():
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("pytesseract") is not None
    except (ImportError, ValueError):
        return False


def provider_info(cfg: Config) -> list[dict[str, Any]]:
    env = os.environ
    codex = env.get("NUMCHECK_CODEX_BIN") or shutil.which("codex")
    availability = {
        "off": True,
        "jev": bool(env.get("AI_GATEWAY_API_KEY")),
        "openai": bool(env.get("OPENAI_API_KEY")),
        "anthropic": bool(env.get("ANTHROPIC_API_KEY")),
        "openrouter": bool(env.get("OPENROUTER_API_KEY")),
        "openai-compatible": bool(cfg.llm_base_url),
        "ollama": True,  # Test the service, since it can run without a CLI on PATH.
        "codex": bool(codex and Path(codex).is_file()),
    }
    reasons = {
        "jev": "Enter a Vercel AI Gateway API key.",
        "openai": "Enter an OpenAI API key.",
        "anthropic": "Enter an Anthropic API key.",
        "openrouter": "Enter an OpenRouter API key.",
        "openai-compatible": "Set NUMCHECK_LLM_BASE_URL on the NumCheck server.",
        "codex": "Install the Codex CLI and add it to PATH, then run codex login.",
    }
    if cfg.llm_api_key and cfg.llm in ("openai", "anthropic", "openrouter", "jev"):
        availability[cfg.llm] = True
    return [
        {"id": p, "label": PROVIDER_LABELS[p], "available": availability[p],
         "reason": "" if availability[p] else reasons[p],
         "model": default_model(p, cfg if cfg.llm == p else Config()),
         "external": p not in LOCAL_PROVIDERS or (p == "ollama" and cfg.llm == p and bool(cfg.llm_base_url))}
        for p in PROVIDERS
    ]


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #

def create_app(config_path: str | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    try:
        max_mb = float(os.environ.get("NUMCHECK_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB))
    except ValueError:
        max_mb = DEFAULT_MAX_UPLOAD_MB
    app.config["MAX_CONTENT_LENGTH"] = int(max_mb * 1024 * 1024)
    app.config["JOB_TTL_SECONDS"] = DEFAULT_TTL_SECONDS
    mgr = JobManager(ttl=DEFAULT_TTL_SECONDS)
    app.extensions["numcheck_jobs"] = mgr

    def error(message: str, status: int, hint: str = "", file: str = ""):
        body: dict[str, Any] = {"error": message}
        if hint:
            body["hint"] = hint
        if file:
            body["file"] = file
        return jsonify(body), status

    # -- hooks ------------------------------------------------------------- #
    @app.before_request
    def guard():
        mgr.ttl = float(app.config["JOB_TTL_SECONDS"])
        mgr.cleanup()
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and \
                request.headers.get("X-Requested-With") != "numcheck":
            return error("Missing X-Requested-With header.", 403,
                         hint="State-changing requests must come from the NumCheck UI.")
        return None

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        elif request.path.startswith("/static") or request.path == "/":
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.errorhandler(RequestError)
    def handle_request_error(exc: RequestError):
        return error(exc.message, exc.status, exc.hint, exc.file)

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        messages = {
            400: "The request could not be understood.",
            404: "Not found.",
            405: "Method not allowed.",
            413: f"Upload too large. The limit is {app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)} MB.",
            415: "Unsupported media type.",
        }
        return error(messages.get(exc.code or 500, exc.name), exc.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        log.exception("unhandled error: %s", type(exc).__name__)
        return error(GENERIC_ERROR, 500)

    # -- pages & meta ------------------------------------------------------ #
    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "version": __version__})

    @app.get("/api/meta")
    def meta():
        try:
            cfg = load_config(config_path)
        except ValueError:
            cfg = Config()
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return jsonify({
            "version": __version__,
            "slide_preview": bool(fileio.soffice_binary()),
            "providers": provider_info(cfg),
            "ocr": {"available": _ocr_available()},
            "limits": {"max_upload_mb": mb, "tolerance_max": 0.1, "base_period_max_chars": 12},
            "config_file": Path(cfg.source_path).name if cfg.source_path else None,
            "defaults": {"llm": cfg.llm if cfg.llm in PROVIDERS else "off",
                         "tolerance": cfg.relative_tolerance, "rounding_mode": cfg.rounding_mode,
                         "base_period": cfg.base_period, "annotate": True, "ocr": False,
                         "require_label_evidence": True},
        })

    @app.post("/api/providers/test")
    def test_provider():
        cfg = _load_cfg(config_path)
        apply_provider_overrides(cfg, request.form)
        if cfg.llm == "off":
            raise RequestError("Select a label matching provider to test.", 422)
        sample = {
            "id": "0", "deck_label": "Adjusted EBITDA", "deck_period": "FY24",
            "candidates": [
                {"ref": "A", "row_label": "Reported EBITDA", "period": "FY24"},
                {"ref": "B", "row_label": "Adjusted EBITDA", "period": "FY24"},
            ],
        }
        warnings: list[ReviewWarning] = []
        result = resolve_ambiguous([sample], cfg, warnings)
        if warnings:
            raise RequestError(warnings[0].message, 422)
        if result.get("0") != "B":
            raise RequestError("The provider replied but did not select the expected sample label.", 422)
        return jsonify({"ok": True})

    # -- jobs -------------------------------------------------------------- #
    def find_job(job_id: str) -> Job:
        job = mgr.get(job_id)
        if job is None:
            raise RequestError("Job not found. It may have expired.", 404)
        return job

    @app.post("/api/jobs")
    def create_job():
        job = create_job_from_request(mgr, config_path)
        mgr.submit(job)
        return jsonify({"job_id": job.id}), 202

    @app.get("/api/demo/files/<name>")
    def demo_file(name: str):
        if not any(name == filename for filename, _label in DEMO_FILES.values()):
            raise RequestError("Not found.", 404)
        return send_from_directory(ASSET_DIR, name, as_attachment=True)

    @app.post("/api/demo")
    def create_demo():
        job = create_job_from_request(mgr, config_path, demo=True)
        mgr.submit(job)
        return jsonify({"job_id": job.id}), 202

    @app.post("/api/review")
    def review_sync():
        job = create_job_from_request(mgr, config_path)
        mgr.execute(job)
        if job.status == "error" and job.error:
            status = 422 if job.error.get("file") or job.error.get("hint") else 500
            return error(job.error["message"], status, job.error.get("hint", ""), job.error.get("file", ""))
        payload = dict(job.payload or {})
        payload["run_id"] = job.id
        return jsonify(payload)

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        job = find_job(job_id)
        return jsonify({"status": job.status, "stage": job.stage, "progress": round(job.progress, 3),
                        "message": job.message, "error": job.error})

    @app.get("/api/jobs/<job_id>/result")
    def job_result(job_id: str):
        job = find_job(job_id)
        if job.status != "done" or job.payload is None:
            return jsonify({"error": "The review has not finished.", "status": job.status}), 409
        return jsonify(job.payload)

    def get_evidence(job):
        if job.result is None or job.status != "done":
            raise RequestError("The review has not finished.", 409)
        issue_id = request.args.get("issue", type=int)
        figure = request.args.get("figure")
        issue = next((i for i in job.result.issues if i.id == issue_id), None) if issue_id is not None else None
        uid = issue.deck_uid if issue is not None else figure
        match = next((m for m in job.result.matches if uid and m.deck.uid == uid), None)
        if issue is None and match is None:
            raise RequestError("This figure is unavailable.", 404)
        return issue, match

    def originals(job):
        yield "deck", Path(job.paths["deck"]), job.names["deck"]
        for index, (path, name) in enumerate(zip(job.paths["models"], job.names["models"])):
            yield f"model-{index}", Path(path), name
        if job.paths.get("model_old"):
            yield "prior", Path(job.paths["model_old"]), job.names["model_old"]

    @app.get("/api/jobs/<job_id>/inputs")
    def review_inputs(job_id: str):
        job = find_job(job_id)
        if job.status != "done":
            raise RequestError("The review has not finished.", 409)
        files = []
        for slot, path, name in originals(job):
            try:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            except FileNotFoundError as exc:
                raise RequestError("An original file has expired.", 404) from exc
            files.append({"slot": slot, "name": name, "sha256": digest})
        return jsonify({"files": files, "version": __version__})

    @app.get("/api/jobs/<job_id>/original/<slot>")
    def original_document(job_id: str, slot: str):
        job = find_job(job_id)
        if job.status == "done":
            for key, path, name in originals(job):
                if key == slot:
                    return send_from_directory(path.parent, path.name, as_attachment=True, download_name=name)
        raise RequestError("The original file is unavailable.", 404)

    @app.get("/api/jobs/<job_id>/evidence/slide")
    def evidence_slide(job_id: str):
        job = find_job(job_id)
        issue, match = get_evidence(job)
        location = match.deck if match is not None else issue
        token = match.deck.raw_text if match is not None else issue.deck_value
        result = {"slide": location.slide_number, "download": f"/api/jobs/{job.id}/original/deck"}
        try:
            with job.preview_lock:
                if not job.dir.is_dir():
                    raise PreviewUnavailable("The review has expired.")
                exact_text = match is not None and match.deck.source.value in ("text_box", "table")
                key = (location.slide_number, location.shape_id, location.table_row, location.table_col,
                       token if exact_text else None)
                preview = job.slide_previews.pop(key, None)
                if preview is None:
                    preview = slide_preview(Path(job.paths["deck"]), job.dir / "preview", location, token,
                                            exact_text=exact_text)
                job.slide_previews[key] = preview
                if len(job.slide_previews) > 12:
                    job.slide_previews.pop(next(iter(job.slide_previews)))
                result.update(preview)
        except PreviewUnavailable as exc:
            result["message"] = str(exc)
        except Exception:  # Preview failure must leave the original download accessible.
            log.exception("Slide preview failed for job %s", job.id)
            result["message"] = "This slide could not be rendered. Open the original PowerPoint."
        return jsonify(result)

    @app.get("/api/jobs/<job_id>/evidence/workbook")
    def evidence_workbook(job_id: str):
        job = find_job(job_id)
        issue, match = get_evidence(job)
        source = issue.source_cell if issue is not None else (match.model.ref if match.model else "")
        options = []
        if match is not None:
            options = list({m.ref: m for m in [match.model, *match.candidates, match.old_model] if m is not None}.values())
        selection = request.args.get("cell")
        selected = next((m for m in options if m.ref == (selection or source)), None)
        workbooks = {path.name: (slot, path, name) for slot, path, name in originals(job) if slot != "deck"}
        result = {"sources": [
            {"ref": m.ref, "label": f"{workbooks[m.workbook][2]} · {m.sheet_name}!{m.cell_address}"
             if m.workbook in workbooks else m.ref,
             "prior": m is match.old_model, "selected": m is selected} for m in options
        ]}
        if selected is None:
            result["message"] = "Choose a candidate cell to inspect." if options else "This finding has no linked workbook cell."
            return jsonify(result)
        workbook = workbooks.get(selected.workbook)
        if workbook is None:
            result["message"] = "The workbook could not be mapped to an original upload."
            return jsonify(result)
        slot, path, name = workbook
        result.update({"file": name, "slot": slot, "download": f"/api/jobs/{job.id}/original/{slot}"})
        try:
            result.update(workbook_preview(path, selected, top=request.args.get("row", type=int),
                                           left=request.args.get("col", type=int)))
        except PreviewUnavailable as exc:
            result["message"] = str(exc)
        except Exception:
            log.exception("Workbook preview failed for job %s", job.id)
            result["message"] = "This range could not be read. Open the original workbook."
        return jsonify(result)

    @app.get("/api/jobs/<job_id>/download/<name>")
    def job_download(job_id: str, name: str):
        job = find_job(job_id)
        path = _download_path(job, name) if name in DOWNLOADS else None
        if path is None:
            raise RequestError("Not found.", 404)
        stem = Path(job.names.get("deck") or "deck").stem
        suffix = Path(name).suffix
        download_name = f"{stem} - NumCheck{' flagged' if name.startswith('deck') else ''}{suffix}"
        return send_from_directory(path.parent, path.name, as_attachment=True, download_name=download_name)

    @app.delete("/api/jobs/<job_id>")
    def job_delete(job_id: str):
        job = find_job(job_id)
        if job.status == "running":
            raise RequestError("The review is still running.", 409)
        if job.future is not None:
            job.future.cancel()
        mgr.purge(job_id)
        return jsonify({"ok": True})

    return app
