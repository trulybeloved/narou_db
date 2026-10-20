# Updater operations

Use a dedicated local checkout, one writer account, Python 3.11+, Git, and Git
LFS. The checkout must use `master` and have an `origin` remote with permission
to fetch and push. Initialize LFS with `git lfs install`, fetch objects with
`git lfs pull`, and configure the writer's Git name, email, and noninteractive
credentials before starting. Missing LFS objects and filters are fatal.

Install the repository dependencies into a virtual environment. The minimal
scraper/test dependency set also suffices for the writer:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python /absolute/path/to/narou_db/autoscrape_autoupdate.py
```

On Windows use `.venv\Scripts\python.exe`. Paths default to the script's
repository, independently of the working directory. `--repository PATH`
selects another dedicated writer checkout; `.env` is read from the script's
directory. Process environment variables take precedence over `.env`.

## Configuration

CLI flags override environment variables, which override defaults. All time
values are seconds and must be finite and positive. Run `--help` for flags.

| Flag | Environment variable | Default |
| --- | --- | --- |
| `--repository` | `NAROU_REPOSITORY` | Script repository |
| `--interval` | `NAROU_INTERVAL` | 900 |
| `--retry-base` | `NAROU_RETRY_BASE` | 30 |
| `--retry-cap` | `NAROU_RETRY_CAP` | 900 |
| `--cycle-timeout` | `NAROU_CYCLE_TIMEOUT` | 1800 |
| `--shutdown-grace` | `NAROU_SHUTDOWN_GRACE` | 30 |
| `--git-timeout` | `NAROU_GIT_TIMEOUT` | 120 |
| `--health-host` | `NAROU_HEALTH_HOST` | 127.0.0.1 |
| `--health-port` | `NAROU_HEALTH_PORT` | 8766 |
| `--max-index-pages` | `NAROU_MAX_INDEX_PAGES` | 100 |
| `--request-timeout` | `NAROU_REQUEST_TIMEOUT` | 30 |
| `--http-attempts` | `NAROU_HTTP_ATTEMPTS` | 4 |
| `--http-retry-deadline` | `NAROU_HTTP_RETRY_DEADLINE` | 120 |

`--once` runs one supervised cycle, including recovery, then exits. It still
starts HTTP health checks and takes the daemon and writer locks. The legacy
`scrape_update.py` calls the locked `update_once()` directly without a health
server or supervisor; use `--once` for a bounded production one-shot run.
The lower-level `scrape_and_persist()` API accepts staging/sandbox storage and
refuses a canonical Git checkout's datastore; production writes go through
`update_once()` and its writer lock.

The first attempt starts immediately. Successful cycles wait a full interval
after completion. Failure ceilings double from 30 to 900 seconds; each wait is
randomized between half the ceiling and the ceiling. Success resets backoff.
All scheduling and deadlines use monotonic time; JSON timestamps use epoch
seconds. There is no catch-up burst after a missed interval.

`DISCORD_WEBHOOK_URL` and optional numeric `PING_USER_ID` enable notifications.
The service reports publication, first failure, hourly continuing failure,
and recovery. Delivery uses a ten-message queue and a ten-second timeout;
delivery failures do not affect updates. Notifications are best effort and
may be lost or repeated across process crashes. HTTP readiness is the
authoritative monitoring signal.

Notifications use Discord embeds with a state-specific title and color, UTC
timestamp, status, next step, and relevant chapter counts, failure counts,
error categories, and phases. Configured user mentions accompany the embed.
Publication embeds include a **View commit** link to the exact commit after
the publisher verifies it is present on the remote, including recovered
publications. The URL is derived from GitHub `origin` push URLs in HTTPS or SSH
form; credentials in the remote URL are omitted. Other hosts and local remotes
omit the link. No additional API token or configuration is required. The
repository must be public for recipients to open the link without GitHub access;
the updater does not check repository visibility.

To send labeled samples of each notification state to the configured Discord
webhook, run:

```sh
.venv/bin/python send_discord_samples.py
```

The script reads the repository `.env`, with environment variables taking
precedence, and uses `DISCORD_WEBHOOK_URL` and optional `PING_USER_ID` just like
the service. It sends publication, retryable failure, continuing failure,
recovery, and fatal failure samples, one second apart. Continuing failures use
a distinct reminder embed; the service sends it at most hourly.
Use `--state recovery` to select a state (repeat `--state` for several),
`--no-ping` to omit mentions, or `--dry-run` to preview the embed payloads without
sending. Sending exits with code 1 if configuration or delivery fails.
The publication sample links to the checkout's current commit; it does not
create or push a commit. That sample link is only reachable if the current
commit already exists on GitHub.

## Health and shutdown

```sh
curl --fail http://127.0.0.1:8766/livez
curl --fail http://127.0.0.1:8766/readyz
```

`/livez` returns 200 when the supervisor's five-second heartbeat is no more
than 30 seconds old. `/readyz` returns 200 only after a complete successful
cycle and while no failure or pending publication remains. Startup, failed
cycles, recovery, pending publication, and shutdown return 503. Normal sleep
and a new scrape after a previous success remain ready until publication is
pending or an error occurs. Readiness expires after the cycle timeout plus
two polling intervals without success (one hour with default settings).

Responses contain state, phase, attempt timestamps, last success, next attempt,
failure count, changed-chapter count, pending publication, and error category.
They omit exception messages, credentials, paths, and response bodies. Probes
read cached supervisor state. Binding failure prevents any worker from starting.
The default listener is local; configure a different address only when the
deployment requires it. Startup logs include the full URLs for both health
endpoints after the listener starts, using its bound address and port.

Ctrl+C, Linux SIGTERM, and Windows console interruption stop scheduling and
wake sleep immediately. A running worker receives up to the shutdown grace
period to finish. A deadline kills the worker and its subprocess tree;
journaled work resumes on restart. OS locks release on process death. Hard
termination of the supervisor may leave a worker briefly running; its own
writer lock prevents a replacement worker from writing concurrently.

Exit codes: **0** orderly shutdown/successful `--once`; **1** fatal runtime
failure or failed `--once`; **2** invalid CLI configuration; **3** daemon lock
already held. Temporary failures in continuous mode keep running and expose
unhealthy readiness. Unexpected worker exits are fatal.

## Supervision

Linux: copy [the example systemd unit](../deploy/narou-updater.service), adjust
the account and absolute paths, then enable it. Its stop timeout allows worker
cleanup and a final bounded notification attempt. Inspect readiness separately
from process status. It restarts unexpected process failures but leaves
reported safety/configuration failures stopped for operator repair.

Windows: create a Task Scheduler task under the dedicated writer account. Use
the absolute `.venv\Scripts\python.exe` as the program and the quoted absolute
`autoscrape_autoupdate.py` path as its argument. Trigger at system startup, run
whether logged on or not, disable the task's execution time limit, and select
**Do not start a new instance**. Do not schedule a second task every 15 minutes;
the service schedules itself. Monitor HTTP readiness and configure any restart
policy with a delay so a fatal local error cannot create a tight restart loop.
Task Scheduler's forced End operation may bypass console shutdown; journal
recovery handles an interrupted publication.

## State, recovery, and diagnostics

Runtime files live in `narou-updater/` under Git's common directory (normally
`.git/narou-updater/`), so they never dirty the checkout. Do not delete lock
files: the operating system releases their locks automatically, and deleting
a live lock file can bypass exclusion on Unix.

Each candidate includes SQLite plus changed JSON, HTML, and TXT provenance.
The journal records the starting commit, transaction ID, exact paths, hashes,
reserved promotion temporary paths, expected Git tree, and publication phase.
Files are flushed and individually
replaced atomically. Multiple files become visible in sequence; external
readers should use the canonical SQLite database, which is itself atomic.
A publication is successful only after its commit is verified on the remote.

On restart, verified original/candidate file hashes allow idempotent promotion.
Partial copies at journal-reserved temporary paths are discarded and recopied.
The recorded parent, tree, and transaction trailer identify a commit whose
acknowledgment was interrupted. A pending push is retried before scraping; if
the remote already contains that commit, cleanup completes without another
commit. Clean checkouts can fast-forward. Remote divergence never triggers an
automatic merge, reset, or force push.

For an intervention-required failure:

1. Stop supervision and confirm no writer remains. Preserve the entire runtime
   transaction directory and copy logs before repairing anything.
2. Inspect `git status`, the local/remote history, and
   `transaction/journal.json`. Restore unrelated user changes outside this
   dedicated checkout, or reconcile diverged history manually.
3. Fix credentials, LFS installation/objects, permissions, free disk space, or
   a corrupt database as indicated by the local traceback. Verify a repaired
   database against known-good Git/LFS data before restarting.
4. Restart with the transaction intact when hashes and history still match.
   If manually replacing updater-owned history or files, preserve the journal
   elsewhere and resolve the entire incomplete transaction before removing its
   active directory. Never discard a pending commit merely to make startup pass.

An interrupted Git process can leave `index.lock`; inspect for live Git
processes before manually removing it. Unowned ahead commits, changed journal
files, and modified candidate files intentionally require operator intervention.

Terminal stderr logs use readable timestamps, levels, messages, and context
such as phases, transaction IDs, durations, and counts. Separate service/worker
JSONL files retain structured records. Both include tracebacks without captured
local variables. Each file rotates at 10 MB with five retained rotations. HTTP
diagnostics retain at most 100 response/metadata pairs and are pruned after
seven days between cycles. Responses are capped at 8 MiB after decompression.
Logs and diagnostics can contain scraped content and remote error messages;
keep the runtime directory accessible only to the writer/operator account.

Use a local filesystem supporting OS file locks and atomic replacement. File
contents are fsynced; directory fsync is used on Linux. Windows provides atomic
replacement and file flushes but does not offer the same directory-fsync API.
Crash recovery assumes the filesystem and storage honour these operations.
Runtime state and staged databases require additional disk space. No automatic
chapter deletion or detection of unannounced body-only edits is performed.

## Verification

```sh
.venv/bin/python -m pip install -r requirements-test.txt -e ./tools/mocksite
.venv/bin/python -m pytest -q
```

Tests use local mock HTTP servers, temporary repositories, and local bare
remotes. Git LFS publication tests require `git-lfs` on PATH. Linux and Windows
CI run the suite without production Git, Discord, or datastore writes.
