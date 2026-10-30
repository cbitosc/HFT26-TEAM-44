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
CREATE INDEX IF NOT EXISTS idx_images_cam ON images(camera_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE INDEX IF NOT EXISTS idx_jobs_path ON jobs(path);
"""


class DB:
    def __init__(self, path):
        self.path = str(path)
        with closing(self._conn()) as c:
            c.executescript(SCHEMA)
            c.commit()

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
