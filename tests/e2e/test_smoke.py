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


@pytest.fixture(scope="module")
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


def test_ask_stream_and_open_source(page, base_url):
    page.goto(base_url)
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
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("link", name="Course map").click()
    page.get_by_role("heading", name="Course map is coming soon").wait_for()
    assert page.get_by_role("link", name="Course map").get_attribute("aria-current") == "page"
