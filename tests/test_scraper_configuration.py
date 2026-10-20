import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import autoscrape_autoupdate as updater
from custom_modules.webscraper import TransportConfig, get_http_response_status


def test_production_orchestrator_stages_before_publishing(tmp_path):
    operation = AsyncMock(return_value=['00002', '00004'])
    database = tmp_path / 'datastores' / 'narou.sqlite'
    database.parent.mkdir()
    from custom_modules.narou_database import connect, initialize
    connection = connect(database, writable=True)
    initialize(connection)
    connection.close()
    with patch.object(updater, 'runtime_directory', return_value=tmp_path / 'state'), \
            patch.object(updater, 'require_clean_current_master') as guard, \
            patch.object(updater, '_git', return_value='base'), \
            patch.object(updater, 'scrape_and_persist', operation), \
            patch.object(updater.Publication, 'record', return_value={'manifest': True}) as record, \
            patch.object(updater.Publication, 'finish', return_value=2) as publish:
        assert asyncio.run(updater.update_once(tmp_path)) == 2
        guard.assert_called_once_with(tmp_path)
        assert operation.call_args.args[0].datastore_root != database.parent
        record.assert_called_once_with('base', ['00002', '00004'])
        publish.assert_called_once()


def test_production_guard_failure_prevents_scraping(tmp_path):
    operation = AsyncMock()
    with patch.object(updater, 'runtime_directory', return_value=tmp_path / 'state'), \
            patch.object(updater, 'require_clean_current_master', side_effect=RuntimeError('dirty')), \
            patch.object(updater, 'scrape_and_persist', operation):
        with pytest.raises(RuntimeError, match='dirty'):
            asyncio.run(updater.update_once())
        operation.assert_not_called()


def test_lower_level_scraper_cannot_bypass_production_writer_lock(tmp_path):
    (tmp_path / '.git').mkdir()
    operation = AsyncMock()
    with patch.object(updater, '_scrape_index_snapshot', operation):
        with pytest.raises(updater.FatalUpdaterError, match='locked update_once'):
            asyncio.run(updater.scrape_and_persist(updater.ScrapeConfig(datastore_root=tmp_path / 'datastores')))
    operation.assert_not_called()


def test_unchanged_production_run_does_not_publish(tmp_path):
    (tmp_path / 'datastores').mkdir()
    (tmp_path / 'datastores' / 'narou.sqlite').write_bytes(b'fake database for mocked validation')
    with patch.object(updater, 'runtime_directory', return_value=tmp_path / 'state'), \
            patch.object(updater, 'require_clean_current_master'), \
            patch.object(updater, 'connect'), patch.object(updater, 'validate'), \
            patch.object(updater, '_git', return_value='base'), \
            patch.object(updater.Publication, 'prepare'), \
            patch.object(updater.shutil, 'rmtree'), \
            patch.object(updater, 'scrape_and_persist', AsyncMock(return_value=[])), \
            patch.object(updater.Publication, 'finish') as publish:
        assert asyncio.run(updater.update_once(tmp_path)) == 0
        publish.assert_not_called()


def test_transport_preserves_canonical_urls_and_rejects_other_origins():
    original = 'https://ncode.syosetu.com/n2267be/?p=2'
    for port in (12001, 12002):
        config = TransportConfig(mock_origin=f'http://127.0.0.1:{port}')
        assert config.map_url(original) == f'http://127.0.0.1:{port}/n2267be/?p=2'
        for outside in ('https://example.org/', f'http://127.0.0.1:{port+1}/', 'https://ncode.syosetu.com.attacker.example/'):
            with pytest.raises(ValueError, match='Blocked'):
                config.map_url(outside)
    assert TransportConfig().map_url(original) == original


def test_production_status_probe_uses_async_transport():
    with patch('custom_modules.webscraper.async_get_http_response_status', AsyncMock(return_value=200)) as head:
        assert get_http_response_status('https://example.org/') == 200
        head.assert_awaited_once_with('https://example.org/', transport=None)


def test_default_scraper_configuration_is_production():
    config = updater.ScrapeConfig()
    assert config.datastore_root == updater.REPOSITORY / 'datastores'
    assert config.database == updater.REPOSITORY / 'datastores/narou.sqlite'
    assert config.transport.mock_origin is None
    assert config.transport.request_timeout_seconds == 30
    assert config.index_urls is None
