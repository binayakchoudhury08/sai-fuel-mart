"""Stand-in for Supabase Storage (private bucket): POST to save, GET (with service key) to read."""
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
STORE = "/tmp/mockstore"; KEY = "Bearer servicekey"
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _path(self):
        p = self.path.split("/storage/v1/object/", 1)[-1]
        return os.path.join(STORE, p.replace("..", ""))
    def do_POST(self):
        if self.headers.get("Authorization") != KEY: self.send_response(401); self.end_headers(); return
        data = self.rfile.read(int(self.headers["Content-Length"])); fp = self._path()
        os.makedirs(os.path.dirname(fp), exist_ok=True); open(fp, "wb").write(data)
        open(fp + ".type", "w").write(self.headers.get("Content-Type", ""))
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b'{"Key":"ok"}')
    def do_GET(self):
        fp = self._path()
        if self.headers.get("Authorization") != KEY or not os.path.exists(fp): self.send_response(404); self.end_headers(); return
        data = open(fp, "rb").read(); self.send_response(200)
        self.send_header("Content-Type", open(fp + ".type").read()); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
HTTPServer(("127.0.0.1", 5076), H).serve_forever()
