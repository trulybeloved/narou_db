import argparse
import json
import socket
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aiohttp import web

from .engine import Engine, validate_manifest
from .server import PREFIX, create_app, load_catalog


def main():
    parser = argparse.ArgumentParser(description='Deterministic mock website service')
    parser.add_argument('--url', default='http://127.0.0.1:8765', help='control API origin')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--directory', help='directory containing scenario JSON and assets')
    serve.add_argument('--scenario', default='narou')
    serve.add_argument('--ready-file', help='write bound origin JSON after startup (test harnesses)')
    for name in ('status', 'scenarios', 'requests', 'reset', 'export'):
        commands.add_parser(name)
    commands.add_parser('state').add_argument('name')
    load = commands.add_parser('load')
    load.add_argument('scenario', help='catalog ID, or --file path')
    load.add_argument('--file', action='store_true')
    commands.add_parser('validate').add_argument('file')
    args = parser.parse_args()
    try:
        if args.command == 'serve':
            catalog = load_catalog(args.directory) if args.directory else None
            app = create_app(catalog=catalog, initial=args.scenario, asset_root=args.directory)
            listener = socket.socket(socket.AF_INET6 if ':' in args.host else socket.AF_INET, socket.SOCK_STREAM)
            listener.bind((args.host, args.port))
            def ready(message):
                host, port = listener.getsockname()[:2]
                origin = f'http://[{host}]:{port}' if ':' in host else f'http://{host}:{port}'
                if args.ready_file:
                    Path(args.ready_file).write_text(json.dumps({'origin': origin}), encoding='utf-8')
                print(f'Mocksite ready: {origin}/__mock/', flush=True)
            web.run_app(app, sock=listener, print=ready)
            return
        if args.command == 'validate':
            path = Path(args.file)
            validate_manifest(json.loads(path.read_text(encoding='utf-8')), path.parent)
            print('Valid scenario')
            return
        command, data = args.command, None
        if command == 'state':
            data = {'state': args.name}
        elif command == 'reset':
            data = {}
        elif command == 'load':
            if args.file:
                # Convert file responses into self-contained bodies before upload.
                path = Path(args.scenario)
                manifest = json.loads(path.read_text(encoding='utf-8'))
                engine = Engine({'upload': manifest}, initial='upload', asset_root=path.parent)
                data = {'manifest': engine.export()}
            else:
                data = {'id': args.scenario}
        if command == 'export':
            command = 'manifest'
        request = Request(args.url.rstrip('/') + PREFIX + '/' + command,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=10) as response:
            print(json.dumps(json.load(response), ensure_ascii=True, indent=2))
    except (ValueError, KeyError, OSError, HTTPError, URLError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
