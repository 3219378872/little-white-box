#!/usr/bin/env python3
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CANARY_TOOL = "assistant_capability_canary"
RESET_MARKER = "E2E_STREAM_RESET_MARKER"
RESEARCH_MARKER = "E2E_RESEARCH_MARKER"
WATCH_MARKER = "UNTRUSTED_WATCH_HITS_JSON"


class FixtureState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset_attempts = 0

    def next_reset_attempt(self):
        with self.lock:
            self.reset_attempts += 1
            return self.reset_attempts


class Handler(BaseHTTPRequestHandler):
    server_version = "xbh-llm-fixture/1"

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True})
            return
        self.send_error(404)

    def do_POST(self):
        if self.path.rstrip("/") not in {"/v1/responses", "/responses", "/search"}:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return

        if self.path.rstrip("/") == "/search":
            if "WEB_UNAVAILABLE" in body.get("query", ""):
                self._json({"error": "fixture unavailable"}, status=503)
            else:
                self._json({"results": [{
                    "title": "Public maintenance reference",
                    "url": "https://example.com/xbh-research-reference",
                    "content": "The public reference recommends a weekly maintenance check.",
                }]})
            return

        serialized = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if CANARY_TOOL in serialized:
            self._canary(body, serialized)
            return
        if RESEARCH_MARKER in serialized:
            self._research(body)
            return
        if WATCH_MARKER in serialized:
            self._watch(body)
            return
        if self.server.strict and RESET_MARKER not in serialized:
            self._json({"error": {"message": "fixture only serves marked test requests"}}, status=503)
            return
        if body.get("stream") is True:
            if RESET_MARKER in serialized:
                self._reset_stream(self.server.state.next_reset_attempt())
            else:
                self._completed_stream("fixture response")
            return
        self._response("fixture response")

    def _canary(self, body, serialized):
        if "function_call_output" in serialized:
            self._response("canary acknowledged")
            return
        tools = body.get("tools") or []
        if not any(tool.get("name") == CANARY_TOOL for tool in tools):
            self.send_error(400)
            return
        self._json({
            "status": "completed",
            "model": "fixture-model",
            "output": [{
                "type": "function_call",
                "call_id": "fixture-canary-call",
                "name": CANARY_TOOL,
                "arguments": json.dumps({"nonce": "agent-canary"}),
            }],
            "usage": {"input_tokens": 8, "output_tokens": 2,
                      "total_tokens": 10},
        })

    def _reset_stream(self, attempt):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if attempt == 1:
            self._event("response.output_text.delta", {
                "type": "response.output_text.delta",
                "delta": "losing attempt",
            })
            return
        self._completed_stream_body("winning response")

    def _completed_stream(self, text):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._completed_stream_body(text)

    def _completed_stream_body(self, text):
        self._event("response.output_text.delta", {
            "type": "response.output_text.delta", "delta": text,
        })
        self._event("response.completed", {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "model": "fixture-model",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": text}],
                }],
                "usage": {"input_tokens": 12, "output_tokens": 3,
                          "total_tokens": 15},
            },
        })

    def _response(self, text):
        self._json({
            "status": "completed",
            "model": "fixture-model",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }],
            "usage": {"input_tokens": 8, "output_tokens": 2,
                      "total_tokens": 10},
        })

    def _research(self, body):
        scenario, outputs = self._marked_exchange(body, RESEARCH_MARKER)
        if "research-question" not in outputs:
            self._tool_response(body, "research-question", "ask_questions", {
                "questions": [{"id": "priority", "text": "更关注哪方面？",
                               "selection": "single", "options": [
                                   {"id": "cost", "label": "使用成本"},
                                   {"id": "experience", "label": "实际体验"},
                               ]}],
            })
            return
        if "research-search" not in outputs:
            self._tool_response(body, "research-search", "search_posts", {
                "keyword": scenario["query"], "page_size": 5,
            })
            return
        sources = self._sources(outputs.get("research-search"))
        if not sources and "research-web" not in outputs:
            self._tool_response(body, "research-web", "web_search", {
                "query": scenario["query"],
            })
            return
        if not sources:
            sources = self._sources(outputs.get("research-web"))
        if scenario.get("invalidCitation") and "research-invalid" not in outputs:
            self._tool_response(body, "research-invalid", "publish_answer", {
                "blocks": [{"kind": "fact", "text": "UNVALIDATED_DRAFT_MARKER",
                            "citations": [{"handle": "forged", "evidenceIds": ["forged"]}]}],
            }, draft="UNVALIDATED_DRAFT_MARKER")
            return
        if sources:
            source = sources[0]
            evidence = source["retrieved_evidence"][0]
            block = {"kind": "fact", "text": evidence["text"], "citations": [{
                "handle": source["handle"], "evidenceIds": [evidence["id"]],
            }]}
        else:
            block = {"kind": "limitation", "text": "社区资料不足，互联网检索暂时不可用，不能作出确定结论。", "citations": []}
        self._tool_response(body, "research-publish", "publish_answer", {"blocks": [block]})

    def _watch(self, body):
        watch, outputs = self._marked_exchange(body, WATCH_MARKER)
        hits = watch.get("hits") or []
        post_id = 0
        if hits:
            raw_post_id = hits[0].get("post_id_exact") or hits[0].get("post_id")
            try:
                post_id = int(raw_post_id)
            except (TypeError, ValueError):
                post_id = 0

        if "watch-get-post" not in outputs and post_id > 0:
            self._tool_response(body, "watch-get-post", "get_post", {
                "post_id": post_id,
            })
            return

        sources = self._sources(outputs.get("watch-get-post"))
        if sources and sources[0].get("retrieved_evidence"):
            source = sources[0]
            evidence = source["retrieved_evidence"][0]
            block = {
                "kind": "fact",
                "text": evidence["text"],
                "citations": [{
                    "handle": source["handle"],
                    "evidenceIds": [evidence["id"]],
                }],
            }
        else:
            block = {
                "kind": "limitation",
                "text": "Watch 命中的帖子当前无法回源，暂不提供未经核实的内容。",
                "citations": [],
            }
        self._tool_response(body, "watch-publish", "publish_answer", {
            "blocks": [block],
        })

    @staticmethod
    def _marked_exchange(body, marker):
        payload = {}
        outputs = {}
        marker_seen = False
        for item in body.get("input") or []:
            content = item.get("content", "")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content)
            if item.get("role") == "user" and marker in content:
                raw = content.split(marker, 1)[1].lstrip(" :\r\n\t")
                payload = json.loads(raw)
                outputs = {}
                marker_seen = True
                continue
            if marker_seen and item.get("type") == "function_call_output":
                outputs[item.get("call_id")] = item.get("output", "")
        return payload, outputs

    @staticmethod
    def _sources(raw):
        try:
            value = json.loads(raw or "{}")
            return value.get("sources", []) if isinstance(value, dict) else []
        except (ValueError, TypeError):
            return []

    def _tool_response(self, body, call_id, name, arguments, draft=""):
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        item = {"type": "function_call", "id": call_id, "call_id": call_id,
                "name": name, "arguments": encoded}
        response = {"status": "completed", "model": "fixture-model", "output": [item],
                    "usage": {"input_tokens": 20, "output_tokens": 40, "total_tokens": 60}}
        if not body.get("stream"):
            self._json(response)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if draft:
            self._event("response.output_text.delta", {"type": "response.output_text.delta", "delta": draft})
        self._event("response.output_item.added", {"type": "response.output_item.added",
                    "item": {**item, "arguments": ""}, "output_index": 0})
        middle = len(encoded) // 2
        for part in (encoded[:middle], encoded[middle:]):
            self._event("response.function_call_arguments.delta", {
                "type": "response.function_call_arguments.delta", "item_id": call_id,
                "output_index": 0, "delta": part})
        self._event("response.completed", {"type": "response.completed", "response": response})

    def _event(self, event, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {event}\ndata: {raw}\n\n".encode())
        self.wfile.flush()

    def _json(self, payload, status=200):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39091)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = FixtureState()
    server.strict = args.strict
    server.serve_forever()


if __name__ == "__main__":
    main()
