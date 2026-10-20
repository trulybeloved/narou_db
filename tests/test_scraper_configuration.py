import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

import autoscrape_autoupdate as updater
from custom_modules.webscraper import TransportConfig, get_http_response_status


def test_production_orchestrator_keeps_effects_outside_scrape():
    operation = AsyncMock(return_value=['00002', '00004'])
    with patch.object(updater, 'require_clean_current_master') as guard, \
            patch.object(updater, 'scrape_and_persist', operation), \
            patch.object(updater, 'send_discord_message') as notify, \
            patch.object(updater, 'commit_datastores_and_push') as publish:
        assert asyncio.run(updater.update_once()) == 2
        guard.assert_called_once_with(updater.REPOSITORY)
        operation.call_args.kwargs['notify']('Found updates')
        notify.assert_called_once_with('Found updates', ping=True)
        publish.assert_called_once_with(updater.REPOSITORY, 'Chapter update for 00002, 00004')


def test_production_guard_failure_prevents_scraping():
    operation = AsyncMock()
    with patch.object(updater, 'require_clean_current_master', side_effect=RuntimeError('dirty')), \
            patch.object(updater, 'scrape_and_persist', operation):
        with pytest.raises(RuntimeError, match='dirty'):
            asyncio.run(updater.update_once())
        operation.assert_not_called()


def test_unchanged_production_run_does_not_publish():
    with patch.object(updater, 'require_clean_current_master'), \
            patch.object(updater, 'scrape_and_persist', AsyncMock(return_value=[])), \
            patch.object(updater, 'commit_datastores_and_push') as publish:
        assert asyncio.run(updater.update_once()) == 0
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


def test_production_status_probe_retains_user_agent_and_redirect_default():
    with patch('custom_modules.webscraper.requests.head', return_value=Mock(status_code=200)) as head:
        assert get_http_response_status('https://example.org/') == 200
        assert head.call_args.kwargs['headers']['User-Agent'].startswith('Mozilla/')
        assert 'allow_redirects' not in head.call_args.kwargs


def test_default_scraper_configuration_is_production():
    config = updater.ScrapeConfig()
    assert config.datastore_root == Path('datastores')
    assert config.database == Path('datastores/narou.sqlite')
    assert config.transport.mock_origin is None
    assert config.transport.navigation_timeout_ms == 0
    assert config.index_urls == [f'https://ncode.syosetu.com/n2267be/?p={n}' for n in range(1, 10)]
