"""Stand-in for Supabase Storage (private bucket): signed upload links, signed download links, range reads."""
import json, os, secrets
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
STORE = "/tmp/mockstore"; TOKENS = {}; BUCKETS = set(os.environ.get("MOCK_BUCKETS", "").split(",")) - {""}
JWT = "eyJservicekey"; SECRET = "sb_secret_abc123"
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET,PUT,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type,x-upsert,authorization")
    def reply(self, code, obj=None, data=None, ctype="application/json", extra=None):
        body = data if data is not None else json.dumps(obj or {}).encode()
        self.send_response(code); self.cors(); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self): self.reply(204, data=b"")
    def fp(self, rel): return os.path.join(STORE, rel.replace("..", ""))
    def authed(self):
        a, k = self.headers.get("Authorization"), self.headers.get("apikey")
        if a and a.startswith("Bearer sb_"):
            self.reply(400, {"statusCode": "403", "error": "Unauthorized", "message": "Invalid Compact JWS"}); return False
        if a == f"Bearer {JWT}" or k in (JWT, SECRET):
            return True
        self.reply(400, {"statusCode": "403", "error": "Unauthorized", "message": "invalid signature"}); return False
    def nobucket(self, rel):
        b = rel.split("/", 1)[0]
        if b not in BUCKETS:
            self.reply(400, {"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"}); return True
        return False
    def do_POST(self):
        u = urlparse(self.path); p = u.path.split("/storage/v1", 1)[-1]
        ln = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(ln)
        if not self.authed(): return
        if p == "/bucket":
            b = json.loads(body); BUCKETS.add(b["id"]); return self.reply(200, {"name": b["id"]})
        if p.startswith("/object/upload/sign/"):
            rel = p[len("/object/upload/sign/"):]
            if self.nobucket(rel): return
            t = secrets.token_hex(8); TOKENS[t] = ("up", rel)
            return self.reply(200, {"url": f"/object/upload/sign/{rel}?token={t}"})
        if p.startswith("/object/sign/"):
            rel = p[len("/object/sign/"):]; t = secrets.token_hex(8); TOKENS[t] = ("down", rel)
            return self.reply(200, {"signedURL": f"/object/sign/{rel}?token={t}"})
        if p.startswith("/object/"):
            rel = p[len("/object/"):]
            if self.nobucket(rel): return
            f = self.fp(rel); os.makedirs(os.path.dirname(f), exist_ok=True)
            open(f, "wb").write(body); open(f + ".type", "w").write(self.headers.get("Content-Type", "")); return self.reply(200, {"Key": rel})
        self.reply(404, {"error": "?"})
    def do_DELETE(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0))); self.reply(200, [])
    def do_PUT(self):
        if self.path.split("/storage/v1", 1)[-1].startswith("/bucket/"):
            self.rfile.read(int(self.headers.get("Content-Length", 0))); return self.reply(200, {"message": "ok"})
        u = urlparse(self.path); p = u.path.split("/storage/v1", 1)[-1]; t = parse_qs(u.query).get("token", [""])[0]
        rel = p[len("/object/upload/sign/"):]
        if TOKENS.get(t) != ("up", rel): return self.reply(403, {"error": "bad token"})
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))); f = self.fp(rel)
        os.makedirs(os.path.dirname(f), exist_ok=True); open(f, "wb").write(body); open(f + ".type", "w").write(self.headers.get("Content-Type", ""))
        del TOKENS[t]; self.reply(200, {"Key": rel})
    def do_GET(self):
        u = urlparse(self.path); p = u.path.split("/storage/v1", 1)[-1]
        if p.startswith("/object/sign/"):
            rel = p[len("/object/sign/"):]; t = parse_qs(u.query).get("token", [""])[0]
            if TOKENS.get(t) != ("down", rel): return self.reply(403, {"error": "bad token"})
        elif p.startswith("/bucket/"):
            if not self.authed(): return
            b = p[len("/bucket/"):]
            return self.reply(200, {"id": b, "public": False}) if b in BUCKETS else self.reply(400, {"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"})
        elif p.startswith("/object/"):
            if not self.authed(): return
            rel = p[len("/object/"):]
        else:
            return self.reply(401, {"error": "unauthorized"})
        f = self.fp(rel)
        if not os.path.exists(f): return self.reply(404, {"error": "not found"})
        data = open(f, "rb").read(); typ = open(f + ".type").read(); rng = self.headers.get("Range")
        if rng and rng.startswith("bytes=0-"):
            end = min(int(rng.split("-")[1]), len(data) - 1)
            return self.reply(206, data=data[:end + 1], ctype=typ, extra={"Content-Range": f"bytes 0-{end}/{len(data)}"})
        self.reply(200, data=data, ctype=typ)
ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("MOCK_PORT", "5076"))), H).serve_forever()
