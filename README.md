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
