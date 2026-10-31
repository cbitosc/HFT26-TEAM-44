# Architecture

```mermaid
flowchart LR
  CAM[Camera trap photos] -->|drop file| INC[(src/data/incoming)]
  UP[Upload page] -->|POST /api/upload| API
  INC -->|folder watcher| Q[[Job queue]]
  API[FastAPI server<br/>src/backend/server.py] --> Q
  Q --> W[Worker thread]
  W --> MD[MegaDetector<br/>boxes: animal / person]
  MD -->|best box < 0.05| EMPTY[Empty frame<br/>discarded]
  MD -->|boxes >= 0.05| CROP[Crop each box]
  CROP --> BC[BioCLIP features]
  BC --> LP[Linear probe<br/>species_probe.pkl]
  LP --> VOTE[Confidence-weighted vote<br/>species + count >= 1]
  VOTE --> RISK[Danger score<br/>weight x count x night x zone]
  RISK --> MSG[Alert message<br/>LLM or template]
  MSG --> DB[(SQLite<br/>src/data/wildwatch.db)]
  EMPTY --> DB
  DB --> API
  API --> UI[Dashboard pages<br/>src/frontend]
```

## Folders
| Path | What lives there |
|---|---|
| `src/backend/` | FastAPI server, SQLite layer, risk scoring, message writer, the real ML pipeline, import helper |
| `src/frontend/` | The five pages (Home, Upload, Alerts, Analytics, Model accuracy), `api.js` data layer, page runtime |
| `src/ml/` | Offline scripts that produced the species model and measured it |
| `src/config.yaml` | Thresholds, risk weights, camera layout, census figures |
| `src/reports/` | `metrics.json` and `model_selection.md`: the only source of the Model accuracy page |
| `tests/` | API, upload, persistence and Playwright browser tests |
| `docs/` | This file, the database schema, evaluation notes |

## Decisions worth explaining to judges
* **Detector and classifier are separate.** MegaDetector says where an animal is; a second model says which species. Each can be
  replaced without touching the other.
* **Missing an animal is worse than a false alarm.** A frame is discarded only when MegaDetector is nearly certain it is empty
  (best box below 0.05). Doubtful frames (0.05 to 0.30) go to a review queue and still raise an alert.
* **No invented numbers.** Every figure on the dashboard comes from the database, `src/reports/` or `src/config.yaml`; anything
  never measured shows "Not measured".
