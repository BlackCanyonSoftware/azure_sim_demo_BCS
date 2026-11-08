"""The demo's own rules and the worker, without Azure: jobs are checked the same way on both sides, and the worker really runs a drop test
through the sim tool and 'uploads' to a stand-in for the results container."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "worker"))
import jobs  # noqa: E402
import worker  # noqa: E402

BOTTLE = "bottle_laboratory_a02"


@pytest.mark.parametrize("body, says", [
    ([], "JSON object"),
    ({}, "objects"),
    ({"objects": []}, "objects"),
    ({"objects": [{"asset": BOTTLE}] * 5}, "1 to 4"),
    ({"objects": [{"asset": "../../etc/passwd"}]}, "SimReady name"),
    ({"objects": [{"asset": "Bottle A"}]}, "SimReady name"),
    ({"objects": [{"asset": BOTTLE, "at": [0, 0]}]}, "within 2 m"),
    ({"objects": [{"asset": BOTTLE, "at": [9, 0, 1]}]}, "within 2 m"),
    ({"objects": [{"asset": BOTTLE, "at": ["a", 0, 1]}]}, "three numbers"),
    ({"objects": [{"asset": BOTTLE}], "seconds": 60}, "0.5 to 10"),
    ({"objects": [{"asset": BOTTLE}], "seconds": "soon"}, "a number"),
])
def test_bad_jobs_are_refused(body, says):
    with pytest.raises(jobs.JobError, match=says):
        jobs.check(body)


def test_a_good_job_keeps_only_what_it_needs():
    job = jobs.check({"objects": [{"asset": BOTTLE, "at": [0.1, 0, 0.5], "rotate_z": 400, "evil": "x"}], "seconds": 2, "command": "rm -rf /"})
    assert job == {"kind": "drop", "objects": [{"asset": BOTTLE, "at": [0.1, 0.0, 0.5], "rotate_z": 40.0}], "seconds": 2.0}


OID = "0f8fad5b-d9cb-469f-a165-70867728950e"
SIGNED_IN = {"X-MS-CLIENT-PRINCIPAL-ID": OID.upper(), "X-MS-CLIENT-PRINCIPAL-IDP": "aad", "X-MS-CLIENT-PRINCIPAL-NAME": "me@school.edu"}


def test_the_signed_in_user_is_read_from_the_platform_headers():
    assert jobs.who(SIGNED_IN, {OID}) == {"signed_in": True, "name": "me@school.edu", "operator": True}
    assert jobs.who(SIGNED_IN, set())["operator"] is False
    assert jobs.who({}, {OID}) == {"signed_in": False, "name": "", "operator": False}
    assert jobs.who({**SIGNED_IN, "X-MS-CLIENT-PRINCIPAL-IDP": "github"}, {OID})["operator"] is False


@pytest.fixture
def web(monkeypatch):
    pytest.importorskip("flask")
    monkeypatch.setenv("OPERATORS", OID)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    sys.modules.pop("app", None)
    import app  # noqa: PLC0415
    return app.app.test_client()


def test_only_operators_reach_the_api_and_every_reply_has_the_safety_headers(web):
    assert web.get("/api/worker").status_code == 403
    assert web.get("/api/jobs", headers={**SIGNED_IN, "X-MS-CLIENT-PRINCIPAL-ID": "someone-else"}).status_code == 403
    assert web.post("/api/jobs", json={"objects": []}, headers=SIGNED_IN).status_code == 400  # past the guard, refused by the rules
    assert web.get("/api/jobs/not-an-id", headers=SIGNED_IN).status_code == 404
    assert web.get("/api/me", headers=SIGNED_IN).get_json()["operator"] is True
    page = web.get("/")
    assert page.status_code == 200 and b"Sim Drop Lab" in page.data
    assert "script-src 'self'" in page.headers["Content-Security-Policy"] and page.headers["X-Content-Type-Options"] == "nosniff"
    assert web.get("/../jobs.py").status_code == 404 and web.get("/jobs.py").status_code == 404  # only static/ is served


def test_the_worker_counts_as_online_only_with_a_recent_heartbeat():
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    beat = {"at": (now - timedelta(seconds=30)).isoformat(), "host": "KPrime", "assets": [BOTTLE]}
    assert jobs.worker_state(beat, now)["online"] is True
    beat["at"] = (now - timedelta(minutes=10)).isoformat()
    assert jobs.worker_state(beat, now)["online"] is False
    assert jobs.worker_state({"at": "yesterday"}, now)["online"] is False


class FakeResults:
    def __init__(self):
        self.blobs = {}

    def put(self, name, data, kind="application/json"):
        self.blobs[name] = data

    def status(self, job, **fields):
        self.blobs[f"jobs/{job['id']}/status.json"] = json.dumps({**job, **fields})

    def state(self, job_id):
        return json.loads(self.blobs[f"jobs/{job_id}/status.json"])


def test_the_worker_checks_jobs_again_and_never_runs_a_bad_one():
    results = FakeResults()
    worker.run_job({"id": "abcdef123456", "objects": [{"asset": "../x"}]}, results, sim=None)
    s = results.state("abcdef123456")
    assert s["state"] == "failed" and s["error"].startswith("refused")
    worker.run_job({"id": "../../etc", "objects": []}, results, sim=None)
    assert len(results.blobs) == 1  # no id, nothing written


SIM = worker.SIM
needs_sim = pytest.mark.skipif(not SIM.exists() or not (worker.ASSETS / BOTTLE).is_dir(),
                               reason="Can-AI's sim tool and the lab bottle are not on this computer")


@needs_sim
def test_the_worker_runs_a_real_drop_and_uploads_the_results():
    sim = worker.load_sim()
    if not sim.VENV_PY.exists():
        pytest.skip("the canai-sim environment is not set up")
    results = FakeResults()
    worker.run_job({"id": "0123456789ab", "objects": [{"asset": BOTTLE, "at": [0, 0, 0.3]}], "seconds": 1.5}, results, sim)
    s = results.state("0123456789ab")
    assert s["state"] == "done", s.get("error")
    assert {"sim.gif", "last.png"} <= set(s["files"])
    assert results.blobs["jobs/0123456789ab/sim.gif"][:6] in (b"GIF89a", b"GIF87a")
    body = s["result"]["free_bodies"][0]
    assert not body["below_floor"]
    assert not list(worker.OUTPUTS.glob("sim/azure-0123456789ab-*"))  # the local copy is removed after upload
