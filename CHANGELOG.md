# Sai Fuel Mart — changes

## 3.19.0 — Scan the handwritten Daily Closing page

Daily Closing → 📷 Scan closing page: a photo of the notebook page is read and every number is re-checked
(nozzle differences, totals, testing, litres × rate, lube qty × rate, CNG, collection, every running total, final
cash; openings vs yesterday's closings; rates vs saved prices). Review screen: photo + MS/HSD readings, CNG (manager
types the opening), lube, every ± line with where it goes, dips → Save to Daily Closing.
- + name = payment received; − transporter name (either column) = credit given (compared with the slips in step 3);
  − PhonePe / Card / HPCL OTP / HP Pay = digital; C.F / discount and everything written under "EXP" = General
  expense; driver advance = Misc expense; Babu car / Babu diesel = a Credit Transport slip for Sai Hitech; an
  unknown name = warning (not saved unless chosen); unreadable lines are listed as "not done" with their neighbours.
- A wrong running total names the lines between the two totals to check.
- After saving: page cash vs the system's expected cash — shortage up to ₹10 is fine; missing slips are pointed out.
- Names learned once (transporters, lube products). Expense category "Misc" added.

## 3.18.1
- WhatsApp check page shows the token's validity: "never expires (permanent)" / "expires in N hours (temporary)" /
  "not valid". The scheduler warns the owner once a day (09:00) on WhatsApp if the token is temporary or invalid.
- SFM Attendance app 1.0.1: fixed crash on opening (camera helper class name).

## 3.18.0 — Quick attendance + separate attendance app

- New phone screen **/attend** (Attendance → 📱 Quick attendance): one card per person with **📷 Present** (camera opens,
  one tap on the shutter saves it — no preview step, no check-out) and **✗ Absent** (no photo). Cards turn green/red
  at once; Present / Absent / Not marked counts at the top. Same rules: phone only, today only, once per person,
  Present by photo inside the check-in hours; only the owner can change a mark.
- **SFM Attendance** Android app (com.saifuelmart.attendance, v1.0): opens straight into Quick attendance; installs
  next to the main app; same signing key; own green icon. Marks go to the same database (website, dashboard,
  WhatsApp report).

## 3.17.1
- WhatsApp: the plain-text menu is the default again (it always delivers). The tap-menu is optional: set
  WA_LISTS=1 in Vercel to switch it on.

## 3.17.0 — Advanced WhatsApp assistant + premium design

WhatsApp
- Tap-menu (WhatsApp lists): Check · Entry (emergency) · More — no typing needed; "typing…" + blue ticks shown at once.
- Emergency entries, each confirmed with YES, saved with the website's own rules and audited "(WhatsApp)":
  credit slip (slip svt OD02CL2129 110 cash 1000 [date]), cash only (cash svt OD02CL2129 2000), payment received
  (paid svt 50000 upi / bank UTR…), expense (expense 500 electricity [yesterday]), lube sold (lube sold servo 4t 2).
- Plain sentences (English / Hindi / Hinglish…) are understood: unknown messages go to a fast Gemini step that turns
  them into one command, shown as "understood as: …", still asking YES for entries.

Design
- Premium pass: canopy stripe under the top bar, ink-navy sidebar with a slim active marker, hairline panels with
  layered soft shadows, meter-style condensed numbers, sticky table headers with tabular figures, clearer buttons and
  focus rings, readable hero text over the forecourt art, roomier KPI cards, better phone hero, tuned dark mode,
  reduced-motion respected.

## 3.16.4 — WhatsApp delivery reports
- The check page now also shows Meta's delivery report for each reply: DELIVERED / READ, or DELIVERY FAILED with
  Meta's code and reason (e.g. 131030 recipient not in allowed list).

## 3.16.3 — WhatsApp connection (subscribe the app to the WhatsApp account)

- Settings → System → **WhatsApp connection** (owner): finds the WhatsApp Business Account from WA_TOKEN (or
  WA_WABA_ID), shows whether the app is subscribed to it, and **Connect** subscribes it. Without this subscription
  Meta delivers messages to the number (grey ticks) but never forwards them to the website.

## 3.16.2 — Pages Meta needs to publish the WhatsApp app

- Public pages (no login): /privacy (Privacy Policy) and /data-deletion (Data deletion instructions), to paste into
  Meta → App settings → Basic before publishing the app (an unpublished app gets only Meta's test events, not real
  messages). WhatsApp "delete my data" clears the sender's assistant records.

## 3.16.1 — WhatsApp: see why a message got no answer

- The check page (/api/wa/webhook in a browser) now lists the last WhatsApp events with the reason:
  RECEIVED / REPLIED, IGNORED (number not in WA_OWNERS — shows which numbers it has; or username not a login),
  REFUSED (WA_APP_SECRET does not match), SEND FAILED (Meta's exact error, e.g. invalid access token,
  recipient not allowed), ERROR. It also checks each WA_OWNERS username exists. Numbers are partly hidden.

## 3.16.0 — Webhook check page, one-transporter bills, bills on WhatsApp

- `/api/wa/webhook` opened in a browser shows a status page: version, and whether WA_VERIFY_TOKEN, WA_TOKEN,
  WA_PHONE_ID, WA_APP_SECRET and WA_OWNERS are set. A 404 there means the site on Vercel is an older version.
  The verify token now ignores spaces copied by mistake, and a wrong token says so.
- Credit Transport → Download bills: each form (Day bill, Month bill) has its own Transporter list, so
  "SVT + 26 Oct + PDF" gives only SVT's PDF for 26 Oct (Excel likewise). "All transporters (ZIP)" is a separate,
  deliberate choice. (Before, the transporter was copied by a script; when that failed, all transporters came as a ZIP.)
- WhatsApp: "svt pdf 26/10/2026", "svt excel yesterday", "svt bill day before yesterday", "svt pdf october" →
  that transporter's bill files (same as the website) sent as documents; "bill" = PDF + Excel.

## 3.15.0 — WhatsApp without a Meta account (Twilio Sandbox)

- The assistant now also works through **Twilio's WhatsApp Sandbox**: no Meta / Facebook business account,
  about 10 minutes to set up (WHATSAPP_SETUP_GUIDE.md, Option 1). Same commands, bill photos, reports and schedule.
- Twilio's signature is checked on every message; long answers are split to Twilio's 1,600-character limit.
- Sandbox links end 3 days after joining: the assistant reminds you (last 12 hours) to send your join code again;
  "join …" messages are answered with the menu.
- Setting TWILIO_ACCOUNT_SID switches to Twilio; without it the Meta Cloud API is used (Option 2).

## 3.14.0 — WhatsApp assistant + automatic schedule

- **WhatsApp assistant** (Meta's free test number; WHATSAPP_SETUP_GUIDE.md): menu; tank stock; bill message from
  photos (read 3 at a time, your format, also saved to Bill Message history); pending credit (all / one
  transporter); closing status; reopen dates for the manager (24 h); see / change HSD-MS-CNG rates (today or
  tomorrow 6 AM); lube stock / lube arrived; day report; alerts; profit, variation, credit score, comparison.
  Only numbers in WA_OWNERS get answers; messages are checked with Meta's signature; duplicates ignored;
  changes need "YES" and are audited as "<user> (WhatsApp)".
- **Schedule on its own clock** (Supabase pg_cron → /api/cron/tick every 15 min): 07:00 morning report, 12:05
  attendance closed, 21:00 closing reminder (manager + owner), 23:00 escalation, urgent alerts (bundled, once a
  day each), 00:30 daily job. Each job once a day.
- **Always free**: messages the assistant starts are sent only inside WhatsApp's 24-hour window; otherwise they
  wait and arrive as "While you were away" when the owner writes.
- Fuel rate saving moved to one shared function used by the website and WhatsApp.

## 3.13.0 — Business Insights (owner)

Management → Business Insights (owner only; can be given to others in Permissions → "insights"):
- **Profit**: weighted-average cost from tanker invoices (opening stock carried at its earlier cost), margin per
  fuel and per litre, stock loss/gain valued at cost shown apart, lube margin from purchase rates, expenses and
  staff payments → net profit. Marked **Not final** with the exact reasons whenever an input is missing (day not
  submitted, tanker without invoice amount, lube sold without purchase rate, old-app days without readings).
  Checked against a hand calculation in tests/test_insights.py.
- **Tank variation**: book vs dip/ATG per tank and day, % of sale against the tolerance (auto_variation_pct,
  default 0.5 %), value, days over the limit, trend (getting worse / steady / improving).
- **Tanker receipts**: invoice vs received litres (allowance auto_receipt_short_pct, default 0.2 %), shortage
  value at invoice cost, challan vs observed density (auto_density_diff, default 3 kg/m³), missing dips/amounts.
- **Transporter credit score** (0–100, A–D): days to pay (FIFO), oldest unpaid bill, limit use, regular payments;
  suggested limit.
- **This month vs last month**: same days last month (fair) and the whole last month.

## 3.12.0 — Bill Message: entry date, no more "no internet"

- Bill label uses the ENTRY date — the date in the Date column of the bill's rows — never the date at the top
  (top 20, entries 19 → Bill 19…). Several entry dates → the latest, pointed out; no entry date readable → top date,
  pointed out. The check column shows "entry date · top of bill 20/10 (not used)".
- Why "no internet" appeared: a slow/busy Google answer could run past Vercel's 60-second limit, and the page
  showed every non-standard answer as "no internet". Now:
  - each request has a 45-second budget and always returns a proper answer ("took too long — Retry",
    "limit reached — wait a minute");
  - the reader first asks Google which models this key can use and skips the others (no time lost);
  - bills (and scan pages) are read 2 at a time; a failed one is retried automatically once, then a
    "↻ Retry failed bills" button re-reads only those; "Continue with the bills that were read" is still offered;
  - real reasons are shown: time-out, limit, signed out, photo too big, no internet (only when truly offline).

## 3.11.0 — Faster reading, app fixes, native app plan

- Bill Message has its own fast reader: asks only for the transporter, bill no., date, TOTAL and the amount
  column; fastest free model first (Gemini 3.5 Flash-Lite, then 3.8 Flash…); Gemini's "thinking" switched off;
  photos sent at 1600 px; 3 bills read at the same time; a second look only when the TOTAL doesn't match.
- Scan bill pages: thinking set to low, 2 pages read at the same time.
- Android app 3.7.0 (versionCode 7, same key): several photos chosen from the gallery are all passed on (before,
  only the first one arrived); Copy and Send on WhatsApp work natively (opens WhatsApp / WhatsApp Business).
- NATIVE_APP_GUIDE.md: plan and Android Studio guide for the native app, Phase 1 and Phase 2.

## 3.10.0 — Bill Message

- New section **Bill Message** (Credit Transport → 💬 Bill message, and Management → Bill Message): photos of a
  transporter's bills → each bill's date (as DDMMYY) and TOTAL → the owner's message, e.g.
  "SVT Logistics / Bill upto 18/10/2026 / Bill 121026 = 54,251 … / TOTAL = 4,36,978".
- Each TOTAL is checked against the bill's rows (second look when they don't match); same bill twice is pointed out.
- Everything can be corrected before sending; live message; Copy and Send on WhatsApp; optional lined-up columns;
  history of sent messages.
- Uses its own key GEMINI_API_KEY_MESSAGE (falls back to GEMINI_API_KEY).

## 3.9.0 — Page reader made robust (Gemini free tier, October 2026)

- Uses the current free Flash models — gemini-3.8-flash first, then gemini-3.5-flash-lite, gemini-3-flash-preview,
  gemini-2.5-flash — and moves to the next one by itself when a model is busy, limited or retired
  (GEMINI_MODEL can force one).
- Asks Gemini for the exact table format (structured output); falls back to plain JSON on models without it.
- Second look: when a page's totals do not add up (or qty × rate ≠ amount), the page is read again with the
  doubtful lines pointed out; the reading that matches the page totals is kept ("read twice" on the page).
- Short wait and retry on the free per-minute limit; clear messages for a wrong key or a used-up limit.
- The key is sent in a request header (not in the web address).
- Settings → System → Page reader (AI) check: key status, which free models the key can use, live test,
  pages read today / last 30 days, link to the free limits in AI Studio.
- SETUP_GUIDE.md: complete, up-to-date setup (Supabase, Gemini, GitHub, Vercel, first sign-in, phones, ATG,
  daily routine, troubleshooting).

## 3.8.0 — Scan many pages of one transporter at once

- Credit Transport → 📄 Scan bill pages: choose the transporter, add every page (take photos one by one or pick
  several from the gallery), press "Read all pages" — each page is read in turn with progress shown.
- One check screen for all pages: at the top "3 pages · 10 entries · 1,418 L · ₹1,61,609" and one chip per challan
  (✓ 1070 · 6 entries / ⚠ 1071 · 2 to check …); below, each challan in its own section with its photo, rows and
  page-total check; a "Save all" bar stays on screen and saves every page together.
- Same challan photographed twice is pointed out. Saved entries are normal Credit transport entries — edit,
  delete or add later on any date.

## 3.7.0 — Scan bill page (Credit Transport)

- Credit Transport → 📄 Scan bill page: photograph a handwritten credit bill page; it is read by Google Gemini
  (free tier) and turned into Credit transport entries that the manager checks and saves in one go.
- Reading rules: transporter from "To M/s" (learned: once "S.V.P" is confirmed as SVT it matches by itself);
  fuel HSD; Bill No. → Challan No.; Credit Bill No. column → Slip No.; a vehicle line + the CASH line under it =
  one entry (diesel + cash taken); a vehicle line with only an amount = cash only; ditto marks copy the row above.
- Checks before saving: qty × rate = amount (nearest rupee), page totals (litres and ₹), vehicle not in that
  transporter's list (closest suggested), page rate vs saved price, already-entered rows and bill numbers.
- Nothing is saved until "Save all"; entries are created exactly like typed ones (SL numbers, rounding, credit
  limit, audit log) and keep a link to the page photo. A scan cannot be saved twice.
- Setup: add GEMINI_API_KEY in Vercel (free key from aistudio.google.com) and redeploy.

## 3.6.2

Attendance cover when the manager is away
- Stand-in (planned leave): Settings → Temporary Access → "Take attendance (stand-in)" for a person and dates —
  future dates allowed (up to 60 days ahead); valid until the end of the last date; ends by itself.
- Backup person (sudden absence): Attendance → "Attendance rules, backup & stand-in (owner)" → choose one login who
  can always take photo attendance.
- Both follow the same rules as the manager: phone only, live photo only, today only, 6–12, once per person.

Totals in crores
- Dashboard Total Sales and report totals (Reports, Outstanding, Credit register, Sales / Transactions) show ₹ x.xx Cr
  with the exact rupees underneath.

App
- Android app 3.6.2 (version code 6), same signing key; ANDROID_STUDIO_GUIDE.md explains building and signing.

## 3.6.1
- Monthly Summary is shown (and calculated) only on the owner's dashboard — not for Admin, Manager or Staff.
- Lube Sales, HSD Sold and MS Sold amounts are shown in crores (₹ x.xx Cr), like Total Fuel Revenue.

## 3.6.0

Dashboard → Monthly Summary (‹ › to change month)
- Total Fuel Revenue (MS + HSD + CNG) in crores (₹ 1.24 Cr), the exact rupee amount and the split per fuel,
  worked out from the daily closings (meter readings × price).
- Lube Sales for the month (₹ and units), HSD Sold (litres) and MS Sold (litres); each with % vs last month.

## 3.5.0

Settings is the single home for owner tools
- Tabs: Station Info · Fuel Prices · Payment Methods · Users · Permissions · Temporary Access · Automation · Photos ·
  System. The sidebar has Settings ▾ (same tabs) and Audit Log; no separate owner pages.

Attendance (strict)
- Photo only for everyone except the owner. Check-in TODAY ONLY, 6:00 AM – 12:00 PM (owner can change the hours),
  once per person; check-out once. Not marked by 12:00 PM = Absent.
- Managers/Admins cannot edit, replace or delete a mark. The owner can change anything on any day (manual status,
  retake photo). Attendance is no longer offered in Temporary Access.

Credit transport
- Slips can be added, edited and deleted on ANY date (old-system days and locked days included).

Design #2 (owner's second reference)
- Top bar: search (transporters, vehicle/slip/challan numbers, nozzles, tanks, staff, lube, pages), light/dark switch,
  date | time, alerts, profile with role.
- Sidebar: Reports ▾ and Settings ▾ sub-menus; station status card (your banner photo, Online, last updated).
- Dashboard: greeting banner + weather; Live Nozzle Status cards; KPI cards with icon and 7-day mini trend;
  Sales Overview with Today / 7 Days / 30 Days and transactions line; Fuel Distribution (litres); Recent Activity
  (daily work only); Alerts & Notifications; Sales / Transactions; Tank Level Monitoring; Quick Actions;
  Employee / Attendance; Fuel Prices; Quick Links; Android app card (download from the site).
- Dark mode for the whole site (remembered on each device).

## 3.4.0

Credit transport is never locked
- Slips can be added, edited and deleted for any date in the last 31 days, even when that day's Daily closing is
  locked (the database allows exactly this; every other entry on a locked day stays blocked). The owner can go further back.
- Managers and Admins edit and delete slips by default (Permissions → "Cancel or correct entries").
  Deleted slips are hidden from the list but kept in history ("Show deleted").
- A typed rate is used when no fuel price was saved for an old date.

Attendance: photo only
- Managers and Admins mark Present only with a live photo. Marking/changing attendance without a photo is
  owner-only and can no longer be given to anyone in Permissions or Temporary access.

Photo proofs
- Phone: big "Take Proof" button and type cards that show ✓ when done today.
- Register: date with ‹ ›, type chips with counts, photo gallery with type/time/by on the photo, 📍 and ▶ badges.
- Tap a photo: large viewer with all details, Map, Video, full size, ‹ › / swipe through the day.
- Camera: icons per type, edge-to-edge dark viewfinder; the bottom bar hides while taking the photo.

## 3.3.0 — layout exactly as the owner's reference design

- Sidebar: Dashboard · Daily Closing · Nozzle Management · Tank Level · Credit Transport · Lube Stock · Attendance ·
  Reports · Settings · Digital Collection; other tools under "Management"; static station logo card at the bottom.
- Top bar: ☰ (collapses the sidebar on computers, opens the menu on phones), page title, date | time, alerts bell,
  user menu (change password, sign out).
- Dashboard: banner with greeting, station name and tagline, weather card; Total Sales (Today), Total Transactions,
  Avg. Sale Amount, Fuel Dispensed (all vs yesterday); Sales Overview (Last 7 Days); Fuel Type Sales donut.
  Settings → Station Info → "Station photo" puts your own station photo in the banner.
- Nozzle Management: HSD (High Speed Diesel), MS (Petrol), CNG groups; Ready, price per litre, status dot.
- Tank Level: tabs (Overview, Stock Check, ATG Readings, Tanker Receipts, History); cards with level bar, capacity, current.
- Sales / Transactions: meter sales, credit slips, lube bills, digital collections and payments with date, fuel and
  payment-mode filters, Export to Excel and pages.
- Reports: Sales / Stock / Credit / Attendance tabs, date range + fuel filter + Generate, totals vs previous period,
  sales chart by fuel.
- Employee Attendance: date, Add Employee, table with Check In / Check Out.
- Settings tabs: Station Info · Fuel Prices (one row per fuel with its own Update) · Payment Methods · System.
- New sign-in page: animated scene of an attendant filling a car with diesel (meter counting, fuel flowing) and
  rotating cards about what the system does; sign-in card on the side.

## 3.2.0

New design (as per the owner's reference)
- Navy sidebar with blue active item and a station card; white content with glass cards that lift on hover.
- Top bar on every page: title, date and time, alerts bell with count, signed-in user.
- Dashboard: hero banner (original forecourt illustration, live weather for your place), KPI cards with
  "vs yesterday", 7-day sales chart (amount bars + litres line), fuel-type donut, Today's work, alerts, tank cards.
- Tanks: cards with an animated liquid level in the fuel's colour (MS amber, HSD green, CNG sky, lube blue).
- New Nozzles page: every nozzle with status, today's price and today's sale.
- Attendance as a table with Check in / Check out (both by live photo).
- Settings in tabs: Station info, Fuel prices, Tanks & nozzles, Payment methods, System.
- Phones: two KPI cards per row, bell in the top bar.

Automation
- Alerts centre (bell + Alerts page) checks by itself: tank low / days of stock left, tanker not recorded, ATG not
  synced, overdue daily closings, credit mismatches, cash shortages, parties over limit or not paying, lube to
  reorder, days waiting for verification, missing dip-check photo. Each person sees only what they can act on.
- Automation page (owner): set every limit; daily job at 6:00 AM (Vercel Cron) ends expired temporary access and,
  if switched on, auto-locks clean days (all readings, credit matched, cash within tolerance). "Run now" button.

## 3.1.1

Photo storage fixed
- Cause: the old app's Supabase bucket is "proof-files"; 3.x looked for "proofs", which does not exist.
  The default is now "proof-files" (SUPABASE_BUCKET can still override it).
- Works with both Supabase key styles: legacy service_role (eyJ…) and the new secret keys (sb_secret_…).
- Settings → Photo storage check (owner): shows URL, key type, bucket, private/public; buttons to create the
  bucket, make it private, and run a full upload/view test. Errors now say plainly what is wrong.

## 3.1.0

Temporary access (owner only — menu → Temporary access)
- Give one person chosen work on chosen dates until a set time (1, 2, 3, 7 days or an exact date & time):
  "Correct old / imported days", "Attendance with live photo", "Attendance without photo", or exact items.
- Locked days in the range — including old-system days — open for them, and lock again automatically when the
  access ends or the owner presses "Stop now". Dates outside the range stay blocked.
- The person sees a banner with the dates and end time. Every grant, stop and end is in the audit log.
- From a locked day in Daily closing, the owner has a direct link "let a manager edit it for a limited time".

Credit transport in whole rupees
- Each slip's HSD amount and cash taken are saved rounded to the nearest rupee (₹9,082.19 → ₹9,082;
  ₹9,082.50 → ₹9,083). The page, PDF and Excel bills and all totals show whole rupees; litres and rate keep decimals.

## 3.0.0

Fixes
- Phone "More" button now opens (website and app). The menu is a bottom sheet with every section as a tile.
- Speed: moved to Vercel in Singapore next to the database (~1–3 ms per database request instead of ~170 ms).
  Database setup runs only when it changes; static files come from Vercel's CDN.
- Android app 3.0.0: GPU drawing and pre-rendering for smoother scrolling; Vercel address by default.

Simpler, clearer design
- New look: indigo navigation, frosted-glass panels, a colour and icon for every section, clear status chips.
- Home shows "Today's work" as numbered steps with Done / To do / Mismatch.
- Daily closing is 7 numbered steps with a step bar: readings, digital & cash, credit check, lube, expenses &
  staff, payments, check & submit.

Roles and permissions
- Owner, Admin, Manager, Staff (data entry). The owner ticks what each role may do (Permissions page).
  Admin gets only what the owner allows and can never create or change Owner/Admin accounts.

Daily closing ↔ Credit transport reconciliation
- Slips are entered only in Credit transport. In Daily closing you write each transporter's total for the day;
  it is checked against the slips (✓ Matched / ✗ Check slips). Submit and lock are blocked until all match.

Lube stock notebook
- Per product for any day: opening, + arrived, − sold, = left. Write what arrived and what you counted on the
  shelf; one Save. Count differences are recorded as stock counts.

Attendance by live photo
- Manager taps a staff member → live camera → photo (stamped with time, GPS, who took it) → Present.
  No photo = absent. Managers only for today + the days the owner allows, within the owner's hours.
  Owner (and anyone allowed) can mark/change any status with a reason.

Removed
- WhatsApp / n8n automation (switched off and removed from the website).


## 2.3.0

Daily closing
- Lube sale: out-of-stock products can be chosen. Tick "Stock has arrived – add it to stock first" to add the
  missing quantity and record the sale in one go, or use "Add stock that arrived" on the same page.
  Both are audited (manager/owner).
- Expenses → "Pay staff – salary / advance / bonus": staff payments are entered in Daily closing for the day they
  are paid. Cash from the counter comes off expected cash. The Salary sheet shows everything but no longer has
  its own entry form.

Photo proofs (rebuilt for the phone)
- Live camera inside the page: no gallery, no file upload. A photo must be taken now (server refuses photos
  older than 15 minutes, uploads from a PC, and anything that is not a real camera picture).
- Every photo is stamped on the picture: proof type, pump, date and time (always IST), GPS with accuracy, user.
- Optional live video up to 30 s from the same camera.
- On a PC the page explains proofs are taken on the phone; viewing and the PDF work everywhere.
- Register: photo cards with type, status, time, user, Video and Map buttons; filters; paging.
- PDF with photos: each proof with its photo, details, and scan codes for the map and the video.
- Files are private: served only to signed-in users through the site (no public links). Old-app proofs keep working.
- Android app 2.3.0: lets the in-page camera/microphone work for your site only, and opens the camera directly
  (never the gallery) for proof photos.


Old / imported data can be corrected
- The owner can reopen any day, including days imported from the old app, with a reason (kept in the audit log).
- Settings → Old data editing: a switch that lets managers reopen, correct and lock old days too, plus
  "reopen / lock a date range" for bulk fixes. Operators can never change old days.

WhatsApp automations (n8n)
- Settings → Automation (n8n): owner creates a read-only key (stored hashed, shown once, can be switched off,
  replaced or deleted; every use is in the audit log).
- Read-only API for n8n: overdue closings, transporter bill info + PDF/Excel file, day summary.
- Four tested n8n workflows: 12 AM closing reminder to the manager, 4 PM transporter bill (PDF + Excel, starting
  with SVT, any number of transporters), 9 PM day summary, and a failure alert. See N8N_WHATSAPP_GUIDE.md.

Speed and large data
- The signed-in user is re-checked against the database every 2 minutes and before every save, instead of on
  every page (Party accounts: 18 → 3 database trips per page).
- Long party statements and the credit register are paged (200 / 300 rows); running balances stay exact.
  A 3-year statement: 6.3 s / 75 MB before → 0.2 s / 0.3 MB now.
- New database indexes for years of data; gunicorn --preload.

Design
- Quick-action bar on the Overview; zebra rows and row highlight in tables; page controls on long lists.


## 2.1.0 — old features restored on the safe v2 base

Credit transport
- Credit Transport page with the daily register: Date, SL No (per day), Transporter, Challan, Vehicle, Slip,
  Qty, Rate, Fuel amount, Cash taken, Total; search by vehicle/slip/challan; filter by transporter and dates.
- One entry = diesel + cash taken together (cash-only entries allowed). Vehicle list fills from the transporter.
- Daily closing keeps the old way: the whole slip total (diesel + cash taken) is one "credit given" figure;
  there is no separate "cash given to drivers" line.
- One slip can carry many entries (20–25 per slip is normal). Only a double tap on Save is ignored.
- "Correct" an entry: the wrong row stays visible (struck through, with the reason) and the corrected row keeps
  the same SL No.
- Receive amount marked as for Fuel / Lube / Fuel + Lube.
- Transporter summary: opening, fuel credit, lube credit, received, discount, balance.
- Per-litre transporter discount (Credit transport → Transporter discounts, or on the party page).
- Transport bill PDF and Excel in the original format: letterhead, bill no., SL, totals, discount, NET PAYABLE,
  signature lines; file name "Party Bill DDMMYY".
- Daily bills ZIP: one PDF + one Excel per day, in "Month YYYY/PDF" and "Month YYYY/Excel" folders; can also
  build one ZIP for all transporters.
- "Post this discount to the account" (once per bill) so the ledger matches the net payable.
- Register PDF and Excel.

Reports (every report has PDF and Excel)
- Daily sales & cash, Nozzle report (per reading + per nozzle), Transport register, Transporter bills,
  Outstanding, Party entries, Digital collections (date × payment app), Tank stock & variance (with ₹ value and
  tanker receipts), Lube stock, Attendance workbook (summary, grid, payments, one sheet per staff).
- Analytics page: month sale, litres by fuel, average per day, credit vs received, cash short/excess,
  daily sale chart, top transporters, top lube products, expenses by category, staff today.
- Business Excel with charts (monthly trend line chart, outstanding pie chart, daily sale bar chart) plus all data.

Credit transport made simple (owner's request)
- One short entry form: Date, Transporter, Vehicle No., Slip No., Challan No., HSD Qty, Cash Taken (rate automatic,
  total shown live). Below it: the day's entries, one Download box, and the month total per transporter.
- Bills show only entries and totals — Date, SL, Vehicle No., Slip No., Challan No., HSD Qty, Rate, HSD Amount,
  Cash Taken, Total, a TOTAL row and signatures. No balance due, no discount, no net payable, no payments.
- Downloads for one transporter or all (each separately): day bill PDF/Excel, month bill PDF/Excel (with a
  subtotal for each day), and a month ZIP = month bill + every day's bill in PDF and Excel, one folder per transporter.
- Balances and payments stay in Party accounts ("Statement PDF" there is the account statement with balance).
- The per-litre discount is no longer printed on bills.

Old-data import no longer copies the old activity log (owner's choice); it stays in the old tables.

Import that "stopped: server restarted" — fixed
- Cause (reproduced): an earlier, abandoned import attempt was still holding the import lock in an open database
  transaction, so the new import waited behind it with no time limit; after 5 minutes of silence the page
  guessed "the server restarted".
- Now: the import waits at most 20 s for another session and then stops with a clear reason; the progress screen
  shows what it is waiting for; the import page lists stuck sessions with an "End stuck sessions" button; a
  separate heartbeat keeps status honest; every step is written to Render's logs; new "Import while I wait" option.

Speed
- Fewer database trips on every page: the per-transaction health check and setup statements went from 4 trips
  to 1; the signed-in user and pump name load in one query; the dashboard reuses its chart data instead of
  recomputing the whole day; tank averages load in one query. Pages now make 6–24 trips (were 18–48).
- Pages and data are sent gzip-compressed (Daily closing 26 KB → 5 KB).
- Old-data import: parties and lube products in batches, progress updates over one reused connection;
  a year of data in 231 trips (≈30 s worst case, ≈1–2 s with server and database in the same region).
- render.yaml now sets region: singapore to match the Supabase database (see DEPLOY_GUIDE → Speed).

Old-data import fixed (was loading forever)
- Cause: it sent every old row to the database one at a time — about 40,000 trips for a year of data, which
  takes 3–100 minutes from Render to Supabase, but Render stops a request after 2 minutes.
- Now rows go in batches of 500 (≈500 trips for a year of data; about 15 s at typical latency) and the import
  runs in the background with a live progress bar. A failed or interrupted import saves nothing and can be retried.

ATG automation (checked against the old app and fixed)
- Same address /api/tank-sync, same secret (TANK_SYNC_SECRET), same JSON from the HP Smart Connect bookmarklet,
  same CORS rules and response — the bookmarklet needs no change if your Render address stays the same.
- Gain/shortage uses the old ATG logic: opening = ATG stock − ATG received today + ATG sold today; sale = meter
  readings once entered, otherwise the ATG's own sale, so a midday sync shows "Balanced", not a false loss.
- Live tank cards are back: stock, fill %, days left, gain/short in L and ₹, temperature, water-in-tank warning,
  "synced X min ago" badge (green < 30 min, amber, red after 2 h), refreshing every 30 seconds via /api/tank-status.
- Detailed live readings table: capacity, net/gross volume, ullage, product and water dip, water litres,
  temperature, density, density at 15 °C, float height, density and decantation status, ATG received/sold today.
- Monthly gain and shortage totals in litres and ₹ on the tank page.
- New safeguards: "ATG shows a tanker unloaded but no tanker receipt was recorded" warning (tank page + dashboard);
  values like "12,345.60" are read correctly (the old code crashed on them); unreadable values are skipped,
  never saved as 0; "Petrol"/"Diesel" accepted as MS/HSD; one audit line per sync; a locked day refuses syncs.

Other restored features
- Proofs: MS/HSD dip check, MS/HSD nozzle testing, stock check with Available / No stock, photo + video, time and
  GPS; "PDF with photos" report for a date and type.
- Tanker receipt: invoice, order, PO no./date, carrier no./name, water check, challan vs observed density
  (alert above 3 kg/m³), dip before/after, 5 compartments (fuel, challan dip, observed dip, volume);
  month stats: tankers, MS/HSD received, dip gain/shortage, density alerts, short challans, last delivery.
- Tank page: gain/loss in ₹, ATG detail line (water, temperature, density, gross, ullage, today receipt/sale).
- Staff department and shift; attendance Excel from the staff and salary pages.
- Owner reminder to download a backup in the first 3 days of each month.
- Importer brings across old SL numbers, cash taken, per-litre discounts, full tanker receipts, proof
  types/status/video, staff department/shift.

Not brought back on purpose
- The old /api/transporters, /api/transport-reports, /api/transport-entry-report and /api/save-transport-entry
  endpoints: they had no login check, so anyone with the address could read every transporter's account or add
  entries. All the same data is available inside the app after signing in.
- Hard delete of transporters and entries: replaced by "inactive" and cancel-with-reason so history can't vanish.
