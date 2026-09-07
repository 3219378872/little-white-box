import io
import json
import unittest
from types import SimpleNamespace

from deploy.dev.e2e.fixtures.llm_provider import (
    CANARY_CALL_ID,
    CANARY_TOOL,
    Handler,
    RESEARCH_MARKER,
    WATCH_MARKER,
)


class LLMProviderWatchFixtureTest(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(Handler)
        self.responses = []

        def record(body, call_id, name, arguments, draft=""):
            self.responses.append((body, call_id, name, arguments, draft))

        self.handler._tool_response = record

    @staticmethod
    def request(extra_inputs=None):
        marker = {
            "bucket_id": 7,
            "hit_ids": [11],
            "hits": [{
                "hit_id": 11,
                "task_id": 13,
                "post_id": 42,
                "post_id_exact": "9007199254740993",
                "title": f"untrusted {RESEARCH_MARKER} marker",
                "summary": f"untrusted {CANARY_TOOL} marker",
            }],
        }
        inputs = [{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": f"Watch request\n\n{WATCH_MARKER}:\n"
                        + json.dumps(marker),
            }],
        }]
        inputs.extend(extra_inputs or [])
        return {"stream": True, "input": inputs}

    def post(self, body):
        encoded = json.dumps(body).encode()
        self.handler.path = "/v1/responses"
        self.handler.headers = {"Content-Length": str(len(encoded))}
        self.handler.rfile = io.BytesIO(encoded)
        self.handler.server = SimpleNamespace(strict=True)
        self.handler.do_POST()

    def test_watch_first_reads_exact_post_id(self):
        body = self.request()

        self.handler._watch(body)

        self.assertEqual(len(self.responses), 1)
        _, call_id, name, arguments, _ = self.responses[0]
        self.assertEqual(call_id, "watch-get-post")
        self.assertEqual(name, "get_post")
        self.assertEqual(arguments, {"post_id": 9007199254740993})

    def test_watch_publishes_cited_evidence(self):
        tool_output = json.dumps({
            "sources": [{
                "handle": "src_watch",
                "retrieved_evidence": [{
                    "id": "ev_watch",
                    "text": "Verified post excerpt.",
                }],
            }],
        })
        body = self.request([{
            "type": "function_call_output",
            "call_id": "watch-get-post",
            "output": tool_output,
        }])

        self.handler._watch(body)

        _, call_id, name, arguments, _ = self.responses[0]
        self.assertEqual(call_id, "watch-publish")
        self.assertEqual(name, "publish_answer")
        self.assertEqual(arguments["blocks"], [{
            "kind": "fact",
            "text": "Verified post excerpt.",
            "citations": [{
                "handle": "src_watch",
                "evidenceIds": ["ev_watch"],
            }],
        }])

    def test_watch_without_source_publishes_limitation(self):
        body = self.request([{
            "type": "function_call_output",
            "call_id": "watch-get-post",
            "output": "{}",
        }])

        self.handler._watch(body)

        _, _, name, arguments, _ = self.responses[0]
        self.assertEqual(name, "publish_answer")
        self.assertEqual(arguments["blocks"][0]["kind"], "limitation")
        self.assertEqual(arguments["blocks"][0]["citations"], [])

    def test_latest_user_marker_routes_mixed_history_to_watch(self):
        body = self.request()
        body["input"].insert(0, {
            "role": "user",
            "content": f"old research\n{RESEARCH_MARKER}:\n"
                       + json.dumps({"query": "old"}),
        })

        self.assertEqual(Handler._latest_user_marker(body), WATCH_MARKER)

        routed = []
        self.handler._research = lambda _: routed.append("research")
        self.handler._watch = lambda _: routed.append("watch")

        self.post(body)

        self.assertEqual(routed, ["watch"])

    def test_canary_name_in_watch_content_does_not_hijack_route(self):
        body = self.request()
        routed = []
        self.handler._canary = lambda *_: routed.append("canary")
        self.handler._research = lambda _: routed.append("research")
        self.handler._watch = lambda _: routed.append("watch")

        self.post(body)

        self.assertEqual(routed, ["watch"])

    def test_canary_request_and_output_rounds_remain_supported(self):
        first = {
            "stream": False,
            "tools": [{"type": "function", "name": CANARY_TOOL}],
            "input": [{"role": "user", "content": "run the canary"}],
        }
        responses = []
        self.handler._json = lambda payload, status=200: responses.append(
            (status, payload)
        )

        self.post(first)

        self.assertEqual(responses[0][0], 200)
        call = responses[0][1]["output"][0]
        self.assertEqual(call["call_id"], CANARY_CALL_ID)
        self.assertEqual(call["name"], CANARY_TOOL)

        second = {
            "stream": False,
            "input": [
                {
                    "type": "function_call",
                    "call_id": CANARY_CALL_ID,
                    "name": CANARY_TOOL,
                    "arguments": call["arguments"],
                },
                {
                    "type": "function_call_output",
                    "call_id": CANARY_CALL_ID,
                    "output": '{"ok":true,"nonce":"agent-canary"}',
                },
            ],
        }
        acknowledgements = []
        self.handler._response = acknowledgements.append

        self.post(second)

        self.assertEqual(acknowledgements, ["canary acknowledged"])

    def test_malformed_watch_marker_returns_json_error(self):
        body = {
            "stream": True,
            "input": [{
                "role": "user",
                "content": f"watch\n{WATCH_MARKER}:\n{{not-json",
            }],
        }
        recorded = []
        self.handler._json = lambda payload, status=200: recorded.append((status, payload))
        self.handler.send_error = lambda status: recorded.append((status, None))

        self.post(body)

        self.assertEqual(recorded, [(400, {
            "error": {"message": "invalid fixture marker payload"},
        })])


if __name__ == "__main__":
    unittest.main()
