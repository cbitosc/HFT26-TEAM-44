# Security notes

WildWatch is a **local, single-user tool**. It binds to `127.0.0.1` only and has **no login**. Do not expose it to the internet
or a shared network as it is; put an authenticating reverse proxy in front first.

## Secrets
- The only secret is the optional `FEATHERLESS_API_KEY`. Put it in `src/.env` (copy `src/.env.example`) or set it as an environment variable.
- `.env`, `*.pkl`, `src/data/` (database and uploaded photos) and `venv/` are in `.gitignore`. Check with `git status` before every commit.
- The key is read on the server only. It is never logged and never sent to the browser (tested).

## Attacks tried (`tests/test_security.py`, `tests/test_e2e.py`) and the defence
| Attack | Defence |
|---|---|
| `../../` or 3000-character file names | Names reduced to `[A-Za-z0-9_-]`, 60 characters, saved under a random id inside `incoming/` |
| HTML/JS hidden in a photo, `.html`/`.svg` upload | Saved extension is chosen from the real image format; served with a fixed `image/*` type and `nosniff`; SVG/HTML rejected |
| Decompression bomb (tiny PNG, 400 million pixels) | Pixel cap enforced before decoding |
| Huge body / chunked flood | Body cap (413) before buffering; files read in 1 MB chunks; max 20 files per request |
| Upload flood | Queue cap `max_queue` (429) |
| SQL injection in `level`, `range`, ids, camera id | Parameterised queries and typed parameters only |
| Cross-site request from another web page | Origin check on every write (403) |
| DNS rebinding | Host header must be `localhost`, `127.0.0.1` or `[::1]` |
| Script in a file name shown on a page | Runtime renders text only; CSP blocks inline and foreign scripts; tested in a real browser |
| Reading `config.yaml`, the database or source via the web server | Only `src/frontend/` is served; `/docs` and `/openapi.json` are off |
| Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` |

## Known limits
- No authentication or per-user roles.
- The CSP needs `'unsafe-eval'` because the small page runtime compiles each page's component script.
- Fonts load from Google Fonts; remove them for a fully offline deployment.
