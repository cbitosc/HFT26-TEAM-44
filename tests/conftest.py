"""Test helpers.

WHAT IS FAKED: only the ML models. FakePipeline stands in for MegaDetector + BioCLIP, which cannot run in CI
without the GPU packages. Everything else under test is the real code: SQLite, API, upload validation,
job worker, folder watcher, risk scoring, static frontend.
Golden images are small generated JPEGs (their names tell FakePipeline what to 'see'). They are NOT real CCT photos."""
import io
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "src"       # the src/ folder
sys.path.insert(0, str(ROOT / "backend"))
from server import create_app  # noqa: E402


class FakePipeline:
    device = "test"

    def run(self, path, on_stage=lambda s: None):
        name = os.path.basename(path).lower()
        on_stage("detect")
        if "empty" in name:
            return {"outcome": "empty", "detections": [], "species": None, "count": 0, "person": False,
                    "detector_ms": 1.0, "classifier_ms": 0.0, "device": "test"}
        on_stage("species")
        on_stage("count")
        box = [0.2, 0.3, 0.4, 0.5]
        if "person" in name:
            return {"outcome": "person", "species": "person", "count": 1, "person": True, "detector_ms": 1.0, "classifier_ms": 1.0,
                    "device": "test", "detections": [{"category": "2", "conf": 0.9, "bbox": box, "species": None, "species_conf": None}]}
        if "faint" in name:
            return {"outcome": "review", "species": "leopard", "count": 1, "person": False, "detector_ms": 1.0, "classifier_ms": 1.0,
                    "device": "test", "detections": [{"category": "1", "conf": 0.12, "bbox": box, "species": "leopard", "species_conf": 0.7}]}
        sp, n = ("coyote", 3) if "coyote" in name else ("raccoon", 1)
        dets = [{"category": "1", "conf": 0.95 - 0.1 * i, "bbox": [0.1 + 0.25 * i, 0.3, 0.2, 0.4], "species": sp, "species_conf": 0.8} for i in range(n)]
        return {"outcome": "animal", "species": sp, "count": n, "person": False, "detector_ms": 1.0, "classifier_ms": 1.0,
                "device": "test", "detections": dets}


def make_jpeg(color=(90, 120, 90), size=(320, 240)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


GOLDEN = {  # filename -> (camera, capture time)
    "golden_coyote_burst.jpg": ("46", "2026-10-30T02:14"),
    "golden_raccoon.jpg": ("61", "2026-10-30T22:10"),
    "golden_person.jpg": ("38", "2026-10-30T01:47"),
    "golden_faint_leopard.jpg": ("90", "2026-10-30T04:05"),
    "golden_empty.jpg": ("7", "2026-10-30T12:00"),
}


def wait_jobs(client, ids, timeout=15):
    end = time.time() + timeout
    out = {}
    while time.time() < end:
        out = {i: client.get(f"/api/jobs/{i}").json() for i in ids}
        if all(j["state"] in ("done", "error") for j in out.values()):
            return out
        time.sleep(0.05)
    raise AssertionError(f"jobs did not finish: {out}")


def upload(client, name, data, camera="", when="", use_exif="true"):
    return client.post("/api/upload", files=[("files", (name, data, "image/jpeg"))],
                       data={"camera_id": camera, "capture_time": when, "use_exif": use_exif})


@pytest.fixture
def make_app(tmp_path):
    started = []

    def _make(real=False, **over):
        from fastapi.testclient import TestClient
        data = tmp_path / "data"
        overrides = {"paths": {"incoming": str(data / "incoming"), "db": str(data / "ww.db"), "reports": str(ROOT / "reports")},
                     "pipeline": {"warmup": False}, "watcher": {"enabled": False, "interval_seconds": 0.2}}
        for k, v in over.items():
            overrides.setdefault(k, {}).update(v)
        pipe = None if real else FakePipeline()   # None -> create_app builds the REAL pipeline
        app = create_app(overrides=overrides, pipeline=pipe)
        c = TestClient(app, base_url="http://localhost")
        c.__enter__()
        started.append(c)
        c.db_path = str(data / "ww.db")
        c.incoming = data / "incoming"
        return c

    yield _make
    for c in started:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_app):
    return make_app()


@pytest.fixture
def seeded(client):
    """Test DB built by pushing every golden image through the real upload endpoint and worker."""
    ids = []
    for name, (cam, when) in GOLDEN.items():
        r = upload(client, name, make_jpeg(), cam, when)
        assert r.status_code == 200, r.text
        ids.append(r.json()["jobs"][0]["job_id"])
    wait_jobs(client, ids)
    return client


def sql(client, q, args=()):
    c = sqlite3.connect(client.db_path)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(q, args)]
    finally:
        c.close()
