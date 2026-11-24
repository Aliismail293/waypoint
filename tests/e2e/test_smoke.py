"""Browser smoke test: ask -> stream -> open a source. Offline (mock LLM, in-memory stores, dev sign-in bypass).

Needs Playwright and its browser (`uv sync --group e2e && uv run --group e2e playwright install chromium`);
skipped when they are missing so the default test run and CI stay dependency-free.
"""

import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from waypoint.agents.runtime import WaypointRuntime
from waypoint.api.app import create_app
from waypoint.config import Settings

sync_api = pytest.importorskip("playwright.sync_api")

OFFLINE = dict(llm="mock", embeddings="mock", store="memory", catalog="memory", sessions="memory")


@pytest.fixture
def base_url():
    settings = Settings(**OFFLINE, dev_skip_auth=True, google_client_id="")
    app = create_app(settings, WaypointRuntime.from_settings(settings))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(base_url):
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"Chromium is not installed: {exc}")
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        # Fonts and Google Sign-In are external; the page must work without them.
        context.route("**/accounts.google.com/**", lambda route: route.abort())
        context.route("**/fonts.googleapis.com/**", lambda route: route.abort())
        yield context.new_page()
        browser.close()


def skip_onboarding(page):
    """First sign-in shows the (skippable) onboarding dialog; most tests just dismiss it."""
    page.locator("#onboard[open]").wait_for(timeout=5000)
    page.get_by_role("button", name="Skip for now").click()
    page.locator("#onboard").wait_for(state="hidden")


def test_ask_stream_and_open_source(page, base_url):
    page.goto(base_url)
    skip_onboarding(page)
    assert page.get_by_role("link", name="Chat").get_attribute("aria-current") == "page"
    page.get_by_label("Ask Pip").fill("What are the prerequisites for MAT224?")
    page.get_by_role("button", name="Ask", exact=True).click()

    page.wait_for_selector(".steps-toggle")  # the finished answer collapses its steps
    assert page.locator(".turn-pip .pip").first.get_attribute("data-state") == "idle"
    assert "step" in page.locator(".steps-toggle").inner_text()
    assert "MAT102H5 and MAT223H5" in page.locator(".turn-pip .answer").inner_text()  # answer first, from the data

    # V3: sources sit at the bottom of the answer, collapsed to compact chips; a citation opens the Source reader.
    foot = page.locator(".card .sources-foot")
    assert foot.locator(".source-chip").count() >= 1 and "sources" in foot.inner_text().lower()
    assert page.evaluate(
        "document.querySelector('.card .answer')"
        ".compareDocumentPosition(document.querySelector('.card .sources-foot')) & 4"
    )  # the footer follows the answer
    assert not page.locator("#sources-panel").count()
    page.locator(".card .cite").first.click()
    reader = page.get_by_role("complementary", name="Source reader")
    reader.wait_for()
    page.wait_for_function("!document.getElementById('reader-text').textContent.startsWith('Loading')")
    assert "MAT" in reader.locator("#reader-text").inner_text()
    assert reader.get_by_role("link", name="Open on the official calendar").get_attribute("href").startswith("https://")
    page.keyboard.press("Escape")
    assert not reader.is_visible()
    assert page.get_by_role("navigation", name="Main").inner_text().split() == [
        "Chat",
        "Course",
        "map",
        "My",
        "plan",
        "Programs",
        "Enrolment",
        "check",
        "History",
    ]


def test_tabs_and_phone_width(page, base_url):
    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(base_url)
    skip_onboarding(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("link", name="History").click()
    page.get_by_role("heading", name="History", exact=True).wait_for()
    assert page.get_by_role("link", name="History").get_attribute("aria-current") == "page"


def test_onboarding_then_map_route_and_course_page(page, base_url):
    page.goto(base_url)
    page.locator("#onboard[open]").wait_for(timeout=5000)
    page.get_by_role("button", name="Continue").click()  # step 1: program (left as "not sure")

    page.get_by_label("Course code").fill("MAT102H5")  # step 2: courses
    page.get_by_role("button", name="Add", exact=True).click()
    page.get_by_role("button", name="In progress").click()
    page.get_by_label("Course code").fill("MAT223H5")
    page.get_by_role("button", name="Add", exact=True).click()
    page.locator(".chip-status").nth(1).wait_for()
    assert page.locator(".chip-status.done").count() == 1 and page.locator(".chip-status.now").count() == 1
    page.get_by_label("Course code").fill("ZZZ999H5")
    page.get_by_role("button", name="Add", exact=True).click()
    page.get_by_text("ZZZ999H5 isn't in Waypoint's data yet.").wait_for()
    page.get_by_role("button", name="Continue").click()

    page.get_by_role("button", name="Not sure").click()  # step 3: goal
    page.get_by_role("button", name="Finish").click()
    page.locator("#onboard").wait_for(state="hidden")

    page.get_by_role("link", name="Course map").click()  # the map colours courses by the student's statuses
    page.locator(".map-node.taken[data-code='MAT102H5']").wait_for()
    page.locator(".map-node.now[data-code='MAT223H5']").wait_for()
    assert page.locator(".map-col", has_text="200-LEVEL").count() == 1
    page.locator(".map-node[data-code='MAT224H5']").click()  # path highlight: the path stays lit, the rest fades
    page.locator(".link.hl").first.wait_for(state="attached")
    assert page.locator(".map-node.dim").count() > 10 and page.locator(".map-node[data-code='MAT223H5']").evaluate(
        "n => !n.classList.contains('dim')"
    )
    assert page.locator(".link.hl").first.get_attribute("stroke") == "#F59E0B"
    pop = page.get_by_role("dialog", name="MAT224H5 options")
    pop.get_by_role("button", name="Taken").click()  # choosing a status recolours the card at once
    page.locator(".map-node.taken[data-code='MAT224H5']").wait_for()
    pop.get_by_role("button", name="Clear").click()
    page.locator(".map-node.other[data-code='MAT224H5']").wait_for()
    pop.locator("summary").click()
    assert "MAT102H5 and MAT223H5" in pop.inner_text()
    page.locator("#map-canvas").click(position={"x": 5, "y": 5})  # empty space resets the highlight
    page.wait_for_function("!document.querySelector('.map-node.dim')")

    page.locator(".map-node[data-code='MAT224H5']").click()
    page.get_by_role("dialog", name="MAT224H5 options").get_by_role("link", name="Open course page").click()
    page.get_by_role("heading", name="MAT224H5").wait_for()
    assert "Sample data" not in page.locator("#course-root").inner_text()
    assert page.locator("#course-root .badge", has_text="in progress").count() >= 0

    page.get_by_role("link", name="Chat").click()  # settings: delete my data
    page.get_by_role("button", name="Your profile").click()
    page.get_by_role("button", name="Delete my data").click()
    page.get_by_role("button", name="Really delete everything?").click()
    page.locator("#settings").wait_for(state="hidden")
    assert page.evaluate("fetch('/api/profile').then(r => r.json()).then(p => p.courses.length)") == 0


def test_plan_shows_notes_and_headsups_not_red_errors_then_enrolment_check(page, base_url):
    page.goto(base_url)
    skip_onboarding(page)
    page.evaluate(
        "fetch('/api/profile', {method: 'PUT', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({courses: [{code: 'MAT102H5', status: 'done'}], onboarded: true})})"
    )

    # Chat: "Add MAT224H5 to my plan" creates Plan A (MAT224H5 needs MAT223H5, which the student hasn't got).
    page.get_by_label("Ask Pip").fill("What are the prerequisites for MAT224H5?")
    page.get_by_role("button", name="Ask", exact=True).click()
    page.get_by_role("button", name="Add MAT224H5 to my plan").click()
    page.get_by_text("Added to Plan A").wait_for()

    page.get_by_role("link", name="My plan").click()
    report = page.get_by_role("complementary", name="Critic report")
    page.get_by_text("Critic has notes").wait_for()  # a missing prerequisite is a neutral note, not a problem
    assert "MAT223H5" in report.inner_text() and "No heads-ups" in report.inner_text()
    assert page.locator(".report li.error, .pill.bad, .plan-card.bad").count() == 0
    page.get_by_role("button", name="Prerequisites of MAT224H5").first.click()  # the card's "i"
    assert "MAT102H5 and MAT223H5" in page.locator(".info-pop").inner_text()
    report.get_by_role("button", name="Open source MAT224H5").first.click()  # the citation opens the Source reader
    page.get_by_role("complementary", name="Source reader").wait_for()
    page.keyboard.press("Escape")

    # Same term as its prerequisite: an amber heads-up, with the reason.
    page.get_by_label("Add a course to 2026 Fall").fill("MAT223H5")
    page.get_by_label("Add a course to 2026 Fall").press("Enter")
    page.get_by_text("Checking…").wait_for(state="hidden")
    page.get_by_text("Critic has heads-ups").wait_for()
    assert "move MAT223H5 to an earlier term" in report.inner_text()

    # Fix it: MAT224H5 moves to a later term.
    page.get_by_role("button", name="+ Add term").click()
    page.get_by_label("Move MAT224H5 to").select_option("2027 Winter")
    page.get_by_text("Checking…").wait_for(state="hidden")
    page.wait_for_function("!document.querySelector('.report li.headsup')")

    # Enrolment check replaces the timetable: planned courses, enrolment notes, other campuses, ACORN list.
    page.get_by_role("link", name="Enrolment check").click()
    page.get_by_role("heading", name="Enrolment check").wait_for()
    page.get_by_role("heading", name="ACORN checklist").wait_for()
    body = page.locator("#enrolment-root").inner_text()
    assert "MAT" in body and "Not checked" in body and "never connects to ACORN" in body
    assert page.get_by_role("link", name="Timetable").count() == 0


def test_programs_tab_checklist_and_credit_target(page, base_url):
    page.goto(base_url)
    skip_onboarding(page)
    page.get_by_role("link", name="Programs").click()
    page.get_by_role("heading", name="Programs", exact=True).wait_for()
    page.get_by_label("Search programs").fill("minor")
    page.get_by_role("button", name="Select").first.click()
    page.get_by_role("button", name="Selected").wait_for()
    page.get_by_role("button", name="Mathematical Sciences - Minor").click()
    page.get_by_role("heading", name="Mathematical Sciences - Minor (Science)").wait_for()
    assert page.locator(".req .badge", has_text="Missing").count() >= 1
    assert page.locator(".req .badge", has_text="Check the calendar").count() >= 1

    page.evaluate(
        "fetch('/api/plans/add-course', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({code: 'MAT102H5', term: '2026 Fall'})})"
    )
    page.get_by_role("link", name="My plan").click()  # the credits bar now has its target
    page.locator(".credits strong", has_text=re.compile(r"^0\.5 of \d")).wait_for()  # "X of Y", target from the program


def test_pin_answer_find_it_in_history_and_export_the_pack(page, base_url):
    page.goto(base_url)
    skip_onboarding(page)
    page.get_by_label("Ask Pip").fill("What are the prerequisites for MAT224H5?")
    page.get_by_role("button", name="Ask", exact=True).click()
    page.get_by_role("button", name="Pin this answer").click()
    page.get_by_text("Pinned. Find it in").wait_for()

    page.get_by_role("navigation", name="Main").get_by_role("link", name="History").click()
    page.get_by_role("heading", name="PINNED").wait_for()
    page.get_by_role("button", name="Prerequisites").click()
    assert page.locator(".entry").count() == 1
    page.get_by_role("button", name="Plans").click()
    page.get_by_text("Nothing matches").wait_for()
    page.get_by_role("button", name="All").click()
    page.get_by_label("Search your questions and answers").fill("zzzz-not-there")
    page.get_by_text("Nothing matches").wait_for()
    page.get_by_label("Search your questions and answers").fill("MAT224")
    page.locator(".entry").wait_for()

    page.locator(".entry summary").click()  # expands: the answer with its citation badge
    page.locator(".entry .cite").first.click()
    page.get_by_role("complementary", name="Source reader").wait_for()
    page.keyboard.press("Escape")

    with page.expect_download() as download:
        page.get_by_role("button", name="Download PDF").click()
    path = download.value.path()
    assert Path(path).read_bytes()[:5] == b"%PDF-"
    page.get_by_role("button", name="Copy as email").click()
    page.locator("#pack-email").wait_for(state="visible")
    assert "Q: What are the prerequisites for MAT224H5?" in page.locator("#pack-email").input_value()
