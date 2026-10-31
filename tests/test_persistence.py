from conftest import make_jpeg, sql, upload, wait_jobs


def test_status_survives_server_restart(make_app):
    c1 = make_app()
    jid = upload(c1, "cam46_coyote.jpg", make_jpeg(), "46", "2026-10-30T02:14").json()["jobs"][0]["job_id"]
    wait_jobs(c1, [jid])
    aid = c1.get("/api/alerts").json()["alerts"][0]["id"]
    assert c1.post(f"/api/alerts/{aid}/status", json={"status": "dispatched"}).status_code == 200
    c1.__exit__(None, None, None)                                    # server down
    c2 = make_app()                                                  # server up again on the same DB file
    a = c2.get("/api/alerts").json()["alerts"]
    assert a[0]["id"] == aid and a[0]["status"] == "dispatched"
    assert c2.get("/api/summary").json()["active_alerts"] == 0
    c2.post(f"/api/alerts/{aid}/status", json={"status": "false_alarm"})
    assert sql(c2, "SELECT status FROM alerts WHERE id=?", (aid,))[0]["status"] == "false_alarm"


def test_queued_jobs_resume_after_restart(make_app):
    c1 = make_app()
    c1.app.state.stop.set()                                          # stop the worker so the job stays queued
    import time; time.sleep(0.4)
    jid = upload(c1, "cam46_coyote.jpg", make_jpeg(), "46", "2026-10-30T02:14").json()["jobs"][0]["job_id"]
    assert c1.get(f"/api/jobs/{jid}").json()["state"] == "queued"
    c1.__exit__(None, None, None)
    c2 = make_app()
    assert wait_jobs(c2, [jid])[jid]["state"] == "done"
