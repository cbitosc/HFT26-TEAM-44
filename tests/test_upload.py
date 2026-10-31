import io
import json
import os
import time

from PIL import Image

from conftest import FakePipeline, make_jpeg, sql, upload, wait_jobs


def test_good_image_reaches_done_with_result(client):
    r = upload(client, "cam46_coyote.jpg", make_jpeg(), "46", "2026-10-30T02:14")
    assert r.status_code == 200
    jid = r.json()["jobs"][0]["job_id"]
    j = wait_jobs(client, [jid])[jid]
    assert j["state"] == "done" and j["stage"] == "done"
    res = j["result"]
    assert res["species"] == "coyote" and res["count"] == 3 and res["level"] == "HIGH" and res["village"] == "Rampur"
    assert j["alert_id"] and sql(client, "SELECT species FROM alerts WHERE id=?", (j["alert_id"],))[0]["species"] == "coyote"
    assert os.path.exists(sql(client, "SELECT path FROM images")[0]["path"])
    assert str(client.incoming) in sql(client, "SELECT path FROM images")[0]["path"]


def test_job_passes_through_real_stages(make_app, monkeypatch):
    seen = []

    class Slow(FakePipeline):
        def run(self, path, on_stage=lambda s: None):
            for s in ("detect", "species"):
                on_stage(s)
                time.sleep(0.3)
            return super().run(path, on_stage)

    c = make_app()
    c.app.state.pipeline.__class__ = Slow
    jid = upload(c, "cam46_coyote.jpg", make_jpeg(), "46", "2026-10-30T02:14").json()["jobs"][0]["job_id"]
    end = time.time() + 10
    while time.time() < end:
        j = c.get(f"/api/jobs/{jid}").json()
        if not seen or seen[-1] != j["stage"]:
            seen.append(j["stage"])
        if j["state"] == "done":
            break
        time.sleep(0.02)
    assert seen[-1] == "done" and "detect" in seen and "species" in seen


def test_empty_frame_result(client):
    jid = upload(client, "golden_empty.jpg", make_jpeg(), "7", "2026-10-30T12:00").json()["jobs"][0]["job_id"]
    j = wait_jobs(client, [jid])[jid]
    assert j["state"] == "done" and j["result"]["outcome"] == "empty" and j["alert_id"] is None
    assert sql(client, "SELECT COUNT(*) n FROM alerts")[0]["n"] == 0


def _assert_rejected_and_alive(client, r, fragment):
    assert r.status_code == 422, r.text
    body = r.json()
    assert fragment in body["error"].lower() and body["rejected"]
    assert client.get("/api/summary").status_code == 200           # server keeps running
    assert sql(client, "SELECT COUNT(*) n FROM jobs")[0]["n"] == 0  # nothing was queued


def test_zero_byte_file(client):
    _assert_rejected_and_alive(client, upload(client, "empty.jpg", b""), "0 bytes")


def test_non_image_file(client):
    r = client.post("/api/upload", files=[("files", ("notes.txt", b"hello wildlife", "text/plain"))])
    _assert_rejected_and_alive(client, r, "not an image")


def test_fake_extension_non_image(client):
    _assert_rejected_and_alive(client, upload(client, "fake.jpg", b"this is not a jpeg at all" * 50), "not an image")


def test_corrupted_image(client):
    good = make_jpeg(size=(800, 600))
    _assert_rejected_and_alive(client, upload(client, "broken.jpg", good[: len(good) // 2]), "corrupt")


def test_huge_image_dimensions(make_app):
    c = make_app(pipeline={"max_pixels": 1_000_000})
    b = io.BytesIO()
    Image.new("RGB", (2000, 2000), (10, 10, 10)).save(b, "PNG")
    _assert_rejected_and_alive(c, upload(c, "huge.png", b.getvalue()), "too large")


def test_huge_file_size(make_app):
    c = make_app(pipeline={"max_upload_mb": 0.001})
    _assert_rejected_and_alive(c, upload(c, "big.jpg", make_jpeg(size=(1200, 900))), "larger than")


def test_mixed_batch_accepts_good_and_reports_bad(client):
    files = [("files", ("cam46_ok.jpg", make_jpeg(), "image/jpeg")), ("files", ("bad.jpg", b"", "image/jpeg")),
             ("files", ("n.txt", b"x", "text/plain"))]
    r = client.post("/api/upload", files=files, data={"camera_id": "46"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["jobs"]) == 1 and len(body["rejected"]) == 2
    wait_jobs(client, [body["jobs"][0]["job_id"]])


def test_bad_capture_time_is_a_clean_error(client):
    r = upload(client, "cam46.jpg", make_jpeg(), "46", "not-a-date")
    assert r.status_code == 422 and "capture time" in r.json()["error"].lower()


def test_pipeline_crash_becomes_job_error_not_server_crash(make_app):
    class Boom(FakePipeline):
        def run(self, path, on_stage=lambda s: None):
            raise RuntimeError("detector exploded")

    c = make_app()
    c.app.state.pipeline.__class__ = Boom
    jid = upload(c, "cam46.jpg", make_jpeg(), "46", "2026-10-30T02:14").json()["jobs"][0]["job_id"]
    j = wait_jobs(c, [jid])[jid]
    assert j["state"] == "error" and "detector exploded" in j["error"]
    assert c.get("/api/summary").status_code == 200
    ok = upload(c, "cam46b.jpg", make_jpeg(), "46", "2026-10-30T02:15").json()["jobs"][0]["job_id"]
    c.app.state.pipeline.__class__ = FakePipeline
    assert wait_jobs(c, [ok])[ok]["state"] == "done"                # worker is still alive


def test_folder_watcher_creates_jobs_without_duplicates(make_app):
    c = make_app(watcher={"enabled": True, "interval_seconds": 0.1})
    (c.incoming / "CAM46_0214_burst2_coyote.jpg").write_bytes(make_jpeg())
    end = time.time() + 10
    jobs = []
    while time.time() < end:
        jobs = c.get("/api/jobs").json()["jobs"]
        if jobs and jobs[0]["state"] == "done":
            break
        time.sleep(0.1)
    assert len(jobs) == 1 and jobs[0]["source"] == "watcher" and jobs[0]["camera_id"] == "46" and jobs[0]["state"] == "done"
    upload(c, "cam61_raccoon.jpg", make_jpeg(), "61", "2026-10-30T22:10")
    time.sleep(1.0)                                                 # watcher must not re-queue uploaded files
    assert len(c.get("/api/jobs").json()["jobs"]) == 2
    assert c.get("/api/summary").json()["watcher"]["running"] is True


def test_review_threshold_logic_in_real_pipeline_config(client):
    p = client.app.state.cfg["pipeline"]
    assert p["empty_below"] < p["detect_conf"] and p["empty_below"] <= 0.05


def test_real_pipeline_missing_models_is_a_clean_job_error(make_app, tmp_path):
    """The real pipeline with a missing probe file (or missing packages) must fail the job with a message, never the server."""
    c = make_app(real=True, pipeline={"species_probe": str(tmp_path / "nope.pkl"), "warmup": True})
    jid = upload(c, "cam46_coyote.jpg", make_jpeg(), "46", "2026-10-30T02:14").json()["jobs"][0]["job_id"]
    j = wait_jobs(c, [jid])[jid]
    assert j["state"] == "error" and j["error"] and ("probe" in j["error"].lower() or "packages" in j["error"].lower()), j
    assert c.get("/api/summary").status_code == 200
