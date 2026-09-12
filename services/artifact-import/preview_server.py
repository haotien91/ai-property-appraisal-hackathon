"""Local-only live frontend bridge. AWS credentials stay in this process."""
import argparse
import json
import re
import hashlib
import threading
from deliver_generated import deliver_generated, MAX_ZIP_BYTES
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

jobs = {}
jobs_lock = threading.Lock()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def reply(self, code, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def do_POST(self):
        host = self.headers.get('Host')
        if host not in (f'127.0.0.1:{args.port}', f'localhost:{args.port}') or self.headers.get('Origin') != 'http://' + host:
            self.send_error(403); return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != '/artifact-api/deliver' or self.headers.get('Content-Type') != 'application/zip':
            self.send_error(404); return
        try: size = int(self.headers.get('Content-Length','0'))
        except ValueError: self.send_error(400); return
        if not 0 < size <= MAX_ZIP_BYTES:
            self.reply(413, {'error':'ZIP 大小必須介於 1 byte 到 40 MB'}); return
        key = self.headers.get('Idempotency-Key','')
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,128}',key):
            self.send_error(400); return
        params = urllib.parse.parse_qs(parsed.query)
        if set(params) - {'case_name','group_name'}:
            self.send_error(400); return
        options = {k:v[0] for k,v in params.items()}
        archive = self.rfile.read(size)
        if len(archive) != size: self.send_error(400); return
        digest = hashlib.sha256(archive + json.dumps(options,sort_keys=True).encode()).hexdigest()
        with jobs_lock:
            old = jobs.get(key)
            if old and old['digest'] != digest:
                self.reply(409, {'error':'同一上傳識別碼不能使用不同檔案'}); return
            if old and old['status'] != 'failed':
                self.reply(202, {'job_id':key}); return
            if sum(j['status']=='processing' for j in jobs.values()) >= 2 or (not old and len(jobs)>=100):
                self.reply(429, {'error':'目前有其他匯入作業，請稍後再試'}); return
            jobs[key] = {'status':'processing','digest':digest}
        def run():
            try:
                result = deliver_generated(client,archive,key,**options)
                value = {'status':'completed','result':result,'digest':digest}
            except ValueError as exc:
                value = {'status':'failed','error':str(exc),'digest':digest}
            except Exception:
                value = {'status':'failed','error':'匯入未完成，請確認生成包、AWS 權限與連線後按重試。','digest':digest}
            with jobs_lock: jobs[key] = value
        threading.Thread(target=run,daemon=True).start()
        self.reply(202, {'job_id':key})

    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{args.port}', f'localhost:{args.port}'):
            self.send_error(403); return
        parsed = urllib.parse.urlsplit(self.path)
        if not parsed.path.startswith('/artifact-api/'):
            return super().do_GET()
        if parsed.path.startswith('/artifact-api/jobs/'):
            key = parsed.path.rsplit('/',1)[-1]
            with jobs_lock: job = jobs.get(key)
            self.reply(200 if job else 404, {k:v for k,v in job.items() if k!='digest'} if job else {'error':'找不到作業，請重新選擇同一檔案重試'})
            return
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
