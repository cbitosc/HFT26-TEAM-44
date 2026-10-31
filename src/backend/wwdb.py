"""SQLite layer. One short-lived connection per call, so it is safe from any thread."""
import sqlite3
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT, path TEXT, camera_id TEXT, captured_at TEXT, night INTEGER DEFAULT 0,
  width INTEGER, height INTEGER,
  outcome TEXT,              -- animal | person | review | empty
  source TEXT,               -- upload | watcher | import
  created_at TEXT);
CREATE TABLE IF NOT EXISTS detections(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  image_id INTEGER, category TEXT, conf REAL, bbox TEXT, species TEXT, species_conf REAL);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  image_id INTEGER, job_id INTEGER, species TEXT, count INTEGER,
  camera_id TEXT, village TEXT, captured_at TEXT, night INTEGER,
  weight REAL, zone REAL, night_factor REAL, score REAL, level TEXT,
  person INTEGER DEFAULT 0, review INTEGER DEFAULT 0,
  response TEXT, message TEXT, message_source TEXT,
  status TEXT NOT NULL DEFAULT 'new',          -- new | dispatched | false_alarm
  status_updated_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT, path TEXT, size INTEGER, camera_id TEXT, capture_time TEXT, source TEXT,
  stage TEXT NOT NULL DEFAULT 'upload',        -- upload | detect | species | count | risk | done | error
  state TEXT NOT NULL DEFAULT 'queued',        -- queued | running | done | error
  error TEXT, result TEXT, image_id INTEGER, alert_id INTEGER,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS cameras(
  camera_id TEXT PRIMARY KEY,
  name TEXT NOT NULL, latitude REAL, longitude REAL, village TEXT NOT NULL,
  zone_type TEXT NOT NULL, zone_factor REAL NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_images_cam ON images(camera_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_jobs_path ON jobs(path);
"""


SCHEMA_VERSION = 3        # v1 first tables, v2 alert status columns, v3 camera registry


class DB:
    def __init__(self, path, seed_cameras=(), zone_types=None):
        """Opens (or creates) the DB and upgrades any older version in place. seed_cameras fills the camera
        registry once, when it is empty; zone_types re-applies the config zone_type -> zone_factor mapping."""
        self.path = str(path)
        with closing(self._conn()) as c:
            old = c.execute("PRAGMA user_version").fetchone()[0]
            cols = {r[1] for r in c.execute("PRAGMA table_info(alerts)")}
            if cols:                                                  # v1 -> v2, before any index on these columns
                for col, ddl in (("status", "TEXT NOT NULL DEFAULT 'new'"), ("status_updated_at", "TEXT")):
                    if col not in cols:
                        c.execute(f"ALTER TABLE alerts ADD COLUMN {col} {ddl}")
            c.executescript(SCHEMA)                                   # creates any table that is missing (e.g. cameras)
            if not c.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]:     # v2 -> v3: migrate config cameras
                for cam in seed_cameras:
                    c.execute("INSERT INTO cameras(camera_id,name,latitude,longitude,village,zone_type,zone_factor,active,created_at)"
                              " VALUES(?,?,?,?,?,?,?,1,datetime('now','localtime'))",
                              (str(cam["id"]), cam["name"], cam.get("latitude"), cam.get("longitude"), cam["village"],
                               cam["zone_type"], float((zone_types or {}).get(cam["zone_type"], cam.get("zone", 1.0)))))
            for zt, f in (zone_types or {}).items():                  # config is the source of truth for the factors
                c.execute("UPDATE cameras SET zone_factor=? WHERE zone_type=?", (float(f), zt))
            if old < SCHEMA_VERSION:
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            c.commit()
        self.upgraded_from = old

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def q(self, sql, args=()):
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with closing(self._conn()) as c:
            cur = c.execute(sql, args)
            c.commit()
            return cur.lastrowid
