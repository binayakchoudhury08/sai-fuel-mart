# Tally — Excel in the website, WhatsApp, and the Tally Cloud robot

## Stage 1 — your Excel lives in the website (owner only)

**One time**
1. Website → **Settings → Tally Excel** → **Upload SFM_Daily_Readings.xlsx** (your latest one).
2. Choose a date that has Daily Closing data → the page lists **Tally names needed** (nozzle → pump meter,
   transporter → ledger, expense → ledger, payment mode → ‘Received In’ ledger, lube product → item).
   A guess is pre-selected only when it is clearly the same name — check each and press **Save**. Asked only once.

**Every day**
1. **➕ Add <date> from Daily Closing** → fills METER READING (closing readings), HSD SALES (credit parties),
   LUBE SALES, PAYMENT (expenses), RECEIPT (payments received). Only input cells of the prepared rows are written;
   formulas, other rows, dropdown lists and every other part of the file stay exactly as they were. No row is deleted.
   Dates are written like your recent rows (1–12 as Excel date mm-dd-yy, 13–31 as text 15/10/2026).
2. **Your adjustment** (MS / CNG / Phone Pe / Paytm / DTPLUS / cash) — in each sheet: change an amount and **Save**,
   or **Add row** (e.g. MS SALES · Phone Pe · 188153).
3. **Daily check** — every total and the **final cash balance**. When it equals the cash you counted, the day is right.
4. **Convert to Tally XML** (from – to) → your own SFM Converter Studio V16 runs inside the website: same checks
   (missing ledger / item / meter, per-date reconciliation) — any error blocks the XML and is listed.
5. **⬇ Download current workbook** any time. **Versions**: every change is kept; **Restore** any earlier one.

## Every day on WhatsApp (3 steps)
1. **excel add 2/11** → YES → the day is adjusted automatically (your rule) and the photo of all sheets comes.
2. Check the ledgers. To correct, write it plainly: **change hsd sales from dtplus to cash** · **change ms sales
   phonepe to paytm** · **change receipt dtplus to cash** → YES → a new photo.
3. All correct → **upload to tally** (or **xml 2/11** for the file).

## Stage 2 — the same on WhatsApp (owner only)

| Send | You get |
|---|---|
| **excel 1/11** | a table photo: every sheet of that date + daily check |
| **excel add 2/11** | adds the day from Daily Closing (asks YES) |
| **excel hsd 1/11 phone pe 1500 L** | litres instead of amount (amount = litres × rate) |
| **excel adjust 1/11** | tap-menus: sheet → name (your most-used names, or 🔎 search your LEDGER / ITEM MASTER) → type amount → YES |
| **excel hsd 1/11 phone pe 62084** · **excel ms 1/11 phone pe 188153** · **excel cng 1/11 cash 11634** · **excel payment 1/11 misc exp 620** · **excel receipt 1/11 svt logistics 50000** | changes that row, or adds it (asks YES) |
| **daily check 1/11** | the check table photo, with the cash counted in Daily Closing |
| **cash 1/11** | final cash of the workbook vs your counted cash → ✅ matches / ❌ difference |
| **xml 1/11 to 3/11** | your converter's XML file (or the exact errors) |
| **excel undo** | back to the previous version (asks YES) |

## Stage 3 — Tally Cloud robot on GitHub Actions (free)

**What it does**
* **tally import 1/11 to 3/11** → robot starts (≈1 min) → asks your **secure PIN** (never stored) → logs in →
  uploads the XML to `D:\My_Applications\SFM_TALLY_IMPORT\NOV 2026` → opens TallyPrime → asks **which company** →
  O: Import → Transactions → Accept → screenshot of the result. A screenshot comes after every step.
* **tally ledger svt this month** → robot opens Ledger Vouchers for SVT LOGISTICS (1st – today) → exports PDF →
  sends it to WhatsApp.
* Dates already imported are refused (duplicate vouchers). On purpose: **tally import again 1/11 to 1/11**.

**Setup (once, about 10 minutes)**
1. Put this package's `1-website-for-github` into GitHub as usual — it now also has `.github/workflows/tally-agent.yml`
   and `agent/tally_agent.py`.
2. GitHub → your repository → **Settings → Secrets and variables → Actions → New repository secret** — add 4:
   * `SFM_SITE` = `https://sai-fuel-mart.vercel.app`
   * `SFM_SECRET` = the same value as **CRON_SECRET** in Vercel
   * `TALLY_EMAIL` = your Tally Cloud email
   * `TALLY_PASSWORD` = your Tally Cloud password
   (GitHub encrypts secrets; nobody can read them back. The secure PIN is never saved anywhere.)
3. GitHub → your photo → **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token** → *Only select repositories*: this repository → *Permissions → Actions: Read and write* →
   Generate → copy it.
4. Vercel → **Settings → Environment Variables**:
   * `GITHUB_TOKEN` = that token
   * `GITHUB_REPO` = `your-github-name/your-repository-name` (as in the address github.com/…/…)
   → **Redeploy**.
5. Test on WhatsApp: **tally import 1/11 to 1/11** → send the PIN → follow the screenshots.
   GitHub → **Actions** tab shows each run and its log.

**Honest note:** the Tally Cloud screens (login, Connect, File Manager, TallyPrime) could not be tested from here.
If a run stops, its screenshot shows exactly where — send it to me and I will adjust that step. If GitHub is not
good enough (≈1 minute start each time), the same robot can move to an Oracle Cloud Always-Free server.
