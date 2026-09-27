"""Fake OpenAI-compatible model server that records every request it receives.

Used as the upstream for tiers a caller must NOT reach: any hit is evidence of a
policy bypass. Hits are appended as JSON lines to the file given as argv[2].
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]


class H(BaseHTTPRequestHandler):
    def _record(self, body: bytes):
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "method": self.command, "path": self.path,
                                "body": body.decode(errors="replace")[:2000]}) + "\n")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        self._record(body)
        out = json.dumps({"id": "canary", "object": "chat.completion", "created": 0, "model": "canary",
                          "choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"role": "assistant", "content": "CANARY-TIER-REACHED"}}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

    def do_GET(self):
        self._record(b""); self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
