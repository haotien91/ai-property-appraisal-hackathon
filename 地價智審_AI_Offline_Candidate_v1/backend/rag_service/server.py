"""Local HTTP/SSE prototype. Run: python -m rag_service.server"""
import hmac
import json
import logging
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import Harness, Store


def make_handler(harness, token, allowed):
    sessions, lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # Do not log question text, tokens or project identifiers.

        def reply(self, code, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok", "mode": harness.mode})
            else:
                self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.close_connection = True
                return self.reply(401, {"error": "Unauthorized"})
            parts = self.path.strip("/").split("/")
            if len(parts) != 4 or parts[0] != "projects" or parts[2:] != ["chat", "stream"]:
                self.close_connection = True
                return self.reply(404, {"error": "Not found"})
            pid = parts[1]
            if pid not in allowed:
                self.close_connection = True
                return self.reply(403, {"error": "Project not authorized"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("Request body must be 1..65536 bytes")
                body = json.loads(self.rfile.read(size))
                question = body["message"]
                if not isinstance(question, str) or not question.strip() or len(question) > 12000:
                    raise ValueError("Invalid message")
                if set(body) - {"message", "conversation_id"}:
                    raise ValueError("Unsupported request field")
                # Authorization comes from the server, never request-supplied IDs.
                scope = set(allowed)
                project = harness.store.project(pid)
                cid = body.get("conversation_id") or str(uuid.uuid4())
                if not isinstance(cid, str) or len(cid) > 100:
                    raise ValueError("Invalid conversation ID")
                binding = (pid, tuple(sorted(scope)))
                with lock:
                    session = sessions.get(cid)
                    if session and session["binding"] != binding:
                        return self.reply(409, {"error": "Project or authorization changed; start a new conversation"})
                    if session and any(str(harness.store.project(p)["version"]) != v
                                       for p, v in session["versions"].items()):
                        return self.reply(409, {"error": "Previously read data changed; start a new conversation"})
                    if session and session["busy"]:
                        return self.reply(409, {"error": "Conversation already generating"})
                    if not session:
                        if len(sessions) >= 1000:
                            return self.reply(503, {"error": "Local session capacity reached; restart service"})
                        session = {"binding": binding, "history": [], "busy": False,
                                   "versions": {pid: str(project["version"])}}
                        sessions[cid] = session
                    session["busy"] = True
                    history = list(session["history"])
            except (ValueError, KeyError, TypeError, OSError):
                self.close_connection = True
                return self.reply(400, {"error": "Invalid request or project JSON"})

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            answer, completed = [], False

            def emit(kind, payload):
                raw = f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                self.wfile.write(raw.encode("utf-8"))
                self.wfile.flush()

            iterator = None
            try:
                emit("session", {"conversation_id": cid, "project_id": pid, "version": project["version"]})
                iterator = harness.run(project, question, history, scope)
                for kind, payload in iterator:
                    if kind == "citation" and "project_id" in payload:
                        session["versions"][payload["project_id"]] = str(payload["version"])
                    emit(kind, payload)
                    if kind == "text_delta":
                        answer.append(payload["text"])
                    if kind == "done":
                        completed = True
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.exception("RAG request failed")
                try:
                    emit("error", {"message": "回答未完成。請檢查模型權限、資料或伺服器紀錄後重試。", "retryable": True})
                except (BrokenPipeError, ConnectionResetError):
                    pass
            finally:
                if iterator is not None:
                    iterator.close()
                with lock:
                    if completed:
                        session["history"] += [{"role": "user", "content": [{"text": question}]},
                                               {"role": "assistant", "content": [{"text": "".join(answer)}]}]
                        session["history"] = session["history"][-20:]
                    session["busy"] = False

    return Handler


def main():
    from backend.runtime_env import load_environment
    load_environment()
    token = os.environ.get("RAG_API_TOKEN")
    if not token:
        raise SystemExit("Set RAG_API_TOKEN before starting (single-user prototype credential).")
    root = os.environ.get("RAG_DATA_DIR", str(Path(__file__).parent / "data"))
    mode = os.environ.get("RAG_MODE", "mock")
    if mode not in ("mock", "bedrock"):
        raise SystemExit("RAG_MODE must be mock or bedrock")
    model_id, client = os.environ.get("BEDROCK_MODEL_ID"), None
    if mode == "bedrock":
        if not model_id:
            raise SystemExit("Set BEDROCK_MODEL_ID to an enabled tool-use/streaming model or inference profile")
        import boto3
        from botocore.config import Config
        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2"),
                              config=Config(read_timeout=120, retries={"max_attempts": 1}))
    allowed = {p.strip() for p in os.environ.get("RAG_ALLOWED_PROJECTS", "demo-a,demo-b").split(",") if p.strip()}
    harness = Harness(Store(root), client, model_id, mode)
    address = ("127.0.0.1", int(os.environ.get("PORT", "8090")))
    server = ThreadingHTTPServer(address, make_handler(harness, token, allowed))
    server.daemon_threads = True
    print(f"RAG prototype http://{address[0]}:{address[1]} mode={mode}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
