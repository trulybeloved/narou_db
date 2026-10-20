import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import urlopen

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
import pytest

from mocksite.engine import validate_manifest
from mocksite.engine import Engine
from mocksite.packs import narou_scenario
from mocksite.server import create_app


def static(routes):
    return {'schema_version': 1, 'id': 'example', 'initial_state': 'baseline',
            'states': {'baseline': {'routes': routes}}}


def test_validation_and_fixture_containment(tmp_path):
    manifest = narou_scenario()
    _, states = validate_manifest(manifest)
    assert len(states) == 19
    (tmp_path / 'body.html').write_text('<p>Fixture</p>', encoding='utf-8')
    manifest = static([{'path': '/file', 'responses': [{'file': 'body.html'}]}])
    _, compiled = validate_manifest(manifest, tmp_path)
    assert compiled['baseline'][0]['responses'][0]['body'] == '<p>Fixture</p>'
    engine = Engine({'example': manifest}, initial='example', asset_root=tmp_path)
    exported = engine.export()
    assert 'file' not in exported['states']['baseline']['routes'][0]['responses'][0]
    validate_manifest(exported)  # Export needs no asset root.
    manifest['states']['baseline']['routes'][0]['responses'][0]['file'] = '../escape.html'
    with pytest.raises(ValueError, match='escapes'):
        validate_manifest(manifest, tmp_path)


@pytest.mark.parametrize('change', [
    lambda m: m.update(schema_version=2),
    lambda m: m.update(initial_state='missing'),
    lambda m: m['states']['baseline']['routes'][0].update(path='/__mock/overlap'),
    lambda m: m['states']['baseline']['routes'][0].update(query={'p': 1}),
    lambda m: m['states']['baseline']['routes'][0]['responses'][0].update(delay_ms=-1),
    lambda m: m['states']['baseline']['routes'][0]['responses'][0].update(status=999),
    lambda m: m['states']['baseline']['routes'][0]['responses'][0].update(headers={'x': 'a\nb'}),
])
def test_invalid_manifest(change):
    manifest = static([{'path': '/', 'responses': [{'body': 'ok'}]}])
    change(manifest)
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_control_api_rejects_atomically_and_resets(mock_service):
    before = mock_service.api('status')
    for action, data in [('state', {'state': 'missing'}), ('load', {'manifest': {'id': 'bad'}})]:
        with pytest.raises(HTTPError) as error:
            mock_service.api(action, data)
        assert error.value.code == 400
        error.value.close()
        assert mock_service.api('status') == before
    mock_service.state('fail-then-recover')
    target = mock_service.origin + '/n2267be/?p=1'
    with pytest.raises(HTTPError) as error:
        urlopen(target)
    assert error.value.code == 500
    error.value.close()
    with urlopen(target) as response:
        assert response.status == 200
    assert [r['attempt'] for r in mock_service.api('requests')['requests']] == [1, 2]
    mock_service.api('reset', {})
    assert mock_service.api('status')['state'] == 'baseline'
    assert mock_service.api('requests')['requests'] == []


def test_methods_queries_and_generic_reuse(mock_service):
    mock_service.api('load', {'id': 'generic'})
    with urlopen(mock_service.origin + '/items/') as response:
        assert b'First item' in response.read()
    mock_service.state('changed')
    with urlopen(mock_service.origin + '/items/') as response:
        assert b'New item' in response.read()
    with pytest.raises(HTTPError) as error:
        urlopen(mock_service.origin + '/items/?unexpected=1')
    assert error.value.code == 404
    error.value.close()
    manifest = mock_service.api('manifest')
    mock_service.api('load', {'manifest': manifest})
    assert mock_service.api('status')['state'] == 'baseline'


def test_inflight_snapshot_and_instance_isolation():
    async def run():
        manifest = static([{'path': '/', 'responses': [{'body': 'old', 'delay_ms': 200}]}])
        manifest['states']['changed'] = {'routes': [{'path': '/', 'responses': [{'body': 'new'}]}]}
        async with TestServer(create_app(catalog={'example': manifest}, initial='example')) as first, TestServer(create_app(catalog={'example': manifest}, initial='example')) as second:
            async with ClientSession() as client:
                pending = asyncio.create_task(client.get(first.make_url('/')))
                for _ in range(100):
                    response = await client.get(first.make_url('/__mock/api/v1/requests'))
                    if (await response.json())['requests']:
                        break
                    await asyncio.sleep(.005)
                response = await client.post(first.make_url('/__mock/api/v1/state'), json={'state': 'changed'})
                assert response.status == 200
                assert await (await pending).text() == 'old'
                assert await (await client.get(first.make_url('/'))).text() == 'new'
                assert await (await client.get(second.make_url('/'))).text() == 'old'
    asyncio.run(run())


@pytest.mark.parametrize('state,status', [('404', 404), ('429', 429), ('500', 500)])
def test_http_faults(mock_service, state, status):
    mock_service.state(state)
    with pytest.raises(HTTPError) as error:
        urlopen(mock_service.origin + '/n2267be/?p=1')
    assert error.value.code == status
    if status == 429:
        assert error.value.headers['Retry-After'] == '1'
    error.value.close()


def test_reference_structure_matches_pack():
    from bs4 import BeautifulSoup
    reference = BeautifulSoup((Path(__file__).parent / 'fixtures' / 'narou-index.html').read_text(encoding='utf-8'), 'html.parser')
    _, states = validate_manifest(narou_scenario())
    generated = BeautifulSoup(states['revised'][0]['responses'][0]['body'], 'html.parser')
    for doc in (reference, generated):
        entry = doc.select_one('.p-eplist__sublist')
        assert entry.select_one('a.p-eplist__subtitle')['href'].startswith('/n2267be/')
        assert entry.select_one('.p-eplist__update')
        assert doc.select_one('.p-eplist__update span[title] u').text == '改'
