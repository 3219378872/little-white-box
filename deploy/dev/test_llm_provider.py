import json
import unittest

from deploy.dev.e2e.fixtures.llm_provider import Handler, WATCH_MARKER


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
                "post_id": 9007199254740993,
                "post_id_exact": "9007199254740993",
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


if __name__ == "__main__":
    unittest.main()
