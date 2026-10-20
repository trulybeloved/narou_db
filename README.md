# Narou database

`datastores/narou.sqlite` is the canonical local dataset. It is tracked with
Git LFS. The JSON, HTML, and TXT files under `datastores/` are retained as
Git-tracked scrape provenance and as the bootstrap input; they are not queried
at runtime.

## First-time setup

Install Git LFS, clone normally, and fetch LFS objects:

```powershell
git lfs install
git lfs pull
```

The committed database is ready after the LFS pull. Run the bootstrap only to
create a missing database or intentionally rebuild it from provenance:

```powershell
.\.venv\Scripts\python.exe bootstrap_narou_sqlite.py --force
```

It supports legacy and current scrape JSON formats, falls back to raw TXT for
missing JSON records, and exits without creating a replacement database if any
source record is unreadable.

## Scheduled writer

Run `autoscrape_autoupdate.py` from a clean checkout on `master`. It fetches
and requires an exact `origin/master` match before every update. It writes
changed provenance files and a temporary SQLite copy, validates the database,
atomically promotes it, then commits and pushes the changed datastore files.

Only one writer may run. A scrape, validation, Git, or push error stops the
writer and leaves changes uncommitted for investigation. No Cloudflare or D1
configuration is used.

## HTTP scraping

The SSR pages are fetched directly with `aiohttp`; no browser installation or
rendering is needed. BeautifulSoup extracts the requested CSS selectors. Batch
requests share a connection pool and allow at most ten concurrent operations.
Each URL has a 30-second request timeout, up to four attempts, and a 120-second
overall retry deadline. HTTP 429 and 5xx responses and transport failures are
retried with exponential backoff; other HTTP errors fail immediately.

`TransportConfig(request_timeout_seconds=..., max_tries=...,
retry_max_seconds=..., diagnostics_dir=...)` overrides these settings.
`async_http_webscrape()` and `sync_http_webscrape()` replace the former
browser-specific helpers. Use the async API inside an active event loop.

## Repeatable scraper tests

The [mock website framework](tools/mocksite/README.md) provides a local Narou
HTTP service, browser control panel, versioned scenarios, and isolated scraper
runs. It includes a generic site example for reuse in other projects.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt -e ./tools/mocksite
.\.venv\Scripts\python.exe -m mocksite serve
```

Open `http://127.0.0.1:8765/__mock/`. In another terminal, run:

```powershell
.\.venv\Scripts\python.exe sandbox_scrape.py --mock-origin http://127.0.0.1:8765 --datastore outputs/narou-sandbox
.\.venv\Scripts\python.exe -m pytest -q
```

Sandbox runs record external-effect intentions and write only to a separate
datastore. Existing production update behavior remains available through
`autoscrape_autoupdate.py`.
