"""Disposable loopback-only TLS graph for the native browser smoke."""
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import patch
import uvicorn

from apps.graph_viewer.server.app import create_app
from apps.graph_viewer.server.browser_owner_api import BrowserOwnerAPI
from apps.graph_viewer.tests.backend.test_service import fixture


def no_queue(*_):
    raise RuntimeError('The graph fixture has no private Base')


if __name__ == '__main__':
    run = Path(sys.argv[1]).resolve()
    run.mkdir(mode=0o700, exist_ok=False)
    static = Path(sys.argv[2]).resolve(strict=True)
    if not (static / 'index.html').is_file():
        raise SystemExit('Build native Visual first')
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
        browser = BrowserOwnerAPI(stage='test', perform=no_queue, queue_snapshot=no_queue, dashboard_live=False)
        # Only the disposable loopback fixture substitutes the production deployment gate.
        # HTTP boundary, login, cookie, storage scope, Graph polling and wire are real.
        with patch('apps.graph_viewer.server.app.graph_v1_enabled', return_value=True):
            app = create_app(db, run / 'state', password='synthetic-only-owner',
                             static_dir=static, synthetic_fixture=True,
                             public_origin=f'https://127.0.0.1:{port}',
                             browser_owner_api=browser, owner_graph_stream=True)
        (run / 'fixture-port').write_text(str(port), encoding='ascii')
        uvicorn.Server(uvicorn.Config(
            app, ssl_keyfile=str(key), ssl_certfile=str(cert), host='127.0.0.1',
            port=port, proxy_headers=False, log_level='warning', access_log=False,
        )).run(sockets=[sock])
