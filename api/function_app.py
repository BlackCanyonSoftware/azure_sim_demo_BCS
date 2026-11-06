"""The demo's API, running as the Static Web App's managed Azure Functions (Python, v2 programming model).

Only someone with the "operator" role reaches /api/* (staticwebapp.config.json says so; the platform checks it before this code runs).

  POST /api/jobs        a drop test: objects (SimReady names) and seconds -> a job id, put on the Service Bus queue
  GET  /api/jobs        the latest jobs and their state
  GET  /api/jobs/{id}   one job: its state, and short-lived links to its GIF, MP4 and last frame
  GET  /api/worker      whether KPrime's worker is online (its heartbeat) and which assets it already has
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import azure.functions as func

import jobs

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)  # the Static Web App's role rule guards the routes
log = logging.getLogger("simdemo.api")


def _reply(body, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(json.dumps(body), status_code=status, mimetype="application/json")


def _container():
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415 - imported late so a cold start stays short
    service = BlobServiceClient.from_connection_string(os.environ["RESULTS_STORAGE"])
    return service, service.get_container_client(os.environ.get("RESULTS_CONTAINER", "results"))


def _read_link(service, container: str, blob: str, minutes: int = 30) -> str:
    """A link that can only read this one blob, for half an hour."""
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas  # noqa: PLC0415
    sas = generate_blob_sas(account_name=service.account_name, container_name=container, blob_name=blob,
                            account_key=service.credential.account_key, permission=BlobSasPermissions(read=True),
                            expiry=datetime.now(timezone.utc) + timedelta(minutes=minutes))
    return f"{service.url}{container}/{blob}?{sas}"


def _who(req: func.HttpRequest) -> str:
    return jobs.principal(req.headers.get("x-ms-client-principal")).get("userDetails", "unknown")


@app.route(route="jobs", methods=["POST"])
def submit(req: func.HttpRequest) -> func.HttpResponse:
    try:
        job = jobs.check(req.get_json())
    except (ValueError, jobs.JobError) as exc:
        return _reply({"error": str(exc)}, 400)
    job["id"] = uuid.uuid4().hex[:12]
    job["by"] = _who(req)
    job["submitted"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    from azure.servicebus import ServiceBusClient, ServiceBusMessage  # noqa: PLC0415
    _, container = _container()
    container.upload_blob(f"jobs/{job['id']}/status.json", json.dumps({**job, "state": "queued"}), overwrite=True)
    with ServiceBusClient.from_connection_string(os.environ["JOBS_SEND"]) as bus, bus.get_queue_sender(os.environ.get("JOBS_QUEUE", "sim-jobs")) as sender:
        sender.send_messages(ServiceBusMessage(json.dumps(job), message_id=job["id"], content_type="application/json"))
    log.info("job queued", extra={"custom_dimensions": {"job": job["id"], "objects": len(job["objects"])}})
    return _reply({"id": job["id"], "state": "queued"}, 202)


@app.route(route="jobs", methods=["GET"])
def recent(req: func.HttpRequest) -> func.HttpResponse:
    _, container = _container()
    found = sorted((b for b in container.list_blobs(name_starts_with="jobs/") if b.name.endswith("/status.json")),
                   key=lambda b: b.last_modified, reverse=True)[:20]
    out = []
    for b in found:
        try:
            out.append(json.loads(container.download_blob(b.name).readall()))
        except Exception:  # noqa: BLE001 - a status being rewritten right now is skipped, not an error
            continue
    return _reply({"jobs": [jobs.summary(s) for s in out]})


@app.route(route="jobs/{job_id}", methods=["GET"])
def one(req: func.HttpRequest) -> func.HttpResponse:
    job_id = req.route_params.get("job_id", "")
    if not jobs.JOB_ID.fullmatch(job_id):
        return _reply({"error": "no such job"}, 404)
    service, container = _container()
    try:
        status = json.loads(container.download_blob(f"jobs/{job_id}/status.json").readall())
    except Exception:  # noqa: BLE001
        return _reply({"error": "no such job"}, 404)
    status["links"] = {name: _read_link(service, container.container_name, f"jobs/{job_id}/{name}")
                       for name in status.get("files", [])}
    return _reply(status)


@app.route(route="worker", methods=["GET"])
def worker(req: func.HttpRequest) -> func.HttpResponse:
    _, container = _container()
    try:
        beat = json.loads(container.download_blob("worker/heartbeat.json").readall())
    except Exception:  # noqa: BLE001
        return _reply({"online": False, "note": "the worker has never reported in"})
    return _reply(jobs.worker_state(beat, datetime.now(timezone.utc)))
