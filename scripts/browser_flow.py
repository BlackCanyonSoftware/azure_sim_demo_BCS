"""Manual browser flow for the Studio Assistant (run while the app runs on 127.0.0.1:4000 with LOCAL_SIGN_IN=1):

    python scripts/browser_flow.py

It signs in with the pretend headers the recorder uses, asks the assistant for a real answer from the model, checks the brief shape,
selects an option, and clicks the 3D job. The job needs the Azure queue (not deployed yet) so it is EXPECTED to report an error here;
what this proves is the page, the sign-in, the model answer and the wiring up to the queue. Screenshots go to docs/screenshots/flow/.
"""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4000"
HEADERS = {"X-MS-CLIENT-PRINCIPAL-ID": "AAAA-1", "X-MS-CLIENT-PRINCIPAL-IDP": "aad", "X-MS-CLIENT-PRINCIPAL-NAME": "Demo User"}
SHOTS = Path(__file__).resolve().parents[1] / "docs" / "screenshots" / "flow"
SHOTS.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(extra_http_headers=HEADERS, viewport={"width": 1280, "height": 800})

    page.goto(BASE + "/", wait_until="networkidle")
    who = page.inner_text("#who")
    print("SIGN-IN:", who)
    assert "Demo User" in who

    page.fill("#prompt", "two-storey design studio, about 140 m2, around a courtyard")
    page.click("#ask")
    page.wait_for_selector("#answer:not([hidden])", timeout=240000)
    print("ASK-STATE:", page.inner_text("#ask-state"))
    page.screenshot(path=SHOTS / "01-brief.png")

    text = page.inner_text("#answer")
    print("BRIEF-VISIBLE:", "spaces-table" if page.query_selector("#answer table") else "NO table",
          "| site notes li:", len(page.query_selector_all("#answer ul li")),
          "| next:", "yes" if "Next:" in text else "no")
    assert page.query_selector("#answer table"), "no spaces table in the brief"

    cards = page.query_selector_all("#option-list label")
    print("OPTIONS:", len(cards), "| svg plans:", len(page.query_selector_all("#option-list svg rect")))
    assert len(cards) >= 2, "expected at least two option cards"

    assert page.is_disabled("#run-job"), "run button should start disabled"
    page.click("#option-list input[type=radio]:first-of-type")
    assert not page.is_disabled("#run-job"), "run button should enable when an option is chosen"
    page.click("#run-job")
    # the app's own CSP forbids unsafe-eval, so poll from the Playwright side, not inside the page
    # the queue+worker are deployed now: the job runs for real and the panel shows the done line
    state = ""
    for _ in range(60):
        state = page.inner_text("#job-list")
        if "done:" in state or "not queued" in state or "failed" in state:
            break
        time.sleep(2)
    print("JOB:", state.replace("\n", " | "))
    assert "done:" in state, f"expected the job panel to show a done line, got {state!r}"
    assert "Thinking" not in page.inner_text("#ask-state") and "Answered" in page.inner_text("#ask-state"), "ask-state should still hold the assistant's state, not the job's"
    page.screenshot(path=SHOTS / "02-options-and-job.png")

    page.reload(wait_until="networkidle")
    print("AFTER-RELOAD sign-in:", page.inner_text("#who"))
    print("AFTER-RELOAD brief visible:", not page.query_selector("#answer").is_hidden(), "(page state is in-memory; nothing is meant to persist)")
    page.screenshot(path=SHOTS / "03-after-reload.png")
    time.sleep(0.5)
    browser.close()
print("FLOW OK")
