# SQLite schema (data/wildwatch.db)

Created automatically by `wwdb.py`. Times are local, ISO `YYYY-MM-DDTHH:MM:SS`.

| Table | Purpose | Columns |
|---|---|---|
| `images` | one row per processed photo | id, filename, path, camera_id, captured_at, night, width, height, `outcome` (animal / person / review / empty), source (upload / watcher), created_at |
| `detections` | every box MegaDetector kept (conf >= `empty_below`) | id, image_id, category (1 animal, 2 person), conf, bbox (JSON x,y,w,h normalised), species, species_conf |
| `alerts` | one per image with an animal or person | id, image_id, job_id, species, count, camera_id, village, captured_at, night, weight, zone, night_factor, score, level, person, review, response, message, message_source (template / llm), **status**, **status_updated_at**, created_at |
| `jobs` | upload and watcher queue | id, filename, path, size, camera_id, capture_time, source, `stage` (upload, detect, species, count, risk, done, error), `state` (queued, running, done, error), error, result (JSON), image_id, alert_id, created_at, updated_at |

## Alert status (added for the dashboard)
`alerts.status` is `new`, `dispatched` or `false_alarm`, default `new`. `POST /api/alerts/{id}/status` writes it and
`status_updated_at`; it is stored in the DB file, so it survives a server restart.

## Rules the data follows
* Danger score = species weight x count x night factor x camera zone factor (all in `config.yaml`).
* A species vote always means a count of at least 1.
* A frame is `empty` only when the best box is below `empty_below` (0.05). Boxes between `empty_below` and `detect_conf`
  (0.30) become `review` frames: they still get a species and an alert, flagged `review = 1`.
* "Active alerts" = `status = 'new'` and `level != 'LOW'`.
