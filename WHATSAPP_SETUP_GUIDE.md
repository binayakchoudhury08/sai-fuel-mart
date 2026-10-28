# WhatsApp assistant + automatic schedule — setup guide

What you get:
- **On WhatsApp:** you message the assistant from your **own WhatsApp**, and it answers from the same data as the
  website, in seconds.
- **On a schedule:** it messages **you by itself** with the morning report, attendance at noon, the closing reminder,
  and urgent alerts.

Time needed: about **15 minutes** with Twilio (Option 1). No new SIM, no GST, no documents.

---

## How it works

```
 Your WhatsApp ──► Twilio Sandbox or Meta test number ──► your website (Vercel) ──► your database
       ◄──────────────── answer in seconds ◄──────────┘
 Supabase scheduler ──every 15 min──► website ──► WhatsApp message to you (morning report, alerts…)
```

**The 24-hour rule (WhatsApp's own, for both Twilio and Meta).** A message the assistant starts by itself (the
7 AM report, alerts) may only be sent as a normal message if you wrote to it in the **last 24 hours**. So:
- **Window open:** if you chatted in the last day, the 7 AM report arrives at 7 AM.
- **Window closed:** if not, the report **waits**, and the moment you send anything ("hi"), you get
  **📬 While you were away** with everything you missed.

Costs: with **Meta**, the answers are free (up to 1,000 a month). With **Twilio**, every message uses trial
credit, and after the trial it is pay-as-you-go (small, see below).

> Tip: send **hi** once each evening. Then everything the next morning arrives on time, by itself.

---

## Choose one connection

| | **Option 1 — Twilio Sandbox** (start here) | **Option 2 — Meta test number** (later) |
|---|---|---|
| Meta / Facebook business account | **Not needed** | Needed (Meta may refuse new Facebook accounts) |
| Setup time | **10 minutes** | 25–45 minutes |
| Cost | First 100 WhatsApp messages free (trial), then pay-as-you-go (~₹0.40 per reply; card needed) | Free (1,000 replies a month) |
| Catch | Send your **join code** again every **3 days** (the assistant reminds you) | None after setup |

The commands, reports and schedule are exactly the same with both. You can switch later by changing the
Vercel settings.

---

## Option 1 — Twilio Sandbox (no Meta account) — 10 minutes

### 1. Twilio account
1. Open **twilio.com/try-twilio** → sign up with your email → verify the email.
2. Verify **your own mobile number** (the one with your WhatsApp) by OTP.

### 2. Switch on the WhatsApp Sandbox
1. Twilio Console → **Messaging → Try it out → Send a WhatsApp message** → accept and **Activate Sandbox**.
2. Note down two things:
   - the **Sandbox number**, usually **+1 415 523 8886** (this is `TWILIO_WHATSAPP_FROM`),
   - your **join code**, e.g. **join happy-tiger** (this is `TWILIO_SANDBOX_JOIN`).
3. Save the Sandbox number in your phone as "Sai Fuel Mart Assistant", and **send it your join code from WhatsApp**.
   Twilio replies that you have joined. (The manager does the same with his phone if he should get reminders.)

### 3. Point it to your website
1. Same page → **Sandbox settings** tab.
2. **When a message comes in:** `https://YOUR-SITE.vercel.app/api/wa/twilio`, method **POST** → **Save**.

### 4. Keys
Twilio Console home → **Account Info**: copy the **Account SID** (`AC…`) and the **Auth Token** (press Show).

### 5. Vercel
Vercel → **Settings → Environment Variables** → add:

| Name | Value |
|---|---|
| `TWILIO_ACCOUNT_SID` | Account SID (`AC…`) |
| `TWILIO_AUTH_TOKEN` | Auth Token |
| `TWILIO_WHATSAPP_FROM` | Sandbox number, e.g. `+14155238886` |
| `TWILIO_SANDBOX_JOIN` | your join code, e.g. `join happy-tiger` (used in the 3-day reminder) |
| `TWILIO_WEBHOOK_URL` | exactly the address from step 3: `https://YOUR-SITE.vercel.app/api/wa/twilio` |
| `WA_OWNERS` | `91XXXXXXXXXX:yourusername,91YYYYYYYYYY:managerusername` (number with 91, colon, website username) |
| `CRON_SECRET` | already set, or any long random text |

Then **Deployments → latest → ⋯ → Redeploy**.

### 6. Try it
From your WhatsApp send **hi** to the Sandbox number. You should get the menu.

**Every 3 days:** Twilio ends the Sandbox link 3 days after you join. When less than 12 hours are left, the
assistant adds a line: *"send join happy-tiger to keep it working"*. Send it, and it's good for 3 more days.

**After the 100 free trial messages:** Twilio Console → **Upgrade** → add a card (pay-as-you-go). At your use, roughly
₹100–200 a month. When Meta works for you later (Option 2), switch to it, and it becomes free.

---

## Option 2 — Meta test number (free, needs a Facebook business portfolio)

**Tip if Meta says the business account can't be created:** Meta often blocks **new** Facebook accounts. Use an
older Facebook account (a few years old, with a real name and photo, two-factor login switched on), or wait a few
days and try again at **business.facebook.com**.

### Part A — Meta (WhatsApp)

### A1. Developer account
1. Open **developers.facebook.com** on a computer and log in with your Facebook account.
2. If asked, press **Get started** and verify your mobile number by OTP.

### A2. Create the app
1. **My Apps → Create App**.
2. Use case: choose **"Connect with customers through WhatsApp"** (or type **Business**) → Next.
3. App name: `Sai Fuel Mart Assistant` → your email.
4. **Business portfolio:** choose **Create a business portfolio** → name `Sai Fuel Mart`, your name, email.
   This is only a name; **no verification or documents are needed** for the test number.
5. Press **Create app**.

### A3. Test number + your own number
1. Left menu: **WhatsApp → API Setup**.
2. You will see a **Test number** (From) with its **Phone number ID**. Copy the Phone number ID; this is `WA_PHONE_ID`.
3. **To:** press **Manage phone number list** → **Add phone number** → your personal WhatsApp number → enter the
   code WhatsApp sends you. Add your manager's number the same way, if he should get the 9 PM reminder
   (up to 5 numbers).
4. **Save the test number in your phone's contacts** as "Sai Fuel Mart Assistant".

### A4. Permanent access token (does not expire)
The token shown on API Setup lasts only 24 hours. Make a permanent one:
1. Open **business.facebook.com/settings** and choose your portfolio.
2. **Users → System users → Add** → name `assistant`, role **Admin** → Create.
3. Select it → **Assign assets** → **Apps** → your app → **Full control** → Save.
4. **Generate new token** → choose your app → expiry **Never** → tick
   **whatsapp_business_messaging** and **whatsapp_business_management** → Generate.
5. Copy the token (it starts with `EAA…`). This is `WA_TOKEN`. It is shown only once, so keep it safe.

### A5. App secret
**developers.facebook.com → your app → App settings → Basic → App secret → Show** → copy. This is `WA_APP_SECRET`.
The website uses it to check that every message really comes from Meta.

---

### Part B — Vercel

Vercel → your project → **Settings → Environment Variables** → add:

| Name | Value |
|---|---|
| `WA_TOKEN` | the permanent token from A4 (`EAA…`) |
| `WA_PHONE_ID` | the Phone number ID from A3 |
| `WA_APP_SECRET` | the App secret from A5 |
| `WA_VERIFY_TOKEN` | any word you choose, e.g. `saifuel-verify-2026` (used once in Part C) |
| `WA_OWNERS` | who may use the assistant: `91XXXXXXXXXX:yourusername,91YYYYYYYYYY:managerusername` (number with 91, colon, website username) |
| `CRON_SECRET` | already set, or set any long random text (the scheduler uses it) |
| `WA_API_VERSION` | *optional*, the version shown in API Setup's sample code (e.g. `v23.0`) if Meta asks for a newer one |

Example `WA_OWNERS`: `919876543210:boss,919812345678:mgr2`
- **The owner** gets everything and can use every command.
- **A manager** gets only the 9 PM closing reminder.
- **Anyone else** writing to the number gets **no answer at all**.

Then **Deployments → latest → ⋯ → Redeploy**.

---

### Part C — connect WhatsApp to the website

**C0. First check the website is ready (1 minute). This is what causes "404" and the red Verify.**
1. In a browser open: `https://YOUR-SITE.vercel.app/api/wa/webhook`
   - **You see "✅ Sai Fuel Mart WhatsApp webhook is live (version 3.16…)"** → good. Every line should say ✅ set.
     Any ❌ → add that variable in Vercel → **Redeploy** → open the page again.
   - **You see 404 / "page not found"** → **the website on Vercel is still an older version**. The WhatsApp part is
     only in 3.14 and newer. Put the files of `1-website-for-github` (this package) into GitHub → wait until
     Vercel shows the new deployment as **Ready** → open the address again.
2. Use your **main address** (Vercel → your project → **Domains**, e.g. `sai-fuel-mart.vercel.app`). **Not** a
   deployment address like `sai-fuel-mart-abc123-yourname.vercel.app`: Vercel protects those with a login, so
   Meta gets "not found / unauthorized".

**C1. In Meta**
1. developers.facebook.com → your app → **WhatsApp → Configuration** → **Webhook → Edit**:
   - **Callback URL:** `https://YOUR-SITE.vercel.app/api/wa/webhook`. Copy it from the browser page in C0, with
     no space and no `/` at the end.
   - **Verify token:** exactly the same word as `WA_VERIFY_TOKEN` in Vercel (capitals matter).
   - **Verify and save** → it turns green.
2. **Webhook fields → messages → Subscribe**.
3. From your WhatsApp, send **hi** to the test number. You should get the menu within 1–2 seconds.

If it is still red: open
`https://YOUR-SITE.vercel.app/api/wa/webhook?hub.mode=subscribe&hub.verify_token=YOUR-WORD&hub.challenge=123`
- **It shows `123`** → the word is right. Re-type the Callback URL in Meta.
- **It says "verify token does not match"** → the word in Meta and in Vercel differ. Change one, Redeploy if you
  changed Vercel.

## Part D — the automatic schedule (Supabase) — 5 minutes

1. Supabase → your project → **Database → Extensions** → switch on **pg_cron** and **pg_net**.
2. **SQL Editor → New query** → paste this, put in **your site address** and **your CRON_SECRET** → **Run**:

```sql
select cron.schedule(
  'sai-fuel-mart-tick',
  '*/15 * * * *',
  $$
  select net.http_post(
    url := 'https://YOUR-SITE.vercel.app/api/cron/tick',
    headers := jsonb_build_object('Content-Type', 'application/json', 'x-cron-secret', 'YOUR_CRON_SECRET'),
    body := '{}'::jsonb,
    timeout_milliseconds := 55000
  );
  $$
);
```

3. **To check it works:** after 15 minutes, run `select * from cron.job_run_details order by start_time desc limit 5;`
   It should show `succeeded`.
4. **To stop it:** `select cron.unschedule('sai-fuel-mart-tick');`

### What runs, and when (India time)

| Time | Job | To |
|---|---|---|
| 00:30 | Ends finished temporary access (re-locks days), auto-lock (if switched on) | — |
| 07:00 | 🌅 **Morning report**: yesterday's sale (₹ and crore), HSD/MS litres, lube, credit given/received, expenses, cash short/excess, urgent alerts | Owner |
| 12:05 | 🕛 **Attendance closed**: how many present, who is absent | Owner |
| 21:00 | 📋 Today's closing **not submitted** reminder | Manager + owner |
| 23:00 | 🔴 Closing **still not submitted** | Owner |
| Every 15 min (from 07:00) | 🔴 **New urgent alerts** (low tank, credit limit crossed, mismatch…), each at most once a day, bundled in one message | Owner |

Each job runs **once a day**, even if the scheduler calls twice.

---

## Using it

Send **hi** or **menu** for the list. Use the numbers or the words:

| Send | You get |
|---|---|
| **1** / **tank** | Each tank: litres, % full, days left, ATG/dip time |
| **2** / **bill** (or **bill SVT**) | Then send the bill photos (any number), then **done** → the message in your format, ready to forward |
| **3** / **pending** · **pending svt** | Pending credit of all transporters (total), or one transporter with oldest bill, days to pay, score |
| **4** / **closing** | Last 7 days: submitted / open / locked |
| **5** / **reopen 20/10** · **reopen 18/10-20/10** · **reopen 20/10 for ramesh** | Opens those dates for the manager for 24 hours (locks again by itself) after **YES** |
| **6** / **rates** | Today's rates |
| **rate HSD 92.50 MS 101.20** (+ **tomorrow** or **28/10**) | Changes rates after **YES**; "tomorrow" means the 6 AM price |
| **7** / **lube** · **lube add servo 4t 12 @ 280** | Lube stock; lube arrived with purchase rate after **YES** |
| **8** / **report** · **report 25/10** | Day summary |
| **9** / **alerts** | Everything that needs attention now |
| **10** / **profit** · **variation** · **score** · **compare** | Business Insights on WhatsApp |
| **svt pdf 26/10/2026** · **svt excel yesterday** · **svt bill day before yesterday** · **svt pdf october** | The bill **PDF / Excel of that transporter only**, for that date (or month), as files in WhatsApp. "bill" = PDF + Excel |
| **cancel** | Stops what is in progress |

**Safety:**
- Rates, reopening and lube changes always ask **"Reply YES to confirm"**; anything else cancels.
- Every change is in the website's **Audit Log** as "*yourname* (WhatsApp)".
- Bill messages made on WhatsApp also appear in the website's **Bill Message** history.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Twilio: no answer to "hi" | Your number must be in **WA_OWNERS** as `91` + 10 digits. You must have sent the **join code** in the last 3 days. **TWILIO_WEBHOOK_URL** must be exactly the address in Sandbox settings. Then Redeploy |
| Twilio: "trial limit" / messages stop | Trial messages are used up: Twilio Console → Upgrade (pay-as-you-go) |
| Meta: "Verify and save" stays red | `WA_VERIFY_TOKEN` in Vercel must equal the word typed in Meta. Redeploy after adding it. Check the URL ends with `/api/wa/webhook` |
| No answer to "hi" | Your number must be in **WA_OWNERS** exactly as `91` + 10 digits. Your number must be added in API Setup → To. Webhook field **messages** must be subscribed |
| Answers stopped after a day | You used the 24-hour token. Make the permanent one (A4) and update `WA_TOKEN` → Redeploy |
| Morning report didn't come at 7 | Normal if you hadn't messaged in the last 24 hours. Send "hi" and you get it at once. Tip: say "hi" each evening |
| Bill reading slow / a photo not read | It reads 3 at a time. A photo it can't read is named, so send that one again. Gemini key: see SETUP_GUIDE.md |
| Scheduler not running | Supabase SQL: `select * from cron.job_run_details order by start_time desc limit 5;` — check the address and CRON_SECRET in the job |
