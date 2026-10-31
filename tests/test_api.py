import json

from conftest import GOLDEN, sql


def test_summary_matches_sql(seeded):
    s = seeded.get("/api/summary").json()
    assert s["images_processed"] == sql(seeded, "SELECT COUNT(*) n FROM images")[0]["n"] == len(GOLDEN)
    assert s["empty_removed"] == sql(seeded, "SELECT COUNT(*) n FROM images WHERE outcome='empty'")[0]["n"] == 1
    assert s["review_queue"] == 1
    assert s["species_count"] == sql(seeded, "SELECT COUNT(DISTINCT species) n FROM alerts WHERE person=0")[0]["n"] == 3
    assert s["active_alerts"] == sql(seeded, "SELECT COUNT(*) n FROM alerts WHERE status='new' AND level!='LOW'")[0]["n"]
    assert s["bursts"] == 5            # five different cameras -> five bursts
    assert s["last_image_at"] and s["watcher"]["status"]
    assert set(s) >= {"queue_depth", "detector", "classifier", "avg_detector_ms", "device", "last_image_ago_s"}


def test_summary_empty_db_is_honest(client):
    s = client.get("/api/summary").json()
    assert s["images_processed"] == 0 and s["last_image_at"] is None and s["last_image_ago_s"] is None
    assert s["avg_detector_ms"] is None


def test_alerts_shape_and_values(seeded):
    a = seeded.get("/api/alerts").json()
    rows = sql(seeded, "SELECT * FROM alerts ORDER BY id")
    assert len(a["alerts"]) == len(rows) == 4          # 5 images, 1 empty -> 4 alerts
    by = {x["species"]: x for x in a["alerts"]}
    coy = by["coyote"]
    for k in ("id", "species", "count", "camera", "village", "time", "night", "weight", "zone", "score", "level", "response",
              "message", "message_source", "status", "image_url", "boxes", "title", "level_label"):
        assert k in coy
    assert coy["count"] == 3 and coy["village"] == "Rampur" and coy["time"] == "02:14" and coy["night"] is True
    assert coy["score"] == round(0.6 * 3 * 1.5 * 1.0, 2) == 2.7 and coy["level"] == "HIGH"
    assert coy["message_source"] == "template" and len(coy["boxes"]) == 3
    assert by["raccoon"]["level"] == "LOW"
    assert by["person"]["level_label"] == "PERSON" and by["person"]["level"] in ("HIGH", "CRITICAL")
    msg = (by["person"]["message"] + " " + by["person"]["title"]).lower()
    assert "poacher" not in msg and "staff" in msg       # a person is never accused: it may be a ranger
    assert seeded.get(coy["image_url"]).status_code == 200


def test_faint_detection_still_raises_an_alert(seeded):
    f = [x for x in seeded.get("/api/alerts").json()["alerts"] if x["species"] == "leopard"][0]
    assert f["count"] >= 1 and f["review"] is True and f["level"] in ("MEDIUM", "HIGH", "CRITICAL")


def test_alert_level_filter(seeded):
    high = seeded.get("/api/alerts?level=HIGH").json()["alerts"]
    assert high and all(a["level"] == "HIGH" and not a["person"] for a in high)
    assert [a["species"] for a in seeded.get("/api/alerts?level=PERSON").json()["alerts"]] == ["person"]
    assert [a["species"] for a in seeded.get("/api/alerts?level=POACHER").json()["alerts"]] == ["person"]   # old alias still works
    assert len(seeded.get("/api/alerts?level=ALL").json()["alerts"]) == 4


def test_status_change_and_validation(seeded):
    aid = seeded.get("/api/alerts?level=HIGH").json()["alerts"][0]["id"]
    before = seeded.get("/api/summary").json()["active_alerts"]
    assert seeded.post(f"/api/alerts/{aid}/status", json={"status": "dispatched"}).json() == {"id": aid, "status": "dispatched"}
    assert sql(seeded, "SELECT status FROM alerts WHERE id=?", (aid,))[0]["status"] == "dispatched"
    assert seeded.get("/api/summary").json()["active_alerts"] == before - 1
    assert seeded.post(f"/api/alerts/{aid}/status", json={"status": "new"}).status_code == 200
    assert seeded.post(f"/api/alerts/{aid}/status", json={"status": "bogus"}).status_code == 422
    assert seeded.post("/api/alerts/99999/status", json={"status": "new"}).status_code == 404


def test_analytics_shapes_and_values(seeded):
    for rng in ("24h", "14d", "30d"):
        a = seeded.get(f"/api/analytics?range={rng}").json()
        n = {"24h": 24, "14d": 14, "30d": 30}[rng]
        assert len(a["series"]["night"]) == len(a["series"]["day"]) == len(a["labels"]) == n and len(a["ticks"]) == 5
        assert a["totals"]["images"] == 5 and a["totals"]["empty"] == 1
        animals = sum(r["count"] for r in sql(seeded, "SELECT count FROM alerts WHERE person=0"))
        assert sum(a["series"]["night"]) + sum(a["series"]["day"]) == animals == a["totals"]["sightings"] == 5
        assert sum(d["n"] for d in a["species"]) == animals
    a = seeded.get("/api/analytics?range=24h").json()
    sp = {d["name"]: d for d in a["species"]}
    assert sp["coyote"]["night"] == 3 and sp["coyote"]["day"] == 0 and sp["raccoon"]["night"] == 1
    assert a["totals"]["night_share"] == 100.0
    act = {d["name"]: d["hours"] for d in a["activity"]}
    assert len(act["coyote"]) == 24 and act["coyote"][2] == 3
    cams = {c["id"]: c for c in a["cameras"]}
    assert cams["46"]["animals"] == 3 and cams["46"]["top"] == "coyote" and cams["7"]["empty_share"] == 100
    assert cams["120"]["images"] == 0 and cams["120"]["empty_share"] is None and cams["120"]["risk"] is None
    assert seeded.get("/api/analytics?range=7y").status_code == 422


def test_accuracy_reports_real_and_null(client, tmp_path):
    a = client.get("/api/accuracy").json()
    m = json.load(open(client.app.state.cfg["paths"]["reports"] + "/metrics.json"))
    assert a["species_accuracy"] == m["species_accuracy"] and a["evaluated"] is True
    assert a["empty_filter"] is None and a["day_night"] is None and a["confusion"] is None and a["generalisation"] is None
    assert any(x["metric"] is None for x in a["models"])           # SpeciesNet was never run


def test_accuracy_with_no_reports_is_all_null(make_app, tmp_path):
    c = make_app(paths={"reports": str(tmp_path / "empty_reports")})
    a = c.get("/api/accuracy").json()
    assert a["evaluated"] is False and a["models"] is None and a["species_accuracy"] is None and a["model_selection_md"] is None


def test_rare_census_and_sightings(seeded):
    r = {x["key"]: x for x in seeded.get("/api/rare").json()["species"]}
    assert r["tiger"]["count"] == 3682 and "2022" in r["tiger"]["source"]
    assert r["elephant"]["count"] == 22446 and "2025" in r["elephant"]["source"]
    assert r["leopard"]["count"] == 13874
    assert r["tiger"]["cameras_this_month"] == 0 and r["elephant"]["cameras_this_month"] == 0
    assert r["leopard"]["cameras_this_month"] == sql(seeded, "SELECT COALESCE(SUM(count),0) n FROM alerts WHERE species='leopard'")[0]["n"] == 1


def test_ticker_latest_ten(seeded):
    t = seeded.get("/api/ticker").json()["items"]
    assert len(t) == 5 and all({"time", "text", "color"} <= set(i) for i in t)
    assert any("Empty frame filtered" in i["text"] for i in t) and any("coyote" in i["text"] for i in t)


def test_cameras_states(seeded):
    c = seeded.get("/api/cameras").json()
    st = {x["id"]: x["state"] for x in c["cameras"]}
    assert st["46"] == "alert" and st["61"] == "seen" and st["120"] == "quiet"
    assert len(c["villages"]) == 3


def test_no_design_leftovers_in_pages(client):
    for p in ("Main", "Upload", "Alerts", "Analytics", "Accuracy"):
        t = client.get(f"/{p}.dc.html").text
        for bad in ("Daily report", "Up from", "Sample data", "Sample alert", "Sample result", "[Phase", "[—", "Try a demo"):
            assert bad not in t, (p, bad)
    lv = client.get("/api/alerts").json()["levels"]
    assert lv == {"CRITICAL": 3.0, "HIGH": 1.5, "MEDIUM": 0.5}
    assert "trend" not in client.get("/api/rare").json()["species"][0]


def test_frontend_is_served(client):
    assert client.get("/").status_code == 200
    for p in ("Main", "Upload", "Alerts", "Analytics", "Accuracy"):
        r = client.get(f"/{p}.dc.html")
        assert r.status_code == 200 and "Sample data" not in r.text and "api.js" in r.text
