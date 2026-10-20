import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
import psutil
import pytest

from custom_modules import git_workflow
from custom_modules.git_workflow import _git, runtime_directory, GitTransportError, GitWorkflowError
from custom_modules import updater_runtime as runtime
from custom_modules.updater_runtime import Health, RuntimeConfig, retry_delay, health_app
from custom_modules.updater_safety import ProcessLock, LockUnavailable


@pytest.fixture
def runtime_config(tmp_path, monkeypatch):
    monkeypatch.delenv('DISCORD_WEBHOOK_URL', raising=False)
    _git(tmp_path, 'init', '--initial-branch=master')
    return RuntimeConfig(repository=tmp_path, health_port=0, interval=.02,
                         retry_base=.01, retry_cap=.04, shutdown_grace=.05)


def test_config_cli_over_environment_and_invalid_values(monkeypatch, tmp_path):
    monkeypatch.setenv('NAROU_INTERVAL', '55')
    monkeypatch.setattr(runtime, 'load_dotenv', lambda *args, **kwargs: None)
    config = RuntimeConfig.from_cli(['--interval', '77', '--repository', str(tmp_path)])
    assert config.interval == 77 and config.repository == tmp_path
    assert RuntimeConfig.from_cli([]).interval == 55
    for value in ('nan', 'inf', '0', '-1'):
        with pytest.raises(SystemExit) as exc:
            RuntimeConfig.from_cli(['--interval', value])
        assert exc.value.code == 2
    with pytest.raises(ValueError):
        RuntimeConfig(retry_base=1000, retry_cap=10)


def test_retry_jitter_growth_cap_and_reset():
    config = RuntimeConfig()
    lower = lambda a, b: a
    upper = lambda a, b: b
    assert [retry_delay(n, config, upper) for n in range(1, 8)] == [30, 60, 120, 240, 480, 900, 900]
    assert retry_delay(10000, config, lower) == 450
    assert retry_delay(1, config, upper) == 30


def test_health_freshness_uses_monotonic_time():
    now = [100.]
    health = Health(RuntimeConfig(), monotonic=lambda: now[0], heartbeat=100)
    assert health.live() and not health.ready()
    health.last_success_mono = 100
    assert health.ready()
    health.pending_publication = True
    assert not health.ready() and health.live()
    health.pending_publication = False
    health.consecutive_failures = 1
    assert not health.ready() and health.live()
    health.consecutive_failures = 0
    now[0] = 131
    assert not health.live()
    health.heartbeat = now[0]
    now[0] = 100 + 3601
    health.heartbeat = now[0]
    assert health.live() and not health.ready()


def test_http_status_and_sanitized_payload(runtime_config):
    async def run():
        health = Health(runtime_config)
        async with TestClient(TestServer(health_app(health))) as client:
            assert (await client.get('/livez')).status == 200
            assert (await client.get('/readyz')).status == 503
            health.last_success_mono = health.monotonic()
            response = await client.get('/readyz')
            assert response.status == 200 and response.headers['Cache-Control'] == 'no-store'
            health.error_category = 'GitTransportError'
            health.consecutive_failures = 1
            response = await client.get('/readyz')
            body = await response.json()
            assert response.status == 503 and body['error_category'] == 'GitTransportError'
            assert set(body) == {'state', 'phase', 'last_success', 'attempt_started', 'attempt_finished',
                                 'next_attempt', 'consecutive_failures', 'pending_publication',
                                 'error_category', 'changed_chapters', 'live', 'ready'}
            health.state = 'stopping'
            assert (await client.get('/livez')).status == 503
    asyncio.run(run())


def test_wait_can_be_stopped_and_ignores_wall_clock():
    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.wait_until(stop, 10**12, monotonic=lambda: 0))
        await asyncio.sleep(0)
        with patch.object(runtime.time, 'time', return_value=-10**12):
            stop.set()
            await asyncio.wait_for(task, .2)
    asyncio.run(run())


def test_service_retries_recovers_and_does_not_overlap(runtime_config):
    async def run():
        stop = asyncio.Event()
        health = Health(runtime_config)
        calls, alerts = [], []
        outcomes = ['retry', 'retry', 'success', 'success']
        commit_url = 'https://github.com/example/narou/commit/' + 'a' * 40

        async def attempt(config, state, health, stop):
            calls.append((len(calls), health.consecutive_failures))
            await asyncio.sleep(.001)
            if len(calls) == 4:
                stop.set()
            return {'outcome': outcomes[len(calls) - 1], 'changed': 2,
                    'error_category': 'SourceDataError', 'commit_url': commit_url}

        with patch.object(runtime.Notifications, 'send', side_effect=lambda state, **details: alerts.append((state, details))):
            assert await runtime.run_service(runtime_config, stop=stop, attempt=attempt, health=health) == 0
        assert calls == [(0, 0), (1, 1), (2, 2), (3, 0)]
        assert len(alerts) == 3  # first failure, recovery, and successful publication
        assert [state for state, _ in alerts] == ['retry', 'recovery', 'publication']
        assert alerts[-1][1]['changed_chapters'] == 2
        assert alerts[-1][1]['commit_url'] == commit_url
        payload = runtime.notification_payload('publication', **alerts[-1][1])
        assert any(field['value'] == f'[View commit]({commit_url})' for field in payload['embeds'][0]['fields'])
        assert health.last_success is not None and health.consecutive_failures == 0
        assert health.state == 'stopped'
    asyncio.run(run())


def test_service_fatal_and_listener_failure_prevent_further_attempts(runtime_config):
    async def run():
        calls = []

        async def attempt(*args):
            calls.append(True)
            return {'outcome': 'fatal', 'error_category': 'FatalUpdaterError'}

        assert await runtime.run_service(runtime_config, attempt=attempt) == 1
        assert len(calls) == 1
        with patch.object(runtime.web.TCPSite, 'start', side_effect=OSError('address in use')):
            with pytest.raises(OSError, match='address in use'):
                await runtime.run_service(runtime_config, attempt=attempt)
        assert len(calls) == 1
    asyncio.run(run())


def test_singleton_and_writer_lock_released_after_process_death(runtime_config, tmp_path):
    lock = tmp_path / 'exclusive.lock'
    ready = tmp_path / 'ready'
    code = ('import sys,time; from pathlib import Path; '
            'from custom_modules.updater_safety import ProcessLock; '
            'lock=ProcessLock(Path(sys.argv[1])); lock.__enter__(); '
            'Path(sys.argv[2]).touch(); time.sleep(60)')
    process = subprocess.Popen([sys.executable, '-c', code, str(lock), str(ready)])
    try:
        import time
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        with pytest.raises(LockUnavailable):
            with ProcessLock(lock):
                pass
    finally:
        process.kill()
        process.wait(timeout=5)
    with ProcessLock(lock):
        pass


def test_daemon_singleton_rejects_second_service(runtime_config):
    async def run():
        state = runtime_directory(runtime_config.repository)
        with ProcessLock(state / 'daemon.lock'):
            with pytest.raises(LockUnavailable):
                await runtime.run_service(runtime_config)
    asyncio.run(run())


def test_hung_worker_is_killed_and_http_remains_responsive(runtime_config, monkeypatch):
    config = replace(runtime_config, cycle_timeout=.25)
    monkeypatch.setattr(RuntimeConfig, 'worker_command', lambda self: [sys.executable, '-c', 'import time; time.sleep(60)'])

    async def run():
        state = runtime_directory(config.repository)
        state.mkdir(parents=True, exist_ok=True)
        health = Health(config)
        task = asyncio.create_task(runtime.run_attempt(config, state, health, asyncio.Event()))
        async with TestClient(TestServer(health_app(health))) as client:
            assert (await asyncio.wait_for(client.get('/livez'), .2)).status == 200
            result = await asyncio.wait_for(task, 5)
        assert result == {'outcome': 'retry', 'error_category': 'CycleTimeout'}
    asyncio.run(run())


def test_shutdown_kills_worker_descendants(runtime_config, monkeypatch, tmp_path):
    ready = tmp_path / 'child-pid'
    code = ('import subprocess,sys,time; from pathlib import Path; '
            'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
            'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
    monkeypatch.setattr(RuntimeConfig, 'worker_command', lambda self: [sys.executable, '-c', code, str(ready)])

    async def run():
        state = runtime_directory(runtime_config.repository)
        state.mkdir(parents=True, exist_ok=True)
        stop = asyncio.Event()
        task = asyncio.create_task(runtime.run_attempt(runtime_config, state, Health(runtime_config), stop))
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(.01)
        assert ready.exists()
        child = psutil.Process(int(ready.read_text()))
        stop.set()
        result = await asyncio.wait_for(task, 6)
        assert result['error_category'] == 'Shutdown'
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    asyncio.run(run())


def test_unexpected_worker_exit_is_fatal(runtime_config, monkeypatch):
    monkeypatch.setattr(RuntimeConfig, 'worker_command', lambda self: [sys.executable, '-c', 'raise SystemExit(9)'])

    async def run():
        state = runtime_directory(runtime_config.repository)
        state.mkdir(parents=True, exist_ok=True)
        result = await runtime.run_attempt(runtime_config, state, Health(runtime_config), asyncio.Event())
        assert result == {'outcome': 'fatal', 'error_category': 'UnexpectedWorkerExit'}
    asyncio.run(run())


@pytest.mark.parametrize('message,kind', [('fatal: Could not resolve host', GitTransportError),
                                        ('fatal: Authentication failed', GitWorkflowError),
                                        ('fatal: unable to access: SSL certificate problem', GitWorkflowError),
                                        ('The requested URL returned error: 503', GitTransportError)])
def test_git_error_classification(message, kind, runtime_config):
    process = type('Process', (), {'returncode': 1, 'communicate': lambda self, **kw: ('', message)})()
    with patch.object(git_workflow.subprocess, 'Popen', return_value=process):
        with pytest.raises(kind):
            _git(runtime_config.repository, 'fetch')


def test_git_timeout_kills_children(runtime_config, monkeypatch):
    process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    monkeypatch.setenv('NAROU_GIT_TIMEOUT', '.05')
    with patch.object(git_workflow.subprocess, 'Popen', return_value=process):
        with pytest.raises(GitTransportError, match='deadline'):
            _git(runtime_config.repository, 'fetch')
    assert process.poll() is not None


def test_notifications_optional_and_bounded(monkeypatch):
    for url in ('', 'invalid', 'http://[bad'):
        monkeypatch.setenv('DISCORD_WEBHOOK_URL', url)
        notifications = runtime.Notifications()
        notifications.send('retry')
        assert notifications.queue.empty()
    monkeypatch.setenv('DISCORD_WEBHOOK_URL', 'http://127.0.0.1:1/webhook')

    async def run():
        notifications = runtime.Notifications()
        for _ in range(11):
            notifications.send('retry')
        assert notifications.queue.qsize() == 10
        sender = asyncio.create_task(notifications.run())
        await asyncio.wait_for(notifications.queue.join(), 3)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
    asyncio.run(run())


def test_actual_cli_worker_reports_safe_local_failure(runtime_config):
    environment = dict(os.environ, DISCORD_WEBHOOK_URL='', WCT_WEBHOOK_URL='')
    result = subprocess.run([sys.executable, str(runtime.SCRIPT), '--once', '--repository',
                             str(runtime_config.repository), '--health-port', '0'],
                            env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    state = runtime_directory(runtime_config.repository)
    status = json.loads((state / 'worker.json').read_text())
    assert status['outcome'] == 'fatal' and status['error_category'] == 'GitWorkflowError'
    assert (state / 'logs' / 'service.jsonl').exists()
    assert (state / 'logs' / 'worker.jsonl').exists()


@pytest.mark.parametrize('remote,expected', [
    ('https://github.com/example/narou.git', 'example/narou'),
    ('git@github.com:example/narou.git', 'example/narou'),
    ('ssh://git@github.com/example/narou.git', 'example/narou'),
    ('https://user:secret@github.com/example/narou.git?token=secret', 'example/narou'),
    ('/tmp/local.git', None),
    ('https://github.com.attacker.invalid/example/narou.git', None),
    ('https://github.com/../narou.git', None),
    ('https://[bad', None),
])
def test_commit_links_use_github_push_remote_without_credentials(remote, expected, tmp_path):
    commit = 'a' * 40
    with patch.object(git_workflow, '_git', return_value=remote):
        url = git_workflow.github_commit_url(tmp_path, commit)
    assert url == (f'https://github.com/{expected}/commit/{commit}' if expected else None)


def test_worker_reports_exact_verified_publication_commit(runtime_config):
    import autoscrape_autoupdate as updater

    commit = 'b' * 40
    commit_url = f'https://github.com/example/narou/commit/{commit}'

    async def update(repository, **kwargs):
        kwargs['published'](commit)
        return 2

    with patch.object(updater, 'update_once', side_effect=update), \
            patch.object(runtime, 'github_commit_url', return_value=commit_url) as resolve:
        assert runtime.worker_main(runtime_config) == 0
    resolve.assert_called_once_with(runtime_config.repository, commit)
    status = runtime.read_worker_status(runtime_directory(runtime_config.repository))
    assert status == {'outcome': 'success', 'changed': 2, 'phase': 'complete',
                      'commit': commit, 'commit_url': commit_url}
