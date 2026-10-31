# SQLite schema (data/wildwatch.db) - version 3

Created and upgraded automatically by `wwdb.py` (version kept in `PRAGMA user_version`). Times are local, ISO `YYYY-MM-DDTHH:MM:SS`.

| Table | Purpose | Columns |
|---|---|---|
| `images` | one row per processed photo | id, filename, path, camera_id, captured_at, night, width, height, `outcome` (animal / person / review / empty), source (upload / watcher), created_at |
| `detections` | every box MegaDetector kept (conf >= `empty_below`) | id, image_id, category (1 animal, 2 person), conf, bbox (JSON x,y,w,h normalised), species, species_conf |
| `alerts` | one per image with an animal or person | id, image_id, job_id, species, count, camera_id, village, captured_at, night, weight, zone, night_factor, score, level, person, review, response, message, message_source (template / llm), **status**, **status_updated_at**, created_at |
| `cameras` | camera registry (v3) | **camera_id** (primary key), name, latitude, longitude, village, zone_type, zone_factor, active, created_at |
| `jobs` | upload and watcher queue | id, filename, path, size, camera_id, capture_time, source, `stage` (upload, detect, species, count, risk, done, error), `state` (queued, running, done, error), error, result (JSON), image_id, alert_id, created_at, updated_at |

## Versions and automatic upgrade
| Version | Change |
|---|---|
| 1 | images, detections, alerts, jobs |
| 2 | `alerts.status`, `alerts.status_updated_at` |
| 3 | `cameras` table; the `cameras:` list in config.yaml is copied in once, when the table is empty |

Opening an older file upgrades it in place on server start: missing columns are added, the cameras table is created and
seeded, existing rows are kept. Opening it again changes nothing. Tested in `tests/test_cameras.py`.

## Camera registry
* `zone_factor` comes from `zone_types` in config.yaml (our team's choices, not learned) and is re-applied to every camera on
  each server start, so editing a multiplier there takes effect after a restart.
* Uploads need a registered, active camera (chosen in the Upload page, or a `CAM46_` style file name). The risk score and
  the alert's village come from this table.
* A camera that has images can be deactivated (`PATCH /api/cameras/{id}` `{"active": false}`) but never deleted;
  `DELETE` only works for a camera that was never used.
* Map: cameras are placed by real latitude / longitude scaled into the map box. The "Fictional layout" label goes away only
  when every active camera has real coordinates; the six seed cameras have none.

## Alert status (added for the dashboard)
`alerts.status` is `new`, `dispatched` or `false_alarm`, default `new`. `POST /api/alerts/{id}/status` writes it and
`status_updated_at`; it is stored in the DB file, so it survives a server restart.

## Rules the data follows
* Danger score = species weight x count x night factor x camera zone factor (weights in `config.yaml`, zone factor from the `cameras` table).
* A species vote always means a count of at least 1.
* A frame is `empty` only when the best box is below `empty_below` (0.05). Boxes between `empty_below` and `detect_conf`
  (0.30) become `review` frames: they still get a species and an alert, flagged `review = 1`.
* "Active alerts" = `status = 'new'` and `level != 'LOW'`.
