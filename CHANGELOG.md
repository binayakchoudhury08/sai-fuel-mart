# Sai Fuel Mart — changes

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
