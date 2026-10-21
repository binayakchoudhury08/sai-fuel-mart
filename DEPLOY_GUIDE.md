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
   salary, tank dips, tanker receipts, photos and the old activity log, all in one go.
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
