"""Local control API and mock HTTP origin."""

import json
from importlib.resources import files
from pathlib import Path

from aiohttp import web

from .engine import Engine
from .packs import narou_scenario

ENGINE = web.AppKey('engine', Engine)
PREFIX = '/__mock/api/v1'


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except (ValueError, KeyError, TypeError, OSError, AttributeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


def create_app(*, catalog=None, initial='narou', asset_root=None):
    if catalog is None:
        generic = json.loads(files('mocksite').joinpath('examples/generic.json').read_text(encoding='utf-8'))
        catalog = {'narou': narou_scenario(), generic['id']: generic}
    app = web.Application(middlewares=[errors], client_max_size=4 * 1024 * 1024)
    engine = Engine(catalog, initial, asset_root)
    app[ENGINE] = engine

    async def panel(request):
        return web.Response(text=files('mocksite').joinpath('panel.html').read_text(encoding='utf-8'), content_type='text/html')

    async def status(request):
        return web.json_response(engine.status())

    async def scenarios(request):
        return web.json_response({"scenarios": list(engine.catalog)})

    async def load(request):
        data = await request.json()
        engine.load(engine.catalog[data['id']] if 'id' in data else data['manifest'])
        return web.json_response(engine.status())

    async def select(request):
        engine.select((await request.json())['state'])
        return web.json_response(engine.status())

    async def reset(request):
        engine.select(engine.manifest['initial_state'], clear=True)
        return web.json_response(engine.status())

    async def history(request):
        return web.json_response({"requests": engine.history})

    async def manifest(request):
        return web.json_response(engine.export())

    app.router.add_get('/__mock/', panel)
    app.router.add_get(PREFIX + '/status', status)
    app.router.add_get(PREFIX + '/scenarios', scenarios)
    app.router.add_post(PREFIX + '/load', load)
    app.router.add_post(PREFIX + '/state', select)
    app.router.add_post(PREFIX + '/reset', reset)
    app.router.add_get(PREFIX + '/requests', history)
    app.router.add_get(PREFIX + '/manifest', manifest)
    app.router.add_route('*', '/{tail:.*}', engine.serve)
    return app


def load_catalog(directory):
    catalog = {'narou': narou_scenario()}
    for path in sorted(Path(directory).glob('*.json')):
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest['id'] in catalog:
            raise ValueError(f"Duplicate scenario id: {manifest['id']}")
        catalog[manifest['id']] = manifest
    return catalog
