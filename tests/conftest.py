import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]


class MockService:
    def __init__(self, origin):
        self.origin = origin

    def api(self, action, data=None):
        request = Request(self.origin + '/__mock/api/v1/' + action,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def state(self, state):
        return self.api('state', {'state': state})


@pytest.fixture
def artifact_dir(request):
    root = Path(os.getenv('NAROU_TEST_ARTIFACTS', ROOT / 'outputs' / 'mock-tests'))
    folder = root / request.node.name.replace('[', '-').replace(']', '')
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.fixture
def mock_service(tmp_path, artifact_dir):
    ready = tmp_path / 'ready.json'
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'tools' / 'mocksite'), PYTHONIOENCODING='utf-8')
    log = (artifact_dir / 'server.log').open('w', encoding='utf-8')
    process = subprocess.Popen([sys.executable, '-m', 'mocksite', 'serve', '--port', '0', '--ready-file', str(ready)],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    service = None
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                log.flush()
                pytest.fail('Mock startup failed: ' + (artifact_dir / 'server.log').read_text(encoding='utf-8'))
            time.sleep(.05)
        service = MockService(json.loads(ready.read_text(encoding='utf-8'))['origin'])
        yield service
    finally:
        if service:
            for name in ('status', 'requests', 'manifest'):
                try:
                    (artifact_dir / f'{name}.json').write_text(json.dumps(service.api(name), ensure_ascii=False, indent=2), encoding='utf-8')
                except OSError:
                    pass
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


@pytest.fixture
def isolated_scraper(tmp_path, mock_service, monkeypatch, artifact_dir):
    import autoscrape_autoupdate as updater
    from custom_modules.webscraper import TransportConfig
    from sandbox_scrape import prepare_sandbox
    production = ROOT / 'datastores' / 'narou.sqlite'
    before = hashlib.sha256(production.read_bytes()).digest() if production.exists() else None
    def forbidden(*args, **kwargs):
        pytest.fail('Production external effect was called')
    for name in ('require_clean_current_master', 'commit_datastores_and_push', 'send_discord_message'):
        monkeypatch.setattr(updater, name, forbidden)
    root = prepare_sandbox(tmp_path / 'sandbox')
    config = updater.ScrapeConfig(
        datastore_root=root,
        index_urls=[f'https://ncode.syosetu.com/n2267be/?p={n}' for n in range(1, 4)],
        transport=TransportConfig(mock_origin=mock_service.origin, navigation_timeout_ms=3000,
                                  max_tries=1, retry_base_seconds=0, jitter=False,
                                  trace_dir=artifact_dir / 'traces'),
        clock=lambda: 1735689600,
    )
    yield config
    after = hashlib.sha256(production.read_bytes()).digest() if production.exists() else None
    assert before == after, 'Production database changed'
