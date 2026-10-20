"""Portable service supervisor, HTTP health probes, and bounded notifications."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
import random
import signal
import sys
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from loguru import logger

from custom_modules.custom_exceptions import WebPageLoadError
from custom_modules.discord_notifications import notification_payload
from custom_modules.git_workflow import github_commit_url, runtime_directory
from custom_modules.updater_safety import (
    FatalUpdaterError, LockUnavailable, ProcessLock, RetryableUpdaterError,
    atomic_json, kill_process_tree,
)
from custom_modules.webscraper import TransportConfig

SCRIPT = Path(__file__).resolve().parents[1] / 'autoscrape_autoupdate.py'


@dataclass(frozen=True)
class RuntimeConfig:
    repository: Path = SCRIPT.parent
    interval: float = 900
    retry_base: float = 30
    retry_cap: float = 900
    cycle_timeout: float = 1800
    shutdown_grace: float = 30
    git_timeout: float = 120
    health_host: str = '127.0.0.1'
    health_port: int = 8766
    max_index_pages: int = 100
    request_timeout: float = 30
    http_attempts: int = 4
    http_retry_deadline: float = 120
    once: bool = False
    worker: bool = False

    def __post_init__(self):
        for name in ('interval', 'retry_base', 'retry_cap', 'cycle_timeout', 'shutdown_grace',
                     'git_timeout', 'request_timeout', 'http_retry_deadline'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if self.retry_cap < self.retry_base:
            raise ValueError('retry_cap must be at least retry_base')
        if not 0 <= self.health_port <= 65535 or not self.health_host.strip():
            raise ValueError('Invalid health listener address')
        if self.max_index_pages < 1 or self.http_attempts < 1:
            raise ValueError('Page limit and HTTP attempts must be positive integers')

    @classmethod
    def from_cli(cls, argv=None):
        load_dotenv(SCRIPT.parent / '.env', override=False)
        parser = argparse.ArgumentParser(description=__doc__)
        defaults = cls()
        parser.add_argument('--repository', type=Path, default=os.getenv('NAROU_REPOSITORY', defaults.repository))
        for name in ('interval', 'retry_base', 'retry_cap', 'cycle_timeout', 'shutdown_grace', 'git_timeout',
                     'health_host', 'health_port', 'max_index_pages', 'request_timeout', 'http_attempts',
                     'http_retry_deadline'):
            default = getattr(defaults, name)
            parser.add_argument('--' + name.replace('_', '-'), type=type(default),
                                default=os.getenv('NAROU_' + name.upper(), default))
        parser.add_argument('--once', action='store_true', help='Run one supervised cycle and exit')
        parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
        try:
            return cls(**vars(parser.parse_args(argv)))
        except ValueError as exc:
            parser.error(str(exc))

    def worker_command(self) -> list[str]:
        command = [sys.executable, str(SCRIPT), '--worker', '--repository', str(self.repository.resolve())]
        for name in ('git_timeout', 'max_index_pages', 'request_timeout', 'http_attempts', 'http_retry_deadline'):
            command += ['--' + name.replace('_', '-'), str(getattr(self, name))]
        return command


def retry_delay(failures: int, config: RuntimeConfig, uniform=random.uniform) -> float:
    ceiling = min(config.retry_cap, config.retry_base * 2 ** min(max(failures - 1, 0), 30))
    # Equal jitter avoids tight retry loops even when randomness yields zero.
    return uniform(ceiling / 2, ceiling)


@dataclass
class Health:
    config: RuntimeConfig
    monotonic: object = time.monotonic
    state: str = 'starting'
    phase: str = 'startup'
    heartbeat: float = field(default_factory=time.monotonic)
    last_success_mono: float | None = None
    last_success: float | None = None
    attempt_started: float | None = None
    attempt_finished: float | None = None
    next_attempt: float | None = None
    consecutive_failures: int = 0
    pending_publication: bool = False
    error_category: str | None = None
    changed_chapters: int = 0

    def live(self) -> bool:
        return self.state not in {'stopping', 'stopped', 'fatal'} and self.monotonic() - self.heartbeat <= 30

    def ready(self) -> bool:
        return (self.live() and self.last_success_mono is not None and not self.pending_publication
                and self.consecutive_failures == 0
                and self.monotonic() - self.last_success_mono <= self.config.cycle_timeout + 2 * self.config.interval)

    def snapshot(self) -> dict:
        return {name: getattr(self, name) for name in (
            'state', 'phase', 'last_success', 'attempt_started', 'attempt_finished', 'next_attempt',
            'consecutive_failures', 'pending_publication', 'error_category', 'changed_chapters',
        )} | {'live': self.live(), 'ready': self.ready()}


def health_app(health: Health) -> web.Application:
    app = web.Application()

    async def probe(request):
        ok = health.live() if request.path == '/livez' else health.ready()
        return web.json_response(health.snapshot(), status=200 if ok else 503,
                                 headers={'Cache-Control': 'no-store'})

    app.router.add_get('/livez', probe)
    app.router.add_get('/readyz', probe)
    return app


class Notifications:
    def __init__(self):
        self.url = os.getenv('DISCORD_WEBHOOK_URL')
        self.ping = os.getenv('PING_USER_ID', '')
        self.queue = asyncio.Queue(maxsize=10)
        try:
            parsed = urlsplit(self.url or '')
            self.enabled = parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and not parsed.username
        except ValueError:
            self.enabled = False

    def send(self, state: str, **details) -> None:
        if not self.enabled:
            return
        try:
            self.queue.put_nowait(notification_payload(state, ping=self.ping, **details))
        except asyncio.QueueFull:
            logger.warning('Discord notification queue full; dropping notification')

    async def run(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False) as session:
            while True:
                payload = await self.queue.get()
                try:
                    async with session.post(self.url, json=payload, allow_redirects=False) as response:
                        if not 200 <= response.status < 300:
                            logger.warning('Discord notification failed with HTTP {}', response.status)
                except (aiohttp.ClientError, TimeoutError, ValueError):
                    logger.warning('Discord notification delivery failed')
                finally:
                    self.queue.task_done()


def _terminal_log_format(record: dict) -> str:
    context = ' '.join(
        f'{key}={value:.2f}' if isinstance(value, float) else f'{key}={value}'
        for key, value in record['extra'].items()
    )
    record['context'] = f' | {context}' if context else ''
    return '<green>{time:YYYY-MM-DD HH:mm:ss Z}</green> | <level>{level: <8}</level> | <level>{message}</level>{context}\n{exception}'


def configure_logging(state: Path, worker: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, format=_terminal_log_format, backtrace=False, diagnose=False)
    logger.add(state / 'logs' / ('worker.jsonl' if worker else 'service.jsonl'),
               serialize=True, rotation='10 MB', retention=5, backtrace=False, diagnose=False)


def worker_main(config: RuntimeConfig) -> int:
    from autoscrape_autoupdate import update_once
    state = runtime_directory(config.repository)
    os.environ['NAROU_GIT_TIMEOUT'] = str(config.git_timeout)
    status = state / 'worker.json'

    def phase(value):
        atomic_json(status, {'phase': value})
        logger.bind(phase=value).info('Worker phase changed')

    try:
        prune_runtime(state)
        published_commits = []
        changed = asyncio.run(update_once(config.repository, phase=phase, published=published_commits.append,
                                         max_index_pages=config.max_index_pages,
                                         transport=TransportConfig(
                                             request_timeout_seconds=config.request_timeout,
                                             max_tries=config.http_attempts,
                                             retry_max_seconds=config.http_retry_deadline,
                                             diagnostics_dir=state / 'diagnostics',
                                         )))
        result = {'outcome': 'success', 'changed': changed, 'phase': 'complete'}
        if changed and published_commits:
            result['commit'] = published_commits[-1]
            result['commit_url'] = github_commit_url(config.repository, result['commit'])
        atomic_json(status, result)
        return 0
    except (RetryableUpdaterError, WebPageLoadError) as exc:
        logger.exception('Retryable update failure: {}', type(exc).__name__)
        atomic_json(status, {'outcome': 'retry', 'error_category': type(exc).__name__, 'phase': 'failed'})
        return 1
    except Exception as exc:
        logger.exception('Fatal update failure: {}', type(exc).__name__)
        atomic_json(status, {'outcome': 'fatal', 'error_category': type(exc).__name__, 'phase': 'failed'})
        return 1


def read_worker_status(state: Path) -> dict:
    try:
        return json.loads((state / 'worker.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


async def run_attempt(config: RuntimeConfig, state: Path, health: Health, stop: asyncio.Event) -> dict:
    (state / 'worker.json').unlink(missing_ok=True)
    process = await asyncio.create_subprocess_exec(
        *config.worker_command(), cwd=config.repository,
        stdout=asyncio.subprocess.DEVNULL,
        **({'creationflags': 0x00000200} if os.name == 'nt' else {'start_new_session': True}),
    )
    deadline = time.monotonic() + config.cycle_timeout
    shutdown_deadline = None
    waiter = asyncio.create_task(process.wait())
    try:
        while not waiter.done():
            if stop.is_set() and shutdown_deadline is None:
                health.state = 'stopping'
                shutdown_deadline = time.monotonic() + config.shutdown_grace
            if time.monotonic() >= min(deadline, shutdown_deadline or deadline):
                await asyncio.to_thread(kill_process_tree, process.pid)
                await waiter
                return {'outcome': 'retry', 'error_category': 'Shutdown' if stop.is_set() else 'CycleTimeout'}
            status = await asyncio.to_thread(read_worker_status, state)
            health.phase = status.get('phase', 'starting-worker')
            health.pending_publication = (state / 'transaction' / 'journal.json').exists()
            await asyncio.wait({waiter}, timeout=.2)
        status = await asyncio.to_thread(read_worker_status, state)
        health.pending_publication = (state / 'transaction' / 'journal.json').exists()
        if status.get('outcome') not in {'success', 'retry', 'fatal'}:
            return {'outcome': 'fatal', 'error_category': 'UnexpectedWorkerExit'}
        if process.returncode != 0 and status['outcome'] == 'success':
            return {'outcome': 'fatal', 'error_category': 'UnexpectedWorkerExit'}
        return status
    finally:
        if process.returncode is None:
            await asyncio.to_thread(kill_process_tree, process.pid)
        await waiter


def prune_runtime(state: Path) -> None:
    diagnostics = state / 'diagnostics'
    if diagnostics.exists():
        files = sorted((path for path in diagnostics.iterdir() if path.is_file()),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        for index, path in enumerate(files):
            if index >= 200 or time.time() - path.stat().st_mtime > 7 * 86400:
                path.unlink(missing_ok=True)
    for folder in state.glob('completed-*'):
        import shutil
        shutil.rmtree(folder)


async def wait_until(stop: asyncio.Event, deadline: float, monotonic=time.monotonic) -> None:
    while not stop.is_set():
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=min(remaining, 30))
        except TimeoutError:
            pass


async def run_service(config: RuntimeConfig, *, stop: asyncio.Event | None = None,
                      attempt=run_attempt, health: Health | None = None) -> int:
    state = runtime_directory(config.repository)
    stop = stop or asyncio.Event()
    health = health or Health(config)
    notifications = Notifications()
    runner = web.AppRunner(health_app(health), access_log=None)
    previous_signals = {}
    tasks = []
    with ProcessLock(state / 'daemon.lock'):
        try:
            await runner.setup()
            await web.TCPSite(runner, config.health_host, config.health_port).start()
            for host, port, *_ in runner.addresses:
                host = f'[{host}]' if ':' in host else host
                logger.info('HTTP health endpoints: http://{}:{}/livez | http://{}:{}/readyz',
                            host, port, host, port)
            loop = asyncio.get_running_loop()
            for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
                if hasattr(signal, name):
                    number = getattr(signal, name)
                    previous_signals[number] = signal.getsignal(number)
                    signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))

            async def heartbeat():
                while True:
                    health.heartbeat = time.monotonic()
                    await asyncio.sleep(5)

            tasks = [asyncio.create_task(heartbeat()), asyncio.create_task(notifications.run())]
            last_alert = None
            while not stop.is_set():
                health.state = 'running'
                health.next_attempt = None
                health.attempt_started = time.time()
                started = time.monotonic()
                result = await attempt(config, state, health, stop)
                health.attempt_finished = time.time()
                health.phase = result.get('phase', 'failed')
                health.error_category = result.get('error_category')
                if stop.is_set():
                    break
                if result['outcome'] == 'success':
                    if health.consecutive_failures:
                        notifications.send('recovery', failures=health.consecutive_failures, phase=health.phase)
                    health.consecutive_failures = 0
                    last_alert = None
                    health.last_success = time.time()
                    health.last_success_mono = time.monotonic()
                    health.changed_chapters = result.get('changed', 0)
                    if health.changed_chapters:
                        notifications.send('publication', changed_chapters=health.changed_chapters,
                                           phase=health.phase, commit_url=result.get('commit_url'))
                    delay = config.interval
                else:
                    health.consecutive_failures += 1
                    health.changed_chapters = 0
                    now = time.monotonic()
                    if last_alert is None or now - last_alert >= 3600 or result['outcome'] == 'fatal':
                        notification_state = ('fatal' if result['outcome'] == 'fatal' else
                                              'retry' if last_alert is None else 'continuing-failure')
                        notifications.send(notification_state, error_category=health.error_category,
                                           failures=health.consecutive_failures, phase=health.phase)
                        last_alert = now
                    if result['outcome'] == 'fatal':
                        health.state = 'fatal'
                        return 1
                    delay = retry_delay(health.consecutive_failures, config)
                logger.bind(phase=health.phase, duration_seconds=time.monotonic() - started,
                            changed_chapters=health.changed_chapters, failures=health.consecutive_failures,
                            outcome=result['outcome']).info('Cycle finished')
                if config.once:
                    return 0 if result['outcome'] == 'success' else 1
                health.state = 'waiting'
                health.next_attempt = time.time() + delay
                logger.info('Next update attempt at {:%Y-%m-%d %H:%M:%S %Z} (in {:.1f} seconds)',
                            datetime.fromtimestamp(health.next_attempt).astimezone(), delay)
                await wait_until(stop, time.monotonic() + delay)
            return 0
        finally:
            health.state = 'stopping'
            # Deliver queued final transitions when possible, bounded by one send timeout.
            if tasks:
                with suppress(TimeoutError):
                    await asyncio.wait_for(notifications.queue.join(), timeout=10)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.cleanup()
            for number, previous in previous_signals.items():
                signal.signal(number, previous)
            health.state = 'stopped'


def cli() -> None:
    config = RuntimeConfig.from_cli()
    try:
        state = runtime_directory(config.repository)
        configure_logging(state, config.worker)
        code = worker_main(config) if config.worker else asyncio.run(run_service(config))
    except LockUnavailable:
        logger.error('Another updater already owns this checkout')
        code = 3
    except (OSError, FatalUpdaterError, ValueError):
        logger.exception('Updater could not start or continue; inspect local logs and recovery state')
        code = 1
    raise SystemExit(code)
