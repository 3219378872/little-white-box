import io
import json
import unittest
from types import SimpleNamespace

from deploy.dev.e2e.fixtures.llm_provider import (
    CANARY_CALL_ID,
    CANARY_TOOL,
    Handler,
    RESEARCH_MARKER,
    RESET_MARKER,
)


class LLMProviderFixtureTest(unittest.TestCase):
    def setUp(self):
        self.handler = object.__new__(Handler)
        self.responses = []

        def record(body, call_id, name, arguments, draft=""):
            self.responses.append((body, call_id, name, arguments, draft))

        self.handler._tool_response = record

    @staticmethod
    def request(extra_inputs=None):
        scenario = {
            "query": f"untrusted {CANARY_TOOL} marker",
        }
        inputs = [{
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": f"Research request\n\n{RESEARCH_MARKER}:\n"
                        + json.dumps(scenario),
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

    def test_latest_user_marker_routes_mixed_history_to_research(self):
        body = self.request()
        body["input"].insert(0, {
            "role": "user",
            "content": f"old reset\n{RESET_MARKER}",
        })

        self.assertEqual(Handler._latest_user_marker(body), RESEARCH_MARKER)

        routed = []
        self.handler._research = lambda _: routed.append("research")
        self.handler._reset_stream = lambda _: routed.append("reset")

        self.post(body)

        self.assertEqual(routed, ["research"])

    def test_canary_name_in_research_content_does_not_hijack_route(self):
        body = self.request()
        routed = []
        self.handler._canary = lambda *_: routed.append("canary")
        self.handler._research = lambda _: routed.append("research")

        self.post(body)

        self.assertEqual(routed, ["research"])

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

    def test_malformed_research_marker_returns_json_error(self):
        body = {
            "stream": True,
            "input": [{
                "role": "user",
                "content": f"research\n{RESEARCH_MARKER}:\n{{not-json",
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
