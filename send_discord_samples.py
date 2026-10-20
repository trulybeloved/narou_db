"""Send labeled samples of the autoscraper's Discord notifications."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

import aiohttp
from dotenv import load_dotenv

from custom_modules.updater_runtime import Notifications
from custom_modules.discord_notifications import notification_payload
from custom_modules.git_workflow import _git, github_commit_url, GitWorkflowError, GitTransportError


SAMPLES = {
    'publication': {'changed_chapters': 2, 'phase': 'complete'},
    'retry': {'error_category': 'SourceDataError', 'failures': 1, 'phase': 'failed'},
    'continuing-failure': {'error_category': 'SourceDataError', 'failures': 4, 'phase': 'failed'},
    'recovery': {'failures': 4, 'phase': 'complete'},
    'fatal': {'error_category': 'FatalUpdaterError', 'failures': 1, 'phase': 'failed'},
}


async def send_samples(states: list[str], *, dry_run: bool = False, no_ping: bool = False) -> int:
    samples = {state: dict(details) for state, details in SAMPLES.items()}
    if 'publication' in states:
        # A sample links to the current real commit; it does not create or push one.
        repository = Path(__file__).resolve().parent
        try:
            commit = _git(repository, 'rev-parse', 'HEAD')
            samples['publication']['commit_url'] = github_commit_url(repository, commit)
        except (GitWorkflowError, GitTransportError):
            pass
    notifications = Notifications()
    if no_ping:
        notifications.ping = ''
    if dry_run:
        for state in states:
            print(json.dumps(notification_payload(state, ping=notifications.ping, sample=True,
                                                  **samples[state]), indent=2, ensure_ascii=False))
        return 0

    if not notifications.enabled:
        print('Set a valid DISCORD_WEBHOOK_URL in the environment or repository .env.', file=sys.stderr)
        return 1
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False) as session:
        for index, state in enumerate(states):
            if index:
                await asyncio.sleep(1)
            notifications.send(state, sample=True, **samples[state])
            payload = notifications.queue.get_nowait()
            try:
                async with session.post(notifications.url, json=payload,
                                        allow_redirects=False) as response:
                    if not 200 <= response.status < 300:
                        print(f'Failed to send {state} sample: HTTP {response.status}.', file=sys.stderr)
                        return 1
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                # Exception text may contain the secret webhook URL.
                print(f'Failed to send {state} sample: {type(exc).__name__}.', file=sys.stderr)
                return 1
            finally:
                notifications.queue.task_done()
            print(f'Sent {state} sample.')
    return 0


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent / '.env', override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', action='append', choices=SAMPLES,
                        help='Send only this state; repeat to select several (default: all)')
    parser.add_argument('--dry-run', action='store_true', help='Print embed payloads without sending')
    parser.add_argument('--no-ping', action='store_true', help='Omit the configured PING_USER_ID mention')
    args = parser.parse_args()
    return asyncio.run(send_samples(args.state or list(SAMPLES), dry_run=args.dry_run, no_ping=args.no_ping))


if __name__ == '__main__':
    raise SystemExit(main())
