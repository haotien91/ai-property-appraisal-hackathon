"""Local-only live frontend bridge. AWS credentials stay in this process."""
import argparse
import json
import re
import urllib.parse
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from client import Client

ROOT = Path(__file__).resolve().parents[2] / '地價智審_AI_Offline_Candidate_v1/frontend/app'
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8002)
parser.add_argument('--profile', default='hackathon')
args = parser.parse_args()
client = Client('https://zyte6qrr2k.execute-api.us-west-2.amazonaws.com', args.profile)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{args.port}', f'localhost:{args.port}'):
            self.send_error(403); return
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith('/artifact-api/'):
            return super().do_GET()
        path = parsed.path.removeprefix('/artifact-api')
        allowed = (r'/v1/cases', r'/v1/cases/[0-9a-f-]+/groups',
                   r'/v1/groups/[0-9a-f-]+/runs', r'/v1/imports/[0-9a-f-]+',
                   r'/v1/imports/[0-9a-f-]+/documents/[0-9a-f-]+/access')
        if not any(re.fullmatch(p, path) for p in allowed):
            self.send_error(404); return
        try:
            result = client.call('GET', path + ('?' + parsed.query if parsed.query else ''))
            if path.endswith('/access'):
                self.send_response(302)
                self.send_header('Location', result['url'])
                self.send_header('Cache-Control', 'no-store')
                self.end_headers(); return
            raw = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
        except Exception:
            raw = json.dumps({'error': {'message': 'AWS 資料讀取失敗，請確認伺服器的憑證與權限。'}}).encode()
            self.send_response(502); self.send_header('Content-Type','application/json')
            self.end_headers(); self.wfile.write(raw)

    def log_message(self, fmt, *values):
        pass  # Do not print signed URLs.

print(f'Live preview: http://127.0.0.1:{args.port}/index.html?data=live', flush=True)
ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
