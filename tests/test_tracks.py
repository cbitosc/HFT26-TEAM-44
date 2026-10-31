"""Auto-ingest from per-camera folders, and species movement tracks across cameras."""
import time

from conftest import make_jpeg, sql, upload, wait_jobs

CAM = lambda cid, lat, lng, village: {"camera_id": cid, "name": f"Cam {cid}", "latitude": lat, "longitude": lng, "village": village, "zone_type": "forest_edge"}


def test_watcher_reads_camera_from_its_folder(make_app):
    c = make_app(watcher={"enabled": True, "interval_seconds": 0.1})
    assert c.post("/api/cameras", json=CAM("52", 17.38, 78.48, "Gandipet")).status_code == 201
    for folder, name in (("cam52", "IMG_0001_coyote.jpg"), ("61", "IMG_0002.jpg"), ("trip", "CAM46_0003.jpg"), ("nope", "IMG_0004.jpg")):
        (c.incoming / folder).mkdir(exist_ok=True)
        (c.incoming / folder / name).write_bytes(make_jpeg())
    end = time.time() + 10
    while time.time() < end:
        jobs = c.get("/api/jobs").json()["jobs"]
        if len(jobs) == 4 and all(j["state"] in ("done", "error") for j in jobs):
            break
        time.sleep(0.1)
    cams = {j["filename"]: j["camera_id"] for j in jobs}
    assert cams == {"IMG_0001_coyote.jpg": "52", "IMG_0002.jpg": "61", "CAM46_0003.jpg": "46", "IMG_0004.jpg": ""}
    assert all(j["source"] == "watcher" for j in jobs)
    a = [x for x in c.get("/api/alerts").json()["alerts"] if x["camera"] == "52"][0]
    assert a["village"] == "Gandipet"
    time.sleep(0.5)                                                  # no duplicates on later scans
    assert len(c.get("/api/jobs").json()["jobs"]) == 4


def seed_trail(c):
    for cam in (CAM("52", 17.380, 78.480, "Gandipet"), CAM("53", 17.390, 78.480, "Osman Sagar")):
        assert c.post("/api/cameras", json=cam).status_code == 201
    shots = [("cam52_coyote_a.jpg", "52", "2026-10-30T02:00"), ("cam52_coyote_b.jpg", "52", "2026-10-30T02:05"),
             ("cam53_coyote.jpg", "53", "2026-10-30T02:40"), ("cam46_coyote.jpg", "46", "2026-10-30T03:30"),
             ("cam46_coyote_late.jpg", "46", "2026-10-30T09:00"),                       # 5.5 h later: new chain, one camera only
             ("cam61_raccoon.jpg", "61", "2026-10-30T01:00"), ("cam46_raccoon.jpg", "46", "2026-10-30T05:00")]   # 4 h apart: no track
    ids = [upload(c, n, make_jpeg(), cam, t).json()["jobs"][0]["job_id"] for n, cam, t in shots]
    wait_jobs(c, ids)


def test_species_seen_at_several_cameras_becomes_one_track(client):
    seed_trail(client)
    r = client.get("/api/tracks?range=14d").json()
    assert len(r["tracks"]) == 1 and r["gap_minutes"] == 60 and "not" in r["note"]
    t = r["tracks"][0]
    assert t["species"] == "coyote" and t["route"] == "Cam 52 → Cam 53 → Cam 46" and t["cameras"] == 3
    assert [s["camera"] for s in t["stops"]] == ["52", "53", "46"]
    assert t["stops"][0]["first"] == "2026-10-30T02:00:00" and t["stops"][0]["last"] == "2026-10-30T02:05:00"   # same camera merged
    assert t["start"] == "2026-10-30T02:00:00" and t["end"] == "2026-10-30T03:30:00" and t["minutes"] == 90
    assert t["distance_km"] is None                                  # cam 46 has no coordinates: never guessed
    assert t["level"] in ("HIGH", "CRITICAL", "MEDIUM", "LOW") and t["villages"] == ["Gandipet", "Osman Sagar", "Rampur"]


def test_track_distance_uses_real_coordinates(make_app):
    c = make_app(tracks={"gap_minutes": 60})
    for cam in (CAM("52", 17.380, 78.480, "Gandipet"), CAM("53", 17.390, 78.480, "Osman Sagar")):
        c.post("/api/cameras", json=cam)
    ids = [upload(c, f"cam{cam}_coyote.jpg", make_jpeg(), cam, t).json()["jobs"][0]["job_id"] for cam, t in (("52", "2026-10-30T02:00"), ("53", "2026-10-30T02:30"))]
    wait_jobs(c, ids)
    t = c.get("/api/tracks").json()["tracks"][0]
    assert abs(t["distance_km"] - 1.11) < 0.02                      # 0.01 degree of latitude is about 1.11 km


def test_gap_setting_controls_tracks(make_app):
    c = make_app(tracks={"gap_minutes": 300})
    seed_trail(c)
    sp = sorted(t["species"] for t in c.get("/api/tracks").json()["tracks"])
    assert sp == ["coyote", "raccoon"]                               # the 4 h raccoon gap now counts
