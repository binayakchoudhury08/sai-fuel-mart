# Sai Fuel Mart — changes

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
