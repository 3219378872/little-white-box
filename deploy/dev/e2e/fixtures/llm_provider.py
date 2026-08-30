#!/usr/bin/env python3
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


CANARY_TOOL = "assistant_capability_canary"
RESET_MARKER = "E2E_STREAM_RESET_MARKER"


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
        if self.path.rstrip("/") not in {"/v1/responses", "/responses"}:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return

        serialized = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        if CANARY_TOOL in serialized:
            self._canary(body, serialized)
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

    def _event(self, event, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {event}\ndata: {raw}\n\n".encode())
        self.wfile.flush()

    def _json(self, payload):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=39091)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.state = FixtureState()
    server.serve_forever()


if __name__ == "__main__":
    main()
