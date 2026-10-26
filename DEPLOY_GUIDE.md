# Sai Fuel Mart 3.9 — complete setup guide

Follow the steps in order. A full setup takes about an hour, and you only do it once. For an update, do step 3.4.

| Folder / file in the package | What it is |
|---|---|
| `1-website-for-github/` | The website, to upload to GitHub. Vercel builds it from there. |
| `2-android-app/SaiFuelMart-v3.6.2.apk` | The ready Android app. |
| `3-android-studio-project/` | The app's source, to build it yourself (see `ANDROID_STUDIO_GUIDE.md`). |
| `SIGNING-KEY-PASSWORDS.txt` | Passwords for the app's signing key. **Keep private.** |

---

## 1. Supabase (database + photo storage)

1. Go to **supabase.com** → New project. Region: **Southeast Asia (Singapore)**. Save the database password.
2. **Database address:** Project → **Connect** → **Transaction pooler** (port **6543**). Copy the URI and put your
   database password in it. This is `DATABASE_URL`.
3. **Photo storage:** Storage → New bucket → name **`proof-files`** → keep it **Private**.
4. **Storage key:** Project Settings → **API Keys** → copy the **secret key** (`sb_secret_…`) or the legacy
   **service_role** key (`eyJ…`). This is `SUPABASE_SERVICE_KEY`. Never share it.
5. **Project URL:** `https://<your-project>.supabase.co`. This is `SUPABASE_URL`.

## 2. Google Gemini (reads handwritten bill pages, free)

1. Open **aistudio.google.com/apikey** and sign in with a Google account. The business Gmail is best.
2. Press **Create API key** → **Create key in new project** → copy the key (it starts with `AIza…`).
   This is `GEMINI_API_KEY`. No card is needed.
3. Your free limits are shown at **aistudio.google.com/rate-limit**. Google sets them and can change them.
   About 10–15 bill pages a day is light use.

What you should know about the free tier:
- **Models:** Google's pricing page (October 2026) lists the Flash models, e.g. **Gemini 3.8 Flash** and
  **Gemini 3.5 Flash-Lite**, as free of charge. The website uses the best one your key can reach, and switches to
  the next one by itself if a model is busy or its limit is reached.
- **Privacy:** on the free tier, Google may use what you send (the bill photos) to improve its products. If you
  want the photos kept private, or higher limits, link a billing account in AI Studio to move to the paid tier.
  At this volume the cost is very small, and nothing else changes in the website.

## 3. GitHub + Vercel (the website)

### 3.1 GitHub
1. Create a **private** repository, e.g. `sai-fuel-mart`.
2. Upload **everything inside** `1-website-for-github` (not the folder itself) to the `main` branch.

### 3.2 Vercel
1. Go to **vercel.com** → Add New → Project → import the repository.
2. Framework Preset: **Other**. Leave the build settings empty. The region (Singapore) and the daily job are
   already in `vercel.json`.
3. Before deploying, open **Environment Variables** and add these:

| Name | Value |
|---|---|
| `DATABASE_URL` | Supabase transaction pooler URI (step 1.2) |
| `SECRET_KEY` | any long random text, 40+ characters (use a password generator) |
| `SETUP_CODE` | a code you choose, used once to create the owner account |
| `SUPABASE_URL` | `https://<your-project>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | secret / service_role key (step 1.4) |
| `TANK_SYNC_SECRET` | any long random text (for the ATG tank-level link) |
| `CRON_SECRET` | any long random text (protects the daily automatic job) |
| `GEMINI_API_KEY` | the key from step 2 (turns on **Scan bill pages**) |
| `SUPABASE_BUCKET` | *optional*, only if your bucket is not `proof-files` |
| `GEMINI_MODEL` | *optional*, e.g. `gemini-3.8-flash`, only to force one model |

4. Press **Deploy**. The first start creates the database tables by itself (about 30 seconds).

### 3.3 Your address
Vercel shows the site address, e.g. `https://sai-fuel-mart.vercel.app`. A custom domain can be added in Vercel →
Domains.

### 3.4 Updating later (each new version)
Replace the files in GitHub with the new `1-website-for-github`, then commit. Vercel redeploys by itself, and the
database updates itself on the first open. **Changing an environment variable needs a Redeploy:** Deployments →
latest → ⋯ → Redeploy.

## 4. First sign-in (owner)

1. Open the site → **Create owner** → enter `SETUP_CODE`, your name, a username and a strong password.
2. **Settings → Station Info:** pump name, tagline, place, latitude/longitude (for the weather), tanks, nozzles.
3. **Settings → Fuel Prices:** today's MS / HSD / CNG prices, then press **Update** on each.
4. **Settings → Payment Methods:** PhonePe, card, UPI…
5. **Settings → Users:** create the Admin, Manager and staff logins. They change their password on first sign-in.
6. **Settings → Permissions:** tick what each role may do.
7. **Settings → Photos:** upload your station photo (dashboard banner) and the sign-in page photo.
8. **Settings → Automation:** alert levels (low stock, credit limit…).
9. **Settings → System:**
   - **Photo storage check:** press **Run full test**. It must say ✓ for bucket, key, upload and download.
   - **Page reader (AI) check:** press **Test page reader**. It must say **Working** and name a model.
10. **Credit transport parties:** add each transporter and their vehicle numbers (this helps the scan checks).
11. **Old data** (only if coming from the old app): Settings → System → **Import old data**.

## 5. Phones (Android app)

1. Send `SaiFuelMart-v3.6.2.apk` to each phone (WhatsApp / Drive) → tap → allow **Install unknown apps** → Install.
2. If your address is not `sai-fuel-mart.vercel.app`, **long-press Back** in the app → type your address → Save.
3. Allow **Camera**, **Location** and **Microphone** when asked.
4. **Attendance and photo proofs work only on phones.** The website refuses them from a computer.
5. The dashboard has a **Download Android app** button for new staff.
6. To build or sign the app yourself, see `ANDROID_STUDIO_GUIDE.md`.

## 6. ATG (automatic tank levels)

On the ATG computer, the **HP Smart Connect bookmarklet** sends tank readings to
`https://<your-address>/api/tank-sync` with your `TANK_SYNC_SECRET`. Edit the bookmarklet: put your Vercel address in
that link, and use the same secret as in Vercel (if you keep the old secret, set `TANK_SYNC_SECRET` to it). Readings
then appear in **Tank Level** with "ATG · synced".

## 7. Daily routine

| When | Who | What |
|---|---|---|
| 6:00–12:00 | Manager (or stand-in / backup) | **Attendance** by live photo on the phone. Not marked by 12:00 = Absent. |
| During the day | Manager / staff | **Credit Transport:** type slips, or **📄 Scan bill pages** (choose transporter → add all pages → Read all → check → Save all) |
| During the day | Staff | **Photo proofs:** dips, nozzle tests, tanker challans (phone) |
| Evening | Manager | **Daily Closing**, 7 steps → Submit |
| Any time | Owner | Dashboard, Monthly Summary, Reports, Alerts, lock closed days |

Manager on leave: **Settings → Temporary Access → "Take attendance (stand-in)"**, with dates (future allowed).
For sudden absence: Attendance → **Attendance rules, backup & stand-in** → choose the **backup person**.

## 8. How Scan bill pages works (and how well)

- **Reading:** each page is read by Gemini into a table. The **website itself** then applies your rules:
  - vehicle line + CASH line = one entry,
  - a vehicle line with only an amount = cash only,
  - Bill No. → Challan No., Credit Bill No. → Slip No.,
  - ditto marks copy the row above.
- **Checks:**
  - every row: qty × rate = amount (nearest rupee),
  - both page totals (litres and ₹),
  - vehicle numbers against the transporter's list,
  - the page rate against the saved price,
  - pages and rows already entered.
- **Second look:** if a page's totals don't add up, the page is **read a second time** with the doubtful lines
  pointed out, and the reading that matches the page totals is kept. The page header then shows "read twice".
- **Accuracy:** neat digits in the printed columns read very well. Overwriting, smudges and folded or dark photos
  read worse. Any misread shows as a **yellow row or a total mismatch**; it is never saved silently. Nothing is
  saved until **Save all**, and every entry can be edited or deleted later, on any date.
- **Good photos:** whole page flat on a table, good light, no shadow of the phone, one page per photo.

## 9. Keep safe

- `SIGNING-KEY-PASSWORDS.txt` + `saifuelmart-release.jks`: needed for every app update. Keep two copies.
- Vercel environment variables: keep a private note of them.
- Supabase: Database → Backups (daily backups on paid plans). You can also download Excel reports regularly.

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| Site shows an error after deploy | Check every variable in 3.2, then Redeploy. |
| Photos don't upload | Settings → System → **Photo storage check**: fix the key or bucket it points to. |
| "Page scanning is not set up" | Add `GEMINI_API_KEY` in Vercel → Redeploy. |
| "Google refused the key" | Copy the key again from aistudio.google.com/apikey (no spaces) → Redeploy. |
| "Free page reader has reached its limit" | Wait a minute (per-minute limit) or until the next day (daily limit). See aistudio.google.com/rate-limit. |
| A page reads badly | Retake it flat in good light, then **Discard** the old scan. |
| Attendance button missing | It is phone-only, today only, 6–12, once per person (owner can change anything). |
| App opens a blank page | Long-press Back in the app → enter the right address. |
