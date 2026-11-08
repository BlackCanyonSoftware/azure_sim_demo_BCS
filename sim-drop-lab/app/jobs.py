"""What a job may be, shared by the web app and the worker (the worker checks every job again: it never trusts the queue).

A job is a drop test: up to 4 SimReady objects placed above the floor, simulated for a few seconds."""
import re
from datetime import datetime

ASSET = re.compile(r"[a-z0-9_]{3,80}")
JOB_ID = re.compile(r"[0-9a-f]{12}")
MAX_OBJECTS = 4
MAX_SECONDS = 10
ONLINE_WITHIN_S = 180


class JobError(Exception):
    pass


def check(body) -> dict:
    """The parts of a request that become a job, or JobError saying what is wrong."""
    if not isinstance(body, dict):
        raise JobError("send a JSON object")
    objects = body.get("objects")
    if not isinstance(objects, list) or not 1 <= len(objects) <= MAX_OBJECTS:
        raise JobError(f"objects: a list of 1 to {MAX_OBJECTS}")
    clean = []
    for i, o in enumerate(objects):
        if not isinstance(o, dict) or not ASSET.fullmatch(str(o.get("asset", ""))):
            raise JobError(f"objects[{i}].asset: a SimReady name such as bottle_laboratory_a02")
        try:
            at = [float(v) for v in o.get("at", [0, 0, 0.3])]
        except (TypeError, ValueError):
            raise JobError(f"objects[{i}].at: three numbers in metres") from None
        if len(at) != 3 or not all(-2 <= v <= 2 for v in at[:2]) or not 0 <= at[2] <= 3:
            raise JobError(f"objects[{i}].at: x and y within 2 m of the middle, height 0 to 3 m")
        clean.append({"asset": o["asset"], "at": [round(v, 3) for v in at], "rotate_z": round(float(o.get("rotate_z") or 0), 1) % 360})
    try:
        seconds = float(body.get("seconds", 3))
    except (TypeError, ValueError):
        raise JobError("seconds: a number") from None
    if not 0.5 <= seconds <= MAX_SECONDS:
        raise JobError(f"seconds: 0.5 to {MAX_SECONDS}")
    return {"kind": "drop", "objects": clean, "seconds": seconds}


def who(headers, operators: set[str]) -> dict:
    """The signed-in user, from the headers App Service authentication adds after an Entra ID sign-in (it removes any copy a
    browser sends), and whether their Entra object id is on the operator list."""
    oid = (headers.get("X-MS-CLIENT-PRINCIPAL-ID") or "").strip().lower()
    idp = (headers.get("X-MS-CLIENT-PRINCIPAL-IDP") or "").strip().lower()
    name = headers.get("X-MS-CLIENT-PRINCIPAL-NAME") or ""
    signed_in = bool(oid) and idp == "aad"
    return {"signed_in": signed_in, "name": name if signed_in else "", "operator": signed_in and oid in operators}


def summary(status: dict) -> dict:
    keep = ("id", "state", "submitted", "finished", "objects", "seconds", "error", "result")
    return {k: status[k] for k in keep if k in status}


def worker_state(beat: dict, now: datetime) -> dict:
    try:
        age = (now - datetime.fromisoformat(beat["at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return {"online": False, "note": "the heartbeat cannot be read"}
    return {"online": age <= ONLINE_WITHIN_S, "seen_s_ago": round(age), "host": beat.get("host"), "gpu": beat.get("gpu"),
            "busy": beat.get("busy"), "assets": beat.get("assets", [])}
