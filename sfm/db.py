"""Postgres access. Every query runs inside `tx()` so a request either
commits fully or not at all, and connections are always returned."""
import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool = None
_pool_lock = threading.Lock()


def _dsn():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it in Render → Environment.")
    return url


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                kwargs = {"cursor_factory": psycopg2.extras.RealDictCursor,
                          "connect_timeout": 15,
                          "keepalives": 1, "keepalives_idle": 30}
                if "sslmode" not in _dsn() and "localhost" not in _dsn() and "/tmp" not in _dsn():
                    kwargs["sslmode"] = "require"
                _pool = psycopg2.pool.ThreadedConnectionPool(1, 6, _dsn(), **kwargs)
    return _pool


def _checkout():
    pool = _get_pool()
    conn = pool.getconn()
    # a pooled connection can die while idle (Supabase/pgbouncer restarts);
    # test it cheaply and replace it once if needed
    try:
        if conn.closed:
            raise psycopg2.InterfaceError("closed")
        with conn.cursor() as c:
            c.execute("SELECT 1")
        conn.rollback()
    except Exception:
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()
    return conn


@contextmanager
def tx():
    """Yield a cursor in one transaction. Commit on success, roll back on error."""
    conn = _checkout()
    broken = False
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL search_path TO pump, public")
        cur.execute("SET LOCAL TIME ZONE 'Asia/Kolkata'")
        yield cur
        conn.commit()
    except psycopg2.OperationalError:
        broken = True
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        try:
            _get_pool().putconn(conn, close=broken or conn.closed)
        except Exception:
            pass


def query(sql, params=None):
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(sql, params=None):
    with tx() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def ensure_schema():
    """Create/upgrade the pump schema. Safe to run on every start; an
    advisory lock stops two gunicorn workers doing it at the same time."""
    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(path, encoding="utf-8") as f:
        ddl = f.read()
    conn = psycopg2.connect(_dsn(), connect_timeout=20,
                            **({} if ("sslmode" in _dsn() or "/tmp" in _dsn() or "localhost" in _dsn())
                               else {"sslmode": "require"}))
    try:
        with conn.cursor() as cur:
            # transaction-scoped lock: released by the commit itself, so it is
            # safe behind Supabase's transaction-mode pooler (port 6543)
            cur.execute("SELECT pg_advisory_xact_lock(842211)")
            cur.execute("SET LOCAL client_min_messages TO warning")
            cur.execute(ddl)
            conn.commit()
    finally:
        conn.close()
