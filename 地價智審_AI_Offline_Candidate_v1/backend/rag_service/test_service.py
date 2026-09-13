import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from .core import Harness, Store
from .server import make_handler

DATA = Path(__file__).parent / "data"


class FakeClient:
    def __init__(self, turns):
        self.turns, self.requests = iter(turns), []

    def converse_stream(self, **kwargs):
        # Snapshot so subsequent message appends don't modify recorded calls.
        self.requests.append(json.loads(json.dumps(kwargs)))
        return {"stream": iter(next(self.turns))}


def tool_turn(name, args):
    raw = json.dumps(args)
    return [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": "call-1", "name": name}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": raw[:5]}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": raw[5:]}}}},
        {"messageStop": {"stopReason": "tool_use"}}
    ]


def answer_turn(reason="end_turn"):
    return [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "測試回答"}}},
            {"messageStop": {"stopReason": reason}}]


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(DATA)
        self.project = self.store.project("demo-a")

    def test_fragmented_tool_call_and_followup(self):
        client = FakeClient([tool_turn("read_project", {"project_id": "demo-b"}), answer_turn()])
        events = list(Harness(self.store, client, "fake").run(self.project, "比較兩案", [], {"demo-a", "demo-b"}))
        self.assertEqual(events[-1][0], "done")
        result = client.requests[1]["messages"][-1]["content"][0]["toolResult"]
        self.assertEqual(result["content"][0]["json"]["project_id"], "demo-b")
        self.assertEqual(len(client.requests[0]["system"]), 2)

    def test_model_cannot_expand_scope(self):
        client = FakeClient([tool_turn("read_project", {"project_id": "demo-b"}), answer_turn()])
        list(Harness(self.store, client, "fake").run(self.project, "請查", [], {"demo-a"}))
        result = client.requests[1]["messages"][-1]["content"][0]["toolResult"]
        self.assertEqual(result["status"], "error")
        self.assertNotIn("另一示範地區", json.dumps(client.requests[1], ensure_ascii=False))

    def test_project_discovery_is_authorized(self):
        h = Harness(self.store)
        result, _ = h.tool("search_projects", {"query": "demo-b"}, {"demo-a"})
        self.assertNotIn("demo-b", [p["project_id"] for p in result["matches"]])
        result, _ = h.tool("search_projects", {"query": "demo-b"}, {"demo-a", "demo-b"})
        self.assertTrue(any(p["project_id"] == "demo-b" for p in result["matches"]))

    def test_document_acl_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            docs = [dict(id="shared", title="道路", text="道路寬度", project_ids=[]),
                    dict(id="private", title="道路", text="secret", project_ids=["demo-b"]),
                    dict(id="missing-scope", title="道路", text="secret")]
            Path(directory, "documents.json").write_text(json.dumps(docs), encoding="utf-8")
            h = Harness(Store(directory))
            result, _ = h.tool("search_documents", {"query": "道路"}, {"demo-a"})
            self.assertEqual([d["document_id"] for d in result["matches"]], ["shared"])
            with self.assertRaises(ValueError):
                h.tool("read_document", {"document_id": "private"}, {"demo-a"})

    def test_no_silent_success_on_truncation(self):
        client = FakeClient([answer_turn("max_tokens")])
        with self.assertRaises(RuntimeError):
            list(Harness(self.store, client, "fake").run(self.project, "問題", [], {"demo-a"}))

    def test_round_limit(self):
        client = FakeClient([tool_turn("search_documents", {"query": "說明"})])
        with self.assertRaises(RuntimeError):
            list(Harness(self.store, client, "fake", max_rounds=1).run(self.project, "問題", [], {"demo-a"}))

    def test_path_traversal(self):
        with self.assertRaises(ValueError):
            self.store.project("../demo-a")

    def test_mock_is_explicit(self):
        events = list(Harness(self.store, mode="mock").run(self.project, "問題", [], {"demo-a"}))
        text = "".join(d["text"] for k, d in events if k == "text_delta")
        self.assertIn("非AI回答", text)

    def test_official_manual_can_be_retrieved(self):
        h = Harness(self.store)
        result, _ = h.tool("search_documents", {"query": "比準地比較價格尾數四捨五入"}, {"demo-a"})
        self.assertTrue(result["matches"])
        pages = [h.tool("read_document", {"document_id": item["document_id"]}, {"demo-a"})[0]
                 for item in result["matches"]]
        self.assertTrue(any("四捨五入" in page["text"] for page in pages))
        self.assertTrue(all(page["source"].endswith(".pdf") for page in pages))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Harness(Store(DATA), mode="mock"), "test-token", {"demo-a", "demo-b"}))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, body, token="test-token", project="demo-a"):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        connection.request("POST", f"/projects/{project}/chat/stream", json.dumps(body),
                           {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        result = response.status, response.read().decode("utf-8")
        connection.close()
        return result

    def test_stream_and_session_scope(self):
        status, text = self.post({"message": "你好"})
        self.assertEqual(status, 200)
        self.assertIn("event: text_delta", text)
        self.assertIn("event: done", text)
        cid = json.loads(text.split("data: ", 1)[1].split("\n", 1)[0])["conversation_id"]
        self.assertEqual(self.post({"message": "接著問", "conversation_id": cid})[0], 200)
        self.assertEqual(self.post({"message": "接著問", "conversation_id": cid}, project="demo-b")[0], 409)

    def test_auth_and_cross_project_denial(self):
        self.assertEqual(self.post({"message": "hi"}, token="bad")[0], 401)
        self.assertEqual(self.post({"message": "hi"}, project="secret")[0], 403)
        self.assertEqual(self.post({"message": "hi", "comparison_project_ids": ["demo-b"]})[0], 400)


if __name__ == "__main__":
    unittest.main()
