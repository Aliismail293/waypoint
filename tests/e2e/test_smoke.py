"""Browser smoke test: ask -> stream -> open a source. Offline (mock LLM, in-memory stores, dev sign-in bypass).

Needs Playwright and its browser (`uv sync --group e2e && uv run --group e2e playwright install chromium`);
skipped when they are missing so the default test run and CI stay dependency-free.
"""

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

    # Pip works while the answer is on its way, then rests.
    page.wait_for_selector(".turn-pip .pip[data-state='thinking'], .turn-pip .pip[data-state='talking']")
    page.wait_for_selector(".steps-toggle")  # the finished answer collapses its steps
    assert page.locator(".turn-pip .pip").first.get_attribute("data-state") == "idle"
    assert "steps" in page.locator(".steps-toggle").inner_text()

    # Sources panel is filled; a citation badge opens the Source reader with the chunk text.
    assert page.locator(".source-row").count() >= 1
    page.locator(".card .cite").first.click()
    reader = page.get_by_role("complementary", name="Source reader")
    reader.wait_for()
    page.wait_for_function("!document.getElementById('reader-text').textContent.startsWith('Loading')")
    assert "MAT" in reader.locator("#reader-text").inner_text()
    assert reader.get_by_role("link", name="Open on the official calendar").get_attribute("href").startswith("https://")
    page.keyboard.press("Escape")
    assert not reader.is_visible()


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
    page.locator(".map-node[data-code='MAT224H5']").click()
    page.locator(".link.route").first.wait_for()
    side = page.get_by_role("complementary", name="Selected course")
    assert (
        "MAT223H5" in side.inner_text()
        and "in progress" in side.inner_text()
        and "only the prerequisite links in its data" in side.inner_text().replace("\n", " ")
    )

    side.get_by_role("link", name="Open course page").click()  # the course page checks prerequisites for this student
    page.get_by_role("heading", name="MAT224H5").wait_for()
    assert "Sample data" in page.locator("#course-root").inner_text()
    assert page.locator("#course-root .badge", has_text="in progress").count() >= 1

    page.get_by_role("link", name="Chat").click()  # settings: delete my data
    page.get_by_role("button", name="Your profile").click()
    page.get_by_role("button", name="Delete my data").click()
    page.get_by_role("button", name="Really delete everything?").click()
    page.locator("#settings").wait_for(state="hidden")
    assert page.evaluate("fetch('/api/profile').then(r => r.json()).then(p => p.courses.length)") == 0


def test_plan_critic_catches_a_broken_prerequisite_and_timetable_fixes_a_clash(page, base_url):
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
    page.get_by_text("Critic found problems").wait_for()
    report = page.get_by_role("complementary", name="Critic report")
    assert "MAT223H5" in report.inner_text() and "first" in report.inner_text()
    report.get_by_role("button", name="MAT224H5").first.click()  # the citation opens the Source reader
    page.get_by_role("complementary", name="Source reader").wait_for()
    page.keyboard.press("Escape")

    # Fix it: MAT223H5 in the first term, MAT224H5 moved to a later term.
    page.get_by_label("Add a course to 2026 Fall").fill("MAT223H5")
    page.get_by_label("Add a course to 2026 Fall").press("Enter")
    page.get_by_role("button", name="+ Add term").click()
    page.get_by_label("Move MAT224H5 to").select_option("2027 Winter")
    page.get_by_text("Checking…").wait_for(state="hidden")
    page.wait_for_function("!document.querySelector('.report li.error')")

    # Timetable: pick the Tue/Thu MAT224H5 section next to MAT244H5 -> a clash Pip can fix in one click.
    page.get_by_label("Add a course to 2027 Winter").fill("MAT244H5")
    page.get_by_label("Add a course to 2027 Winter").press("Enter")
    page.locator(".plan-card[data-code='MAT244H5']").wait_for()
    page.get_by_text("Checking…").wait_for(state="hidden")
    page.get_by_role("link", name="Timetable").click()
    page.locator("#tt-root .badge", has_text="Sample data").wait_for()
    page.locator("#tt-term").wait_for()
    page.locator("#tt-term").select_option("2027 Winter")
    page.get_by_label("MAT224H5 · Lecture").select_option(label="LEC0102 · Tue, Thu 15:00")
    page.get_by_text("1 clash found").wait_for()
    assert "overlap on Tuesday" in page.locator(".pip-card.bad").inner_text()
    assert page.locator(".tt-block.clash").count() >= 2
    page.get_by_role("button", name="Switch MAT224H5 to LEC0101").click()
    page.get_by_text("No clashes").first.wait_for()
    assert page.locator(".tt-block.clash").count() == 0


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
