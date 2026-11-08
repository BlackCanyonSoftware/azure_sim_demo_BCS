"""The starter's web app: an internal tool with AI in it. Staff sign in (App Service does that, in front of the app), ask the assistant, and send
heavier jobs to a worker through a queue; results come back as short-lived read links.

Everything reaches Azure with an identity, never a key: on App Service its managed identity, on KPrime (local runs, recordings) Can-AI's lab
identity from AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_CERTIFICATE_PATH. Settings come from environment variables (see README).

Routes:
  GET  /api/me              who is signed in, and may they use it
  POST /api/ask             {"prompt": ...} -> the assistant's answer (JSON the page shows)
  POST /api/jobs            {"kind": ..., "input": {...}} -> a job for the worker
  GET  /api/jobs/<id>       the job's status and read links to its files
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

import ai
import identity
import validate

HERE = Path(__file__).resolve().parent
OPERATORS = {o.strip().lower() for o in os.environ.get("OPERATORS", "").split(",") if o.strip()}
LOCAL_SIGN_IN = os.environ.get("LOCAL_SIGN_IN") == "1"  # only for runs on this computer: trust X-MS-CLIENT-PRINCIPAL-* headers sent by the recorder
JOB_KINDS = {k.strip() for k in os.environ.get("JOB_KINDS", "echo").split(",") if k.strip()}

log = logging.getLogger("starter")
logging.basicConfig(level=logging.INFO)
logging.getLogger("azure").setLevel(logging.WARNING)
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(logger_name="starter")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


def _credential():
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential  # noqa: PLC0415

    return ManagedIdentityCredential() if os.environ.get("IDENTITY_ENDPOINT") else DefaultAzureCredential(exclude_interactive_browser_credential=True)


_clients: dict = {}


def _blobs():
    if "blobs" not in _clients:
        from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

        _clients["blobs"] = BlobServiceClient(os.environ["RESULTS_ACCOUNT_URL"], credential=_credential())
    return _clients["blobs"]


def _bus():
    if "bus" not in _clients:
        from azure.servicebus import ServiceBusClient  # noqa: PLC0415

        _clients["bus"] = ServiceBusClient(os.environ["JOBS_NAMESPACE"], credential=_credential())
    return _clients["bus"]


def _read_link(blob_name: str) -> str:
    """A link that lets the browser read one file for an hour (user delegation SAS: signed with an identity, not an account key)."""
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    key = _clients.get("delegation")
    if not key or key[1] < now + timedelta(hours=2):
        key = (_blobs().get_user_delegation_key(now - timedelta(minutes=5), now + timedelta(days=1)), now + timedelta(days=1))
        _clients["delegation"] = key
    container = os.environ.get("RESULTS_CONTAINER", "results")
    sas = generate_blob_sas(_blobs().account_name, container, blob_name, user_delegation_key=key[0],
                            permission=BlobSasPermissions(read=True), expiry=now + timedelta(hours=1))
    return f"{_blobs().url}{container}/{blob_name}?{sas}"


@app.after_request
def safety_headers(response):
    response.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: https://*.blob.core.windows.net; "
                                                   "media-src 'self' https://*.blob.core.windows.net; connect-src 'self' https://*.blob.core.windows.net; "
                                                   "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def operator_only(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        me = identity.who(request.headers, OPERATORS, trust_headers=LOCAL_SIGN_IN or bool(os.environ.get("WEBSITE_SITE_NAME")))
        if not me["signed_in"]:
            return jsonify(error="sign in first"), 401
        if not me["allowed"]:
            return jsonify(error="you are signed in, but not on this tool's list of users"), 403
        return view(me, *args, **kwargs)
    return wrapped


@app.get("/")
def index():
    return send_from_directory(HERE / "static", "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(HERE / "static", name)


@app.get("/api/me")
def me():
    return jsonify(identity.who(request.headers, OPERATORS, trust_headers=LOCAL_SIGN_IN or bool(os.environ.get("WEBSITE_SITE_NAME"))))


@app.post("/api/ask")
@operator_only
def ask(me):
    prompt = str((request.get_json(silent=True) or {}).get("prompt") or "").strip()
    if not prompt:
        return jsonify(error="type something first"), 400
    if len(prompt) > 4000:
        return jsonify(error="keep it under 4,000 characters"), 400
    started = time.monotonic()
    try:
        answer = ai.answer(prompt)
        problems = validate.check(answer, prompt)
        reasks = 0
        while problems and reasks < 2:  # the check failed: ask the model again, at most twice
            reasks += 1
            messages = [{"role": "system", "content": ai.INSTRUCTIONS.read_text()},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": json.dumps(answer)},
                        {"role": "user", "content": validate.fix_note(problems)}]
            answer = ai.answer(prompt, messages=messages)
            problems = validate.check(answer, prompt)
        if reasks:
            log.info("ask needed %d re-ask(s) for %s", reasks, me["name"] or "someone")
    except ai.NotConfigured as exc:
        return jsonify(error=str(exc)), 503
    except ai.ModelError as exc:
        log.warning("model call failed: %s", exc)
        return jsonify(error="the assistant could not answer just now; try again"), 502
    log.info("ask answered in %.1f s for %s", time.monotonic() - started, me["name"] or "someone")
    return jsonify(answer=answer, seconds=round(time.monotonic() - started, 1),
                   validation={"ok": not problems, "problems": problems, "reasks": reasks})


@app.post("/api/jobs")
@operator_only
def new_job(me):
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "")
    if kind not in JOB_KINDS:
        return jsonify(error=f"kind must be one of {', '.join(sorted(JOB_KINDS))}"), 400
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "input": body.get("input") or {}, "by": me["name"], "at": time.time()}
    from azure.servicebus import ServiceBusMessage  # noqa: PLC0415

    with _bus().get_queue_sender(os.environ.get("JOBS_QUEUE", "jobs")) as sender:
        sender.send_messages(ServiceBusMessage(json.dumps(job), message_id=job["id"], content_type="application/json"))
    log.info("job %s (%s) sent", job["id"], kind)
    return jsonify(id=job["id"], state="queued"), 202


@app.get("/api/jobs/<job_id>")
@operator_only
def job_status(me, job_id):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        abort(404)
    container = _blobs().get_container_client(os.environ.get("RESULTS_CONTAINER", "results"))
    try:
        status = json.loads(container.download_blob(f"{job_id}/status.json").readall())
    except Exception:  # noqa: BLE001 — not written yet: the worker has not picked it up
        return jsonify(id=job_id, state="queued")
    status["links"] = {name: _read_link(f"{job_id}/{name}") for name in status.get("files", [])}
    return jsonify(status)


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
