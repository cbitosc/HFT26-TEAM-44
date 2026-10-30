# WildWatch: camera-trap monitoring for forest-edge villages

Camera traps produce thousands of photos, and rangers cannot open them all. WildWatch finds the animals, names the species,
counts them, scores how dangerous the sighting is for the nearest village, and raises an alert with a recommended response.

**Team:** [add team name and members]

```
.
├── src/        # Source code
│   ├── backend/    FastAPI server, SQLite, risk scoring, MegaDetector + BioCLIP pipeline
│   ├── frontend/   the five dashboard pages
│   ├── ml/         scripts that trained and measured the species model
│   ├── config.yaml thresholds, risk weights, camera layout, census figures
│   └── reports/    metrics.json: the only source of the Model accuracy page
├── docs/       # Architecture diagram, database schema, evaluation notes
├── tests/      # 41 tests: API, upload, restart persistence, Playwright browser
├── README.md
└── LICENSE     # MIT
```

## How it works
Photo -> **MegaDetector** (boxes) -> crop each box -> **BioCLIP** features -> trained **linear probe** (species) ->
vote and count -> danger score (species weight x count x night x zone) -> alert + message -> dashboard.
Diagram and design decisions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run
```powershell
powershell -ExecutionPolicy Bypass -File src\run.ps1 dashboard
```
Opens http://localhost:8000. The first start loads the models (about 30 s). Needs a Python with `torch`, `megadetector`
and `open_clip_torch` (the hackathon venv at `D:\hackathon\venv`) and the trained probe `species_probe.pkl`
(path in `src/config.yaml`, built by `src/ml/train_probe.py`). Without the probe, uploads fail with a clear message and the rest of the
dashboard still works.

* **Upload page**: drop photos; the five stage pills follow the real job.
* **Camera drops**: files copied into `src/data/incoming/` become jobs automatically. Fill the dashboard from CCT with
  `src\run.ps1 import D:\hackathon\cct_subset 46 --limit 200`.
* **Alerts**: Dispatch / False alarm / Reopen are saved in the database.
* Set `FEATHERLESS_API_KEY` for LLM-written messages; otherwise messages are template text and the page says so.

## Results (honest)
Species accuracy on CCT20: **26.8%** zero-shot BioCLIP; with the trained probe **78.9%** on camera locations seen in training and **62.8%** on new locations (the realistic number).
Day vs night, empty-frame filter and count error are **not measured yet** and the dashboard shows "Not measured".
Details: [docs/EVALUATION.md](docs/EVALUATION.md).

## Tests
```powershell
src\run.ps1 test        # or: python -m pytest tests
```
The ML models are replaced by a stub in tests; everything else (database, API, upload validation, job worker, folder watcher,
restart persistence, browser pages) is real. See `tests/conftest.py`.

## Limits
* The model only knows CCT animals, so it can never name a tiger, leopard or elephant; the rare-species cards show census
  figures plus real camera sightings (0 if none).
* Camera names, villages and zone factors in `src/config.yaml` are a fictional layout until real ones are entered.
* The frontend loads its fonts from Google Fonts when online.

## License
MIT, see [LICENSE](LICENSE). Replace the copyright line with your name or team.


## Security
Local-only tool: binds to 127.0.0.1, no login, never expose it to the internet. Secrets live in `src/.env` (git-ignored; template in `src/.env.example`). See [docs/SECURITY.md](docs/SECURITY.md) for the attacks tested and the limits.
