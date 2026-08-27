#!/usr/bin/env python3
"""Static server for the built Flutter web app (build/web).

Serves the release bundle on 127.0.0.1:<port> with:
- correct application/wasm MIME (CanvasKit/Skwasm streaming instantiation
  requires it),
- SPA fallback: unknown paths without a dot serve index.html so deep links
  like /post/<id> survive a hard refresh,
- no caching of index.html / flutter_bootstrap.js (hashed assets get long
  cache), keeping refreshes consistent across rebuilds.

Usage: serve_release.py <port> <build_dir>
"""
import functools
import http.server
import os
import sys

WASM_MIME = "application/wasm"
NO_CACHE_TYPES = (".html", ".js", ".json")


class ReleaseHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=kwargs.pop("directory"), **kwargs)

    def guess_type(self, path):
        if path.endswith(".wasm"):
            return WASM_MIME
        return super().guess_type(path)

    def end_headers(self):
        path = self.translate_path(self.path)
        rel = os.path.relpath(path, self.directory)
        top = rel.split(os.sep)[0] if os.sep in rel or "." in rel else rel
        request_path = self.path.split("?", 1)[0]
        if (
            request_path in {"", "/"}
            or rel.endswith(NO_CACHE_TYPES)
            or not os.path.exists(path)
        ):
            self.send_header("Cache-Control", "no-cache")
        elif top == "canvaskit":
            self.send_header("Cache-Control", "max-age=3600")
        else:
            self.send_header("Cache-Control", "max-age=3600")
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) and "/" in self.path and "." not in os.path.basename(self.path):
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, fmt, *args):  # keep logs terse
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: serve_release.py <port> <build_dir>")
    port, build_dir = int(sys.argv[1]), sys.argv[2]
    if not os.path.isdir(build_dir):
        sys.exit(f"missing build dir: {build_dir}")
    handler = functools.partial(ReleaseHandler, directory=build_dir)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    sys.stderr.write(f"serving {build_dir} on http://127.0.0.1:{port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
