"""The demo's web app: the page and its API, one small Flask app on Azure App Service (Linux, Free F1 plan).

Sign-in is App Service's built-in authentication with Microsoft Entra ID: nobody reaches this code without signing in, and the
platform passes the signed-in user in X-MS-CLIENT-PRINCIPAL-* headers (it removes any copy a browser sends). On top of that only
the accounts listed in OPERATORS (Entra object ids) may use the API.

The app holds no keys: it reaches Blob Storage and Service Bus as its own managed identity, through role assignments that
infra/main.bicep makes (Storage Blob Data Contributor on the results container, Blob Delegator on the account, Service Bus Data
Sender on the queue). Read links for result files are user delegation SAS links, signed with a key Azure lends to that identity.

  GET  /                the page (static/)
  GET  /api/me          who is signed in, and whether they are an operator
  POST /api/jobs        a drop test: objects (SimReady names) and seconds -> a job id, put on the Service Bus queue
  GET  /api/jobs        the latest jobs and their state
  GET  /api/jobs/<id>   one job: its state, and 30-minute links to its GIF, MP4 and last frame
  GET  /api/worker      whether KPrime's worker is online (its heartbeat) and which assets it already has
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from functools import cache, wraps

from flask import Flask, jsonify, request, send_from_directory

import jobs

if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415
    configure_azure_monitor(logger_name="simdemo")

log = logging.getLogger("simdemo.api")
log.setLevel(logging.INFO)
app = Flask(__name__, static_folder=None)
STATIC = os.path.join(os.path.dirname(__file__), "static")
OPERATORS = {s.strip().lower() for s in os.environ.get("OPERATORS", "").split(",") if s.strip()}
CSP = ("default-src 'self'; img-src 'self' https://*.blob.core.windows.net data:; media-src https://*.blob.core.windows.net; "
       "style-src 'self'; script-src 'self'; frame-ancestors 'none'")


@app.after_request
def headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


def operator_only(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not jobs.who(request.headers, OPERATORS)["operator"]:
            return jsonify({"error": "only an operator can do this; ask the owner to add your account"}), 403
        return view(*args, **kwargs)
    return guarded


@cache
def _credential():
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential  # noqa: PLC0415
    return ManagedIdentityCredential() if os.environ.get("IDENTITY_ENDPOINT") else DefaultAzureCredential()


@cache
def _service():
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415
    return BlobServiceClient(os.environ["RESULTS_ACCOUNT_URL"], credential=_credential())


def _container():
    return _service().get_container_client(os.environ.get("RESULTS_CONTAINER", "results"))


_lent = {}


def _read_link(blob: str, minutes: int = 30) -> str:
    """A link that can only read this one blob, for half an hour, signed with a user delegation key (no account key involved)."""
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas  # noqa: PLC0415
    service, now = _service(), datetime.now(timezone.utc)
    if not _lent or _lent["until"] < now + timedelta(minutes=minutes + 5):  # Azure lends the key for up to 7 days; ask for 1
        _lent.update(key=service.get_user_delegation_key(now - timedelta(minutes=5), now + timedelta(days=1)), until=now + timedelta(days=1))
    container = os.environ.get("RESULTS_CONTAINER", "results")
    sas = generate_blob_sas(account_name=service.account_name, container_name=container, blob_name=blob,
                            user_delegation_key=_lent["key"], permission=BlobSasPermissions(read=True),
                            expiry=now + timedelta(minutes=minutes))
    return f"{service.url.rstrip('/')}/{container}/{blob}?{sas}"


@app.get("/")
def page():
    return send_from_directory(STATIC, "index.html")


@app.get("/<path:name>")
def files(name):
    return send_from_directory(STATIC, name)


@app.get("/api/me")
def me():
    return jsonify(jobs.who(request.headers, OPERATORS))


@app.post("/api/jobs")
@operator_only
def submit():
    try:
        job = jobs.check(request.get_json(silent=True))
    except jobs.JobError as exc:
        return jsonify({"error": str(exc)}), 400
    job["id"] = uuid.uuid4().hex[:12]
    job["by"] = jobs.who(request.headers, OPERATORS)["name"]
    job["submitted"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    from azure.servicebus import ServiceBusClient, ServiceBusMessage  # noqa: PLC0415
    _container().upload_blob(f"jobs/{job['id']}/status.json", json.dumps({**job, "state": "queued"}), overwrite=True)
    with ServiceBusClient(os.environ["JOBS_NAMESPACE"], credential=_credential()) as bus, \
            bus.get_queue_sender(os.environ.get("JOBS_QUEUE", "sim-jobs")) as sender:
        sender.send_messages(ServiceBusMessage(json.dumps(job), message_id=job["id"], content_type="application/json"))
    log.info("job queued", extra={"job": job["id"], "objects": len(job["objects"])})
    return jsonify({"id": job["id"], "state": "queued"}), 202


@app.get("/api/jobs")
@operator_only
def recent():
    container = _container()
    found = sorted((b for b in container.list_blobs(name_starts_with="jobs/") if b.name.endswith("/status.json")),
                   key=lambda b: b.last_modified, reverse=True)[:20]
    out = []
    for b in found:
        try:
            out.append(json.loads(container.download_blob(b.name).readall()))
        except Exception:  # noqa: BLE001, S112 - a status being rewritten right now is skipped, not an error
            continue
    return jsonify({"jobs": [jobs.summary(s) for s in out]})


@app.get("/api/jobs/<job_id>")
@operator_only
def one(job_id):
    if not jobs.JOB_ID.fullmatch(job_id):
        return jsonify({"error": "no such job"}), 404
    try:
        status = json.loads(_container().download_blob(f"jobs/{job_id}/status.json").readall())
    except Exception:  # noqa: BLE001
        return jsonify({"error": "no such job"}), 404
    status["links"] = {name: _read_link(f"jobs/{job_id}/{name}") for name in status.get("files", [])}
    return jsonify(status)


@app.get("/api/worker")
@operator_only
def worker():
    try:
        beat = json.loads(_container().download_blob("worker/heartbeat.json").readall())
    except Exception:  # noqa: BLE001
        return jsonify({"online": False, "note": "the worker has never reported in"})
    return jsonify(jobs.worker_state(beat, datetime.now(timezone.utc)))
