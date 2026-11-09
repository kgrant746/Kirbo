"""Run with Python, then open http://127.0.0.1:8081 for isolated browser checks."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import mimetypes

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'web' / 'static'


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/':
            html = (STATIC / 'index.html').read_text(encoding='utf-8')
            html = html.replace('<script src="/static/app.js"', '<script src="/frontend-checks.js"></script><script src="/static/app.js"')
            data, mime = html.encode(), 'text/html; charset=utf-8'
        elif path == '/frontend-checks.js':
            data, mime = (ROOT / 'tests' / 'frontend_checks.js').read_bytes(), 'text/javascript'
        elif path.startswith('/static/'):
            file = (STATIC / path.removeprefix('/static/')).resolve()
            if not file.is_relative_to(STATIC) or not file.is_file():
                self.send_error(404)
                return
            data, mime = file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    print('Frontend checks: http://127.0.0.1:8081 (mock data; no bot credentials)', flush=True)
    HTTPServer(('127.0.0.1', 8081), Preview).serve_forever()
