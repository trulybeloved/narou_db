"""Validated immutable snapshots and deterministic request handling."""

import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web

from .packs import compile_routes


def _integer(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate_manifest(manifest, root=None):
    """Compile every state before making any change to the running service."""
    manifest = deepcopy(manifest)
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be an object')
    if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise ValueError('schema_version must be 1')
    if not isinstance(manifest.get('id'), str) or not manifest['id']:
        raise ValueError('id must be a nonempty string')
    states = manifest.get('states')
    if not isinstance(states, dict) or not states or manifest.get('initial_state') not in states:
        raise ValueError('states must contain initial_state')
    compiled = {}
    for name, state in states.items():
        if not isinstance(name, str) or not isinstance(state, dict):
            raise ValueError('states must map names to objects')
        routes = deepcopy(compile_routes(manifest.get('site_pack', 'static'), state))
        if not isinstance(routes, list):
            raise ValueError('routes must be a list')
        seen = set()
        for route in routes:
            if not isinstance(route, dict):
                raise ValueError('route must be an object')
            method = route.setdefault('method', 'GET')
            path = route['path']
            query = route.setdefault('query', {})
            if method not in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
                raise ValueError('unsupported route method')
            if not isinstance(path, str) or not path.startswith('/') or '?' in path or path.startswith('/__mock'):
                raise ValueError('route path must be absolute and outside /__mock')
            if not isinstance(query, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in query.items()):
                raise ValueError('query must map strings to strings')
            key = (method, path, tuple(sorted(query.items())))
            if key in seen:
                raise ValueError(f'duplicate route: {key}')
            seen.add(key)
            responses = route['responses']
            if not isinstance(responses, list) or not responses:
                raise ValueError('responses must be a nonempty list')
            for response in responses:
                if not isinstance(response, dict):
                    raise ValueError('response must be an object')
                if not _integer(response.get('status', 200), 200, 599):
                    raise ValueError('status must be an integer from 200 to 599')
                if not _integer(response.get('delay_ms', 0), 0, 60000):
                    raise ValueError('delay_ms must be between 0 and 60000')
                headers = response.setdefault('headers', {})
                if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) and '\r' not in k+v and '\n' not in k+v for k, v in headers.items()):
                    raise ValueError('headers must contain strings without newlines')
                if any(k.lower() in ('content-length', 'transfer-encoding') for k in headers):
                    raise ValueError('response framing headers are managed by the server')
                if 'file' in response:
                    if root is None or 'body' in response:
                        raise ValueError('file responses require an asset root and no body')
                    base = Path(root).resolve()
                    asset = (base / response['file']).resolve()
                    if not asset.is_relative_to(base):
                        raise ValueError('fixture file escapes asset root')
                    response['body'] = asset.read_text(encoding='utf-8')
                if not isinstance(response.get('body', ''), str):
                    raise ValueError('body must be a string')
        compiled[name] = routes
    return manifest, compiled


@dataclass
class Snapshot:
    state: str
    routes: list
    generation: int
    counters: Counter = field(default_factory=Counter)


class Engine:
    def __init__(self, catalog, initial='narou', asset_root=None):
        self.catalog = catalog
        self.asset_root = asset_root
        self.generation = 0
        self.history = []
        self.load(catalog[initial])

    def load(self, manifest):
        validated, compiled = validate_manifest(manifest, self.asset_root)
        self.manifest, self.compiled = validated, compiled
        self.select(validated['initial_state'], clear=True)

    def select(self, state, clear=False):
        if state not in self.compiled:
            raise ValueError(f'Unknown state: {state}')
        self.generation += 1
        self.snapshot = Snapshot(state, self.compiled[state], self.generation)
        if clear:
            self.history.clear()

    def status(self):
        return {"scenario": self.manifest['id'], "schema_version": 1,
                "site_pack": self.manifest.get('site_pack', 'static'),
                "state": self.snapshot.state, "states": list(self.compiled),
                "generation": self.generation, "routes": self.snapshot.routes,
                "request_count": len(self.history)}

    def export(self):
        result = deepcopy(self.manifest)
        for name, state in result['states'].items():
            for route in state.get('routes', []):
                key = (route.get('method', 'GET'), route['path'], route.get('query', {}))
                compiled = next(r for r in self.compiled[name] if (r['method'], r['path'], r['query']) == key)
                for response, compiled_response in zip(route['responses'], compiled['responses']):
                    if 'file' in response:
                        response.pop('file')
                        response['body'] = compiled_response['body']
        return result

    async def serve(self, request):
        snapshot = self.snapshot
        query = tuple(sorted(request.query.items()))
        response = {"status": 404, "body": 'No matching mock route'}
        key = (request.method, request.path, query)
        route = next((r for r in snapshot.routes if (r['method'], r['path'], tuple(sorted(r['query'].items()))) == key), None)
        if route is None and request.method == 'HEAD':
            route = next((r for r in snapshot.routes if (r['method'], r['path'], tuple(sorted(r['query'].items()))) == ('GET', request.path, query)), None)
        snapshot.counters[key] += 1
        attempt = snapshot.counters[key]
        if route:
            response = route['responses'][min(attempt - 1, len(route['responses']) - 1)]
        record = {"sequence": len(self.history) + 1, "method": request.method,
                  "url": request.path_qs, "status": response.get('status', 200),
                  "state": snapshot.state, "generation": snapshot.generation, "attempt": attempt}
        self.history.append(record)
        await asyncio.sleep(response.get('delay_ms', 0) / 1000)
        headers = {"Content-Type": "text/html; charset=utf-8", **response.get('headers', {})}
        return web.Response(body=response.get('body', '').encode('utf-8'), status=record['status'], headers=headers)
