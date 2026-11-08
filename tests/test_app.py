"""The starter's own tests: only people who signed in (and are on the list) reach the API, every reply carries the safety headers, the
assistant's answer comes back as JSON, and the worker turns a job into uploaded files and a status. No Azure or model is needed.
The massing tests need FreeCAD and Blender (they are KPrime's tools); they skip quietly when those are not on the PATH.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "worker")]

import ai  # noqa: E402
import handlers  # noqa: E402
import identity  # noqa: E402
import validate  # noqa: E402
import worker  # noqa: E402

ME = {"X-MS-CLIENT-PRINCIPAL-ID": "AAAA-1", "X-MS-CLIENT-PRINCIPAL-IDP": "aad", "X-MS-CLIENT-PRINCIPAL-NAME": "pat@example.com"}

TWO_BOXES = {"project": "Test studio", "option": {"id": "A", "name": "Test option", "idea": "two boxes", "volumes": [
    {"name": "studio", "x": 0, "y": 0, "w": 12, "d": 9, "h": 7},
    {"name": "link", "x": 12, "y": 0, "w": 5, "d": 6, "h": 3.5}]}}

needs_cad = pytest.mark.skipif(shutil.which("freecad") is None or shutil.which("blender") is None,
                               reason="the massing job runs FreeCAD and Blender, which are KPrime's tools")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LOCAL_SIGN_IN", "1")
    monkeypatch.setenv("OPERATORS", "aaaa-1")
    for name in [m for m in sys.modules if m == "app"]:
        del sys.modules[name]
    import app as web  # noqa: PLC0415

    monkeypatch.setattr(ai, "answer", lambda prompt, messages=None: {"title": "Studio", "summary": f"about: {prompt}", "points": ["one"], "next": "sketch it"})
    return web.app.test_client()


def test_who_trusts_the_sign_in_headers_only_where_it_should():
    assert identity.who(ME, set(), trust_headers=False) == {"signed_in": False, "name": "", "allowed": False}
    assert identity.who(ME, set(), trust_headers=True)["allowed"] is True
    assert identity.who(ME, {"someone-else"}, trust_headers=True) == {"signed_in": True, "name": "pat@example.com", "allowed": False}
    assert identity.who({**ME, "X-MS-CLIENT-PRINCIPAL-IDP": "github"}, set(), trust_headers=True)["signed_in"] is False


def test_only_signed_in_operators_reach_the_api_and_every_reply_has_the_safety_headers(client):
    assert client.post("/api/ask", json={"prompt": "hi"}).status_code == 401
    assert client.post("/api/ask", json={"prompt": "hi"}, headers={**ME, "X-MS-CLIENT-PRINCIPAL-ID": "other"}).status_code == 403
    reply = client.post("/api/ask", json={"prompt": "a studio"}, headers=ME)
    assert reply.status_code == 200 and reply.get_json()["answer"]["summary"] == "about: a studio"
    for path in ("/", "/api/me"):
        headers = client.get(path).headers
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"


def test_bad_requests_are_refused(client):
    assert client.post("/api/ask", json={"prompt": " "}, headers=ME).status_code == 400
    assert client.post("/api/ask", json={"prompt": "x" * 4001}, headers=ME).status_code == 400
    assert client.post("/api/jobs", json={"kind": "rm -rf"}, headers=ME).status_code == 400
    assert client.get("/api/jobs/../../etc", headers=ME).status_code == 404


def test_no_model_set_up_says_so(monkeypatch):
    for name in ("AI_ENDPOINT", "AI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    ai._client.clear()
    with pytest.raises(ai.NotConfigured):
        ai.answer("hello")


class Container:
    def __init__(self):
        self.blobs = {}

    def upload_blob(self, name, data, overwrite=False):
        self.blobs[name] = data if isinstance(data, bytes) else data.read()


def test_the_worker_uploads_what_a_handler_made_and_reports_done_or_failed(tmp_path, monkeypatch):
    box = Container()
    done = worker.run_job({"id": "abc123abc123", "kind": "echo", "input": {"brief": "studio"}}, box, tmp_path)
    assert done == {"state": "done", "files": ["input.json"]}
    assert json.loads(box.blobs["abc123abc123/input.json"]) == {"brief": "studio"}
    assert json.loads(box.blobs["abc123abc123/status.json"])["state"] == "done"
    monkeypatch.setitem(handlers.HANDLERS, "boom", lambda job_input, folder: 1 / 0)
    assert worker.run_job({"id": "def456def456", "kind": "boom"}, box, tmp_path)["state"] == "failed"
    assert "ZeroDivisionError" in json.loads(box.blobs["def456def456/status.json"])["message"]
    assert worker.run_job({"id": "aaa111aaa111", "kind": "unknown"}, box, tmp_path)["state"] == "failed"
    assert list(tmp_path.iterdir()) == []  # work folders are cleaned up


def test_the_page_says_it_is_the_studio_assistant(client):
    page = client.get("/").get_data().decode()
    assert "Studio Assistant" in page and "Massing options" in page
    assert "FreeCAD" in page and "Blender" in page


def test_the_prompt_wants_the_design_brief_shape():
    prompt = (ROOT / "app" / "prompt.md").read_text()
    for key in ("spaces", "site_notes", "options", "volumes", "area_m2"):
        assert f'"{key}"' in prompt


def test_massing_is_a_kind_the_worker_and_the_page_agree_on():
    assert "massing" in handlers.HANDLERS
    assert "massing" in (ROOT / "infra" / "main.bicep").read_text()  # the web app's JOB_KINDS must list it too
    with pytest.raises(ValueError):
        handlers.massing({"option": {"volumes": []}}, Path("."))  # nothing to build -> the job fails with a clear message


@needs_cad
def test_the_massing_job_builds_a_freeCad_model_and_blender_picture():
    # the work folder is under home, not /tmp: the FreeCAD snap is strictly confined and cannot see the system temp dir
    folder = Path.home() / "ai-suite-outputs" / "pytest-massing-job"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        message = handlers.massing(TWO_BOXES, folder)
        assert "built 2 volumes" in message
        fcstd, obj, png = folder / "massing.FCStd", folder / "massing.obj", folder / "render.png"
        assert fcstd.is_file() and fcstd.stat().st_size > 1000
        assert obj.read_text().lstrip().startswith("#") and "\nv " in obj.read_text()  # a real mesh, vertices and all
        assert png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size > 20_000
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# ---- the deterministic check on every brief before it is shown (app/validate.py) ----
# fixtures hand-checked and then confirmed by running the code; x east, y north per prompt.md's convention.
ASK_COURTYARD = "Two-storey studio, about 140 m2, around a courtyard"  # names an outdoor space, so rule (a) must be watched
ASK_PLAIN = "Two-storey studio, about 140 m2"                          # no outdoor words: rules b/c/d tested in isolation
SPACES_A = [{"name": "Studios", "floors": "both", "area_m2": 115},
            {"name": "Meeting", "floors": "ground", "area_m2": 20},
            {"name": "Courtyard", "floors": "outdoor", "area_m2": 30}]  # indoor 135 of 140 within 10%, the courtyard listed apart
SPACES_B = [{"name": "Studios", "floors": "both", "area_m2": 136}]      # alone, so only the rule under test can fire
USHAPE = [{"name": "west arm", "x": 0, "y": 0, "w": 6, "d": 14, "h": 7},
          {"name": "middle", "x": 6, "y": 0, "w": 14, "d": 6, "h": 7},
          {"name": "east arm", "x": 20, "y": 0, "w": 6, "d": 14, "h": 7}]   # closed along the south, the inner yard opens north
RING = [{"name": "west", "x": 0, "y": 0, "w": 10, "d": 24, "h": 7},
        {"name": "north", "x": 10, "y": 14, "w": 20, "d": 10, "h": 7},
        {"name": "east", "x": 30, "y": 0, "w": 10, "d": 24, "h": 7},
        {"name": "south", "x": 10, "y": 0, "w": 20, "d": 8, "h": 3.5}]        # walled on all sides: no open side at all
L_PAIR = [{"name": "a", "x": 0, "y": 0, "w": 10, "d": 10, "h": 7},
          {"name": "b", "x": 10, "y": 10, "w": 10, "d": 10, "h": 7}]           # meeting at a corner: each pocket opens all four

def brief(idea, volumes, spaces=SPACES_B):
    return {"spaces": spaces, "options": [{"id": "A", "name": "x", "idea": idea, "volumes": volumes}]}

# rule (a): areas add up, and an outdoor space is listed apart and kept out of the total
def test_the_check_passes_a_good_brief_on_areas():
    assert validate.check(brief("blocks around the planted heart", USHAPE, SPACES_A), ASK_COURTYARD) == []

def test_the_check_fails_a_courtyard_that_is_listed_but_not_flagged_outdoor():
    answer = brief("blocks around the planted heart", USHAPE, SPACES_A[:-1] + [{"name": "Courtyard", "floors": "ground", "area_m2": 30}])
    assert "not flagged" in " ".join(validate.check(answer, ASK_COURTYARD))

def test_the_check_fails_when_the_areas_do_not_add_up():
    answer = brief("blocks around the planted heart", USHAPE,
                   [{"name": "Studios", "floors": "both", "area_m2": 100}, {"name": "Meeting", "floors": "ground", "area_m2": 25},
                    {"name": "Courtyard", "floors": "outdoor", "area_m2": 30}])
    assert validate.check(answer, ASK_COURTYARD)

def test_the_check_fails_a_courtyard_the_prompt_asked_for_but_the_answer_left_out():
    assert validate.check({"spaces": SPACES_B, "options": []}, ASK_COURTYARD)

def test_the_check_says_nothing_about_areas_when_no_outdoor_space_is_asked_or_listed():
    assert validate.check({"spaces": SPACES_B, "options": []}, ASK_PLAIN) == []

def test_the_check_keeps_the_outdoor_space_out_of_the_indoor_total():
    answer = {"spaces": [{"name": "Studios", "floors": "both", "area_m2": 115}, {"name": "Meeting", "floors": "ground", "area_m2": 20},
                         {"name": "Garden", "floors": "outdoor", "area_m2": 25}], "options": []}
    assert validate.check(answer, "Two-storey studio, about 135 m2, with a garden") == []

# rule (b): the idea may only name an open side the boxes really leave open
def test_the_idea_naming_the_real_open_side_passes():
    assert validate.check(brief("the courtyard opens to the north", USHAPE), ASK_PLAIN) == []

def test_the_idea_naming_a_closed_side_fails():
    assert validate.check(brief("the courtyard opens to the south", USHAPE), ASK_PLAIN)

def test_the_idea_calling_a_walled_courtyard_open_fails():
    assert validate.check(brief("the courtyard opens to the north", RING), ASK_PLAIN)

def test_the_idea_that_names_no_side_is_left_alone():
    assert validate.check(brief("blocks around the planted heart", USHAPE), ASK_PLAIN) == []
    assert validate.check(brief("blocks all around the courtyard", RING), ASK_PLAIN) == []

def test_two_boxes_meeting_at_a_corner_leave_every_side_open():
    for side in ("north", "east", "south", "west"):
        assert validate.check(brief(f"the courtyard opens to the {side}", L_PAIR), ASK_PLAIN) == []

def test_the_adjectival_form_of_a_side_still_counts():
    assert validate._names_side("the courtyard stays open to the northern edge") == "north"
    assert validate._names_side("two wings around a planted heart") is None

# rule (c): every volume touches at least one other and none overlap
def test_the_check_passes_a_pair_sharing_a_full_face():
    assert validate.check(brief("two blocks", TWO_BOXES["option"]["volumes"]), ASK_PLAIN) == []

def test_touching_at_a_corner_counts_as_touching():
    assert validate._touch(validate._boxes(L_PAIR)[0], validate._boxes(L_PAIR)[1])

def test_the_check_fails_a_volume_touching_no_other():
    assert validate.check(brief("two blocks", [{"name": "a", "x": 0, "y": 0, "w": 10, "d": 10, "h": 7},
                                               {"name": "b", "x": 22, "y": 4, "w": 8, "d": 8, "h": 3.5}]), ASK_PLAIN)

def test_the_check_fails_overlapping_volumes():
    assert validate.check(brief("two blocks", [{"name": "a", "x": 0, "y": 0, "w": 6, "d": 10, "h": 7},
                                               {"name": "b", "x": 3, "y": 4, "w": 6, "d": 10, "h": 3.5}]), ASK_PLAIN)

# rule (d): heights are whole storeys of about 3.5 m
def test_the_check_passes_whole_storeys():
    assert validate.check(brief("two blocks", [{"name": "a", "x": 0, "y": 0, "w": 10, "d": 6, "h": 7},
                                               {"name": "b", "x": 10, "y": 0, "w": 6, "d": 6, "h": 3.5}]), ASK_PLAIN) == []

def test_the_check_fails_a_height_that_is_not_a_whole_storey():
    assert validate.check(brief("two blocks", [{"name": "a", "x": 0, "y": 0, "w": 10, "d": 6, "h": 7},
                                               {"name": "b", "x": 10, "y": 0, "w": 6, "d": 6, "h": 5.2}]), ASK_PLAIN)

def test_the_fix_note_names_every_broken_rule():
    note = validate.fix_note(["the courtyard is called open to the south but the boxes leave the north open"])
    assert note and "south" in note

# ---- /api/ask runs the check before showing anything, and asks the model again at most twice ----
def good_brief():
    return {"title": "Studio", "summary": "s", "spaces": SPACES_B, "options": [{"id": "A", "name": "x", "idea": "two blocks",
            "volumes": [{"name": "a", "x": 0, "y": 0, "w": 10, "d": 6, "h": 7}, {"name": "b", "x": 10, "y": 0, "w": 6, "d": 6, "h": 3.5}]}]}

def bad_brief():
    return {"title": "Studio", "summary": "s", "spaces": SPACES_B, "options": []}  # the prompt asked for a courtyard; the answer left it out

def test_a_clean_brief_is_shown_without_asking_the_model_again(client, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "answer", lambda prompt, messages=None: (calls.append(messages), good_brief())[1])
    reply = client.post("/api/ask", json={"prompt": ASK_PLAIN}, headers=ME).get_json()
    assert len(calls) == 1 and calls[0] is None and reply["validation"]["ok"] is True

def test_the_app_asks_the_model_again_when_the_check_fails(client, monkeypatch):
    calls = []
    fixed = {"title": "Studio", "summary": "s", "options": [],
             "spaces": [{"name": "Studios", "floors": "both", "area_m2": 115}, {"name": "Meeting", "floors": "ground", "area_m2": 20},
                        {"name": "Courtyard", "floors": "outdoor", "area_m2": 30}]}  # the courtyard named in the prompt, now listed apart
    answers = iter([bad_brief(), fixed])
    monkeypatch.setattr(ai, "answer", lambda prompt, messages=None: (calls.append(messages), next(answers))[1])
    reply = client.post("/api/ask", json={"prompt": ASK_COURTYARD}, headers=ME).get_json()
    assert len(calls) == 2 and calls[0] is None and calls[1]  # the second call carried the fix note
    assert reply["validation"]["ok"] is True and reply["validation"]["reasks"] == 1

def test_the_app_gives_up_after_two_re_asks_and_shows_the_answer_flagged(client, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "answer", lambda prompt, messages=None: (calls.append(messages), bad_brief())[1])
    reply = client.post("/api/ask", json={"prompt": ASK_COURTYARD}, headers=ME).get_json()
    assert len(calls) == 3  # the first answer plus the two allowed re-asks, no more
    assert reply["validation"]["ok"] is False and reply["validation"]["problems"]
    assert reply["answer"]["title"] == "Studio"  # it still shows the last answer, honestly flagged


# ---- rule (e): a named courtyard must really exist ----------------------------

E_GOOD_U = {"id": "A", "name": "Courtyard U", "idea": "Two wings and a low link around a courtyard, open to the north",
            "volumes": [{"name": "West Wing", "x": 0, "y": 0, "w": 6, "d": 14, "h": 7},
                        {"name": "Middle", "x": 6, "y": 0, "w": 14, "d": 6, "h": 7},
                        {"name": "East Wing", "x": 20, "y": 0, "w": 6, "d": 14, "h": 7}]}
E_GOOD_RING = {"id": "A", "name": "Enclosed Courtyard Ring", "idea": "Four joined wings enclose the courtyard on all sides",
               "volumes": [{"name": "West Wing", "x": 0, "y": 0, "w": 10, "d": 24, "h": 7},
                           {"name": "North Wing", "x": 10, "y": 14, "w": 20, "d": 10, "h": 7},
                           {"name": "East Wing", "x": 30, "y": 0, "w": 10, "d": 24, "h": 7},
                           {"name": "South Wing", "x": 10, "y": 0, "w": 20, "d": 8, "h": 3.5}]}
E_BAD_FRAME_A = {"id": "A", "name": "Central Courtyard with Twin Wings", "idea": "Two rectangular volumes on either side of a central courtyard forming a U-shape",
                 "volumes": [{"name": "Left Volume", "x": 0, "y": 0, "w": 7, "d": 10, "h": 7},
                             {"name": "Right Volume", "x": 7, "y": 0, "w": 7, "d": 10, "h": 7}]}
E_BAD_EDGE = {"id": "A", "name": "Enclosed Courtyard Ring", "idea": "An enclosed courtyard; the garden side opens north",
              "volumes": [{"name": "West Wing", "x": 0, "y": 0, "w": 10, "d": 24, "h": 7},
                          {"name": "East Wing", "x": 30, "y": 0, "w": 10, "d": 24, "h": 7},
                          {"name": "South Wing", "x": 10, "y": 0, "w": 20, "d": 8, "h": 3.5}]}
E_PROMPT = "Design a two-storey studio building around a central courtyard"
E_SPACES = [{"name": "living", "floors": "both", "area_m2": 100}, {"name": "courtyard", "floors": "outdoor", "area_m2": 30}]


def _e(opt):
    return {"title": "t", "summary": "s", "spaces": E_SPACES, "options": [opt], "next": "n"}


def test_rule_e_good_ushape_open_north():
    assert validate.check(_e(E_GOOD_U), E_PROMPT) == []

def test_rule_e_good_ring_fully_enclosed():
    assert validate.check(_e(E_GOOD_RING), E_PROMPT) == []

def test_rule_e_bad_flush_pair_has_no_courtyard():
    got = validate.check(_e(E_BAD_FRAME_A), E_PROMPT)
    assert any("no empty area inside" in p for p in got), got

def test_rule_e_bad_enclosed_text_open_edge():
    got = validate.check(_e(E_BAD_EDGE), E_PROMPT)
    assert any("reaches the outline" in p for p in got), got



def test_the_3d_result_has_its_own_full_width_panel_under_both_columns(client):
    """Owner, 2026-11-07: the render sat in the narrow right column below all the options, out of view in the recording."""
    page = client.get("/").get_data().decode()
    results = page[page.index('id="results"'):]
    assert 'id="job-list"' in results and page.index('id="results"') > page.index('id="run-job"')  # after the button, outside the options panel
    assert "hidden" in page[page.index('id="results"') - 40:page.index('id="results"') + 40]  # shown only once there is a job
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "style.css").read_text()
    assert ".results { grid-column: 1 / -1; }" in css and "max-width: 900px" in css
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text()
    assert js.count('$("results").hidden = false') == 2  # for a job and for a job that could not be queued
