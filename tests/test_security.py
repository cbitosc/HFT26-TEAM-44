"""Attack tests: each one tries to break the server the way an outsider would. A passing test means the attack failed."""
import io
import sqlite3
import time
from pathlib import Path

from PIL import Image

from conftest import make_jpeg, sql, upload, wait_jobs


def _png_bomb(side=20000):
    b = io.BytesIO()
    Image.new("1", (side, side)).save(b, "PNG", optimize=False)
    return b.getvalue()


# ---- files and paths -------------------------------------------------------------------------------------------------
def test_path_traversal_in_upload_filename_stays_inside_incoming(client):
    r = upload(client, "../../../../evil_" + "a" * 3000 + ".jpg", make_jpeg(), "46", "2026-10-30T02:14")
    assert r.status_code == 200, r.text
    wait_jobs(client, [r.json()["jobs"][0]["job_id"]])
    for row in sql(client, "SELECT path FROM jobs"):
        assert Path(row["path"]).resolve().parent == client.incoming.resolve()
        assert len(Path(row["path"]).name) < 255


def test_html_polyglot_cannot_be_served_as_html(client):
    evil = make_jpeg() + b"<html><script>alert(document.domain)</script></html>"
    r = client.post("/api/upload", files=[("files", ("evil.html", evil, "text/html"))], data={"camera_id": "46", "capture_time": "2026-10-30T02:14"})
    assert r.status_code == 200, r.text
    jid = r.json()["jobs"][0]["job_id"]
    j = wait_jobs(client, [jid])[jid]
    for url in (f"/api/jobs/{jid}/file", f"/api/images/{j['image_id']}/file"):
        resp = client.get(url)
        assert resp.headers["content-type"].startswith("image/"), resp.headers["content-type"]
        assert resp.headers.get("x-content-type-options") == "nosniff"
    assert not sql(client, "SELECT path FROM jobs")[0]["path"].lower().endswith((".html", ".htm", ".svg", ".js"))


def test_svg_and_html_uploads_are_rejected(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>'
    r = client.post("/api/upload", files=[("files", ("x.svg", svg, "image/svg+xml"))])
    assert r.status_code == 422 and sql(client, "SELECT COUNT(*) n FROM jobs")[0]["n"] == 0


def test_decompression_bomb_png_is_rejected_fast(client):
    data = _png_bomb()
    assert len(data) < 5_000_000
    t = time.time()
    r = upload(client, "bomb.png", data)
    assert r.status_code == 422 and "too large" in r.json()["error"].lower()
    assert time.time() - t < 20


def test_static_server_exposes_only_the_frontend_folder(client):
    for path in ("/config.yaml", "/../config.yaml", "/%2e%2e/config.yaml", "/..%2fconfig.yaml", "/data/ww.db", "/backend/server.py",
                 "/reports/metrics.json", "/.env", "/%2e%2e/%2e%2e/etc/passwd", "/frontend/../config.yaml"):
        r = client.get(path)
        assert r.status_code in (400, 404), (path, r.status_code)
        assert b"species_weight" not in r.content and b"root:" not in r.content


def test_interactive_docs_are_not_exposed(client):
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(p).status_code == 404


# ---- injection -------------------------------------------------------------------------------------------------------
def test_sql_injection_attempts_do_nothing(seeded):
    n = sql(seeded, "SELECT COUNT(*) n FROM alerts")[0]["n"]
    r = seeded.get("/api/alerts", params={"level": "' OR '1'='1"})
    assert r.status_code == 200 and r.json()["alerts"] == []
    assert seeded.get("/api/analytics", params={"range": "24h'; DROP TABLE alerts;--"}).status_code == 422
    assert seeded.get("/api/jobs/1;DROP TABLE jobs").status_code == 422
    assert seeded.post("/api/alerts/1;DELETE FROM alerts/status", json={"status": "new"}).status_code in (404, 422)
    assert sql(seeded, "SELECT COUNT(*) n FROM alerts")[0]["n"] == n


def test_camera_id_is_sanitised(client):
    r = upload(client, "a.jpg", make_jpeg(), "46<script>alert(1)</script>'--", "2026-10-30T02:14")
    assert r.status_code == 200
    wait_jobs(client, [r.json()["jobs"][0]["job_id"]])
    for t in ("jobs", "images", "alerts"):
        for row in sql(client, f"SELECT camera_id FROM {t}"):
            assert not any(ch in (row["camera_id"] or "") for ch in "<>'\"; ")


def test_negative_limit_cannot_dump_the_queue(seeded):
    assert len(seeded.get("/api/jobs", params={"limit": -1}).json()["jobs"]) == 1
    assert len(seeded.get("/api/jobs", params={"limit": 10**9}).json()["jobs"]) <= 200


# ---- request forgery and rebinding ----------------------------------------------------------------------------------
def test_cross_site_post_is_blocked(seeded):
    aid = seeded.get("/api/alerts?level=HIGH").json()["alerts"][0]["id"]
    for origin in ("http://evil.example.com", "https://localhost.evil.com", "null"):
        r = seeded.post(f"/api/alerts/{aid}/status", json={"status": "false_alarm"}, headers={"Origin": origin})
        assert r.status_code == 403, (origin, r.status_code)
        r = seeded.post("/api/upload", files=[("files", ("a.jpg", make_jpeg(), "image/jpeg"))], headers={"Origin": origin})
        assert r.status_code == 403, (origin, r.status_code)
    assert sql(seeded, "SELECT status FROM alerts WHERE id=?", (aid,))[0]["status"] == "new"


def test_form_post_without_json_content_type_is_refused(seeded):
    aid = seeded.get("/api/alerts?level=HIGH").json()["alerts"][0]["id"]
    r = seeded.post(f"/api/alerts/{aid}/status", content='{"status":"false_alarm"}', headers={"Content-Type": "text/plain"})
    assert r.status_code in (415, 422)
    assert sql(seeded, "SELECT status FROM alerts WHERE id=?", (aid,))[0]["status"] == "new"


def test_dns_rebinding_host_header_is_refused(client):
    for host in ("evil.example.com", "attacker.com:8000", "127.0.0.1.evil.com"):
        assert client.get("/api/summary", headers={"Host": host}).status_code == 400, host
    assert client.get("/api/summary", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/api/summary", headers={"Host": "127.0.0.1:8000"}).status_code == 200


def test_security_headers_present(client):
    h = client.get("/Main.dc.html").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert client.get("/api/summary").headers["cache-control"] == "no-store"


# ---- resource exhaustion ---------------------------------------------------------------------------------------------
def test_oversized_body_is_cut_off_without_buffering(make_app):
    c = make_app(pipeline={"max_upload_mb": 1})
    big = b"\x00" * (60 * 1024 * 1024)
    r = c.post("/api/upload", content=big, headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413
    def chunks():                                                    # no Content-Length: chunked transfer
        for _ in range(60):
            yield b"\x00" * (1024 * 1024)
    r = c.post("/api/upload", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413
    assert c.get("/api/summary").status_code == 200


def test_too_many_files_in_one_request(client):
    files = [("files", (f"cam46_{i}.jpg", make_jpeg(size=(40, 30)), "image/jpeg")) for i in range(25)]
    r = client.post("/api/upload", files=files)
    assert r.status_code in (413, 422) and sql(client, "SELECT COUNT(*) n FROM jobs")[0]["n"] == 0


def test_queue_flood_is_refused(make_app):
    c = make_app(pipeline={"max_queue": 3})
    c.app.state.stop.set()                                           # worker stopped, jobs pile up
    time.sleep(0.4)
    codes = [upload(c, f"cam46_{i}.jpg", make_jpeg(size=(40, 30)), "46", "2026-10-30T02:14").status_code for i in range(6)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429, 429], codes
    assert c.get("/api/summary").status_code == 200


# ---- secrets ---------------------------------------------------------------------------------------------------------
def test_api_key_never_leaves_the_server(make_app, monkeypatch):
    monkeypatch.setenv("FEATHERLESS_API_KEY", "sk-test-SECRET-123456")
    c = make_app()
    upload(c, "cam46_coyote.jpg", make_jpeg(), "46", "2026-10-30T02:14")
    time.sleep(1)
    for p in ("/api/summary", "/api/alerts", "/api/jobs", "/api/accuracy", "/api/analytics", "/api/rare", "/api/cameras", "/api/ticker", "/Main.dc.html", "/api.js"):
        body = c.get(p).text
        assert "SECRET-123456" not in body, p


def test_gitignore_hides_secrets_and_user_data():
    ig = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    for pat in (".env", "src/data/", "*.pkl", "venv/"):
        assert pat in ig, pat
    assert (Path(__file__).resolve().parents[1] / "src" / ".env.example").exists()
