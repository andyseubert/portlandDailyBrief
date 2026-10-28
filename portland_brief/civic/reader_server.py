"""Loopback-only reader and explicit human report-review endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from ..config import load_config
from ..pipeline import run_lock
from .reports import review
from .store import CivicStore
from .ingestion import bootstrap
from .reader import build, catalog


def serve(root: Path, settings: dict, port: int = 8765):
    root = root.resolve()
    output = root / settings['output_path'] / 'civic-reader'
    output.mkdir(parents=True, exist_ok=True)
    database = root / settings['data_path'] / 'civic.sqlite3'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'PortlandBriefLocal/1'

        def log_message(self, fmt, *args):
            # Avoid writing report IDs, form notes, or request bodies to logs.
            return

        def _send(self, status, body, content_type):
            raw = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(raw)

        def _local_request(self):
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin', '')
            return host in {f'127.0.0.1:{port}', f'localhost:{port}'} and (not origin or origin in {f'http://127.0.0.1:{port}', f'http://localhost:{port}'})

        def do_GET(self):
            if not self._local_request():
                return self._send(403, 'Local access only', 'text/plain; charset=utf-8')
            path = urlsplit(self.path).path
            if path in {'/', '/index.html', '/api/catalog'}:
                store = CivicStore(database)
                try:
                    bootstrap(store, root)
                    if path == '/api/catalog':
                        return self._send(200, json.dumps(catalog(store, root, settings), ensure_ascii=False), 'application/json; charset=utf-8')
                    build(store, root, settings)
                finally:
                    store.close()
                return self._file('index.html', 'text/html; charset=utf-8')
            for prefix, directory in (('/development/', 'development-review'), ('/council-watch/', 'council-watch')):
                if path.startswith(prefix):
                    relative = path[len(prefix):] or 'index.html'
                    parts = Path(relative).parts
                    base = (root / settings['output_path'] / directory).resolve()
                    candidate = (base / relative).resolve()
                    if '..' in parts or not candidate.is_relative_to(base) or not candidate.is_file() or candidate.suffix.lower() not in {'.html', '.css', '.js', '.png', '.svg', '.json'}:
                        return self._send(404, 'Not found', 'text/plain; charset=utf-8')
                    mime = mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
                    if mime.startswith('text/') or mime in {'application/javascript', 'application/json'}:
                        mime += '; charset=utf-8'
                    return self._send(200, candidate.read_bytes(), mime)
            names = {'/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in names:
                return self._file(*names[path])
            return self._send(404, 'Not found', 'text/plain; charset=utf-8')

        def _file(self, name, content_type):
            file = output / name
            if not file.is_file():
                return self._send(404, 'Reader is not built. Run portland-civic reader.', 'text/plain; charset=utf-8')
            return self._send(200, file.read_bytes(), content_type)

        def do_POST(self):
            if not self._local_request() or urlsplit(self.path).path != '/api/review':
                return self._send(403, 'Local review endpoint only', 'text/plain; charset=utf-8')
            if self.headers.get_content_type() != 'application/json':
                return self._send(415, 'Expected JSON', 'text/plain; charset=utf-8')
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 16_384:
                return self._send(413, 'Invalid request size', 'text/plain; charset=utf-8')
            try:
                payload = json.loads(self.rfile.read(length))
                if payload.get('acknowledged') is not True:
                    raise ValueError('Confirm that you reviewed the report and its official sources.')
                action = payload.get('action')
                if action not in {'approve', 'hold'}:
                    raise ValueError('Choose approve or hold.')
                actor, note = str(payload.get('actor', '')).strip(), str(payload.get('note', '')).strip()
                seconds = float(payload.get('review_seconds'))
                if not actor or not note or seconds < 1 or seconds > 86_400:
                    raise ValueError('Enter your name, a review note, and the actual time you spent reviewing.')
                with run_lock(database.parent / 'reader.lock'):
                    store = CivicStore(database)
                    try:
                        bootstrap(store, root)
                        result = review(store, str(payload.get('report_id', '')), action, actor, note, seconds)
                        result['catalog'] = build(store, root, settings)
                    finally:
                        store.close()
                return self._send(200, json.dumps(result), 'application/json; charset=utf-8')
            except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
                return self._send(400, json.dumps({'error': str(exc)}), 'application/json; charset=utf-8')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f"Private Portland reading hub: http://127.0.0.1:{port}/")
    print('Keep this terminal process running while you read. Press Ctrl-C to stop.')
    try:
        server.serve_forever()
    finally:
        server.server_close()
