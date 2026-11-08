"""Job kinds the worker knows. Each is a function (input, folder) -> a short message; it writes its result files into folder, and the worker
uploads them and tells the web app. Add a kind here AND to the web app's JOB_KINDS setting.

A handler can run anything on KPrime: Blender or FreeCAD from the command line, a Python script, a local model. Keep each job under about four
minutes (the queue gives the worker five before it hands the job out again), and write pictures as PNG so the page can show them.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent / "scripts"


def echo(job_input: dict, folder: Path) -> str:
    """The starter's example: writes what it was given back as a file, so the whole path (page, queue, worker, storage, page) can be checked."""
    (folder / "input.json").write_text(json.dumps(job_input, indent=2))
    return "the worker received it and stored it"


def _tool(name: str) -> str:
    found = shutil.which(name) or f"/snap/bin/{name}"
    if not Path(found).is_file():
        raise RuntimeError(f"this worker has no {name} to run its heavy jobs")
    return found


def massing(job_input: dict, folder: Path) -> str:
    """The demo's job: build the chosen massing option as a simple model with FreeCAD, then render a 3/4 view with Blender.

    The page sends back the chosen option: {"project": "...", "option": {"id", "name", "idea",
    "volumes": [{"name", "x", "y", "w", "d", "h"}]}} — metres on the site grid (x east, y north), each volume a box standing on the ground.
    Writes layout.json, massing.FCStd, massing.obj and render.png into folder. Scripts get their paths in the environment because
    FreeCAD console mode treats spare command-line arguments as files to open.
    """
    option = (job_input or {}).get("option") or {}
    volumes = [v for v in (option.get("volumes") or [])
               if all(k in v for k in ("x", "y", "w", "d", "h"))
               and float(v["w"]) > 0 and float(v["d"]) > 0 and float(v["h"]) > 0]
    if not volumes:
        raise ValueError("the chosen option has no volumes for the worker to build")
    layout = {"project": str(job_input.get("project") or ""), "option": {**option, "volumes": volumes}}
    (folder / "layout.json").write_text(json.dumps(layout, indent=2))

    env = {**os.environ, "LAYOUT_JSON": str(folder / "layout.json"),
           "FC_STD": str(folder / "massing.FCStd"), "FC_OBJ": str(folder / "massing.obj")}
    subprocess.run([_tool("freecad"), "-c", str(SCRIPTS / "fc_massing.py")], env=env, timeout=170,
                   stdin=subprocess.DEVNULL, capture_output=True, check=True)
    subprocess.run([_tool("blender"), "--background", "--python", str(SCRIPTS / "bl_render.py"), "--",
                    env["FC_OBJ"], str(folder / "render.png")], timeout=170, capture_output=True, check=True)
    for name in ("massing.FCStd", "massing.obj", "render.png"):
        if not (folder / name).is_file():
            raise RuntimeError(f"{name} did not appear (the {name.rsplit('.', 1)[-1]} step failed)")
    return f"built {len(volumes)} volumes for option {option.get('id', '?')}; rendered a 3/4 view"


HANDLERS = {"echo": echo, "massing": massing}
