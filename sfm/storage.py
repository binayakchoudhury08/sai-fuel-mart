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
    bucket = os.environ.get("SUPABASE_BUCKET", "proofs")
    return base, key, bucket


def ready():
    base, key, _ = config()
    return bool(base and key)


def _h(key, extra=None):
    h = {"Authorization": f"Bearer {key}", "apikey": key}
    h.update(extra or {})
    return h


def put(path, data, mime):
    base, key, bucket = config()
    r = requests.post(f"{base}/storage/v1/object/{bucket}/{path}", data=data, timeout=60,
                      headers=_h(key, {"Content-Type": mime, "x-upsert": "true"}))
    if r.status_code >= 300:
        raise UserError(f"Could not save the file ({r.status_code}). Check the '{bucket}' bucket in Supabase Storage.")


def signed_upload(path):
    """One-time link the phone can PUT a file to (valid ~2 hours, only for this exact path)."""
    base, key, bucket = config()
    r = requests.post(f"{base}/storage/v1/object/upload/sign/{bucket}/{path}", headers=_h(key), json={}, timeout=20)
    if r.status_code >= 300:
        raise UserError(f"Storage refused the upload ({r.status_code}). Check the '{bucket}' bucket exists.")
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
