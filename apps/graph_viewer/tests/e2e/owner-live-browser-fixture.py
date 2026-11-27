"""Disposable loopback-only TLS graph with native or exact packed Visual."""
from contextlib import nullcontext
from hashlib import sha256
import json
import re
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace
import uvicorn
from starlette.staticfiles import StaticFiles

from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.tests.backend.test_service import fixture


def no_write(*_):
    raise RuntimeError('The graph fixture has no write control')


def fixture_queue(*, limit, offset):
    return {
        'stage': 'test', 'release_version': 'fixture-base',
        'queues': [{'id': lane, 'status': 'active', 'total': 0, 'items': []}
                   for lane in ('interactive', 'source', 'maintenance')],
    }


def verify_packed(static):
    manifest = json.loads((static / 'manifest.json').read_bytes())
    pattern = re.compile(r'assets/(?:index|graph\.worker)-[A-Za-z0-9_-]+\.(?:js|css)')
    if len(manifest) != 4 or 'index.html' not in manifest:
        raise ValueError('Packed Visual manifest has wrong member count')
    for name, digest in manifest.items():
        if (name != 'index.html' and pattern.fullmatch(name) is None
                or sha256((static / name).read_bytes()).hexdigest() != digest):
            raise ValueError('Packed Visual member differs from archive manifest')


def canonical_preview_fixture(run, static, cert, key):
    # Separate disposable canonical DB; old SSE tests keep their legacy fixture.
    import asyncio
    from prototypes.assertion_memory.storage import MemoryStore
    from prototypes.assertion_memory.thought_runtime import initialize_empty
    from prototypes.assertion_memory.thought_store import ThoughtStore
    from prototypes.assertion_memory.thought_graph import export_graph

    db = run / 'preview.sqlite3'
    with MemoryStore(db) as store:
        initialize_empty(store)
        thoughts = ThoughtStore(store)
        thoughts.publish('Fictional node preview')
        node = export_graph(thoughts)['nodes'][0]['id']
    (run / 'preview-node').write_text(node, encoding='ascii')
    (run / 'preview-db-sha').write_text(sha256(db.read_bytes()).hexdigest(), encoding='ascii')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    async def watch(*_):
        await asyncio.Event().wait()
    dashboard = SimpleNamespace(stage='test', db=db, activation=lambda: ('fixture', 'generation'),
                                release_lease=nullcontext, watch=watch)
    browser = BrowserOwnerAPI(stage='test', perform=no_write, queue_snapshot=fixture_queue, dashboard_live=False)
    app = create_app(db, run / 'preview-state', password='synthetic-only-owner',
                     synthetic_fixture=True, public_origin=f'https://127.0.0.1:{port}',
                     browser_owner_api=browser, queue_dashboard=dashboard)
    frontend = StaticFiles(directory=static, html=True)
    async def ingress(scope, receive, send):
        target = app if scope['type'] != 'http' or scope['path'].startswith('/api/') else frontend
        await target(scope, receive, send)
    server = uvicorn.Server(uvicorn.Config(ingress, ssl_keyfile=str(key), ssl_certfile=str(cert),
                                           host='127.0.0.1', port=port, proxy_headers=False, access_log=False))
    thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    (run / 'preview-port').write_text(str(port), encoding='ascii')
    return server, thread, sock



if __name__ == '__main__':
    run = Path(sys.argv[1]).resolve()
    run.mkdir(mode=0o700, exist_ok=False)
    static = Path(sys.argv[2]).resolve(strict=True)
    variant = sys.argv[3] if len(sys.argv) > 3 else 'native'
    if variant not in ('packed', 'native') or not (static / 'index.html').is_file():
        raise SystemExit('Build the selected native or packed Visual first')
    if variant == 'packed':
        verify_packed(static)
    cert, key, db = (run / n for n in ('cert', 'key', 'fixture.sqlite3'))
    fixture(db)
    subprocess.run([
        'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
        '-keyout', str(key), '-out', str(cert)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen(32)
        port = sock.getsockname()[1]
        browser = BrowserOwnerAPI(stage='test', perform=no_write, queue_snapshot=fixture_queue, dashboard_live=False)
        app = create_app(db, run / 'state', password='synthetic-only-owner',
                         synthetic_fixture=True,
                         public_origin=f'https://127.0.0.1:{port}',
                         browser_owner_api=browser)
        frontend = StaticFiles(directory=static, html=True)

        async def fixture_entrypoint(scope, receive, send):
            # Visual and Graph remain separate applications, sharing only this
            # fixture's loopback origin. No API path can enter the static app.
            if scope['type'] != 'http' or scope['path'].startswith('/api/'):
                await app(scope, receive, send)
            elif variant == 'packed' and scope['path'].startswith('/visual/'):
                # Relative archive assets must resolve exactly as on /visual/.
                path = scope['path'][len('/visual'):]
                await frontend({**scope, 'path': path, 'raw_path': path.encode('ascii')}, receive, send)
            elif variant == 'packed':
                await app(scope, receive, send)
            else:
                await frontend(scope, receive, send)

        preview = canonical_preview_fixture(run, static, cert, key) if variant == 'native' else None
        (run / 'fixture-port').write_text(str(port), encoding='ascii')
        try:
            uvicorn.Server(uvicorn.Config(
                fixture_entrypoint, ssl_keyfile=str(key), ssl_certfile=str(cert), host='127.0.0.1',
                port=port, proxy_headers=False, log_level='warning', access_log=False,
            )).run(sockets=[sock])
        finally:
            if preview is not None:
                server, thread, preview_socket = preview
                server.should_exit = True
                thread.join(timeout=3)
                preview_socket.close()
