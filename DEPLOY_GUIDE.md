# Sai Fuel Mart v2.1 — setup guide

See CHANGELOG.md for everything restored from the old app in 2.1.

## 0. Before anything: secure your database (do this today)

Your old project zip contained `.env` with the live Supabase password and service key.
If that file was ever in GitHub, treat both as stolen.

1. Supabase → Project Settings → Database → **Reset database password**. Copy the new connection string
   (Transaction pooler, port 6543).
2. Supabase → Project Settings → API → **roll the service_role key** (JWT keys / "Generate new").
3. In GitHub, delete `.env` and `sai_fuel_mart.db` from the repo. They are in `.gitignore` now.
   (Old commits still contain the old password, which is why step 1 matters.)

## 1. Put the new code on GitHub

1. In your repo, create a tag or branch of the old code first, e.g. branch `old-v1`, so you can go back anytime.
2. Replace the repo contents with this folder (`app.py`, `sfm/`, `templates/`, `static/`, `requirements.txt`,
   `render.yaml`, `.python-version`, `.gitignore`, `tests/`). Commit and push.

The new app uses its own database area (schema `pump`). Your old tables are never changed or deleted,
so going back to the old branch still works with all old data.

## 2. Render settings

Render → your service → **Environment**, set:

| Key | Value |
|---|---|
| `DATABASE_URL` | the NEW Supabase connection string from step 0 |
| `SECRET_KEY` | any long random text (40+ characters) |
| `SETUP_CODE` | a secret word you choose, used once to create the owner account |
| `TANK_SYNC_SECRET` | the same secret your ATG bookmarklet already sends (`saifuelmart-tank-4021` before; change both if you like) |
| `SUPABASE_URL` | `https://<project>.supabase.co` (for photo proofs) |
| `SUPABASE_SERVICE_KEY` | the NEW service_role key |
| `PYTHON_VERSION` | `3.12.8` |

Render → Settings: **Start command** `gunicorn app:app --workers 2 --threads 4 --timeout 120`,
**Health check path** `/healthz`. Deploy.

For photo proofs: Supabase → Storage → New bucket named `proofs`, set to **Public**.

## 3. First run

1. Open your site. It goes to **Create the owner account**. Enter the `SETUP_CODE`, your username and a strong password.
2. Settings → **Import old data** (shown when old tables exist). Download a backup from the old app first.
   Type `IMPORT`. It copies nozzles, rates, transporters with history, lube stock, staff, attendance,
   salary, tank dips, tanker receipts and photos, all in one go (the old activity log is left out).
3. Read the import report. For each transporter it shows the balance the old app displayed and what its
   entries actually add up to. Differences (from the old app's bugs) are added as one labelled
   "adjustment" so balances stay what you have been billing. Check any big difference with the party.
4. Settings: confirm fuel rates, link each nozzle to its tank, check tank capacities.
5. User accounts: create one login per person (operator / manager). Never share the owner login.

If you skip the import, add rates, tanks and nozzles in Settings; the first day you type each nozzle's
opening reading once.

## 4. Daily routine

1. Operator/manager opens **Daily closing** for today.
2. Meter readings: type closings and testing. Openings come from yesterday automatically.
3. Digital collections: PhonePe, card, HP Pay, etc. from the settlement reports.
4. **Credit transport** page (or the same form on Daily closing): one entry per slip — diesel litres and
   cash taken together. Each gets the day's SL No. Payments: "Receive amount" on the same page.
   Bills: Credit transport → Bills & daily ZIP (PDF, Excel, or a ZIP with one bill per day).
5. Lube sales and expenses.
6. Count cash with the note counter. The board shows expected cash, counted cash and short/excess.
7. **Submit day**. Owner checks and taps **Verify & lock**. A locked day cannot be changed by anyone,
   even directly in the database, until the owner unlocks it with a written reason.

## 5. Android app

* Install `SaiFuelMart-v2.0.0.apk` (allow "Install unknown apps" for your file manager or WhatsApp).
* First open asks for the server address. It is pre-filled with `https://sai-fuel-mart.onrender.com`;
  change it if your Render URL is different. Later: long-press the Back button, or use
  "Change server address" on the no-connection screen.
* Downloads (PDF bills, Excel) go to the phone's Downloads folder. Receipts print/save as PDF.
  Photo proofs can use the camera.
* Render's free plan sleeps after 15 minutes; the first open can take up to a minute. A paid Render plan
  ($7/month Starter) removes that wait.

### Building it yourself / Play Store (AAB)

Open the `android-project` folder in Android Studio, let Gradle sync, then
**Build → Generate Signed App Bundle / APK**, choose the keystore `saifuelmart-release.jks`; the
passwords are in `keystore.properties`.

**Keep `saifuelmart-release.jks` and its passwords safe and backed up in two places.** Every future
update must be signed with this same key, or phones will refuse to install the update.

## Updating from 2.0 to 2.1

Push the new code; Render redeploys. The database upgrades itself on start (new columns are only added,
nothing is removed). If you already ran "Import old data" on 2.0, old transport entries stay as they are
(fuel and cash as separate lines) — balances are unaffected. The Android app does not need reinstalling.

## Speed: put the server next to the database (most important)

Your Supabase database is in **Singapore** (`aws-1-ap-southeast-1`). The old `render.yaml` had no region, so
Render created the web service in its default region, **Oregon, USA**. Every database request then crosses the
Pacific (~170 ms each), and a page needs 6–24 of them.

| Page | Server in Oregon | Server in Singapore |
|---|---|---|
| Overview (dashboard) | ~4 s | ~0.1 s |
| Daily closing | ~3.6 s | ~0.1 s |
| Credit transport | ~1.7 s | ~0.03 s |
| Import a year of old data | ~30 s | ~1–2 s |

Render cannot change the region of an existing service, so make a new one (about 10 minutes):

1. Render → **New → Web Service** → pick the same GitHub repo → **Region: Singapore**.
   Build command `pip install -r requirements.txt`, start command
   `gunicorn app:app --workers 2 --threads 4 --timeout 120`, health check path `/healthz`.
2. Old service → **Environment** → copy every variable into the new service (DATABASE_URL, SECRET_KEY,
   SETUP_CODE, TANK_SYNC_SECRET, SUPABASE_URL, SUPABASE_SERVICE_KEY, PYTHON_VERSION).
   Use the **same SECRET_KEY** so nobody is signed out.
3. Deploy and open the new address. It uses the same database, so all data is already there; don't import again.
4. The new service gets a new address (e.g. `https://sai-fuel-mart-xxxx.onrender.com`). Update it in:
   - the Android app: long-press the Back button → Server address;
   - the **ATG bookmarklet** on the ATG computer: replace the old address in its `/api/tank-sync` link.
   (To keep one permanent address, add your own domain under Settings → Custom Domains.)
5. When everything works, delete the old Oregon service.

Also worth knowing: the **free** Render plan sleeps after 15 minutes idle (first open then takes 30–60 s) and gets
a small slice of CPU. For a business used all day, the **Starter** plan (about $7/month) keeps it awake and faster.

Built-in speed-ups in this version: about 60% fewer database trips per page, pages sent compressed (about 5×
smaller on mobile data), and the old-data import sends rows in batches.

## If the old-data import seems stuck or says it stopped

1. Open Settings → Import old data. If a red box **Stuck database sessions** appears, press
   **End stuck sessions**. These are earlier import attempts that never finished (for example from the old slow
   version) and still hold the import lock; ending them is safe because their work was never saved.
2. Type IMPORT and press **Import now** (background, with progress bar). If the import has to wait more than
   20 seconds for another session it stops by itself and tells you why — it never hangs silently any more.
3. If the background import still stops, use **Import while I wait** (runs inside the page; keep it open).
4. Render → your service → **Logs** shows each step as `IMPORT 35% Transport entries` and any error in full.
   Also check Render → Settings → Start command is
   `gunicorn app:app --workers 2 --threads 4 --timeout 120` (the old one was just `gunicorn app:app`).

## ATG automation (HP Smart Connect bookmarklet)

1. Render → Environment → `TANK_SYNC_SECRET` = exactly the same secret your bookmarklet already sends
   (it was in the old `.env` under the same name). If you change it, change it in the bookmarklet too.
2. Keep the same Render service/address. The bookmarklet posts to `https://<your-site>/api/tank-sync`; the
   address, the secret and the data format are unchanged, so the bookmarklet itself needs no edit.
3. Settings → Tanks: one active tank per fuel (MS, HSD). Each nozzle should point to its tank.
4. Click the bookmarklet on the ATG computer. On Tanks & tankers the cards should show "ATG · synced just now".
   If the bookmarklet shows "Invalid or missing secret", the secret on Render doesn't match.

## 6. Roles

| | Operator | Manager | Owner |
|---|---|---|---|
| Readings, cash, credit, payments, lube sales, expenses, attendance | ✓ | ✓ | ✓ |
| Change days older than the back-date window (Settings, default 2 days) | | | ✓ |
| Cancel an entry (with reason) | | ✓ | ✓ |
| Parties, lube products/purchases, staff, salary, reports | | ✓ | ✓ |
| Fuel rates, nozzles, tanks, users, lock/unlock days, audit log, backup | | | ✓ |

## 7. Backups

Owner → Reports → **Full backup (Excel)** every week. Supabase also keeps daily backups on paid plans.

## 8. Running the tests (optional, for a developer)

```
DATABASE_URL=postgresql://... SETUP_CODE=x python tests/test_flows.py
```
Uses an empty test database; it deletes the `pump` schema first. Never point it at the real database.
