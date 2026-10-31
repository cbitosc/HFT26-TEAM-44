"""Camera registry (schema v3): add, validate, edit, deactivate, use in uploads and risk, and upgrade an old database."""
import sqlite3

from conftest import make_jpeg, sql, upload, wait_jobs
from wwdb import SCHEMA_VERSION

LAKE = {"camera_id": "52", "name": "Lake road", "latitude": 17.385, "longitude": 78.4867, "village": "Gandipet", "zone_type": "village"}


def add(c, **over):
    return c.post("/api/cameras", json={**LAKE, **over})


def test_config_cameras_are_migrated_into_the_table(client):
    rows = {r["camera_id"]: r for r in sql(client, "SELECT * FROM cameras")}
    assert set(rows) == {"46", "61", "120", "38", "7", "90"}
    zt = client.app.state.cfg["zone_types"]
    assert all(r["zone_factor"] == zt[r["zone_type"]] for r in rows.values())
    assert rows["46"]["zone_factor"] == 1.0 and rows["90"]["zone_factor"] == 1.5 and rows["7"]["zone_factor"] == 0.5
    assert sql(client, "PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION == 3


def test_add_camera_then_upload_uses_its_zone_and_village(client):
    r = add(client)
    assert r.status_code == 201, r.text
    assert "52" in [c["id"] for c in client.get("/api/cameras?active=1").json()["cameras"]]      # shows in the upload dropdown
    jid = upload(client, "golden_coyote_burst.jpg", make_jpeg(), "52", "2026-10-30T02:14").json()["jobs"][0]["job_id"]
    j = wait_jobs(client, [jid])[jid]
    a = client.get(f"/api/alerts").json()["alerts"][0]
    assert a["id"] == j["alert_id"] and a["camera"] == "52"
    assert a["village"] == "Gandipet" and a["zone"] == 1.5                       # read from the cameras table
    assert a["score"] == round(0.6 * 3 * 1.5 * 1.5, 2) == 4.05 and a["level"] == "CRITICAL"
    assert a["camera_name"] == "Lake road" and a["camera_lat"] == 17.385 and a["camera_lng"] == 78.4867
    assert sql(client, "SELECT village, zone FROM alerts")[0] == {"village": "Gandipet", "zone": 1.5}


def test_invalid_cameras_are_rejected_with_field_errors(client):
    r = add(client, camera_id="46")
    assert r.status_code == 409 and "already exists" in r.json()["fields"]["camera_id"]
    for over, field in (({"latitude": 91}, "latitude"), ({"latitude": -90.5}, "latitude"), ({"longitude": 181}, "longitude"),
                        ({"longitude": "abc"}, "longitude"), ({"latitude": None}, "latitude"), ({"village": ""}, "village"),
                        ({"village": "   "}, "village"), ({"zone_type": "volcano"}, "zone_type"), ({"camera_id": "5 2<"}, "camera_id"),
                        ({"camera_id": ""}, "camera_id")):
        r = add(client, **over)
        assert r.status_code == 422 and field in r.json()["fields"], (over, r.text)
    no_village = {k: v for k, v in LAKE.items() if k != "village"}
    assert client.post("/api/cameras", json=no_village).status_code == 422
    assert sql(client, "SELECT COUNT(*) n FROM cameras")[0]["n"] == 6


def test_edit_deactivate_and_never_delete_a_camera_with_images(client):
    assert add(client).status_code == 201
    r = client.put("/api/cameras/52", json={**LAKE, "zone_type": "farmland", "village": "Gandipet North"})
    assert r.status_code == 200 and r.json()["camera"]["zone"] == 1.2 and r.json()["camera"]["village"] == "Gandipet North"
    assert client.put("/api/cameras/52", json={**LAKE, "latitude": 200}).status_code == 422
    assert client.put("/api/cameras/nope", json=LAKE).status_code == 404
    wait_jobs(client, [upload(client, "golden_raccoon.jpg", make_jpeg(), "52", "2026-10-30T22:10").json()["jobs"][0]["job_id"]])
    assert client.delete("/api/cameras/52").status_code == 409                         # has images: keep it
    assert client.patch("/api/cameras/52", json={"active": False}).json()["camera"]["active"] is False
    assert "52" not in [c["id"] for c in client.get("/api/cameras?active=1").json()["cameras"]]
    r = upload(client, "golden_raccoon2.jpg", make_jpeg(), "52", "2026-10-30T22:11")
    assert r.status_code == 422 and "deactivated" in r.json()["error"]
    assert client.patch("/api/cameras/52", json={"active": "no"}).status_code == 422
    assert client.patch("/api/cameras/52", json={"active": True}).json()["camera"]["active"] is True
    assert add(client, camera_id="99").status_code == 201
    assert client.delete("/api/cameras/99").status_code == 200                         # never used: may be removed
    assert sql(client, "SELECT COUNT(*) n FROM cameras WHERE camera_id='52'")[0]["n"] == 1


def test_upload_needs_a_registered_camera(client):
    r = upload(client, "photo.jpg", make_jpeg(), "", "2026-10-30T02:14")
    assert r.status_code == 422 and "Choose a camera" in r.json()["error"]
    r = upload(client, "photo.jpg", make_jpeg(), "777", "2026-10-30T02:14")
    assert r.status_code == 422 and "not registered" in r.json()["error"]
    r = upload(client, "CAM46_coyote.jpg", make_jpeg(), "", "2026-10-30T02:14")        # camera taken from the file name
    assert r.status_code == 200
    assert sql(client, "SELECT camera_id FROM jobs")[0]["camera_id"] == "46"


def test_map_switches_to_real_coordinates_only_when_every_camera_has_them(client):
    assert client.get("/api/cameras").json()["layout"] == "fictional"
    add(client)
    assert client.get("/api/cameras").json()["layout"] == "fictional"                  # old cameras still have none
    for cid in ("46", "61", "120", "38", "7", "90"):
        client.patch(f"/api/cameras/{cid}", json={"active": False})
    add(client, camera_id="53", latitude=17.40, longitude=78.30, village="Osman Sagar")
    m = client.get("/api/cameras").json()
    assert m["layout"] == "real"
    pos = {c["id"]: (float(c["x"][:-1]), float(c["y"][:-1])) for c in m["cameras"] if c["active"]}
    assert all(8 <= v <= 92 for xy in pos.values() for v in xy)
    assert pos["52"][0] > pos["53"][0] and pos["52"][1] > pos["53"][1]                  # east is right, north is up
    assert {v["name"] for v in m["villages"]} == {"Gandipet", "Osman Sagar"}


def test_zone_factor_follows_config_on_restart(make_app):
    c = make_app(zone_types={"forest_edge": 2.0})
    assert sql(c, "SELECT zone_factor FROM cameras WHERE camera_id='46'")[0]["zone_factor"] == 2.0


def test_old_database_upgrades_cleanly(make_app, tmp_path):
    db = tmp_path / "data" / "ww.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)                                   # a v1 database: no cameras table, alerts without status
    con.executescript("""
      CREATE TABLE images(id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, path TEXT, camera_id TEXT, captured_at TEXT,
        night INTEGER DEFAULT 0, width INTEGER, height INTEGER, outcome TEXT, source TEXT, created_at TEXT);
      CREATE TABLE detections(id INTEGER PRIMARY KEY AUTOINCREMENT, image_id INTEGER, category TEXT, conf REAL, bbox TEXT, species TEXT, species_conf REAL);
      CREATE TABLE alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, image_id INTEGER, job_id INTEGER, species TEXT, count INTEGER,
        camera_id TEXT, village TEXT, captured_at TEXT, night INTEGER, weight REAL, zone REAL, night_factor REAL, score REAL, level TEXT,
        person INTEGER DEFAULT 0, review INTEGER DEFAULT 0, response TEXT, message TEXT, message_source TEXT, created_at TEXT);
      CREATE TABLE jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, path TEXT, size INTEGER, camera_id TEXT, capture_time TEXT,
        source TEXT, stage TEXT NOT NULL DEFAULT 'upload', state TEXT NOT NULL DEFAULT 'queued', error TEXT, result TEXT,
        image_id INTEGER, alert_id INTEGER, created_at TEXT, updated_at TEXT);
      INSERT INTO images(filename,path,camera_id,captured_at,night,width,height,outcome,source,created_at)
        VALUES('old.jpg','x','46','2026-10-29T03:00:00',1,320,240,'animal','upload','2026-10-29T03:00:00');
      INSERT INTO alerts(image_id,job_id,species,count,camera_id,village,captured_at,night,weight,zone,night_factor,score,level,response,message,message_source,created_at)
        VALUES(1,NULL,'coyote',2,'46','Rampur','2026-10-29T03:00:00',1,0.6,1.0,1.5,1.8,'HIGH','4 rangers','old alert','template','2026-10-29T03:00:00');
    """)
    con.commit(); con.close()
    c = make_app()                                              # opens the same file
    assert sql(c, "PRAGMA user_version")[0]["user_version"] == 3
    assert sql(c, "SELECT COUNT(*) n FROM cameras")[0]["n"] == 6
    old = c.get("/api/alerts").json()["alerts"]
    assert len(old) == 1 and old[0]["message"] == "old alert" and old[0]["status"] == "new" and old[0]["camera_name"] == "Rampur edge"
    assert c.post(f"/api/alerts/{old[0]['id']}/status", json={"status": "dispatched"}).status_code == 200
    assert c.get("/api/analytics?range=30d").status_code == 200
    c2 = make_app()                                             # opening again changes nothing
    assert sql(c2, "SELECT COUNT(*) n FROM cameras")[0]["n"] == 6 and sql(c2, "SELECT status FROM alerts")[0]["status"] == "dispatched"
