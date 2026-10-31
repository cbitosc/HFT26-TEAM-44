"""WildWatch live dashboard server (FastAPI). Run:  python server.py   or   run.ps1 dashboard"""
import io
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

import risk
from messages import make_message
from wwdb import DB

log = logging.getLogger("wildwatch")
BASE = Path(__file__).resolve().parent.parent      # src/ (holds config.yaml, frontend/, reports/, data/)
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FMT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}      # saved extension comes from the REAL format, never the filename
FMT_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp"}
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'")
OK_FORMATS = {"JPEG", "PNG", "WEBP", "BMP"}
STAGES = ["upload", "detect", "species", "count", "risk", "done"]
TS = "%Y-%m-%dT%H:%M:%S"


# ------------------------------------------------------------------------------------------ config
def _merge(a, b):
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def load_config(path=None, overrides=None):
    path = Path(path or BASE / "config.yaml")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    _merge(cfg, overrides)
    root = path.parent
    for k, v in cfg["paths"].items():
        p = Path(v)
        cfg["paths"][k] = str(p if p.is_absolute() else root / p)
    return cfg


class StatusBody(BaseModel):
    status: Literal["new", "dispatched", "false_alarm"]


def load_dotenv(path):
    """Tiny .env reader: KEY=value lines; never overrides a variable that is already set; never logs values."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass


def clean_camera(s):
    return re.sub(r"[^A-Za-z0-9_-]", "", s or "")[:16]


def now_s():
    return datetime.now().strftime(TS)


# ----------------------------------------------------------------------------------- image checks
def check_image(data: bytes, cfg):
    """Return (error, width, height, exif_time). error is None when the file is a usable image."""
    p = cfg["pipeline"]
    if len(data) == 0:
        return "File is empty (0 bytes).", 0, 0, None
    if len(data) > p["max_upload_mb"] * 1024 * 1024:
        return f"File is larger than {p['max_upload_mb']} MB.", 0, 0, None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            fmt, (w, h) = im.format, im.size
    except Image.DecompressionBombError:
        return "Image is too large (decompression bomb refused).", 0, 0, None
    except (UnidentifiedImageError, OSError, ValueError):
        return "This is not an image file.", 0, 0, None
    if fmt not in OK_FORMATS:
        return f"Unsupported image type ({fmt}). Use JPG or PNG.", w, h, None
    if w * h > p["max_pixels"]:
        return f"Image is too large ({w}x{h} pixels).", w, h, None
    exif_time = None
    try:
        raw = im.getexif()
        s = (raw.get_ifd(0x8769).get(36867) or raw.get(306)) if raw else None
        if s:
            exif_time = datetime.strptime(str(s).strip(), "%Y:%m:%d %H:%M:%S").strftime(TS)
    except Exception:
        exif_time = None
    try:
        im2 = Image.open(io.BytesIO(data))
        im2.load()                                   # catches truncated / corrupted pixel data
    except Exception:
        return "The image file is corrupted and cannot be read.", w, h, None
    return None, w, h, exif_time


def parse_camera(filename):
    m = re.search(r"cam[_\-\s]?(\d+)", filename, re.I)
    return m.group(1) if m else ""


def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip()).strftime(TS)
    except ValueError:
        return None


# ------------------------------------------------------------------------------------ security
class Guard:
    """Pure-ASGI middleware: Host allow-list (DNS rebinding), Origin check on writes (CSRF), request-body cap
    (also for chunked bodies), and security headers on every response."""

    def __init__(self, app, max_body):
        self.app, self.max_body = app, max_body

    @staticmethod
    def _host(h):
        h = (h or "").strip().lower()
        return h.rsplit(":", 1)[0] if (":" in h and not h.endswith("]")) else h

    async def _reply(self, send, status, msg):
        body = json.dumps({"error": msg}).encode()
        hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + self._sec(b"/api")
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    def _sec(path):
        h = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"),
             (b"content-security-policy", CSP.encode()), (b"cross-origin-opener-policy", b"same-origin"),
             (b"cross-origin-resource-policy", b"same-origin")]
        if path.startswith(b"/api"):
            h.append((b"cache-control", b"no-store"))
        return h

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        hd = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if scope["type"] == "websocket":
            return
        if self._host(hd.get("host")) not in ALLOWED_HOSTS:
            return await self._reply(send, 400, "Invalid Host header.")
        method = scope["method"]
        if method not in ("GET", "HEAD", "OPTIONS"):
            origin = hd.get("origin")
            if origin is not None:
                m = re.match(r"^https?://([^/:]+|\[[^\]]+\])(:\d+)?$", origin.lower())
                if not m or m.group(1) not in ALLOWED_HOSTS:
                    return await self._reply(send, 403, "Cross-site request refused.")
            cl = hd.get("content-length")
            if cl is not None and cl.isdigit() and int(cl) > self.max_body:
                return await self._reply(send, 413, "Request is too large.")
        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_body:
                    raise HTTPException(413, "Request is too large.")
            return msg

        async def send2(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                names = {bytes(k).lower() for k, _ in msg["headers"]}
                msg = dict(msg, headers=list(msg["headers"]) + [h for h in self._sec(scope["path"].encode()) if h[0] not in names])
            await send(msg)

        try:
            await self.app(scope, limited_receive, send2)
        except HTTPException as e:
            if not started:
                await self._reply(send, e.status_code, str(e.detail))


# ------------------------------------------------------------------------------------------- app
def create_app(config_path=None, pipeline=None, overrides=None, start_worker=True, start_watcher=None):
    load_dotenv(BASE / ".env")
    cfg = load_config(config_path, overrides)
    Image.MAX_IMAGE_PIXELS = int(cfg["pipeline"]["max_pixels"])
    for k in ("incoming", "reports"):
        os.makedirs(cfg["paths"][k], exist_ok=True)
    os.makedirs(os.path.dirname(cfg["paths"]["db"]), exist_ok=True)
    db = DB(cfg["paths"]["db"])
    if pipeline is None:
        from pipeline import RealPipeline
        pipeline = RealPipeline(cfg)
    cams = {c["id"]: c for c in cfg["cameras"]}
    app = FastAPI(title="WildWatch", docs_url=None, redoc_url=None, openapi_url=None)
    pl = cfg["pipeline"]
    max_files, max_queue = int(pl.get("max_files_per_request", 20)), int(pl.get("max_queue", 200))
    app.add_middleware(Guard, max_body=(pl["max_upload_mb"] * max_files + 1) * 1024 * 1024)
    jobq: "queue.Queue[int]" = queue.Queue()
    stop = threading.Event()
    app.state.cfg, app.state.db, app.state.jobq, app.state.stop = cfg, db, jobq, stop
    app.state.pipeline = pipeline
    app.state.watcher_alive = False
    app.state.pipeline_error = None

    def cam_info(cid):
        c = cams.get(cid)
        return c or {"id": cid or "?", "name": "Unknown camera", "village": "Unknown", "zone": 1.0}

    # ---------------------------------------------------------------------------- job processing
    def set_job(jid, **kw):
        kw["updated_at"] = now_s()
        db.x(f"UPDATE jobs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), jid))

    def process(jid):
        job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not job or job["state"] in ("done", "error"):
            return
        try:
            set_job(jid, state="running", stage="detect")
            if not os.path.exists(job["path"]):
                raise FileNotFoundError("The image file is missing from data/incoming.")
            with open(job["path"], "rb") as f:
                err, w, h, exif_time = check_image(f.read(), cfg)
            if err:
                raise ValueError(err)
            res = pipeline.run(job["path"], lambda s: set_job(jid, stage=s))
            set_job(jid, stage="count")
            when = job["capture_time"] or exif_time or datetime.fromtimestamp(os.path.getmtime(job["path"])).strftime(TS)
            dt = datetime.strptime(when, TS)
            night = 1 if risk.is_night(dt) else 0
            cid = job["camera_id"] or ""
            outcome = res["outcome"]
            image_id = db.x("INSERT INTO images(filename,path,camera_id,captured_at,night,width,height,outcome,source,created_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (job["filename"], job["path"], cid, when, night, w, h, outcome, job["source"], now_s()))
            for d in res.get("detections", []):
                db.x("INSERT INTO detections(image_id,category,conf,bbox,species,species_conf) VALUES(?,?,?,?,?,?)",
                     (image_id, d["category"], d["conf"], json.dumps(d["bbox"]), d.get("species"), d.get("species_conf")))
            alert_id, summary = None, {"outcome": outcome}
            set_job(jid, stage="risk")
            if outcome != "empty" and res.get("species"):
                ci = cam_info(cid)
                person = outcome == "person"
                a = risk.assess(res["species"], res["count"], night, ci["zone"], person, cfg)
                if outcome == "review" and a["level"] == "LOW":
                    pass                                    # review frames still raise a LOW alert, never nothing
                alert = {"species": res["species"], "count": a["count"], "camera_id": cid or "?", "village": ci["village"],
                         "captured_at": when, "night": night, "person": int(person), "review": int(outcome == "review"), **a}
                text, src = make_message(alert, cfg)
                alert_id = db.x(
                    "INSERT INTO alerts(image_id,job_id,species,count,camera_id,village,captured_at,night,weight,zone,night_factor,"
                    "score,level,person,review,response,message,message_source,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (image_id, jid, alert["species"], a["count"], alert["camera_id"], alert["village"], when, night, a["weight"],
                     a["zone"], a["night_factor"], a["score"], a["level"], int(person), int(outcome == "review"), a["response"],
                     text, src, "new", now_s()))
                summary.update(species=alert["species"], species_label=risk.plural(alert["species"], a["count"]), count=a["count"], level="PERSON" if person else a["level"],
                               response=a["response"], village=alert["village"], review=outcome == "review")
            summary.update(detector_ms=res.get("detector_ms"), classifier_ms=res.get("classifier_ms"), device=res.get("device"),
                           top_conf=max([d["conf"] for d in res.get("detections", [])], default=None),
                           species_conf=max([d["species_conf"] or 0 for d in res.get("detections", [])], default=None))
            set_job(jid, state="done", stage="done", image_id=image_id, alert_id=alert_id, result=json.dumps(summary))
        except Exception as e:                              # never crash the worker
            log.exception("job %s failed", jid)
            set_job(jid, state="error", stage="error", error=str(e) or e.__class__.__name__)

    def worker():
        try:                                                 # warm the models so the first upload is fast
            if cfg["pipeline"].get("warmup") and hasattr(pipeline, "load"):
                pipeline.load()
        except Exception as e:
            app.state.pipeline_error = str(e)
            log.warning("pipeline warm-up failed: %s", e)
        while not stop.is_set():
            try:
                jid = jobq.get(timeout=0.2)
            except queue.Empty:
                continue
            process(jid)

    def new_job(path, filename, size, source, camera_id="", capture_time=""):
        return db.x("INSERT INTO jobs(filename,path,size,camera_id,capture_time,source,stage,state,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (filename, str(path), size, camera_id, capture_time or None, source, "upload", "queued", now_s(), now_s()))
    app.state.new_job = new_job

    def watcher():
        app.state.watcher_alive = True
        seen = {}
        while not stop.is_set():
            try:
                for p in Path(cfg["paths"]["incoming"]).iterdir():
                    if p.suffix.lower() not in IMG_EXT or not p.is_file():
                        continue
                    key = str(p)
                    size = p.stat().st_size
                    if seen.get(key) != size:               # wait until the camera finished writing
                        seen[key] = size
                        continue
                    if db.one("SELECT id FROM jobs WHERE path=?", (key,)):
                        continue
                    jid = new_job(key, p.name[:120], size, "watcher", clean_camera(parse_camera(p.name)))
                    jobq.put(jid)
            except Exception:
                log.exception("watcher error")
            stop.wait(cfg["watcher"]["interval_seconds"])
        app.state.watcher_alive = False

    @app.on_event("startup")
    def _startup():
        for j in db.q("SELECT id FROM jobs WHERE state IN ('queued','running') ORDER BY id"):
            db.x("UPDATE jobs SET state='queued', stage='upload' WHERE id=?", (j["id"],))
            jobq.put(j["id"])
        if start_worker:
            threading.Thread(target=worker, daemon=True, name="ww-worker").start()
        if (cfg["watcher"]["enabled"] if start_watcher is None else start_watcher):
            threading.Thread(target=watcher, daemon=True, name="ww-watcher").start()

    @app.on_event("shutdown")
    def _shutdown():
        stop.set()

    # --------------------------------------------------------------------------------- helpers
    def level_label(r):
        return "PERSON" if r["person"] else r["level"]

    def alert_out(r):
        boxes = []
        det = db.q("SELECT category,conf,bbox,species FROM detections WHERE image_id=? AND conf>=?", (r["image_id"], cfg["pipeline"]["empty_below"]))
        for d in det:
            x, y, w, h = json.loads(d["bbox"])
            boxes.append({"x": x, "y": y, "w": w, "h": h, "label": d["species"] or ("person" if d["category"] == "2" else "animal"),
                          "conf": d["conf"]})
        img = db.one("SELECT width,height FROM images WHERE id=?", (r["image_id"],)) or {}
        ci = cam_info(r["camera_id"])
        return {"id": r["id"], "species": r["species"], "count": r["count"], "camera": r["camera_id"], "camera_name": ci["name"],
                "village": r["village"], "captured_at": r["captured_at"], "time": r["captured_at"][11:16], "night": bool(r["night"]),
                "weight": r["weight"], "zone": r["zone"], "night_factor": r["night_factor"], "score": r["score"], "level": r["level"],
                "level_label": level_label(r), "species_label": risk.plural(r["species"], r["count"]), "person": bool(r["person"]), "review": bool(r["review"]),
                "response": r["response"], "message": r["message"], "message_source": r["message_source"],
                "status": r["status"], "image_url": f"/api/images/{r['image_id']}/file", "image_w": img.get("width"),
                "image_h": img.get("height"), "boxes": boxes, "title": risk.title({**r, "person": bool(r["person"])})}

    def anchor():
        r = db.one("SELECT MAX(captured_at) m FROM images")
        return datetime.strptime(r["m"], TS) if r and r["m"] else datetime.now()

    # ------------------------------------------------------------------------------------- API
    @app.get("/api/summary")
    def summary():
        imgs = db.q("SELECT camera_id,captured_at,outcome FROM images ORDER BY camera_id,captured_at")
        gap, bursts, last_cam, last_t = cfg["pipeline"]["burst_seconds"], 0, None, None
        for r in imgs:
            t = datetime.strptime(r["captured_at"], TS)
            if r["camera_id"] != last_cam or last_t is None or (t - last_t).total_seconds() > gap:
                bursts += 1
            last_cam, last_t = r["camera_id"], t
        last = db.one("SELECT MAX(created_at) m FROM images")["m"]
        ago = int((datetime.now() - datetime.strptime(last, TS)).total_seconds()) if last else None
        sp = db.one("SELECT COUNT(DISTINCT species) n FROM alerts WHERE person=0")["n"]
        perf = db.q("SELECT result FROM jobs WHERE state='done' AND result IS NOT NULL ORDER BY id DESC LIMIT 50")
        det_ms, cls_ms, dev = [], [], None
        for p in perf:
            r = json.loads(p["result"])
            if r.get("detector_ms") is not None:
                det_ms.append(r["detector_ms"])
            if r.get("classifier_ms"):
                cls_ms.append(r["classifier_ms"])
            dev = dev or r.get("device")
        avg = lambda a: round(sum(a) / len(a), 1) if a else None
        return {"images_processed": len(imgs), "empty_removed": sum(1 for r in imgs if r["outcome"] == "empty"),
                "review_queue": sum(1 for r in imgs if r["outcome"] == "review"), "bursts": bursts, "species_count": sp,
                "active_alerts": db.one("SELECT COUNT(*) n FROM alerts WHERE status='new' AND level!='LOW'")["n"],
                "last_image_at": last, "last_image_ago_s": ago,
                "watcher": {"running": app.state.watcher_alive, "folder": "data/incoming/",
                            "status": "Watching incoming folder" if app.state.watcher_alive else "Folder watcher off"},
                "queue_depth": db.one("SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')")["n"],
                "detector": cfg["pipeline"]["detector_label"], "classifier": cfg["pipeline"]["classifier_label"],
                "avg_detector_ms": avg(det_ms), "avg_classifier_ms": avg(cls_ms), "device": dev,
                "pipeline_error": app.state.pipeline_error}

    @app.get("/api/cameras")
    def cameras():
        end = anchor()
        since = (end - timedelta(hours=24)).strftime(TS)
        out = []
        for c in cfg["cameras"]:
            act = db.one("SELECT COUNT(*) n FROM alerts WHERE camera_id=? AND status='new' AND level!='LOW'", (c["id"],))["n"]
            seen = db.one("SELECT COUNT(*) n FROM alerts WHERE camera_id=? AND captured_at>=?", (c["id"], since))["n"]
            out.append({**c, "state": "alert" if act else "seen" if seen else "quiet"})
        return {"cameras": out, "villages": cfg["villages"]}

    @app.get("/api/alerts")
    def alerts(level: str = Query("ALL")):
        rows = db.q("SELECT * FROM alerts ORDER BY captured_at DESC, id DESC")
        lv = level.upper()
        if lv in ("PERSON", "POACHER"):              # POACHER kept as an alias for old links
            rows = [r for r in rows if r["person"]]
        elif lv != "ALL":
            rows = [r for r in rows if r["level"] == lv and not r["person"]]
        return {"alerts": [alert_out(r) for r in rows], "open_count": db.one("SELECT COUNT(*) n FROM alerts WHERE status='new' AND level!='LOW'")["n"],
                "levels": cfg["risk"]["levels"]}

    @app.post("/api/alerts/{aid}/status")
    def alert_status(aid: int, body: StatusBody):
        if not db.one("SELECT id FROM alerts WHERE id=?", (aid,)):
            raise HTTPException(404, "Alert not found")
        db.x("UPDATE alerts SET status=?, status_updated_at=? WHERE id=?", (body.status, now_s(), aid))
        return {"id": aid, "status": body.status}

    @app.get("/api/ticker")
    def ticker():
        out = []
        for r in db.q("SELECT i.id,i.camera_id,i.captured_at,i.outcome,a.species,a.count,a.level,a.person FROM images i "
                      "LEFT JOIN alerts a ON a.image_id=i.id ORDER BY i.captured_at DESC, i.id DESC LIMIT 10"):
            cam = r["camera_id"] or "?"
            if r["outcome"] == "empty":
                text, color = f"Cam {cam} · Empty frame filtered", "#4E6B5F"
            elif r["person"]:
                text, color = f"Cam {cam} · Person detected · VERIFY", "#B9A3FF"
            elif r["species"]:
                text = f"Cam {cam} · {r['count']} {risk.plural(r['species'], r['count'])} · {r['level']}"
                color = {"CRITICAL": "#FF3D6E", "HIGH": "#FF6B5B", "MEDIUM": "#F2A93B", "LOW": "#5FD39A"}[r["level"]]
            else:
                continue
            out.append({"time": r["captured_at"][11:16], "text": text, "color": color})
        return {"items": out}

    def bucketize(rng, end):
        if rng == "24h":
            end_h = end.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            n, step = 24, timedelta(hours=1)
            start = end_h - n * step
            lab = [(start + i * step).strftime("%H") for i in range(n)]
            ticks = ["00", "06", "12", "18", "23"]
            label = f"Last 24 hours to {end.strftime('%d %b %H:%M')}"
            idx = lambda t: int((t - start) / step)
        else:
            n = 14 if rng == "14d" else 30
            end_d = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            step = timedelta(days=1)
            start = end_d - n * step
            lab = [(start + i * step).strftime("%b %d").replace(" 0", " ") for i in range(n)]
            ticks = [lab[round(i * (n - 1) / 4)] for i in range(5)]
            label = f"Last {n} days to {end.strftime('%d %b')}"
            idx = lambda t: int((t - start) / step)
        return start, start + n * step, n, lab, ticks, label, idx

    @app.get("/api/analytics")
    def analytics(range: Literal["24h", "14d", "30d"] = "14d"):
        end = anchor()
        start, stop_t, n, lab, ticks, label, idx = bucketize(range, end)
        s, e = start.strftime(TS), stop_t.strftime(TS)
        imgs = db.q("SELECT * FROM images WHERE captured_at>=? AND captured_at<?", (s, e))
        al = db.q("SELECT * FROM alerts WHERE captured_at>=? AND captured_at<? AND person=0", (s, e))
        night_s, day_s = [0] * n, [0] * n
        spc, act = {}, {}
        for a in al:
            t = datetime.strptime(a["captured_at"], TS)
            i = idx(t)
            if 0 <= i < n:
                (night_s if a["night"] else day_s)[i] += a["count"]
            d = spc.setdefault(a["species"], {"name": a["species"], "n": 0, "day": 0, "night": 0})
            d["n"] += a["count"]
            d["night" if a["night"] else "day"] += a["count"]
            act.setdefault(a["species"], [0] * 24)[t.hour] += a["count"]
        species = sorted(spc.values(), key=lambda d: -d["n"])
        tot = sum(d["n"] for d in species)
        night_total = sum(d["night"] for d in species)
        gap, bursts, last_cam, last_t = cfg["pipeline"]["burst_seconds"], 0, None, None
        for r in sorted(imgs, key=lambda r: (r["camera_id"], r["captured_at"])):
            t = datetime.strptime(r["captured_at"], TS)
            if r["camera_id"] != last_cam or last_t is None or (t - last_t).total_seconds() > gap:
                bursts += 1
            last_cam, last_t = r["camera_id"], t
        empty = sum(1 for r in imgs if r["outcome"] == "empty")
        cam_rows = []
        since24 = (end - timedelta(hours=24)).strftime(TS)
        ids = [c["id"] for c in cfg["cameras"]] + sorted({r["camera_id"] for r in imgs if r["camera_id"] not in cams})
        for cid in ids:
            ci = cam_info(cid)
            ci_imgs = [r for r in imgs if r["camera_id"] == cid]
            ci_al = [a for a in al if a["camera_id"] == cid]
            sp = {}
            for a in ci_al:
                sp[a["species"]] = sp.get(a["species"], 0) + a["count"]
            recent = db.q("SELECT level,person FROM alerts WHERE camera_id=? AND captured_at>=?", (cid, since24))
            order = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
            rk = max((order.index(r["level"]) for r in recent), default=None)
            cam_rows.append({"id": cid, "name": ci["name"], "village": f"{ci['name']}" if cid in cams else "Unknown camera",
                             "images": len(ci_imgs), "animals": sum(sp.values()),
                             "empty_share": round(100 * sum(1 for r in ci_imgs if r["outcome"] == "empty") / len(ci_imgs)) if ci_imgs else None,
                             "top": max(sp, key=sp.get) if sp else None, "risk": order[rk] if rk is not None else None})
        return {"range": range, "label": label, "ticks": ticks, "labels": lab,
                "totals": {"images": len(imgs), "with_animals": sum(1 for r in imgs if r["outcome"] != "empty"), "empty": empty,
                           "bursts": bursts, "sightings": tot,
                           "night_share": round(100 * night_total / tot, 1) if tot else None,
                           "empty_pct": round(100 * empty / len(imgs), 1) if imgs else None},
                "series": {"night": night_s, "day": day_s},
                "species": species,
                "activity": [{"name": d["name"], "hours": act[d["name"]]} for d in species[:8]],
                "cameras": cam_rows}

    @app.get("/api/accuracy")
    def accuracy():
        rep = Path(cfg["paths"]["reports"])
        m = {}
        mj = rep / "metrics.json"
        if mj.exists():
            try:
                m = json.loads(mj.read_text(encoding="utf-8"))
            except Exception:
                m = {"error": "reports/metrics.json is not valid JSON"}
        md = rep / "model_selection.md"
        out = {"models": m.get("models"), "species_accuracy": m.get("species_accuracy"), "accuracy_label": m.get("accuracy_label"),
               "empty_filter": m.get("empty_filter"), "day_night": m.get("day_night"), "confusion": m.get("confusion"),
               "generalisation": m.get("generalisation"), "splits": m.get("splits"), "integrity_note": m.get("integrity_note"),
               "tests": m.get("tests"), "check_note": m.get("check_note"), "leak_checked": m.get("leak_checked"), "evaluated": bool(m.get("models")), "source": m.get("source"), "error": m.get("error"),
               "model_selection_md": md.read_text(encoding="utf-8") if md.exists() else None}
        return out

    @app.get("/api/rare")
    def rare():
        end = anchor()
        month = (end - timedelta(days=30)).strftime(TS)
        out = []
        for r in cfg["rare_species"]:
            n = 0
            for key in r["match"]:
                n += db.one("SELECT COALESCE(SUM(count),0) n FROM alerts WHERE species=? AND captured_at>=?", (key, month))["n"]
            out.append({**{k: r[k] for k in ("key", "name", "sci", "status", "count", "source")}, "cameras_this_month": int(n)})
        return {"species": out}

    # ---------------------------------------------------------------------------------- upload
    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...), camera_id: str = Form(""), capture_time: str = Form(""),
                     use_exif: str = Form("true")):
        if len(files) > max_files:
            raise HTTPException(413, detail={"error": f"Too many files in one request (max {max_files}).", "rejected": []})
        pending = db.one("SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')")["n"]
        if pending + len(files) > max_queue:
            raise HTTPException(429, detail={"error": "The processing queue is full. Wait for it to drain and try again.", "rejected": []})
        accepted, rejected = [], []
        ct = parse_time(capture_time)
        if capture_time and not ct:
            raise HTTPException(422, detail={"error": "Capture time is not a valid date and time.", "rejected": []})
        cam_in = clean_camera(camera_id)
        limit_b = cfg["pipeline"]["max_upload_mb"] * 1024 * 1024
        for f in files:
            name = os.path.basename((f.filename or "image").replace("\\", "/"))[:120] or "image"
            try:
                data = b""
                while True:                                          # chunked read: never hold more than the cap
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > limit_b:
                        break
                err, w, h, exif_time = check_image(data, cfg)
            except Exception as ex:
                err, exif_time = f"Could not read this file: {type(ex).__name__}", None
            if err:
                rejected.append({"filename": name, "error": err})
                continue
            fmt = Image.open(io.BytesIO(data)).format
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(name)[0])[:60] or "image"
            final = Path(cfg["paths"]["incoming"]) / f"{uuid.uuid4().hex[:8]}_{safe}{FMT_EXT[fmt]}"
            part = final.with_name(final.name + ".part")
            part.write_bytes(data)
            when = exif_time if (use_exif.lower() != "false" and exif_time) else ct
            jid = new_job(final, name, len(data), "upload", cam_in or clean_camera(parse_camera(name)), when or "")
            os.replace(part, final)                  # job row exists first, so the watcher never double-counts
            jobq.put(jid)
            accepted.append({"filename": name, "job_id": jid})
        if not accepted:
            raise HTTPException(422, detail={"error": rejected[0]["error"] if rejected else "No files were sent.", "rejected": rejected})
        return {"jobs": accepted, "rejected": rejected}

    def serve_image(path):
        mime = FMT_MIME.get(Path(path).suffix.lower())
        if not mime:                                 # never serve anything that is not one of our four image types
            raise HTTPException(404, "Image not found")
        return FileResponse(path, media_type=mime, headers={"Content-Disposition": "inline", "Cache-Control": "private, max-age=3600"})

    def job_out(j):
        res = json.loads(j["result"]) if j["result"] else None
        return {"id": j["id"], "filename": j["filename"], "size": j["size"], "source": j["source"], "camera_id": j["camera_id"],
                "stage": j["stage"], "stage_index": STAGES.index(j["stage"]) if j["stage"] in STAGES else 0, "state": j["state"],
                "error": j["error"], "result": res, "alert_id": j["alert_id"], "image_id": j["image_id"],
                "created_at": j["created_at"], "thumb_url": f"/api/jobs/{j['id']}/file"}

    @app.get("/api/jobs")
    def jobs(limit: int = 20):
        return {"jobs": [job_out(j) for j in db.q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),))]}

    @app.get("/api/jobs/{jid}")
    def job(jid: int):
        j = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if not j:
            raise HTTPException(404, "Job not found")
        return job_out(j)

    @app.get("/api/jobs/{jid}/file")
    def job_file(jid: int):
        j = db.one("SELECT path FROM jobs WHERE id=?", (jid,))
        if not j or not os.path.exists(j["path"]):
            raise HTTPException(404, "Image not found")
        return serve_image(j["path"])

    @app.get("/api/images/{iid}/file")
    def image_file(iid: int):
        r = db.one("SELECT path FROM images WHERE id=?", (iid,))
        if not r or not os.path.exists(r["path"]):
            raise HTTPException(404, "Image not found")
        return serve_image(r["path"])

    @app.exception_handler(HTTPException)
    async def http_exc(request, exc):
        d = exc.detail
        return JSONResponse({"error": d["error"], "rejected": d.get("rejected", [])} if isinstance(d, dict) else {"error": str(d)},
                            status_code=exc.status_code)

    app.mount("/", StaticFiles(directory=cfg["paths"]["frontend"], html=True), name="frontend")
    return app


def main():
    import uvicorn
    port = int(os.environ.get("WILDWATCH_PORT", "8000"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
