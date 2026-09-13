"""Serve the existing simplified frontend with a local automatic-data backend.

The files under frontend/app are served byte-for-byte except for the api.js
response, which receives an in-memory runtime bridge so the existing upload
screen can call the local API without changing the frontend source tree.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import sys
import traceback
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "app"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.runtime_env import load_environment, frontend_config

# Handlers capture bucket/table/model settings at import time.
load_environment()

from backend.local_workflow_service import (  # noqa: E402
    LocalWorkflowError,
    LocalWorkflowService,
    extract_case_metadata,
)

MAX_BODY_BYTES = 120 * 1024 * 1024
# case-library.js shows its demo list unless MODE is production; the local
# backend has a real case list, so the served copy also accepts LOCAL_BACKEND.
CASE_LIBRARY_MODE_CHECK = "((window.APP_CONFIG || {}).MODE === 'production')"
CASE_LIBRARY_LOCAL_CHECK = ("((window.APP_CONFIG || {}).MODE === 'production'"
                            " || (window.APP_CONFIG || {}).LOCAL_BACKEND === true)")

API_BRIDGE = r'''

/* Runtime-only local backend bridge. The source file on disk is unchanged. */
;(function (global) {
  "use strict";
  async function localJson(path, options) {
    const response = await fetch(path, options);
    let data = null;
    try { data = await response.json(); } catch (error) { /* handled below */ }
    if (!response.ok) {
      throw data || { error: { code: "LOCAL_API_ERROR", message: "本機後端處理失敗" } };
    }
    return data;
  }
  async function postDocuments(path, payload, appraisalFiles, criteriaFile) {
    const body = new FormData();
    body.append("payload", JSON.stringify(payload || {}));
    (appraisalFiles || []).forEach(function (file) {
      body.append("appraisal", file, file.name);
    });
    if (criteriaFile) body.append("criteria", criteriaFile, criteriaFile.name);
    const response = await fetch(path, { method: "POST", body: body });
    let data = null;
    try { data = await response.json(); } catch (error) { /* handled below */ }
    if (!response.ok) {
      throw data || { error: { code: "LOCAL_API_ERROR", message: "本機後端處理失敗" } };
    }
    return data;
  }
  global.Api.extractCaseMetadata = function (appraisalFiles, criteriaFile) {
    return postDocuments("/api/local/extract-metadata", {}, appraisalFiles, criteriaFile);
  };
  global.Api.createCaseWithDocuments = function (payload, appraisalFiles, criteriaFile) {
    return postDocuments("/api/local/create-with-documents", payload, appraisalFiles, criteriaFile);
  };
  // Keep APP_CONFIG and the visible frontend exactly as shipped. Replace the
  // data methods at runtime so every existing page reads this local backend.
  global.Api.listCases = function () { return localJson("/api/cases"); };
  global.Api.getCase = function (caseNo) { return localJson("/api/cases/" + encodeURIComponent(caseNo)); };
  global.Api.getPdf = function (caseNo) { return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/pdf"); };
  global.Api.getResult = function (caseNo) { return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/result"); };
  global.Api.collectData = function (caseNo, payload) {
    return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/collect-data", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {})
    });
  };
  global.Api.analyze = function (caseNo) {
    return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/analyze", { method: "POST" });
  };
  global.Api.completeForm = function (caseNo) {
    return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/complete-form", { method: "POST" });
  };
  if (global.APP_CONFIG) global.APP_CONFIG.LOCAL_BACKEND = true;
  global.Api.getExportExcel = function (caseNo) {
    return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/export/excel");
  };
  global.Api.getExportBundle = function (caseNo) {
    return localJson("/api/cases/" + encodeURIComponent(caseNo) + "/export/bundle");
  };
  global.Api.getExportJson = async function (caseNo) {
    const response = await fetch("/api/cases/" + encodeURIComponent(caseNo) + "/export/json");
    const text = await response.text();
    if (!response.ok) {
      let data = null;
      try { data = JSON.parse(text); } catch (error) { /* handled below */ }
      throw data || { error: { code: "LOCAL_API_ERROR", message: "JSON 匯出失敗" } };
    }
    return { text: text, data: JSON.parse(text) };
  };
})(window);
'''


def _multipart(content_type: str, body: bytes):
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", "strict") + b"\r\n"
        b"MIME-Version: 1.0\r\n\r\n" + body
    )
    payload = {}
    appraisals = []
    criteria = b""
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        value = part.get_payload(decode=True) or b""
        if name == "payload":
            charset = part.get_content_charset() or "utf-8-sig"
            if charset.lower().replace("_", "-") == "utf-8":
                charset = "utf-8-sig"
            payload = json.loads(value.decode(charset))
        elif name == "appraisal" and filename:
            appraisals.append((Path(filename).name, value))
        elif name == "criteria":
            criteria = value
    return payload, appraisals, criteria


class AppHandler(SimpleHTTPRequestHandler):
    server_version = "LandAppraisalLocal/1.0"

    def __init__(self, *args, service=None, **kwargs):
        self.service = service
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    def log_message(self, format, *args):
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), format % args))
        sys.stdout.flush()

    def _json(self, data, status=HTTPStatus.OK):
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _bytes(self, content, content_type, status=HTTPStatus.OK, disposition=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status, code, message):
        self._json({"error": {"code": code, "message": message, "field_id": None, "details": {}}}, status)

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.netloc == self.headers.get("Host") and parsed.scheme in {"http", "https"}

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("無效的 Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise OverflowError("上傳內容超過 120 MB")
        return self.rfile.read(length)

    def _parts(self):
        content_type = self.headers.get("Content-Type", "")
        body = self._body()
        if content_type.startswith("multipart/form-data"):
            return _multipart(content_type, body)
        if content_type.startswith("application/json"):
            return json.loads(body.decode("utf-8-sig") or "{}"), [], b""
        raise ValueError("僅接受 JSON 或 multipart/form-data")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/":
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/index.html")
                self.end_headers()
                return
            if path == "/js/api.js":
                source = (FRONTEND / "js/api.js").read_text("utf-8")
                if frontend_config()["LOCAL_BACKEND"]:
                    source += API_BRIDGE
                self._bytes(source.encode("utf-8"), "text/javascript; charset=utf-8")
                return
            if path == "/js/config.js":
                source = (FRONTEND / "js/config.js").read_text("utf-8")
                source += "\nObject.assign(window.APP_CONFIG, " + json.dumps(frontend_config()) + ");\n"
                self._bytes(source.encode("utf-8"), "text/javascript; charset=utf-8")
                return
            if path == "/js/case-library.js":
                source = (FRONTEND / "js/case-library.js").read_text("utf-8")
                source = source.replace(CASE_LIBRARY_MODE_CHECK, CASE_LIBRARY_LOCAL_CHECK, 1)
                self._bytes(source.encode("utf-8"), "text/javascript; charset=utf-8")
                return
            if path == "/api/cases":
                self._json(self.service.list_cases())
                return
            if path.startswith("/local-files/"):
                name = Path(path).name
                file_path = self.service.file_path(name)
                if not file_path:
                    raise KeyError(name)
                content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
                self._bytes(file_path.read_bytes(), content_type, disposition=f'inline; filename="{name}"')
                return
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[:2] == ["api", "cases"]:
                case_no = parts[2]
                record = self.service.load(case_no)
                if not record:
                    raise KeyError(case_no)
                if len(parts) == 3:
                    self._json({**record.get("metadata", {}), "case_no": case_no, "status": record["status"]})
                elif parts[3:] == ["pdf"]:
                    self._json(self.service.pdf_response(case_no))
                elif parts[3:] == ["result"]:
                    self._json(self.service.result(case_no))
                elif parts[3:] == ["export", "json"]:
                    from export.json_exporter import export_bundle_to_json_bytes
                    self._bytes(export_bundle_to_json_bytes(self.service.export_bundle(case_no, record)),
                                "application/json; charset=utf-8")
                elif parts[3:] in (["export", "excel"], ["export", "bundle"]):
                    kind = parts[4]
                    name = f"{'excel' if kind == 'excel' else 'bundle'}-{self.service._key(case_no)}.zip"
                    if not self.service.file_path(name):
                        raise KeyError(name)
                    self._json({"case_no": case_no, f"{kind}_zip_url": f"/local-files/{name}"})
                else:
                    self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "找不到此 API")
                return
            if path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "找不到此 API")
                return
            super().do_GET()
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "CASE_NOT_FOUND", "找不到此案件")
        except Exception as exc:
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", str(exc))

    def do_POST(self):
        if not self._same_origin():
            self._error(HTTPStatus.FORBIDDEN, "ORIGIN_REJECTED", "拒絕跨站寫入")
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/local/extract-metadata":
                _payload, appraisals, criteria = self._parts()
                if not appraisals or not criteria:
                    raise ValueError("請同時上傳查估書與評價基準明細表")
                self._json(extract_case_metadata(appraisals, criteria))
                return
            if path in {"/api/local/create-with-documents", "/api/cases"}:
                payload, appraisals, criteria = self._parts()
                self._json(self.service.create(payload, appraisals, criteria), HTTPStatus.CREATED)
                return
            parts = [p for p in path.split("/") if p]
            if len(parts) == 4 and parts[:2] == ["api", "cases"]:
                case_no, action = parts[2], parts[3]
                self._body()
                if action == "collect-data":
                    self._json(self.service.collection(case_no))
                elif action == "analyze":
                    self._json(self.service.analysis(case_no))
                elif action == "complete-form":
                    self._json(self.service.form_completion(case_no))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "找不到此 API")
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "找不到此 API")
        except OverflowError as exc:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "UPLOAD_TOO_LARGE", str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "VALIDATION_ERROR", str(exc))
        except LocalWorkflowError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "UNSUPPORTED_CASE", str(exc))
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "CASE_NOT_FOUND", "找不到此案件")
        except Exception as exc:
            traceback.print_exc()
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", str(exc))


def main():
    frontend_config()  # Fail before starting if production configuration is incomplete.
    parser = argparse.ArgumentParser(description="地價智審本機應用程式")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--storage-dir")
    args = parser.parse_args()
    service = LocalWorkflowService(args.storage_dir)

    def handler(*handler_args, **handler_kwargs):
        return AppHandler(*handler_args, service=service, **handler_kwargs)

    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"地價智審已啟動：http://{args.host}:{args.port}/case-new.html", flush=True)
    print("按 Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
