"""The home side of the demo: takes drop-test jobs off the Azure Service Bus queue, runs them with Can-AI's sim tool (MuJoCo on the GPU), and
uploads the results to Blob Storage. Every connection goes out from here; nothing listens.

Settings come from ~/.config/azure-sim-demo/worker.env (written by scripts/deploy.sh, readable by this user only):
  JOBS_LISTEN       Service Bus connection string with Listen rights on the queue only
  JOBS_QUEUE        the queue name (sim-jobs)
  RESULTS_URL       the results container's URL with a SAS that can only create and write blobs, from a stored access policy
                    (deleting the policy in Azure cancels it at once)
  APPLICATIONINSIGHTS_CONNECTION_STRING   optional: logs and traces go to Application Insights

The queue is not trusted: every job is checked again here (jobs.check), and only drop tests with SimReady names run.
"""
import importlib.util
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
import jobs  # noqa: E402 - the same rules as the API

SIM = Path(os.environ.get("CANAI_SIM", Path.home() / "ai-suite" / "modules" / "hermes_plugins" / "general" / "can-ai-sim" / "sim.py"))
ASSETS = Path.home() / "ai-suite-assets" / "simready"
OUTPUTS = Path.home() / "ai-suite-outputs"
BEAT_EVERY_S = 60
log = logging.getLogger("simdemo.worker")
stop = threading.Event()
busy = threading.Event()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_sim():
    spec = importlib.util.spec_from_file_location("simdemo_sim", SIM)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def gpu() -> str | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, check=True).stdout.strip().splitlines()[0]
        name, util, used, total = [p.strip() for p in out.split(",")]
        return f"{name.replace('NVIDIA GeForce ', '')}, {util} busy, {used} of {total}"
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def downloaded() -> list[str]:
    return sorted(p.name for p in ASSETS.iterdir() if p.is_dir() and not p.name.startswith(".")) if ASSETS.is_dir() else []


class Results:
    """The results container, through the write-only link."""

    def __init__(self, url: str):
        from azure.storage.blob import ContainerClient  # noqa: PLC0415
        self.box = ContainerClient.from_container_url(url)

    def put(self, name: str, data: bytes | str, kind: str = "application/json") -> None:
        from azure.storage.blob import ContentSettings  # noqa: PLC0415
        self.box.upload_blob(name, data, overwrite=True, content_settings=ContentSettings(content_type=kind))

    def status(self, job: dict, **fields) -> None:
        self.put(f"jobs/{job['id']}/status.json", json.dumps({**job, **fields}))


def heartbeat(results: Results) -> None:
    while not stop.is_set():
        try:
            results.put("worker/heartbeat.json", json.dumps({"at": now(), "host": socket.gethostname(), "gpu": gpu(),
                                                             "busy": busy.is_set(), "assets": downloaded()}))
        except Exception as exc:  # noqa: BLE001 - a missed beat only makes the page say offline for a while
            log.warning("heartbeat failed: %s", exc)
        stop.wait(BEAT_EVERY_S)


def run_job(raw: dict, results: Results, sim) -> None:
    job_id = str(raw.get("id", ""))
    if not jobs.JOB_ID.fullmatch(job_id):
        log.warning("dropped a message without a valid job id")
        return
    meta = {k: raw.get(k) for k in ("id", "by", "submitted")}
    try:
        job = {**meta, **jobs.check(raw)}
    except jobs.JobError as exc:
        results.status({**meta, "objects": raw.get("objects")}, state="failed", error=f"refused: {exc}", finished=now())
        return
    results.status(job, state="running", started=now())
    env = sim.Env(python=sim.VENV_PY, blender=sim.find_blender(), outputs=OUTPUTS, run=sim.run_program)
    started = time.time()
    folder = None
    try:
        for o in job["objects"]:  # fetch what is missing, physics files only (no textures): the drop test needs no more
            if not (ASSETS / o["asset"]).is_dir():
                results.status(job, state="running", started=now(), note=f"downloading {o['asset']}")
                got = sim.execute(sim.plan({"action": "simready_fetch", "asset": o["asset"], "usd_only": True}, env), env)
                if not got.get("ok"):
                    raise sim.SimError(got.get("error") or f"could not download {o['asset']}")
        assets = [{"simready": o["asset"], "at": o["at"], "rotate_z": o["rotate_z"], "name": f"{o['asset']}_{i}"} for i, o in enumerate(job["objects"])]
        r = sim.execute(sim.plan({"action": "mujoco_run", "name": f"azure-{job_id}", "seconds": job["seconds"], "assets": assets,
                                  "camera": {"distance": 1.6, "elevation": -22, "azimuth": 135, "lookat": [0, 0, 0.15]}}, env), env)
        folder = r.get("folder")
        if not r.get("ok"):
            raise sim.SimError(r.get("error") or "the simulation failed")
        files = []
        for key, name, kind in (("gif", "sim.gif", "image/gif"), ("last_frame", "last.png", "image/png"), ("mp4", "sim.mp4", "video/mp4")):
            path = r["files"].get(key)
            if path and Path(path).exists():
                results.put(f"jobs/{job_id}/{name}", Path(path).read_bytes(), kind)
                files.append(name)
        summary = {"real_s": round(time.time() - started, 1), "simulated_s": r.get("simulated_s"), "free_bodies": r.get("free_bodies"),
                   "contacts_now": r.get("contacts_now")}
        results.status(job, state="done", finished=now(), files=files, result=summary)
        log.info("job done", extra={"custom_dimensions": {"job": job_id, "seconds": summary["real_s"]}})
    except Exception as exc:  # noqa: BLE001 - whatever went wrong is shown on the page
        results.status(job, state="failed", error=str(exc)[:500], finished=now())
        log.exception("job %s failed", job_id)
    finally:
        if folder:  # the results are in Azure now; the local copy would only pile up
            shutil.rmtree(folder, ignore_errors=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)  # the Azure libraries log every HTTP request at INFO
    if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        from azure.monitor.opentelemetry import configure_azure_monitor  # noqa: PLC0415
        configure_azure_monitor(logger_name="simdemo")
    from azure.servicebus import AutoLockRenewer, ServiceBusClient  # noqa: PLC0415
    sim = load_sim()
    results = Results(os.environ["RESULTS_URL"])
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=heartbeat, args=(results,), daemon=True).start()
    log.info("worker started on %s, waiting for jobs", socket.gethostname())
    with ServiceBusClient.from_connection_string(os.environ["JOBS_LISTEN"]) as bus, \
            bus.get_queue_receiver(os.environ.get("JOBS_QUEUE", "sim-jobs")) as queue, AutoLockRenewer(max_lock_renewal_duration=900) as renew:
        while not stop.is_set():
            for msg in queue.receive_messages(max_message_count=1, max_wait_time=20):
                renew.register(queue, msg)
                busy.set()
                try:
                    run_job(json.loads(str(msg)), results, sim)
                    queue.complete_message(msg)  # done or failed, the page shows it; a retry would fail the same way
                except json.JSONDecodeError:
                    queue.dead_letter_message(msg, reason="not JSON")
                finally:
                    busy.clear()
    log.info("worker stopped")


if __name__ == "__main__":
    main()
