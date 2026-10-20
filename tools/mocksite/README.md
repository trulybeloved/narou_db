# Scrape mocksite

A standalone, deterministic HTTP fixture service. Narou is a site pack; the
server has no dependency on this repository's scraper, SQLite, Git, or Discord.
It also ships a generic two-state example at `/items/`.

## Start locally

From the repository root, using Python 3.11 or later:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt -e ./tools/mocksite
.\.venv\Scripts\python.exe -m mocksite serve
```

Open **http://127.0.0.1:8765/__mock/**. Select a scenario or state, preview the
pages, inspect responses and requests, edit scenario JSON, and export it for
version control. Refresh the request table after running your scraper.

The framework alone needs only `pip install ./tools/mocksite`;
`requirements-test.txt` adds this repository's scraper/test dependencies.
The complete suite runs without browser packages or binaries.

Docker alternative (no local Python required for the service):

```powershell
docker compose -f tools/mocksite/compose.yaml up --build --wait
docker compose -f tools/mocksite/compose.yaml down
```

The container binds to all interfaces internally; Compose publishes only
`127.0.0.1:8765` on the host. Clients inside the same Compose network use
`http://mocksite:8765`, not `localhost`. This release targets trusted local/CI
use, without public access, authentication, or shared multi-user sessions.

## Repeatable Narou walkthrough

Keep the server running and use another terminal:

```powershell
# All commands below run from the repository root.
.\.venv\Scripts\python.exe -m mocksite reset
.\.venv\Scripts\python.exe sandbox_scrape.py --mock-origin http://127.0.0.1:8765 --datastore outputs/narou-sandbox
# changed_uids: [1, 2, 3]
.\.venv\Scripts\python.exe sandbox_scrape.py --mock-origin http://127.0.0.1:8765 --datastore outputs/narou-sandbox
# changed_uids: []
.\.venv\Scripts\python.exe -m mocksite state revised
.\.venv\Scripts\python.exe sandbox_scrape.py --mock-origin http://127.0.0.1:8765 --datastore outputs/narou-sandbox
# changed_uids: [2]
.\.venv\Scripts\python.exe -m mocksite state appended
.\.venv\Scripts\python.exe sandbox_scrape.py --mock-origin http://127.0.0.1:8765 --datastore outputs/narou-sandbox
# changed_uids: [2, 4] -- complete snapshots: chapter 2 reverts to baseline.
.\.venv\Scripts\python.exe -m mocksite requests
```

The sandbox creates an empty SQLite database, uses a fixed scrape clock, and
records notification and publish intentions in `sandbox-report.json`. It never
calls production Git or Discord operations. Omitting `--datastore` uses a fresh
temporary directory removed on exit. Persistent runs require an empty directory
or one marked by `.narou-sandbox`; production datastore paths are rejected.
Resetting the service resets the website, **not** the sandbox database. Use a
new empty sandbox directory to restart a full test from scratch.

By default the runner requests three index pages, enough for all bundled states;
the baseline has two populated pages and the pagination-boundary state has three.
Unused pages up to page three are explicit HTTP 200 empty-index fixtures.
Requests beyond the configured routes return 404 and fail the scrape. Use
`--pages` to match the routes in custom packs.

## CLI and control API

Global `--url http://host:port` selects the service origin. Commands:

| CLI | HTTP endpoint under `/__mock/api/v1` |
| --- | --- |
| `scenarios` | `GET /scenarios` |
| `status` | `GET /status` |
| `load narou` | `POST /load` with `{"id":"narou"}` |
| `load --file scenario.json` | `POST /load` with `{"manifest":{...}}` |
| `state revised` | `POST /state` with `{"state":"revised"}` |
| `reset` | `POST /reset` with `{}` |
| `requests` | `GET /requests` |
| `export` | `GET /manifest` |

`validate scenario.json` validates locally without starting a service.
`serve --directory fixtures --scenario my-scenario` adds manifests from that
directory to the built-in Narou catalog. IDs must be unique. Relative response
files resolve below that directory. File contents are read when loading, so
editing a file does not silently change an active snapshot. UI JSON uploads
cannot read server files unless an asset root was explicitly configured.

`serve --port 0 --ready-file outputs/ready.json` assigns an available port and
writes `{"origin":"http://127.0.0.1:..."}` after startup. Harnesses should remove
stale readiness files before starting and own service shutdown.

## Scenario contract v1

See [the generic example](mocksite/examples/generic.json). Each manifest has
`schema_version: 1`, a nonempty `id`, `site_pack` (defaults to `static`),
`initial_state`, and a map of named `states`. Each static state contains routes:

```json
{
  "schema_version": 1,
  "id": "example",
  "site_pack": "static",
  "initial_state": "baseline",
  "states": {
    "baseline": {
      "routes": [{
        "method": "GET",
        "path": "/items/",
        "query": {"page": "1"},
        "responses": [
          {"status": 429, "headers": {"Retry-After": "1"}, "body": "Wait"},
          {"status": 200, "delay_ms": 25, "body": "<article>Ready</article>"}
        ]
      }]
    }
  }
}
```

- Match method, exact path, and the complete decoded query map; query order does
  not matter. No wildcard fallback. Unknown routes return 404. `/__mock` is reserved.
- Method defaults to GET. HEAD falls back to a matching GET route, with an
  independent response counter and no transmitted body.
- Status defaults to 200, body to empty text, delay to zero (maximum 60 seconds),
  and content type to UTF-8 HTML. Set headers explicitly for JSON or redirects.
- `file` replaces `body` for a UTF-8 fixture file inside the asset root; traversal
  and symlink escapes are rejected. CLI uploads and exports inline static files.
- Advance response sequences per route and method, repeating the last response
  once exhausted. Requests reserve their attempt before any delay.
- Every state is a complete snapshot. Selecting a state starts its counters at
  zero and preserves history; reset restores the initial state and clears history.
- Load validates every state before replacing anything and clears history on
  success. Invalid control requests return JSON errors with HTTP 400.
- In-flight requests keep their captured state after load, select, or reset.
  History is recorded at request acceptance, with response status, state,
  generation, sequence, and attempt; it does not imply delivery to the client.
- There is no wall-clock mutation, scheduler, or random generation in v1. Dates
  and content are explicit fixtures. Exported JSON can be committed and replayed.
- One process owns one active scenario. Use separate processes for parallel test
  workers. Request history stays in memory until load/reset or shutdown.

## Narou pack and extensions

The `narou` pack takes a full `chapters` list per state. Each chapter has positive
integer `uid`, `title`, raw `body_html`, optional `published` (default
`2025/01/01 12:00` JST), and optional `edited` in that same format. `page_size`
defaults to two; `layout` selects `current` or `legacy`. An optional `routes`
list overrides generated routes by method/path/query for fault injection.

Export the bundled scenario to inspect or modify all 19 states. They cover
unchanged/appended/revised content, title/body edits, removed listings,
pagination, duplicate entries, malformed dates, partial indexes, missing DOM,
legacy DOM, 404/429/500 responses, redirects, delays, and recovery sequences.
HTML is intentionally minimal; no ads, external assets, or visual Narou replica.
The structural reference in `tests/fixtures/narou-index.html` comes from the
existing tracked capture, with synthetic text and dates.

New projects can use the static pack directly. For generated sites, register an
entry point in their own package:

```toml
[project.entry-points."mocksite.packs"]
my_site = "my_package:compile_state"
```

`compile_state(state: dict) -> list[dict]` returns ordinary routes in the contract
above. Compilers should be pure and deterministic; the framework validates and
freezes their output on load. No Narou or SQLite integration is required.

## Scraper integration and current limitations

`ScrapeConfig` configures datastore root, index URLs, transport, and clock.
`scrape_and_persist(config, notify=...)` runs the real HTTP/parser/comparison/
SQLite path and returns changed UIDs. Production `update_once()` retains Git
preconditions, Discord notification, and commit/push.

`TransportConfig(mock_origin=..., request_timeout_seconds=..., max_tries=...,
retry_base_seconds=..., jitter=False, diagnostics_dir=...)` redirects canonical
Narou requests to the mock without changing reported URLs or filenames.
Sync/async HTTP and HEAD helpers use it. Every redirect hop is checked before
requesting it; mock requests cannot leave that origin. Environment proxies are
disabled. Scripts, images, and other subresources are never fetched.

The default request timeout is 30 seconds, with four attempts and a 120-second
monotonic deadline per URL including redirects and retry waits. Connection
failures, timeouts, HTTP 429 and 5xx responses retry with exponential backoff
and jitter. Valid `Retry-After` seconds or dates are honored; a wait beyond the
remaining budget fails immediately. Other HTTP errors fail without retry.
HEAD helpers report status codes without retrying and return 0 for transport
failures. The synchronous helpers must not be called from an active event loop.

| Scenario | Current behavior |
| --- | --- |
| Body change without index metadata change | Chapter is not fetched |
| Removed index listing | Existing database chapter remains |
| HTTP 200 index missing its selector, other valid entries | Partial index is accepted |
| Entire index absent or legacy-only DOM | Update fails |
| Malformed timestamp | Parsing fails |
| Duplicate index UID | Not explicitly rejected |
| HTTP 429/5xx | Retried within the configured attempts/deadline, then fails |
| Other HTTP errors, including 404 | Fails immediately |
| Failed chapter HTTP batch | No chapter provenance or database changes |
| Failed chapter parsing | SQLite stays unchanged; earlier provenance writes can remain |

Website states are independent of expected scraper results. Future adapters can
assert improved behavior against the same fixtures.

## Tests and diagnostics

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite starts a fresh mock process on a dynamic port for each integration
test, using temporary databases and synthetic chapters. It includes database/Git
checks, framework contracts, HTTP scraper lifecycle, retry/deadline and egress
checks, and panel delivery plus control API/export checks. JavaScript UI
interactions are not exercised by these HTTP tests.

Logs, selected state, manifest, requests, and exported JSON are saved under
ignored `outputs/mock-tests/` (override with `NAROU_TEST_ARTIFACTS`). Scraper
fixtures also save HTTP diagnostics: per-request JSON records contain URL,
method, attempt, redirect hop, status, duration, error, and the name of an HTML
response file when available. Pass `diagnostics_dir` to capture these outside
tests. Raw response bytes retain their original encoding.

GitHub Actions runs the complete suite and retains diagnostics on failure, plus
a separate Docker build/startup smoke test. CI does not need the production
SQLite LFS object.
