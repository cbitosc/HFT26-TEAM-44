"""Playwright end-to-end: real server, real browser. Only the ML models are faked (see conftest)."""
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from conftest import FakePipeline, GOLDEN, ROOT, make_jpeg, upload, wait_jobs
from server import create_app

PAGES = ["Main", "Upload", "Alerts", "Analytics", "Accuracy"]
EXTERNAL_FONTS = ("fonts.googleapis.com", "fonts.gstatic.com")     # fonts load from the internet; offline CI cannot fetch them


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    app = create_app(overrides={"paths": {"incoming": str(tmp / "incoming"), "db": str(tmp / "ww.db"), "reports": str(ROOT / "reports")},
                                "pipeline": {"warmup": False}, "watcher": {"enabled": False}}, pipeline=FakePipeline())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    end = time.time() + 10
    while not server.started and time.time() < end:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}", tmp
    server.should_exit = True
    th.join(5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_page(browser, errors):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text} @ {m.location.get('url')}")
            if m.type == "error" and not any(h in (m.location.get("url") or "") for h in EXTERNAL_FONTS) else None)
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url}") if not any(h in r.url for h in EXTERNAL_FONTS) else None)
    return page


def seed(base):
    import requests  # noqa
    for name, (cam, when) in GOLDEN.items():
        r = requests.post(base + "/api/upload", files=[("files", (name, make_jpeg(), "image/jpeg"))], data={"camera_id": cam, "capture_time": when})
        assert r.status_code == 200, r.text
    end = time.time() + 15
    while time.time() < end:
        jobs = requests.get(base + "/api/jobs").json()["jobs"]
        if len(jobs) == len(GOLDEN) and all(j["state"] == "done" for j in jobs):
            return
        time.sleep(0.1)
    raise AssertionError("seed jobs did not finish")


@pytest.mark.parametrize("name", PAGES)
def test_every_page_loads_clean_with_empty_and_with_seeded_db(live, browser, name):
    base, _ = live
    errors = []
    page = new_page(browser, errors)
    page.goto(f"{base}/{name}.dc.html")
    page.wait_for_function("document.querySelector('main') && !document.querySelector('main').classList.contains('wwloading')", timeout=10000)
    page.wait_for_timeout(500)
    text = page.inner_text("body")
    assert "{{" not in text and "}}" not in text, text[:300]
    assert "Sample data" not in text
    assert not errors, errors
    assert page.locator("[role=alert]").count() == 0                 # API-down banner must not show
    page.close()


def test_seeded_pages_show_real_values(live, browser):
    base, _ = live
    seed(base)
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Main.dc.html")
    page.wait_for_function("document.body.innerText.includes('coyotes')", timeout=10000)
    page.wait_for_timeout(2000)                                      # count-up animation
    t = page.inner_text("body")
    assert "3682" in t.replace(",", "") and "22446" in t.replace(",", "") and "13874" in t.replace(",", "")
    assert "Cam 46" in t and "{{" not in t and "Sample data" not in t
    page.goto(base + "/Analytics.dc.html")
    page.wait_for_function("document.body.innerText.includes('Coyote')", timeout=10000)
    assert "{{" not in page.inner_text("body")
    page.goto(base + "/Accuracy.dc.html")
    page.wait_for_function("document.body.innerText.includes('62.8%')", timeout=10000)
    body = page.inner_text("body")
    assert "78.9%" in body and "26.8%" in body and "59.4%" in body       # every measured number from metrics.json is on the page
    assert "62.9%" not in body                                           # the superseded mixed-split score is gone
    assert "Not measured" in body                 # unmeasured metrics say so
    assert not errors, errors
    page.close()


def test_upload_golden_image_and_wait_for_result_card(live, browser, tmp_path):
    base, _ = live
    f = tmp_path / "cam46_coyote_golden.jpg"
    f.write_bytes(make_jpeg())
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Upload.dc.html")
    page.wait_for_selector("select#cam option", state="attached")
    page.select_option("select#cam", "46")
    page.fill("#when", "2026-10-30T03:30")
    page.locator("#exif").uncheck()
    page.set_input_files("input[type=file]", str(f))
    mine = page.locator("div.rise", has_text="cam46_coyote_golden.jpg").filter(has=page.locator("a.btn")).last
    mine.wait_for(timeout=15000)                                      # this exact job's result card, not an older one
    card = mine.inner_text()
    assert "3 coyotes" in card and "Rampur" in card and "HIGH" in card and "Real model output" in card
    href = mine.locator("a.btn").get_attribute("href")
    assert href.startswith("Alerts.dc.html?id=")
    mine.locator("a.btn").click()
    page.wait_for_selector("text=3 coyotes near Rampur", timeout=10000)
    assert not errors, errors
    page.close()


def test_upload_rejects_bad_files_with_clear_message(live, browser, tmp_path):
    base, _ = live
    bad = tmp_path / "zero.jpg"; bad.write_bytes(b"")
    txt = tmp_path / "notes.txt"; txt.write_text("hello")
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Upload.dc.html")
    page.wait_for_selector("select#cam option[value='46']", state="attached")
    page.select_option("select#cam", "46")
    page.set_input_files("input[type=file]", str(bad))
    page.wait_for_selector("text=0 bytes", timeout=10000)
    page.set_input_files("input[type=file]", str(txt))
    page.wait_for_selector("text=not an image", timeout=10000)
    assert page.locator("[role=alert]").count() == 0                 # server stayed up
    assert not [e for e in errors if "422" not in e], errors         # the browser logs the 422 itself; nothing else may error
    page.close()


def test_dispatch_alert_drops_sidebar_count(live, browser):
    base, _ = live
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Alerts.dc.html")
    badge = page.locator('nav a[href="Alerts.dc.html"] span')
    page.wait_for_function("(() => { var s = document.querySelector('nav a[href=\"Alerts.dc.html\"] span'); return s && /^[0-9]+$/.test(s.innerText); })()", timeout=10000)
    before = int(badge.inner_text())
    assert before >= 1
    page.locator("button.row", has_text="coyotes near Rampur").first.click()   # a HIGH alert (LOW ones are not counted as active)
    page.wait_for_function("(() => { var i = document.querySelector('img[alt=\"Camera frame for this alert\"]'); return i && i.complete && i.naturalWidth > 0; })()", timeout=10000)
    page.click("text=Dispatch team")
    page.wait_for_function(f"document.querySelector('nav a[href=\"Alerts.dc.html\"] span').innerText === '{before - 1}'", timeout=10000)
    assert "Team dispatched" in page.inner_text("body")
    page.click("text=Reopen")                                         # and back again
    page.wait_for_function(f"document.querySelector('nav a[href=\"Alerts.dc.html\"] span').innerText === '{before}'", timeout=10000)
    page.goto(base + "/Main.dc.html")                                  # sidebar count comes from the API on every page
    page.wait_for_function(f"document.querySelector('nav a[href=\"Alerts.dc.html\"] span').innerText === '{before}'", timeout=10000)
    assert not errors, errors
    page.close()


def test_api_down_shows_clear_error(live, browser):
    base, _ = live
    page = browser.new_page()
    page.route("**/api/summary", lambda r: r.abort())
    page.goto(base + "/Main.dc.html")
    page.wait_for_selector("[role=alert]", timeout=10000)
    assert "Cannot reach the WildWatch server" in page.inner_text("[role=alert]")
    page.close()


def test_script_in_filename_is_shown_as_text_and_never_runs(live, browser, tmp_path):
    """XSS attempt: a hostile file name must render as plain text on every page that lists jobs, and no script may run."""
    base, _ = live
    evil = '"><img src=x onerror=window.__pwn=1>cam46.jpg'
    f = tmp_path / evil
    f.write_bytes(make_jpeg())
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Upload.dc.html")
    page.wait_for_selector("select#cam option", state="attached")
    page.set_input_files("input[type=file]", str(f))
    page.wait_for_selector("text=onerror=window.__pwn=1", timeout=15000)          # visible as TEXT
    assert page.locator("img[src='x']").count() == 0
    for name in PAGES:
        page.goto(f"{base}/{name}.dc.html")
        page.wait_for_timeout(700)
        assert page.evaluate("window.__pwn") is None, name
        assert page.locator("img[src='x']").count() == 0, name
    assert not [e for e in errors if "Content Security Policy" in e], errors      # the page's own scripts obey the CSP
    page.close()


def test_response_headers_lock_the_pages_down(live):
    import requests
    base, _ = live
    r = requests.get(base + "/Main.dc.html")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert requests.get(base + "/api/summary", headers={"Host": "evil.example.com"}).status_code == 400
    assert requests.post(base + "/api/upload", headers={"Origin": "http://evil.example.com"}).status_code == 403


def test_add_camera_in_form_then_upload_and_alert_show_it(live, browser, tmp_path):
    """Analytics form -> camera in Upload dropdown -> picked from a CAM53_ file name -> alert shows its name, village and coordinates."""
    base, _ = live
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Analytics.dc.html")
    page.wait_for_selector("#nc-zone option[value='village']", state="attached")
    page.click("#nc-add")                                              # empty form: inline errors, nothing sent
    assert "Camera ID is required" in page.locator("#nc-id-err").inner_text()
    assert "Village is required" in page.locator("#nc-village-err").inner_text()
    page.fill("#nc-id", "53"); page.fill("#nc-name", "Lake road"); page.fill("#nc-lat", "17.3850"); page.fill("#nc-lng", "78.4867")
    page.fill("#nc-village", "Gandipet"); page.select_option("#nc-zone", "village")
    page.click("#nc-add")
    page.wait_for_selector("text=Camera 53 added")
    page.goto(base + "/Upload.dc.html")
    page.wait_for_selector("select#cam option[value='53']", state="attached")
    assert "Gandipet" in page.locator("select#cam option[value='53']").inner_text()
    plain = tmp_path / "photo_coyote.jpg"; plain.write_bytes(make_jpeg())
    page.set_input_files("input[type=file]", str(plain))              # no camera chosen, none in the name: blocked
    page.wait_for_selector("text=Choose a camera first")
    f = tmp_path / "CAM53_coyote.jpg"; f.write_bytes(make_jpeg())
    page.set_input_files("input[type=file]", str(f))                  # camera picked from the file name
    assert page.locator("select#cam").input_value() == "53"
    card = page.locator("div.rise", has_text="CAM53_coyote.jpg").filter(has=page.locator("a.btn")).last
    card.wait_for(timeout=15000)
    card.locator("a.btn").click()
    page.wait_for_selector("text=Lake road")
    body = page.inner_text("body")
    assert "Gandipet" in body and "17.3850, 78.4867" in body
    assert not [e for e in errors if "409" not in e and "422" not in e], errors
    page.close()


def test_folder_upload_batches_and_tracks_show_on_map(live, browser, tmp_path):
    """A folder with camera subfolders (27 photos + a text file) is sent in batches of 20; each photo gets its folder's camera."""
    import requests
    base, _ = live
    trip = tmp_path / "sd_card"
    for cam, n in (("cam61", 25), ("cam38", 2)):
        (trip / cam).mkdir(parents=True)
        for i in range(n):
            (trip / cam / f"IMG_{i:04d}_coyote.jpg").write_bytes(make_jpeg(size=(64, 48)))
    (trip / "readme.txt").write_text("not a photo")
    before = len(requests.get(base + "/api/jobs?limit=200").json()["jobs"])
    errors = []
    page = new_page(browser, errors)
    page.goto(base + "/Upload.dc.html")
    page.wait_for_selector("select#cam option[value='61']", state="attached")
    page.set_input_files("#folder", str(trip))                        # the "Upload a whole folder" button
    page.wait_for_selector("text=27 photos sent", timeout=20000)
    jobs = requests.get(base + "/api/jobs?limit=200").json()["jobs"][: 27]
    assert len(requests.get(base + "/api/jobs?limit=200").json()["jobs"]) - before == 27
    by = {}
    for j in jobs:
        by[j["camera_id"]] = by.get(j["camera_id"], 0) + 1
    assert by == {"61": 25, "38": 2}
    ids = [j["id"] for j in jobs]
    end = time.time() + 20
    while time.time() < end and not all(requests.get(f"{base}/api/jobs/{i}").json()["state"] == "done" for i in ids):
        time.sleep(0.3)
    page.goto(base + "/Main.dc.html")
    page.wait_for_selector("svg[aria-label='Movement tracks'] polyline", state="attached", timeout=15000)
    assert "Movement: Coyote" in page.inner_text("body")
    page.goto(base + "/Analytics.dc.html")
    page.wait_for_selector("section[aria-label='Movement tracks'] >> text=Coyote", timeout=15000)
    assert not [e for e in errors if "409" not in e and "422" not in e], errors
    page.close()
