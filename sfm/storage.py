"""Supabase Storage (private bucket) helpers.

Phones upload photos/videos DIRECTLY to Supabase through one-time signed upload links (Vercel limits a request
to 4.5 MB). Files are read back through short-lived signed links handed only to signed-in users. Large
downloads (backups, month ZIPs) are delivered the same way when they are bigger than Vercel allows.
"""
import os
import uuid
from io import BytesIO

import requests
from flask import redirect, send_file

from .util import UserError

VERCEL_LIMIT = 4_000_000   # stay under Vercel's 4.5 MB response cap


def config():
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    # the old app's bucket was "proof-files"; keep using it unless told otherwise
    bucket = os.environ.get("SUPABASE_BUCKET", "").strip() or "proof-files"
    return base, key, bucket


def ready():
    base, key, _ = config()
    return bool(base and key)


def key_kind(key):
    if key.startswith("eyJ"):
        return "legacy service_role (JWT)"
    if key.startswith("sb_secret_"):
        return "secret key (sb_secret_…)"
    if key.startswith("sb_publishable_") or key.startswith("sb_anon"):
        return "PUBLISHABLE key — wrong one"
    return "unknown"


def _h(key, extra=None):
    # Legacy JWT keys go in both headers. New sb_secret_ keys must go only in "apikey"
    # (Supabase rejects a non-JWT value in Authorization: Bearer).
    h = {"apikey": key}
    if key.startswith("eyJ"):
        h["Authorization"] = f"Bearer {key}"
    h.update(extra or {})
    return h


def _why(r):
    """Supabase's own error text, so the screen says what is really wrong."""
    try:
        j = r.json()
        msg = j.get("message") or j.get("error") or ""
    except Exception:
        msg = (r.text or "")[:120]
    low = msg.lower()
    if "bucket not found" in low or (r.status_code in (400, 404) and "bucket" in low):
        return f"the storage bucket does not exist ({msg}). Open Settings → Photo storage and press “Create bucket”."
    if any(w in low for w in ("jws", "jwt", "signature", "unauthorized", "invalid key", "apikey")) or r.status_code in (401, 403):
        return f"Supabase did not accept the key ({msg or r.status_code}). Check SUPABASE_SERVICE_KEY is the service_role / secret key."
    return f"{r.status_code} {msg}".strip()


def put(path, data, mime):
    base, key, bucket = config()
    r = requests.post(f"{base}/storage/v1/object/{bucket}/{path}", data=data, timeout=60,
                      headers=_h(key, {"Content-Type": mime, "x-upsert": "true"}))
    if r.status_code >= 300:
        raise UserError("Could not save the file: " + _why(r))


def signed_upload(path):
    """One-time link the phone can PUT a file to (valid ~2 hours, only for this exact path)."""
    base, key, bucket = config()
    r = requests.post(f"{base}/storage/v1/object/upload/sign/{bucket}/{path}", headers=_h(key), json={}, timeout=20)
    if r.status_code >= 300:
        raise UserError("Photo storage refused the upload: " + _why(r))
    rel = r.json().get("url") or r.json().get("signedURL")
    return f"{base}/storage/v1{rel}"


def signed_download(path, seconds=600, filename=None):
    base, key, bucket = config()
    body = {"expiresIn": seconds}
    r = requests.post(f"{base}/storage/v1/object/sign/{bucket}/{path}", headers=_h(key), json=body, timeout=20)
    if r.status_code >= 300:
        return None
    rel = r.json().get("signedURL") or r.json().get("signedUrl")
    url = f"{base}/storage/v1{rel}"
    if filename:
        from urllib.parse import quote
        url += ("&" if "?" in url else "?") + "download=" + quote(filename)
    return url


def get(path, first_bytes=None):
    """(bytes, content_type, total_size) — optionally only the first N bytes."""
    base, key, bucket = config()
    extra = {"Range": f"bytes=0-{first_bytes - 1}"} if first_bytes else None
    r = requests.get(f"{base}/storage/v1/object/{bucket}/{path}", headers=_h(key, extra), timeout=60)
    if r.status_code >= 300:
        return None, None, 0
    total = len(r.content)
    cr = r.headers.get("Content-Range", "")
    if "/" in cr:
        try:
            total = int(cr.rsplit("/", 1)[1])
        except ValueError:
            pass
    return r.content, r.headers.get("Content-Type", "application/octet-stream"), total


def deliver(buf, filename, mimetype):
    """send_file, or — when the file is too big for Vercel and storage is set up — a private 10-minute link."""
    data = buf.getvalue() if hasattr(buf, "getvalue") else buf
    if len(data) > VERCEL_LIMIT and os.environ.get("VERCEL") and ready():
        path = f"exports/{uuid.uuid4().hex}/{filename}"
        put(path, data, mimetype)
        url = signed_download(path, 600, filename)
        if url:
            return redirect(url)
    return send_file(BytesIO(data), mimetype=mimetype, as_attachment=True, download_name=filename)


# ------------------------------------------------------------------ owner's storage check
def bucket_info():
    base, key, bucket = config()
    r = requests.get(f"{base}/storage/v1/bucket/{bucket}", headers=_h(key), timeout=20)
    if r.status_code == 200:
        return r.json(), None
    return None, _why(r)


def create_bucket():
    base, key, bucket = config()
    r = requests.post(f"{base}/storage/v1/bucket", headers=_h(key), timeout=20,
                      json={"id": bucket, "name": bucket, "public": False, "file_size_limit": 52428800})
    return r.status_code < 300, (None if r.status_code < 300 else _why(r))


def make_private():
    base, key, bucket = config()
    r = requests.put(f"{base}/storage/v1/bucket/{bucket}", headers=_h(key), timeout=20,
                     json={"id": bucket, "name": bucket, "public": False})
    return r.status_code < 300, (None if r.status_code < 300 else _why(r))


def self_test():
    """Upload a tiny file through a one-time link, read it back through a signed link, delete it."""
    base, key, bucket = config()
    steps = []
    path = f"_check/{uuid.uuid4().hex}.txt"
    body = b"sai-fuel-mart storage check"
    try:
        up = signed_upload(path)
        steps.append(("Create one-time upload link", True, ""))
        r = requests.put(up, data=body, headers={"Content-Type": "text/plain"}, timeout=20)
        steps.append(("Upload a test file (like a phone does)", r.status_code < 300, "" if r.status_code < 300 else _why(r)))
        link = signed_download(path, 60)
        ok = bool(link) and requests.get(link, timeout=20).content == body
        steps.append(("Open it through a private signed link", ok, "" if ok else "could not read it back"))
    except UserError as e:
        steps.append(("Upload", False, str(e)))
    finally:
        try:
            requests.delete(f"{base}/storage/v1/object/{bucket}", headers=_h(key, {"Content-Type": "application/json"}),
                            json={"prefixes": [path]}, timeout=20)
        except Exception:
            pass
    return steps
