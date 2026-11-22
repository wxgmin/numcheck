"""Local Sign in with ChatGPT using OpenAI's public OAuth registration flow."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = AUTH + "/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
CALLBACK = "/auth/callback"


class ConnectionError(Exception):
    """A safe message for the connection UI."""


def _request(url, *, form=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = urlencode(form).encode() if form is not None else None
    with urlopen(Request(url, headers=headers, data=data), timeout=15) as response:
        body = response.read()
        return json.loads(body) if body else {}


def _host_id() -> str:
    directory = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "NumCheck"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "chatgpt-host-id"
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(uuid.uuid4().urn)
    except FileExistsError:
        pass
    return uuid.UUID(path.read_text(encoding="ascii").strip()).urn


def _identity(token: str, client_id: str, nonce: str) -> dict:
    import jwt

    jwks = jwt.PyJWKClient(AUTH + "/.well-known/jwks.json", timeout=10)
    signing_key = jwks.get_signing_key_from_jwt(token)
    identity = jwt.decode(token, signing_key.key, algorithms=["RS256"], issuer=AUTH, audience=client_id,
                          options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
    if not secrets.compare_digest(str(identity["nonce"]), nonce):
        raise ConnectionError("ChatGPT returned an invalid sign-in response. Try again.")
    return identity


def _credentials(body: dict, client_id: str, previous: dict | None = None) -> dict:
    previous = previous or {}
    scopes = set(body.get("scope", previous.get("scope", "")).split())
    if PLAN_SCOPE not in scopes or "resource.invoke" not in scopes:
        raise ConnectionError("ChatGPT plan usage was not approved. Reconnect and allow plan usage.")
    if not body.get("access_token") or not body.get("refresh_token", previous.get("refresh_token")):
        raise ConnectionError("ChatGPT did not return a renewable plan connection.")
    return {**previous, "client_id": client_id, "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", previous.get("refresh_token")), "scope": " ".join(scopes),
            "expires": time.monotonic() + float(body.get("expires_in", 300))}


class ChatGPTConnection:
    """Credentials stay in the local process. Only the installation ID is persisted."""

    def __init__(self):
        self.lock = threading.RLock()
        self.current: dict | None = None
        self.pending = False
        self.message = ""

    def status(self) -> dict:
        with self.lock:
            return {"connected": self.current is not None, "pending": self.pending,
                    "account": self.current["account"] if self.current else "", "message": self.message}

    def start(self) -> str:
        try:
            import jwt  # noqa: F401
        except ImportError as exc:
            raise ConnectionError("Install NumCheck with its llm extra to enable ChatGPT sign-in.") from exc
        with self.lock:
            if self.pending:
                raise ConnectionError("A sign-in attempt is already open in your browser.")
            try:
                host_id = _host_id()
                listener = HTTPServer(("127.0.0.1", 0), type("Callback", (_Callback,), {}))
            except (OSError, ValueError) as exc:
                raise ConnectionError("NumCheck could not prepare its local sign-in callback.") from exc
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            redirect = f"http://127.0.0.1:{listener.server_port}{CALLBACK}"
            self.pending, self.message = True, ""

            def finish(query):
                if not secrets.compare_digest(query.get("state", [""])[0], state):
                    return False, "This sign-in response does not match the active request."
                try:
                    if query.get("error"):
                        raise ConnectionError("ChatGPT sign-in was declined or canceled.")
                    client_id, code = query.get("client_id", [""])[0], query.get("code", [""])[0]
                    if not client_id or client_id == "dynamic_agent_client" or not code:
                        raise ConnectionError("ChatGPT did not complete registration. Try again.")
                    body = _request(TOKEN_URL, form={"grant_type": "authorization_code", "client_id": client_id,
                                                    "code": code, "code_verifier": verifier, "resource": RESOURCE,
                                                    "redirect_uri": redirect})
                    identity = _identity(body["id_token"], client_id, nonce)
                    saved = _credentials(body, client_id)
                    saved["account"] = str(identity.get("email") or "ChatGPT account")
                    with self.lock:
                        self.current = saved
                        self.message = ""
                    return True, "ChatGPT connected. You can close this tab and return to NumCheck."
                except ConnectionError as exc:
                    message = str(exc)
                except Exception:
                    message = "ChatGPT could not finish signing in. Try again in NumCheck."
                with self.lock:
                    self.message = message
                return True, message

            listener.RequestHandlerClass.finish_auth = staticmethod(finish)
            threading.Thread(target=self._listen, args=(listener,), daemon=True, name="numcheck-chatgpt").start()
            return AUTH + "/api/accounts/authorize?" + urlencode({
                "client_id": "dynamic_agent_client", "agent_name_hint": "NumCheck", "ext_agent_host_id": host_id,
                "response_type": "code", "redirect_uri": redirect, "resource": RESOURCE,
                "scope": "openid profile email offline_access resource.invoke " + PLAN_SCOPE,
                "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge,
            })

    def _listen(self, listener):
        deadline = time.monotonic() + 300
        listener.timeout = 1
        listener.completed = False
        try:
            with listener:
                while not listener.completed and time.monotonic() < deadline:
                    listener.handle_request()
        finally:
            with self.lock:
                self.pending = False
                if not listener.completed:
                    self.message = "ChatGPT sign-in timed out. Try again."

    def token(self) -> str:
        with self.lock:
            if self.current is None:
                raise ConnectionError("Connect your ChatGPT account first.")
            if self.current["expires"] - time.monotonic() < 60:
                try:
                    body = _request(TOKEN_URL, form={"grant_type": "refresh_token", "resource": RESOURCE,
                                                    "client_id": self.current["client_id"],
                                                    "refresh_token": self.current["refresh_token"]})
                    self.current = _credentials(body, self.current["client_id"], self.current)
                except Exception:
                    raise ConnectionError("ChatGPT could not renew the connection. Try again or reconnect.") from None
            return self.current["access_token"]

    def models(self) -> list[dict]:
        token = self.token()
        try:
            catalog = _request(RESOURCE + "/models", token=token)
            return [{"id": m["slug"], "label": m.get("display_name") or m["slug"]}
                    for m in catalog.get("models", []) if m.get("visibility") == "list" and m.get("slug")]
        except Exception:
            raise ConnectionError("ChatGPT could not load your model catalog. Check the connection.") from None

    def disconnect(self) -> dict:
        with self.lock:
            if self.pending:
                raise ConnectionError("Finish the open sign-in attempt before disconnecting.")
            session, self.current = self.current, None
            self.message = ""
        if session:
            try:
                _request(AUTH + "/api/accounts/oauth/revoke", form={"token": session["refresh_token"],
                         "client_id": session["client_id"], "token_type_hint": "refresh_token"})
            except Exception:
                with self.lock:
                    self.message = "Disconnected locally. Remote revocation could not be confirmed."
        return self.status()


class _Callback(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(4)
        super().setup()

    def log_message(self, *args):
        pass  # The callback path contains the short-lived authorization code.

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != CALLBACK or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(404)
            return
        self.server.completed, message = self.finish_auth(parse_qs(parsed.query))
        data = message.encode("utf-8")
        self.send_response(200 if self.server.completed else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
