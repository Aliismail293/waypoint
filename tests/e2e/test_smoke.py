"""Browser smoke test: ask -> stream -> open a source. Offline (mock LLM, in-memory stores, dev sign-in bypass).

Needs Playwright and its browser (`uv sync --group e2e && uv run --group e2e playwright install chromium`);
skipped when they are missing so the default test run and CI stay dependency-free.
"""

import socket
import threading
import time

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
    page.get_by_role("link", name="My plan").click()
    page.get_by_role("heading", name="My plan is coming soon").wait_for()
    assert page.get_by_role("link", name="My plan").get_attribute("aria-current") == "page"


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
