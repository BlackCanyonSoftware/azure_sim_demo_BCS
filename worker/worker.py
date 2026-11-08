"""The worker: runs on KPrime (its GPU and tools), takes jobs off the queue, does the heavy part, stores the results for the web app.

It signs in as Can-AI's lab identity with its certificate (read from ~/.config/canai-azure: no keys). Each job kind is a function in
handlers.py that writes files into a folder; this file does the rest: status.json ("working", then "done" or "failed"), uploads, and
taking the job off the queue only when it is finished (a crash puts it back; after 3 tries Service Bus sets it aside).

    python worker.py              keep taking jobs until stopped (Ctrl+C)
    python worker.py --once       take at most one job, then stop (tests, a quick check)
Settings: JOBS_NAMESPACE, JOBS_QUEUE, RESULTS_ACCOUNT_URL, RESULTS_CONTAINER (the deployment's outputs), optional WORK_DIR.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import handlers

LAB = Path(os.environ.get("CANAI_AZURE_HOME", Path.home() / ".config" / "canai-azure"))
log = logging.getLogger("worker")


def lab_credential():
    from azure.identity import CertificateCredential  # noqa: PLC0415

    values = dict(line.split("=", 1) for line in (LAB / "lab.env").read_text().splitlines() if "=" in line)
    return CertificateCredential(values["CANAI_LAB_TENANT"], values["CANAI_LAB_APP_ID"], certificate_path=str(LAB / "canai-lab.pem"))


def write_status(container, job_id: str, state: str, **extra) -> None:
    body = json.dumps({"id": job_id, "state": state, "at": time.time(), **extra}).encode()
    container.upload_blob(f"{job_id}/status.json", body, overwrite=True)


def run_job(job: dict, container, work_root: Path) -> dict:
    """Do one job: the handler fills a folder, every file in it is uploaded. Returns the final status."""
    job_id, kind = job["id"], job.get("kind")
    handler = handlers.HANDLERS.get(kind)
    if handler is None:
        write_status(container, job_id, "failed", message=f"this worker has no '{kind}' jobs")
        return {"state": "failed"}
    write_status(container, job_id, "working", message="the worker has it")
    folder = Path(tempfile.mkdtemp(prefix=f"job-{job_id}-", dir=work_root))
    try:
        message = handler(job.get("input") or {}, folder) or ""
        files = []
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            name = path.relative_to(folder).as_posix()
            with path.open("rb") as handle:
                container.upload_blob(f"{job_id}/{name}", handle, overwrite=True)
            files.append(name)
        write_status(container, job_id, "done", message=str(message)[:300], files=files)
        return {"state": "done", "files": files}
    except Exception as exc:  # noqa: BLE001 — the job fails, the worker keeps going
        log.error("job %s failed: %s", job_id, traceback.format_exc(limit=3))
        write_status(container, job_id, "failed", message=f"{exc.__class__.__name__}: {str(exc)[:200]}")
        return {"state": "failed"}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main(once: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    from azure.servicebus import ServiceBusClient  # noqa: PLC0415
    from azure.storage.blob import BlobServiceClient  # noqa: PLC0415

    credential = lab_credential()
    container = BlobServiceClient(os.environ["RESULTS_ACCOUNT_URL"], credential=credential).get_container_client(os.environ.get("RESULTS_CONTAINER", "results"))
    work_root = Path(os.environ.get("WORK_DIR", Path.home() / "ai-suite-outputs" / "azure-worker"))
    work_root.mkdir(parents=True, exist_ok=True)
    log.info("worker ready: kinds %s", ", ".join(sorted(handlers.HANDLERS)))
    with ServiceBusClient(os.environ["JOBS_NAMESPACE"], credential=credential) as bus, \
            bus.get_queue_receiver(os.environ.get("JOBS_QUEUE", "jobs"), max_wait_time=20) as receiver:
        while True:
            for message in receiver.receive_messages(max_message_count=1, max_wait_time=20):
                try:
                    job = json.loads(str(message))
                except ValueError:
                    receiver.dead_letter_message(message, reason="not JSON")
                    continue
                log.info("job %s (%s) taken", job.get("id"), job.get("kind"))
                result = run_job(job, container, work_root)
                receiver.complete_message(message)
                log.info("job %s %s", job.get("id"), result["state"])
                if once:
                    return
            if once:
                return


if __name__ == "__main__":
    main(once="--once" in sys.argv)
