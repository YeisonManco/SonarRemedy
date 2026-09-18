"""Loopback credential broker: real token never enters scanner arguments/env."""
import base64
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import threading
from urllib.error import HTTPError

from sonar_client import Client, Blocked


class Gateway(AbstractContextManager):
    """Only same configured HTTPS upstream; no redirect headers forwarded."""
    def __init__(self, client):
        self.client = client
        self.credential = secrets.token_hex(32)
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.forward(False)

            def do_POST(self):
                self.forward(True)

            def forward(self, post):
                expected = 'Basic ' + base64.b64encode((gateway.credential + ':').encode()).decode()
                auth = self.headers.get('Authorization')
                # Scanner version discovery can be unauthenticated. Only the random path
                # grants access; the ephemeral token is an additional check when present.
                prefix = '/' + gateway.credential + '/'
                if (self.headers.get('Host') != gateway.host or not self.path.startswith(prefix)
                        or (auth is not None and auth not in (expected, 'Bearer ' + gateway.credential))):
                    self.send_error(403)
                    return
                try:
                    if self.headers.get('Transfer-Encoding'):
                        raise Blocked('chunked uploads unsupported')
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= length <= 64 * 1024 * 1024:
                        raise Blocked('upload budget exceeded')
                    self.connection.settimeout(gateway.client.timeout)
                    data = self.rfile.read(length) if post else None
                    if post and len(data) != length:
                        raise Blocked('incomplete upload')
                    raw, content_type = gateway.client.request(
                        self.path[len(prefix):], data, self.headers.get('Content-Type'),
                        limit=64 * 1024 * 1024)
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except HTTPError as error:
                    # Never return upstream headers/body (may contain credentials/redirects).
                    self.send_error(error.code if error.code in (400, 401, 403, 404) else 502)
                except Exception:
                    self.send_error(502)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.url = 'http://' + self.host + '/' + self.credential

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
