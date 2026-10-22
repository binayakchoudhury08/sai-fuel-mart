"""Temporary access: the owner lets one user do chosen things for chosen dates until a set time.
Locked days in the range are opened when the access is given and locked again when it ends."""
import time

from flask import g

from . import db

PRESET_NOTES = {
    "old_days": "Daily closing, credit transport slips, payments, lube, tanks, salary, and cancel/correct entries",
    "att_photo": "Mark staff present with a live photo on these dates",
    "att_manual": "Mark present / absent / leave / off without a photo on these dates",
}
PRESETS = {
    "old_days": ("Correct old / imported days",
                 ["closing_enter", "transport_enter", "payments", "tanks", "lube_stock", "salary",
                  "cancel_entries", "rate_override", "bills", "accounts_view"]),
    "att_photo": ("Attendance with live photo", ["attendance_photo"]),
    "att_manual": ("Attendance without photo (mark / change any status)", ["attendance_manual"]),
}
_last_sweep = {"t": 0.0}


def close_ended(force=False):
    """Lock again the days opened by access that has expired or was revoked. Runs at most once a
    minute per server instance (cheap), and every time the owner opens the access page."""
    if not force and time.time() - _last_sweep["t"] < 60:
        return 0
    _last_sweep["t"] = time.time()
    closed = 0
    try:
        with db.tx() as cur:
            cur.execute("""SELECT * FROM access_grants WHERE closed_at IS NULL
                           AND (revoked_at IS NOT NULL OR valid_until <= NOW())
                           ORDER BY id FOR UPDATE SKIP LOCKED""")
            for gr in cur.fetchall():
                relock = []
                if gr["reopened_days"]:
                    # days still opened by another live grant stay open
                    cur.execute("""SELECT DISTINCT unnest(reopened_days) AS d FROM access_grants
                                   WHERE id <> %s AND closed_at IS NULL AND revoked_at IS NULL AND valid_until > NOW()""",
                                (gr["id"],))
                    keep = {r["d"] for r in cur.fetchall()}
                    days = [d for d in gr["reopened_days"] if d not in keep]
                    if days:
                        cur.execute("""UPDATE days SET status='locked', locked_by='auto (temporary access ended)',
                                       locked_at=NOW() WHERE business_date = ANY(%s) AND status <> 'locked'
                                       RETURNING business_date""", (days,))
                        relock = [r["business_date"] for r in cur.fetchall()]
                cur.execute("UPDATE access_grants SET closed_at=NOW() WHERE id=%s", (gr["id"],))
                g_user = g.get("user")
                g.user = {"username": "system", "role": "system"}
                from .security import audit
                audit(cur, "lock", "access_grant", gr["id"],
                      f"Temporary access #{gr['id']} ended ({'revoked' if gr['revoked_at'] else 'expired'}); "
                      f"{len(relock)} day(s) locked again")
                g.user = g_user
                closed += 1
    except Exception:
        pass
    return closed
