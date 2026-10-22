# Sai Fuel Mart 3.0 — setup guide (Vercel)

Version 3.0 runs on **Vercel**, in the **Singapore** region (`sin1`), next to your Supabase database
(`ap-southeast-1`). Every database request now takes about 1–3 ms instead of ~170 ms from Render's US server,
so pages open fast. The database, your data and all logins stay exactly as they are.

## 0. Security first (if not done yet)
Supabase → Project Settings → Database → **Reset database password**; API → roll the **service_role** key.
Never put `.env` in GitHub.

## 1. Put the code on GitHub
Replace your repo's files with the contents of `1-website-for-github`. Commit and push.

## 2. Create the Vercel project (about 10 minutes)
1. vercel.com → sign in with GitHub → **Add New… → Project** → import the `sai-fuel-mart` repo.
2. Framework preset: **Other**. Leave build settings empty (the included `vercel.json` does everything:
   Python function in `api/index.py`, region `sin1`, 60-second limit, fast static files).
3. **Environment Variables** (Settings → Environment Variables → Production):

| Name | Value |
|---|---|
| `DATABASE_URL` | Supabase **Transaction pooler** string (port **6543**) with your new password |
| `SECRET_KEY` | 40+ random characters (keep the same one forever, or everyone gets signed out) |
| `SETUP_CODE` | any secret word (only used to create the first owner on an empty database) |
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | the NEW service_role key |
| `TANK_SYNC_SECRET` | the same secret your ATG bookmarklet sends |

4. **Deploy**. Open the address Vercel gives (e.g. `https://sai-fuel-mart.vercel.app`) and sign in with your
   existing username and password.
5. Check that **Settings → Functions → Region** shows **Singapore (sin1)**.

## 3. Supabase Storage (photos)
Supabase → **Storage** → bucket **`proofs`** → set it **Private**. Photos and videos are uploaded straight from
the phone to this bucket through one-time links and shown only to signed-in users through 10-minute links.

## 4. Point everything at the new address
- **Android app**: install `SaiFuelMart-v3.0.0.apk` (installs over the old app). On first open it suggests
  `https://sai-fuel-mart.vercel.app`; if your Vercel address is different, type yours. Change it later by
  long-pressing the Back button.
- **ATG bookmarklet** on the ATG computer: replace the old Render address with the Vercel address in its
  `/api/tank-sync` link.
- When everything works, delete the old Render service.

## 5. First things to set as owner
1. **Permissions** (menu → Permissions): tick what Admin, Manager and Staff may do. You can always do everything;
   only you can open this page. Changes apply within seconds and are written to the audit log.
2. **Users**: one login per person. Roles: **Owner** (you), **Admin** (what you allow), **Manager**,
   **Staff (data entry)**. Admins can never create or change Owner/Admin accounts.
3. **Attendance → Rules for photo attendance**: how many previous days managers may mark (default 2) and
   between which hours.

## 5b. Letting a manager fix old days (Temporary access)
Menu → **Temporary access** → choose the person → dates (e.g. 1–30 Sep) → **Correct old / imported days** →
valid for 2 or 3 days → reason → **Give access**. Locked and old-system days in that range open for them and lock
again by themselves when the time ends. Press **Stop now** to end it early. Attendance catch-up works the same way.

## 6. Daily routine (what to teach the team)
Open **Home → Today's work** and follow the steps in order:
1. **Meter readings** — closing of each nozzle.
2. **Credit transport** — every slip: transporter, vehicle, slip, challan, HSD litres, cash taken.
3. **Credit check** (Daily closing, step 3) — write each transporter's total for the day. It must say
   **✓ Matched** against the slips; if not, fix the slips or the total.
4. **Digital collections & cash count**.
5. **Attendance** — on the phone, tap each staff member → live photo → present.
6. **Submit the day**. The owner (or whoever you allow) verifies and locks it.

## 7. Notes
- Downloads bigger than Vercel's limit (big backups, month ZIPs) are handed over through a private 10-minute link.
- Importing old data runs inside the page on Vercel (keep it open; usually under a minute).
- Free Vercel (Hobby) is fine to start. If you ever see "function timed out" on huge reports, Vercel Pro raises
  the limits.
