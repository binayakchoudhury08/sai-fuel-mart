"""Jobs on their own clock. Supabase (pg_cron + pg_net) calls /api/cron/tick every 15 minutes; each job runs
once per day after its time (job_runs makes that safe even if two calls overlap). Messages go to the WhatsApp
numbers in WA_OWNERS: owner gets everything, a manager gets the closing reminder."""
from datetime import timedelta

from . import db
from .util import now_ist, today


def _once(cur, job, key):
    cur.execute("INSERT INTO job_runs (job, run_key) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING job", (job, key))
    return cur.fetchone() is not None


def _people(cur):
    from .wa import owners
    out = []
    for phone, username in owners().items():
        cur.execute("SELECT id, username, full_name, role FROM users WHERE username=%s AND active", (username,))
        u = cur.fetchone()
        if u:
            out.append((phone, u))
    return out


def tick():
    """Run whatever is due. Returns what was done (for the log)."""
    from . import wa
    from .automation import compute, daily_job
    n = now_ist()
    t = n.date()
    hm = n.strftime("%H:%M")
    done = []
    with db.tx() as cur:
        people = _people(cur)
        owners_ = [p for p, u in people if u["role"] == "owner"]
        managers = [p for p, u in people if u["role"] in ("manager", "admin")]

        if hm >= "00:30" and _once(cur, "daily_job", t.isoformat()):
            done.append(("daily_job", None))
            run_daily = True
        else:
            run_daily = False

        if hm >= "07:00" and _once(cur, "morning_report", t.isoformat()):
            text = "🌅 *Good morning!*\n\n" + wa.report_text(cur, t - timedelta(days=1))
            done.append(("morning_report", [wa.notify(cur, p, text) for p in owners_]))

        if hm >= "12:05" and _once(cur, "attendance_close", t.isoformat()):
            cur.execute("""SELECT s.name FROM staff s LEFT JOIN attendance a ON a.staff_id=s.id AND a.work_date=%s
                           WHERE s.active AND a.staff_id IS NULL ORDER BY s.name""", (t,))
            absent = [r["name"] for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) AS n FROM attendance WHERE work_date=%s AND status IN ('P','H')", (t,))
            present = cur.fetchone()["n"]
            text = (f"🕛 *Attendance closed* — {present} present"
                    + (f", *{len(absent)} absent*: {', '.join(absent)}" if absent else ", nobody absent ✅"))
            done.append(("attendance_close", [wa.notify(cur, p, text) for p in owners_]))

        cur.execute("SELECT status FROM days WHERE business_date=%s", (t,))
        st = (cur.fetchone() or {}).get("status")
        if hm >= "21:00" and st not in ("submitted", "locked") and _once(cur, "closing_reminder", t.isoformat()):
            text = f"📋 *Daily closing for {t:%d %b} is not submitted yet.* Please finish the 7 steps and press Submit."
            done.append(("closing_reminder", [wa.notify(cur, p, text) for p in managers + owners_]))
        if hm >= "23:00" and st not in ("submitted", "locked") and _once(cur, "closing_escalate", t.isoformat()):
            text = f"🔴 *Daily closing for {t:%d %b} is still not submitted* (23:00)."
            done.append(("closing_escalate", [wa.notify(cur, p, text) for p in owners_]))

        # urgent alerts: each one at most once a day, new ones bundled into one message
        if hm >= "07:00":
            fresh = [a for a in compute(cur, lambda p: True)
                     if a["sev"] == "high" and _once(cur, "alert:" + a["title"][:120], t.isoformat())]
            if fresh:
                text = f"🔴 *Needs attention ({len(fresh)})*\n" + "\n".join(f"• {a['title']}\n   _{a['detail']}_" for a in fresh)
                done.append(("alerts", [wa.notify(cur, p, text) for p in owners_]))

        cur.execute("DELETE FROM wa_outbox WHERE created_at < NOW() - interval '3 days'")
        cur.execute("DELETE FROM wa_seen WHERE at < NOW() - interval '3 days'")
        cur.execute("DELETE FROM job_runs WHERE at < NOW() - interval '40 days'")
    if run_daily:
        daily_job()
    return done
