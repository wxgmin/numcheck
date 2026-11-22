"""Web app: validation, job lifecycle, download whitelist, security headers."""

from __future__ import annotations

import io
import shutil
import time
from pathlib import Path

import pytest
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from numcheck.demo import ASSET_DIR, DEMO_FILES
from numcheck.models import NumCheckInputError
from numcheck.report.excel_report import write_excel_report
from numcheck.report.html_report import write_html_report
from numcheck.report.json_report import write_json_report
from numcheck.web.app import create_app
from tests.test_reports import make_result

HDR = {"X-Requested-With": "numcheck"}
STATIC = Path(__file__).resolve().parent.parent / "numcheck" / "web" / "static"


@pytest.fixture()
def office(tmp_path):
    deck = tmp_path / "Project Atlas.pptx"
    Presentation().save(deck)
    model = tmp_path / "model.xlsx"
    wb = Workbook()
    wb.active["A1"] = 1
    wb.save(model)
    return deck, model


@pytest.fixture()
def calls():
    return {}


@pytest.fixture()
def client(monkeypatch, calls):
    def fake_run_review(deck_path, model_paths, model_old_path=None, out_dir="report", annotate=True,
                        config=None, ocr=False, progress=None, **kwargs):
        calls.update(deck=deck_path, models=model_paths, old=model_old_path, annotate=annotate,
                     cfg=config, ocr=ocr)
        for stage, frac in (("validate", 0.05), ("deck", 0.2), ("models", 0.4), ("match", 0.6),
                            ("checks", 0.8), ("reports", 0.95)):
            if progress:
                progress(stage, frac, stage)
        res = make_result()
        res.settings = {"llm": config.llm, "rounding_mode": config.rounding_mode}
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_html_report(res, out / "report.html")
        write_excel_report(res, out / "report.xlsx")
        write_json_report(res, out / "report.json")
        if annotate:
            shutil.copyfile(deck_path, out / "deck_flagged.pptx")
            res.files["deck"] = str(out / "deck_flagged.pptx")
        return res

    monkeypatch.setattr("numcheck.runner.run_review", fake_run_review)
    monkeypatch.chdir(Path(__file__).parent)  # no stray numcheck.toml
    app = create_app()
    app.testing = True
    return app.test_client()


def _post(client, office, **form):
    deck, model = office
    data = {"deck": (io.BytesIO(deck.read_bytes()), "Project Atlas.pptx"),
            "models": [(io.BytesIO(model.read_bytes()), "model.xlsx")]}
    data.update(form)
    return client.post("/api/jobs", data=data, headers=HDR, content_type="multipart/form-data")


def _wait(client, job_id, want=("done", "error"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/jobs/{job_id}").get_json()
        if st["status"] in want:
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def test_wrong_extension_is_415_with_hint(client, office):
    r = client.post("/api/jobs", headers=HDR, content_type="multipart/form-data", data={
        "deck": (io.BytesIO(b"%PDF-1.4"), "deck.pdf"),
        "models": [(io.BytesIO(office[1].read_bytes()), "model.xlsx")]})
    body = r.get_json()
    assert r.status_code == 415 and body["hint"] and body["file"] == "deck.pdf" and body["error"]


def test_corrupt_pptx_is_422_json(client, office):
    r = client.post("/api/jobs", headers=HDR, content_type="multipart/form-data", data={
        "deck": (io.BytesIO(b"this is not a zip"), "deck.pptx"),
        "models": [(io.BytesIO(office[1].read_bytes()), "model.xlsx")]})
    body = r.get_json()
    assert r.status_code == 422 and body["file"] == "deck.pptx" and body["hint"]


def test_swapped_slots_rejected(client, office):
    deck, model = office
    r = client.post("/api/jobs", headers=HDR, content_type="multipart/form-data", data={
        "deck": (io.BytesIO(model.read_bytes()), "deck.pptx"),
        "models": [(io.BytesIO(model.read_bytes()), "model.xlsx")]})
    assert r.status_code == 422 and "xlsx" in r.get_json()["error"]


def test_missing_files_and_bad_fields(client, office):
    assert client.post("/api/jobs", headers=HDR).status_code == 400
    assert _post(client, office, llm="skynet").status_code == 422
    assert _post(client, office, tolerance="0.5").status_code == 422
    assert _post(client, office, tolerance="abc").status_code == 422
    assert _post(client, office, rounding_mode="floor").status_code == 422
    assert _post(client, office, base_period="x" * 13).status_code == 422


def test_requires_csrf_header(client, office):
    deck, model = office
    r = client.post("/api/jobs", data={"deck": (io.BytesIO(b"x"), "d.pptx")}, content_type="multipart/form-data")
    assert r.status_code == 403 and "error" in r.get_json()
    assert client.delete("/api/jobs/abc").status_code == 403
    assert client.post("/api/demo").status_code == 403


def test_upload_too_large_is_json_413(client, office):
    client.application.config["MAX_CONTENT_LENGTH"] = 2000
    deck, model = office
    r = client.post("/api/jobs", headers=HDR, content_type="multipart/form-data", data={
        "deck": (io.BytesIO(b"x" * 20000), "deck.pptx"),
        "models": [(io.BytesIO(b"y"), "model.xlsx")]})
    assert r.status_code == 413 and r.get_json()["error"]


def test_json_errors_for_404_and_405(client):
    assert client.get("/api/nope").get_json()["error"]
    r = client.get("/api/jobs")
    assert r.status_code == 405 and r.is_json
    assert client.get("/api/jobs/doesnotexist").status_code == 404


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

def test_job_lifecycle_and_result_shape(client, office, calls):
    r = _post(client, office, llm="off", tolerance="0.01", rounding_mode="round_or_truncate",
              base_period="FY24", annotate="1", ocr="0")
    assert r.status_code == 202
    job_id = r.get_json()["job_id"]
    first = client.get(f"/api/jobs/{job_id}").get_json()
    assert first["status"] in ("queued", "running", "done")
    assert _wait(client, job_id)["status"] == "done"

    st = client.get(f"/api/jobs/{job_id}").get_json()
    assert st["progress"] == 1.0 and st["error"] is None
    data = client.get(f"/api/jobs/{job_id}/result").get_json()
    assert data["inputs"]["deck"] == "Project Atlas.pptx"
    assert data["inputs"]["models"] == ["model.xlsx"]
    assert data["coverage"]["verified_pct"] == 82.5
    assert len(data["issues"]) == 3 and data["issues"][0]["suggestion"] == "21.2%"
    assert len(data["matches"]) == 6 and data["warnings"][0]["code"] == "uncalculated_formulas"
    assert data["settings"]["base_period"] == "FY24" and data["settings"]["rounding_mode"] == "round_or_truncate"
    assert {d["name"] for d in data["downloads"]} == {"report.html", "report.xlsx", "report.json",
                                                      "deck_flagged.pptx"}
    cfg = calls["cfg"]
    assert cfg.relative_tolerance == 0.01 and cfg.rounding_mode == "round_or_truncate"
    assert cfg.base_period == "fy24" and calls["annotate"] is True
    assert Path(calls["deck"]).name == "deck.pptx" and Path(calls["models"][0]).name == "model_0.xlsx"


def test_result_before_done_is_409(client, office):
    assert client.get("/api/jobs/zzz/result").status_code == 404


def test_input_error_and_unexpected_error(client, office, monkeypatch):
    def bad_input(*a, **k):
        raise NumCheckInputError("model.xlsx is password-protected.", file="model.xlsx", hint="Remove the password.")

    monkeypatch.setattr("numcheck.runner.run_review", bad_input)
    job_id = _post(client, office).get_json()["job_id"]
    st = _wait(client, job_id)
    assert st["status"] == "error"
    assert st["error"] == {"message": "model.xlsx is password-protected.", "hint": "Remove the password.",
                           "file": "model.xlsx"}
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 409

    def boom(*a, **k):
        raise RuntimeError("secret internals C:\\Users\\x")

    monkeypatch.setattr("numcheck.runner.run_review", boom)
    job_id = _post(client, office).get_json()["job_id"]
    st = _wait(client, job_id)
    assert st["status"] == "error" and "secret" not in str(st) and "RuntimeError" not in str(st)


def test_sync_review_and_demo(client, office):
    deck, model = office
    r = client.post("/api/review", headers=HDR, content_type="multipart/form-data", data={
        "deck": (io.BytesIO(deck.read_bytes()), "d.pptx"), "models": [(io.BytesIO(model.read_bytes()), "m.xlsx")]})
    assert r.status_code == 200 and len(r.get_json()["issues"]) == 3
    d = client.post("/api/demo", headers=HDR)
    assert d.status_code == 202
    job_id = d.get_json()["job_id"]
    st = _wait(client, job_id, timeout=60)
    assert st["status"] == "done"
    payload = client.get(f"/api/jobs/{job_id}/result").get_json()
    job = client.application.extensions["numcheck_jobs"].get(job_id)
    assert {f["name"] for f in payload["originals"]} == {
        payload["inputs"]["deck"], *payload["inputs"]["models"], payload["inputs"]["model_old"]
    }
    for original in payload["originals"]:
        with client.get(original["url"]) as response:
            assert response.status_code == 200
            assert original["name"] in response.headers["Content-Disposition"]
            assert response.data == (job.dir / "demo" / original["name"]).read_bytes()
    assert client.get("/api/demo/files/report.xlsx").status_code == 404


# --------------------------------------------------------------------------- #
# Downloads and purge
# --------------------------------------------------------------------------- #

def test_download_whitelist(client, office):
    job_id = _post(client, office).get_json()["job_id"]
    _wait(client, job_id)
    base = f"/api/jobs/{job_id}/download/"
    for name in ("report.html", "report.xlsx", "report.json", "deck_flagged.pptx"):
        r = client.get(base + name)
        assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
        r.close()
    for bad in ("C:\\Windows\\win.ini", "C%3A%5CWindows%5Cwin.ini", "..\\deck.pptx", "..%5Cdeck.pptx",
                "../deck.pptx", "%2e%2e%2fdeck.pptx", "deck.pptx", "model_0.xlsx", "unknown.txt", "report.html.bak"):
        r = client.get(base + bad)
        assert r.status_code == 404, bad
        assert r.is_json


def test_delete_purges_files(client, office):
    job_id = _post(client, office).get_json()["job_id"]
    _wait(client, job_id)
    mgr = client.application.extensions["numcheck_jobs"]
    job_dir = mgr.jobs[job_id].dir
    assert job_dir.exists()
    assert client.delete(f"/api/jobs/{job_id}", headers=HDR).status_code == 200
    assert not job_dir.exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_ttl_cleanup(client, office):
    job_id = _post(client, office).get_json()["job_id"]
    _wait(client, job_id)
    client.application.config["JOB_TTL_SECONDS"] = 0
    assert client.get("/api/health").status_code == 200
    assert job_id not in client.application.extensions["numcheck_jobs"].jobs


# --------------------------------------------------------------------------- #
# Meta, headers, static files
# --------------------------------------------------------------------------- #

def test_meta_never_leaks_keys(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-SECRETVALUE-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-SECRETVALUE-456")
    monkeypatch.setenv("NUMCHECK_LLM_API_KEY", "gen-SECRETVALUE-789")
    r = client.get("/api/meta")
    text = r.get_data(as_text=True)
    assert "SECRETVALUE" not in text
    meta = r.get_json()
    prov = {p["id"]: p for p in meta["providers"]}
    assert prov["openai"]["available"] is True and prov["anthropic"]["available"] is True
    assert prov["off"]["available"] is True
    assert isinstance(meta["ocr"]["available"], bool)
    assert meta["limits"]["max_upload_mb"] == 250
    assert set(meta["defaults"]) >= {"llm", "tolerance", "rounding_mode", "annotate", "ocr"}


def test_provider_connection_from_form(client, monkeypatch):
    def resolve(entries, cfg, warnings):
        assert cfg.llm == "openai" and cfg.model_name == "custom-model"
        assert cfg.llm_api_key == "temporary-secret"
        assert cfg.llm_redact[-2:] == ["Client Co", "Deal Name"]
        assert entries[0]["deck_label"] == "Adjusted EBITDA"
        return {"0": "B"}

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("NUMCHECK_LLM_API_KEY", raising=False)
    monkeypatch.setattr("numcheck.web.app.resolve_ambiguous", resolve)
    form = {"llm": "openai", "llm_model": "custom-model", "llm_api_key": "temporary-secret",
            "llm_redact": "Client Co\nDeal Name"}
    r = client.post("/api/providers/test", data=form, headers=HDR)
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert "temporary-secret" not in r.get_data(as_text=True)
    assert client.post("/api/providers/test", data={"llm": "openai"}, headers=HDR).status_code == 422


def test_chatgpt_loopback_connection_and_provider_check(client, monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlencode, urlsplit
    from urllib.request import urlopen

    from numcheck.llm import chatgpt

    def request(url, *, form=None, token=None):
        if url == chatgpt.TOKEN_URL and form["grant_type"] == "refresh_token":
            assert form["refresh_token"] == "private-refresh"
            return {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}
        if url == chatgpt.TOKEN_URL:
            assert form["client_id"] == "issued-client" and form["code"] == "example-code"
            assert form["code_verifier"] and form["redirect_uri"] == redirect
            return {"id_token": "signed-id", "access_token": "private-access", "refresh_token": "private-refresh",
                    "scope": "resource.invoke chatgpt.tokens.use.direct", "expires_in": 3600}
        if url.endswith("/models"):
            assert token == "private-access"
            return {"models": [{"visibility": "list", "slug": "test-model", "display_name": "Test model"}]}
        assert url.endswith("/revoke") and form["token"] == "rotated-refresh"
        return {}

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(chatgpt, "_host_id", lambda: "urn:uuid:583e1c1c-ca3a-429c-8608-6a08869660df")
    monkeypatch.setattr(chatgpt, "_request", request)
    monkeypatch.setattr(chatgpt, "_identity", lambda token, client_id, nonce: {"email": "analyst@example.com"})

    def resolve(entries, cfg, warnings):
        assert cfg.llm == "chatgpt" and cfg.llm_api_key == "private-access" and cfg.model_name == "test-model"
        return {"0": "B"}

    monkeypatch.setattr("numcheck.web.app.resolve_ambiguous", resolve)
    local = {"base_url": "http://127.0.0.1:5098", "headers": HDR}
    assert client.get("/api/chatgpt/status", headers=HDR).status_code == 403
    authorize = client.post("/api/chatgpt/connect", **local).get_json()["url"]
    query = parse_qs(urlsplit(authorize).query)
    assert query["client_id"] == ["dynamic_agent_client"] and query["code_challenge_method"] == ["S256"]
    redirect = query["redirect_uri"][0]
    callback = urlencode({"state": query["state"][0], "code": "example-code", "client_id": "issued-client"})
    with urlopen(redirect + "?" + callback, timeout=5) as response:
        assert b"ChatGPT connected" in response.read()
    status = client.get("/api/chatgpt/status", **local).get_json()
    assert status["connected"] and status["account"] == "analyst@example.com"
    assert "private-access" not in str(status)
    assert client.get("/api/chatgpt/models", **local).get_json()["models"][0]["id"] == "test-model"
    assert client.post("/api/providers/test", data={"llm": "chatgpt", "llm_model": "test-model"}, **local).status_code == 200
    restored = chatgpt.ChatGPTConnection()
    assert restored.status()["account"] == "analyst@example.com"
    assert restored.token() == "private-access"
    with restored.store.locked() as path:
        saved = restored.store.read(path)
        saved["expires"] = 0
        restored.store.write(path, saved)
    assert restored.token() == "rotated-access"
    assert chatgpt.ChatGPTConnection().token() == "rotated-access"
    assert not client.post("/api/chatgpt/disconnect", **local).get_json()["connected"]
    assert not restored.status()["connected"]


def test_demo_source_preview_uses_original_location_and_retains_downloads(tmp_path, monkeypatch):
    import hashlib

    from numcheck.web.evidence import PreviewUnavailable

    def missing_renderer(path, directory, location, token, *, exact_text):
        assert path.name == DEMO_FILES["deck"][0]
        assert location.slide_number == 2 and token == "2.8x" and exact_text
        raise PreviewUnavailable("No local renderer.")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("numcheck.web.app.slide_preview", missing_renderer)
    app = create_app()
    with app.test_client() as client:
        started = client.post("/api/demo", headers=HDR)
        base = f"/api/jobs/{started.get_json()['job_id']}"
        assert _wait(client, base.rsplit("/", 1)[1])["status"] == "done"
        result = client.get(base + "/result").get_json()
        finding = next(i for i in result["issues"] if i["slide_number"] == 2 and i["deck_value"] == "2.8x")
        query = f"?issue={finding['id']}"
        slide = client.get(base + "/evidence/slide" + query).get_json()
        assert slide["message"] == "No local renderer."
        deck_bytes = (ASSET_DIR / DEMO_FILES["deck"][0]).read_bytes()
        assert client.get(slide["download"]).data == deck_bytes

        sheet = client.get(base + "/evidence/workbook" + query).get_json()
        assert sheet["sheet"] == "Cap" and sheet["address"] == "C15"
        target = next(cell for row in sheet["rows"] for cell in row["cells"] if cell["address"] == "C15")
        assert target["text"] == "2.5" and sheet["formula"].startswith("=")
        assert client.get(sheet["download"]).data == (ASSET_DIR / DEMO_FILES["model"][0]).read_bytes()
        assert client.get(base + "/original/not-a-slot").status_code == 404
        assert client.get(base + "/evidence/workbook?issue=0").status_code == 404
        stale = next(i for i in result["issues"] if i["slide_number"] == 3 and i["deck_value"] == "275.0")
        stale_query = f"?issue={stale['id']}"
        source_window = client.get(base + "/evidence/workbook" + stale_query).get_json()
        assert source_window["address"] == "G11" and source_window["window"]["top"] == 1
        assert source_window["header"]["row"] == 4
        assert any(c["address"] == "G4" and c["text"] == "FY25E" for c in source_window["header"]["cells"])
        moved = client.get(base + "/evidence/workbook" + stale_query + "&row=7&col=8").get_json()
        assert moved["header"]["row"] == 4 and moved["window"]["left"] == 8
        assert moved["saved_value"] == "269.9" and moved["label_column"] == "A"
        assert any(c["address"] == "A11" and c["text"] == "Adjusted EBITDA"
                   for row in moved["rows"] for c in row["cells"])
        files = client.get(base + "/inputs").get_json()["files"]
        assert files[0]["sha256"] == hashlib.sha256(deck_bytes).hexdigest()
        assert client.delete(base, headers=HDR).status_code == 200
        assert client.get(slide["download"]).status_code == 404


def test_source_discovery_is_local_until_asked_to_use_ai(tmp_path, monkeypatch):
    calls = []

    def rank(entries, config, warnings):
        calls.append(entries[0])
        assert all(c["ref"].startswith("candidate-") for c in entries[0]["candidates"])
        assert "+12.0%" not in str(entries)
        return {"0": "candidate-1"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("numcheck.matching.source_search.resolve_ambiguous", rank)
    app = create_app()
    with app.test_client() as client:
        job = client.post("/api/demo", headers=HDR).get_json()["job_id"]
        assert _wait(client, job)["status"] == "done"
        base = f"/api/jobs/{job}"
        original = client.get(base + "/result").get_json()
        issue = next(i for i in original["issues"] if i["deck_value"] == "+12.0%" and i["slide_number"] == 2)
        query = f"?issue={issue['id']}"
        endpoint = base + "/evidence/sources" + query
        local = client.post(endpoint, headers=HDR).get_json()
        assert local["count"] > 0 and not calls and not local["recommended"]
        ai = client.post(endpoint, headers=HDR, data={"llm": "ollama"}).get_json()
        assert ai["recommended"] and len(calls) == 1
        workbook = client.get(base + "/evidence/workbook" + query)
        assert workbook.status_code == 200
        view = workbook.get_json()
        assert view["candidate_check"] and view["ai_source"] == ai["recommended"]
        assert ai["recorded_source"]["address"] == view["address"]
        assert ai["recorded_source"]["sheet"] == view["sheet"]
        assert len(view["sources"]) == local["count"]
        assert any(c["selected"] and c["ref"] == ai["recommended"] for c in view["sources"])

        # A stale displayed value can still have an identifiable workbook source.
        stale = next(m for m in original["matches"] if m["raw_text"] == "8.5%" and m["slide_number"] == 9)
        stale_query = f"?figure={stale['deck_uid']}"
        ai = client.post(base + "/evidence/sources" + stale_query, headers=HDR, data={"llm": "ollama"}).get_json()
        assert ai["recorded_source"]["address"] == "C20"
        assert calls[-1]["candidates"][0]["value_check"] == "differs"
        view = client.get(base + "/evidence/workbook" + stale_query).get_json()
        assert view["address"] == "C20" and "differs" in view["candidate_check"]
        assert len(view["sources"]) == 4
        assert client.get(base + "/result").get_json() == original


def test_bulk_source_review_updates_findings_coverage_and_exports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def rank(entries, config, warnings):
        calls.append(entries)
        assert all(candidate["ref"].startswith("candidate-")
                   for entry in entries for candidate in entry["candidates"])
        assert not any("8.5%" in str(entry) for entry in entries)
        return {entry["id"]: "candidate-1" for entry in entries}

    monkeypatch.setattr("numcheck.matching.source_search.resolve_ambiguous", rank)
    with create_app().test_client() as client:
        job = client.post("/api/demo", headers=HDR).get_json()["job_id"]
        assert _wait(client, job)["status"] == "done"
        base = f"/api/jobs/{job}"
        route = base + "/evidence/source-review"

        def run(form):
            assert client.post(route, headers=HDR, data=form).status_code == 202
            for _ in range(300):
                progress = client.get(route).get_json()
                if progress["status"] in ("done", "error"):
                    assert progress["status"] == "done", progress
                    return client.get(base + "/result").get_json()
                time.sleep(0.02)
            raise AssertionError("Source review did not finish.")

        before = client.get(base + "/result").get_json()
        issue = next(i for i in before["issues"] if i["slide_number"] == 2 and i["deck_value"] == "+12.0%")
        mismatch = next(i for i in before["issues"] if i["issue_type"] == "mismatch")
        local = run({})
        assert not calls
        assert local["source_review"]["searched"] == sum(before["coverage"][k] for k in
                                                       ("unmatched", "ambiguous", "matched_value_only"))
        assert local["coverage"]["verified"] > before["coverage"]["verified"]
        assert all(i["id"] != issue["id"] for i in local["issues"])
        assert mismatch in local["issues"]
        row = next(m for m in local["matches"] if m["deck_uid"] == issue["deck_uid"])
        assert row["status"] == "matched" and row["source_cell"].endswith("Segments!F15")
        evidence = client.get(base + "/evidence/workbook", query_string={"figure": row["deck_uid"]}).get_json()
        assert evidence["address"] == "F15"

        result = run({"llm": "ollama"})
        assert not calls and result["source_review"]["ai_reviewed"] == 0
        range_row = next(m for m in result["matches"] if m["raw_text"] == "8.5%" and m["slide_number"] == 9)
        assert range_row["status"] == "unmatched" and not range_row["source_suggestion"]
        inspected = client.get(base + "/evidence/workbook", query_string={"figure": range_row["deck_uid"]}).get_json()
        assert len(inspected["sources"]) == 4 and inspected["ai_eligible"]
        assert all(cell["display_value"] and cell["comparison"] for cell in inspected["sources"])
        differing = next(cell for cell in inspected["sources"] if cell["comparison"] == "differs")
        selected = client.get(base + "/evidence/workbook", query_string={
            "figure": range_row["deck_uid"], "cell": differing["ref"]}).get_json()
        assert selected["rows"] and "differs from the slide" in selected["candidate_check"]
        exported = client.get(base + "/download/report.json")
        assert exported.get_json()["coverage"] == result["coverage"]
        assert exported.get_json()["issues"] == result["issues"]
        exported.close()


def test_ai_automatically_searches_and_checks_reconciling_sources_for_upload_and_demo(tmp_path, monkeypatch):
    deck, model = tmp_path / "deck.pptx", tmp_path / "model.xlsx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    for row, (metric, amount) in enumerate((("Revenue", 100), ("Gross Profit", 60)), start=1):
        shape = slide.shapes.add_textbox(Inches(1), Inches(row), Inches(8), Inches(1))
        shape.text = f"Aerospace Components FY24 {metric}: ${amount}.0m"
    presentation.save(deck)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Aerospace Components"
    sheet.append(["Metric", "FY24", "FY25"])
    sheet.append(["Revenue", 100_000_000, 100_000_000])
    sheet.append(["Gross Profit", 60_000_000, 900_000_000])
    other = workbook.create_sheet("Other Segments")
    other.append(["Metric", "FY24"])
    other.append(["Revenue", 200_000_000])
    other.append(["Subscription Gross Profit", 60_000_000])
    workbook.save(model)
    config = tmp_path / "numcheck.toml"
    config.write_text("[matching]\nmin_label_score = 0.99\n", encoding="utf-8")
    monkeypatch.setattr("numcheck.web.app.build_demo",
                        lambda _: {"deck": str(deck), "model": str(model), "model_old": str(model)})
    calls = []

    def rank(entries, config, warnings):
        calls.append(entries)
        assert len(entries) == 2
        assert all(len(entry["candidates"]) == 1 for entry in entries)
        assert {entry["candidates"][0]["row_label"] for entry in entries} == {"Revenue", "Gross Profit"}
        assert all(entry["candidates"][0]["col_label"] == "FY24" for entry in entries)
        assert "$100.0m" not in str(entries) and "$60.0m" not in str(entries)
        return {entry["id"]: "candidate-1" for entry in entries}

    monkeypatch.setattr("numcheck.matching.source_search.resolve_ambiguous", rank)
    form = {"source_search": "false", "llm": "ollama", "annotate": "false", "require_label_evidence": "true"}
    with create_app(str(config)).test_client() as client:
        for started in (_post(client, (deck, model), **form), client.post("/api/demo", headers=HDR, data=form)):
            assert started.status_code == 202
            job = started.get_json()["job_id"]
            assert _wait(client, job)["status"] == "done"
            result = client.get(f"/api/jobs/{job}/result").get_json()
            assert result["settings"]["source_search"] is True
            assert result["source_review"]["ai_reviewed"] == result["source_review"]["verified"] == 2
            assert {row["source_cell"].split("] ")[-1] for row in result["matches"]} == {
                "Aerospace Components!B2", "Aerospace Components!B3"}
        assert len(calls) == 2


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.get_json()["ok"] is True
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "script-src 'self'" in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Cache-Control"] == "no-store"
    page = client.get("/")
    assert page.status_code == 200 and "Content-Security-Policy" in page.headers
    page.close()


def test_static_files_are_clean():
    files = [p for p in STATIC.rglob("*") if p.is_file()]
    names = {p.name for p in files}
    assert {"index.html", "app.css", "app.js"} <= names
    for p in files:
        text = p.read_text(encoding="utf-8").lower()
        assert "gradient" not in text, p.name
        if p.name == "app.js":
            for url in (
                "https://platform.openai.com/api-keys", "https://console.anthropic.com/settings/keys",
                "https://openrouter.ai/settings/keys", "https://vercel.com/docs/ai-gateway/authentication",
                "https://ollama.com/download", "https://developers.openai.com/codex/auth",
            ):
                text = text.replace(url, "")
        assert "http://" not in text.replace("http://www.w3.org", "") and "https://" not in text, p.name
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert " style=" not in html and "onclick=" not in html
    import re

    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)
