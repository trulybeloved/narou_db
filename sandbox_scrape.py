"""Run the real scraper against a mock origin, using an isolated datastore."""

import argparse
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import tempfile

from autoscrape_autoupdate import ScrapeConfig, scrape_and_persist
from custom_modules.narou_database import connect, finalize, initialize
from custom_modules.webscraper import TransportConfig


def prepare_sandbox(root):
    root = Path(root).resolve()
    production = (Path(__file__).resolve().parent / 'datastores').resolve()
    if root == production or root.is_relative_to(production) or production.is_relative_to(root):
        raise ValueError('Sandbox root must be separate from the production datastore')
    marker = root / '.narou-sandbox'
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise ValueError('Refusing to use a nonempty directory without a sandbox marker')
    root.mkdir(parents=True, exist_ok=True)
    marker.touch()
    database = root / 'narou.sqlite'
    if not database.exists():
        connection = connect(database, writable=True)
        try:
            initialize(connection)
            finalize(connection)
        finally:
            connection.close()
    return root


async def run_sandbox(origin, root, *, pages=3, timestamp=1735689600):
    if pages < 1:
        raise ValueError('pages must be positive')
    root = prepare_sandbox(root)
    if not origin:
        raise ValueError('A mock origin is required')
    events = []
    config = ScrapeConfig(
        datastore_root=root,
        index_urls=[f'https://ncode.syosetu.com/n2267be/?p={n}' for n in range(1, pages + 1)],
        transport=TransportConfig(mock_origin=origin, navigation_timeout_ms=3000,
                                  max_tries=2, retry_base_seconds=0, jitter=False),
        clock=lambda: timestamp,
    )
    changed = await scrape_and_persist(config, notify=lambda message: events.append({'type': 'notification', 'message': message}))
    if changed:
        events.append({'type': 'publish-request', 'uids': changed})
    report = {'datastore': str(root), 'changed_uids': [int(uid) for uid in changed], 'events': events}
    (root / 'sandbox-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock-origin', required=True)
    parser.add_argument('--datastore', type=Path, help='empty directory, or a previously marked sandbox; temporary by default')
    parser.add_argument('--pages', type=int, default=3)
    parser.add_argument('--timestamp', type=int, default=1735689600)
    args = parser.parse_args()
    def execute(root):
        with redirect_stdout(sys.stderr):
            report = asyncio.run(run_sandbox(args.mock_origin, root, pages=args.pages, timestamp=args.timestamp))
        print(json.dumps(report, indent=2))
    if args.datastore:
        execute(args.datastore)
    else:
        with tempfile.TemporaryDirectory(prefix='narou-sandbox-') as root:
            execute(root)


if __name__ == '__main__':
    main()
