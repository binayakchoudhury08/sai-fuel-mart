"""Tally Cloud robot (runs on GitHub Actions). Every step reports to the website, which sends it to the owner's
WhatsApp with a screenshot. The secure PIN and the company name come from the owner on WhatsApp, at the moment needed.

Steps (import):  login (email, password, PIN) → Connect → File Manager → D:\\My_Applications\\SFM_TALLY_IMPORT\\<MON YYYY>
→ upload the XML → open TallyPrime → company (asked on WhatsApp) → O: Import → Transactions → path + file → Accept.
Steps (ledger):  login → Connect → TallyPrime → company → Go To Ledger Vouchers → ledger + period → export PDF to D:
→ download it from File Manager → send to WhatsApp.
"""
import base64
import re
import os
import sys
import time
import io
import subprocess
import tempfile
import shutil
import importlib.util
import csv
from pathlib import Path
from datetime import date

import requests
from playwright.sync_api import sync_playwright

SITE, SECRET, JOB = os.environ["SFM_SITE"].rstrip("/"), os.environ["SFM_SECRET"], int(os.environ["JOB_ID"].removesuffix(":retry-login"))
H = {"x-cron-secret": SECRET}
LOGIN = "https://in.cloudaccess.tallysolutions.com/login"
IMPORT_ROOT = r"D:\My_Applications\SFM_TALLY_IMPORT"
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


HTTP = requests.Session()


def api(path, **kw):
    r = HTTP.request(kw.pop("method", "GET"), f"{SITE}/api/tally/job/{JOB}{path}", headers=H, timeout=60, **kw)
    r.raise_for_status()
    return r


def step(name, message, page=None, done=False, failed=False, png=None, evidence=None):
    if page is not None and png is None:
        try:
            png = base64.b64encode(page.screenshot(type="png")).decode()
        except Exception:
            png = None
    r = api("/step", method="POST", json={"step": name, "message": message, "png": png, "done": done, "failed": failed, "evidence": evidence}).json()
    if not r.get("ok"):
        raise RuntimeError("Step/screenshot delivery was not accepted; import will not continue")
    print(f"[{name}] {message}", flush=True)


def wait_answer(field, minutes=10):
    end = time.time() + minutes * 60
    while time.time() < end:
        r = api(f"/wait/{field}").json()
        if r.get("state") == "ok":
            return r["answer"]
        if r.get("state") in ("expired", "missing", "cancelled"):
            break
        time.sleep(2)
    raise RuntimeError(f"no {field} received in {minutes} minutes")


def login(ctx):
    page = ctx.new_page()
    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto(LOGIN, wait_until="domcontentloaded")
    pin = wait_answer("pin")
    inputs = page.locator("input")
    inputs.nth(0).fill(os.environ["TALLY_EMAIL"])
    inputs.nth(1).fill(os.environ["TALLY_PASSWORD"])
    inputs.nth(2).fill(pin)
    pin = None
    page.get_by_role("button", name="Login").click()
    page.wait_for_url("**/dashboard**", timeout=60000)
    step("login", "Login successful")
    return page


LAST_DESK = None


def ui(desk):
    """The part of the page that holds the remote desktop and its side panel / File Manager — when Tally Cloud embeds
    the desktop inside its session page, that is an inner frame, so text and buttons are searched there."""
    for f in desk.frames:
        if _safe(lambda: f.locator("canvas").count() > 0, False):
            return f
    return desk.main_frame


def _safe(fn, default=None):
    """Page checks while Tally Cloud reloads the page (starting the virtual computer) must not crash the robot."""
    try:
        return fn()
    except Exception:
        return default


def connect(page):
    stopped = _safe(lambda: page.get_by_text("Stopped").count(), 0)
    if stopped:
        step("starting", "Starting the virtual computer (it was Stopped) — up to 5 minutes…")
    with page.context.expect_page(timeout=600000) as newp:
        page.get_by_text("Connect", exact=True).click()
    desk = newp.value
    _safe(lambda: desk.wait_for_load_state("domcontentloaded"))
    _safe(lambda: desk.set_viewport_size({"width": 1600, "height": 900}))
    global LAST_DESK
    LAST_DESK = desk
    ready = False
    for _ in range(120):                                    # up to 6 minutes; the page reloads several times
        if _safe(lambda: any(f.locator("canvas").count() > 0 for f in desk.frames), False):
            ready = True                                    # the remote screen is there (often embedded in the session page)
            break
        time.sleep(3)
    if not ready:
        raise RuntimeError("the virtual computer did not open within 6 minutes (last address: "
                           + _safe(lambda: desk.url.split("?")[0], "?") + ")")
    # Remote desktop streams may never become network-idle. Wait only for its canvas.
    time.sleep(2)
    dismiss_popups(desk)
    step("connect", "Virtual computer connected", desk)
    return desk


def dismiss_popups(desk):
    """Tally Cloud's "Auto-Shutdown on Timeout" / "Scheduled Shutdown" alerts are drawn inside the remote screen
    (not page buttons): click their "Okay" (measured from a real screenshot: ~x 593, y 453 at 1600×900), then Enter."""
    for label in ("Okay", "OK", "Ok"):
        b = ui(desk).get_by_role("button", name=label)
        if _safe(lambda: b.count(), 0):
            _safe(lambda: b.first.click(timeout=2000))
    text, _ = read_screen(desk)
    if any(label in text.lower() for label in ("auto-shutdown on timeout", "scheduled shutdown")):
        desk.mouse.click(593, 453)
        time.sleep(1)
        text, _ = read_screen(desk)
        if any(label in text.lower() for label in ("auto-shutdown on timeout", "scheduled shutdown")):
            raise RuntimeError("Shutdown alert remains open; stopped before typing into Tally")


# The side panel (opened with « at the right edge): power, full screen, notifications, File Manager, permissions,
# display, help — one button every 72 px, the first 44 px from the top, 49 px from the right edge (measured).
SIDE = {"power": 0, "fullscreen": 1, "notifications": 2, "File Manager": 3, "permissions": 4, "display": 5, "help": 6}


def _fm_open(desk):
    return _safe(lambda: any(f.get_by_text("Navigate to folders").count() > 0 or f.get_by_text("Drop files here").count() > 0 for f in desk.frames), False)


def side_button(desk, title):
    vw, vh = desk.viewport_size["width"], desk.viewport_size["height"]
    fr = ui(desk)
    btn = fr.locator(f"[title='{title}'], [aria-label='{title}']")
    if _safe(lambda: btn.count(), 0):
        btn.first.click(); time.sleep(2)
        if title != "File Manager" or _fm_open(desk):
            return
    if not _safe(lambda: fr.locator("[title='File Manager'], [aria-label='File Manager']").count(), 0):
        desk.mouse.click(vw - 55, vh // 2); time.sleep(1.5)            # « open the panel (if it is closed)
    idx = SIDE[title]
    for i in (idx, idx - 1, idx + 1):                                  # measured position first, then its neighbours
        desk.mouse.click(vw - 49, 44 + 72 * i); time.sleep(2.5)
        if title != "File Manager" or _fm_open(desk):
            return
        desk.keyboard.press("Escape"); time.sleep(1)                   # a different panel opened — close it
    raise RuntimeError("the File Manager did not open")


def upload_xml(desk, name, xml):
    side_button(desk, "File Manager")
    fm = next((f for f in desk.frames if _safe(lambda: f.get_by_text("Drop files here").count() > 0, False)), ui(desk))
    month = import_month(name)
    for part in ("My_Applications", "SFM_TALLY_IMPORT"):
        fm.get_by_text(part, exact=True).first.dblclick(); time.sleep(2)
    if not _safe(lambda: fm.get_by_text(month, exact=True).count(), 0):     # new month: create the folder
        _safe(lambda: fm.locator("button[title*='older'], button[aria-label*='older']").first.click(timeout=5000))
        time.sleep(1)
        box = fm.get_by_placeholder(re.compile("folder name", re.I))
        if _safe(lambda: box.count(), 0):
            box.first.fill(month)
            _safe(lambda: fm.get_by_role("button", name=re.compile("^create$", re.I)).first.click(timeout=5000))
        else:
            desk.keyboard.type(month); desk.keyboard.press("Enter")
        _safe(lambda: fm.get_by_text(month, exact=True).first.wait_for(timeout=15000))
    if not _safe(lambda: fm.get_by_text(month, exact=True).count(), 0):
        raise RuntimeError(f"Could not create the intended folder {month}; upload stopped")
    fm.get_by_text(month, exact=True).first.dblclick(); time.sleep(2)
    if Path(name).name != name or not re.fullmatch(r"SFM_Tally_Import_\d{2}-\d{2}-\d{4}_to_\d{2}-\d{2}-\d{4}(?:_job\d+)?\.xml", name):
        raise RuntimeError("Unexpected import filename")
    # Browser payload avoids a second disk copy and filename collisions.
    fm.locator("input[type=file]").first.set_input_files({"name": name, "mimeType": "application/xml", "buffer": xml.encode("utf8")})
    fm.get_by_text("Upload", exact=False).last.click()
    fm.get_by_text(name, exact=True).first.wait_for(timeout=120000)
    step("upload", f"Uploaded to {IMPORT_ROOT}\\{month}\\{name}", desk)
    desk.keyboard.press("Escape")
    return month


def _tally_on_screen(desk):
    """True when the TallyPrime window is showing: its top bar is blue (#1C4F9C-ish); the empty desktop is dark grey
    (#252525). Looks at a strip across the top of the remote screen (left of the side panel)."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(desk.screenshot(type="png"))).convert("RGB")
        w, h = im.size
        blue = total = 0
        for x in range(int(w * 0.10), int(w * 0.85), 12):
            for y in range(int(h * 0.01), int(h * 0.12), 6):
                r, g, b = im.getpixel((x, y))
                total += 1
                if b > 100 and b > r + 40 and b > g + 10:
                    blue += 1
        return blue / max(total, 1) > 0.25
    except Exception:
        return False


TALLY_ICON = (60, 135)            # measured on the remote desktop (1600×900): the TallyPrime icon under Disk (D:)


def close_side_panel(desk):
    vw, vh = desk.viewport_size["width"], desk.viewport_size["height"]
    if _safe(lambda: ui(desk).locator("[title='File Manager'], [aria-label='File Manager']").count(), 0):
        desk.mouse.click(vw - 129, vh // 2); time.sleep(1)        # » closes the side panel when it is open
    # Never send an unconditional Esc here: Gateway would open the quit prompt.
    if _fm_open(desk):
        desk.keyboard.press("Escape"); time.sleep(0.5)


def quit_dialog(text):
    return bool(re.search(r"\bquit\s*\?", text, re.I) or
                (re.search(r"\bquit\b", text, re.I) and re.search(r"\byes\s+or\s+no\b", text, re.I)))


def dismiss_quit(desk):
    # File Manager/side-panel clicks can retain keyboard focus outside Tally.
    # Focus the observed dialog title (not Yes), then cancel it.
    click_setting(desk, ["Quit"], activate=False, ocr_mode=11)
    desk.keyboard.press("Escape", delay=100)
    try:
        return wait_screen(desk, lambda t: not quit_dialog(t), 5)
    except RuntimeError:
        text, png = read_screen(desk)
        if not quit_dialog(text):
            return text, png
        # Only a still-visible Quit confirmation may receive the explicit No key.
        desk.keyboard.press("n", delay=100)
        return wait_screen(desk, lambda t: not quit_dialog(t), 10)


def recover_tally_screen(desk):
    """Return from known reports/dialogs without accepting or modifying vouchers."""
    for _ in range(10):
        text, _ = read_screen(desk)
        low = text.lower()
        if quit_dialog(text):
            text, _ = dismiss_quit(desk)
            low = text.lower()
        if any(label in low for label in ("specify file path", "incorrect file", "import transactions", "list of file formats",
                                               "day book", "ledger vouchers", "account books", "display more reports",
                                               "name of ledger", "list of ledgers", "export configuration", "change voucher type")):
            desk.keyboard.press("Escape"); time.sleep(0.5)
            continue
        if "gateway of tally" in low or "select company" in low or "list of companies" in low:
            return
        raise RuntimeError("Tally is on an unrecognised screen; return to Gateway of Tally and try again")
    raise RuntimeError("Could not return to Gateway from the previous task")


def open_tally(desk):
    close_side_panel(desk)
    dismiss_popups(desk)
    opened = _tally_on_screen(desk)
    for attempt in range(2):
        if opened:
            break
        desk.mouse.dblclick(*TALLY_ICON)
        for _ in range(30):                                      # up to 90 s for TallyPrime to start
            time.sleep(3)
            if _tally_on_screen(desk):
                opened = True
                break
        if opened:
            break
    if not opened:
        raise RuntimeError("TallyPrime did not open (the screen still shows the desktop) — nothing was imported")
    time.sleep(5)
    recover_tally_screen(desk)
    step("tally", "TallyPrime opened — which company?", desk)
    company = wait_answer("company")
    text, _ = read_screen(desk)
    if "gateway of tally" in text.lower():
        if compact(company) not in compact(text):
            raise RuntimeError("A different company is already open. Select the requested company in Tally and restart the job.")
    else:
        desk.keyboard.type(company, delay=35)
        desk.keyboard.press("Enter")
    if not _tally_on_screen(desk):
        raise RuntimeError("Tally closed while opening the company — nothing was imported")
    wait_screen(desk, lambda text: compact(company) in compact(text) and "gateway of tally" in text.lower(), 30)
    step("company", f"Company visible: {company}", desk)
    return company


def compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def read_screen(desk):
    """Read the remote canvas, never infer import success from a window colour."""
    from PIL import Image, ImageOps
    png = desk.screenshot(type="png")
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = ImageOps.grayscale(image).resize((image.width * 2, image.height * 2))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "screen.png"
        image.save(path)
        result = subprocess.run(["tesseract", str(path), "stdout", "--psm", "11"],
                                capture_output=True, text=True, timeout=15, check=True)
    return result.stdout, base64.b64encode(png).decode()


def wait_screen(desk, predicate, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        text, png = read_screen(desk)
        if predicate(text):
            return text, png
        time.sleep(1)
    raise RuntimeError("Expected Tally screen was not verified; inspect the screenshot")


def result_counts(text):
    low = text.lower()
    if any(label in low for label in ("file to import", "list of file formats", "file path")):
        return None
    counts = {}
    for key in ("created", "altered", "errors", "ignored"):
        found = re.findall(r"\b" + key + r"\s*[:=]?\s*(\d+)\b", low)
        if len(found) != 1:
            return None
        counts[key] = int(found[0])
    return counts


def import_month(name):
    match = re.fullmatch(r"SFM_Tally_Import_(\d{2}-\d{2}-\d{4})_to_\d{2}-\d{2}-\d{4}(?:_job\d+)?\.xml", name)
    if not match:
        raise RuntimeError("Unexpected import filename")
    from datetime import datetime
    start = datetime.strptime(match[1], "%d-%m-%Y")
    return f"{MON[start.month - 1]} {start.year}"


def remote_type(desk, value):
    # Canvas RDP clients consume physical key events, not browser text insertion.
    shifted = {"_": "Minus", ":": "Semicolon"}
    for char in value:
        if char in shifted or char.isascii() and char.isupper():
            key = shifted.get(char, "Key" + char)
            desk.keyboard.down("Shift")
            try:
                desk.keyboard.press(key, delay=35)
            finally:
                desk.keyboard.up("Shift")
        else:
            key = {"\\": "Backslash", " ": "Space", ".": "Period", "-": "Minus"}.get(char, char)
            desk.keyboard.press(key, delay=35)


def exact_path_visible(text, path):
    # Preserve punctuation: underscores and hyphens are different directory names.
    return path.casefold() in " ".join(text.split()).casefold()


def replace_field(desk, value):
    # Tally fields do not reliably implement Windows Shift+End selection.
    # Home + Delete clears the focused field without Ctrl+A (which would submit).
    desk.keyboard.press("Home")
    for _ in range(260):
        desk.keyboard.press("Delete")
    remote_type(desk, value)


def replace_import_field(desk, value):
    # Home can navigate to File Format in this Tally form. Clear on both sides
    # of the current caret without any field-navigation or acceptance keys.
    for _ in range(260):
        desk.keyboard.press("Backspace")
    for _ in range(260):
        desk.keyboard.press("Delete")
    remote_type(desk, value)


def path_dialog(text):
    return "specify file path" in text.lower() or "specify folder path" in text.lower()


def path_error(text):
    return any(x in text.lower() for x in ("incorrect file", "invalid path", "path does not exist", "file not found"))


def format_list_open(text):
    return ("listoffileformats" in compact(text) or
            all(re.search(r"\b" + word + r"\b", text, re.I) for word in ("excel", "json", "xml")))


def select_import_path(desk, folder):
    text, _ = read_screen(desk)
    if not path_dialog(text):
        if format_list_open(text):
            raise RuntimeError("File Path is covered by a dropdown; stopped before typing")
        if exact_path_visible(text, folder):
            return
        click_setting(desk, ["File Path"], value_field=True)
        text, _ = read_screen(desk)
        if not path_dialog(text):
            if "specifypath" not in compact(text):
                raise RuntimeError("Tally did not show Specify Path; stopped before typing the folder")
            click_setting(desk, ["Specify Path"], activate=True, ocr_mode=11)
            wait_screen(desk, path_dialog, 10)
    replace_import_field(desk, folder)
    desk.keyboard.press("Enter")
    # Never type a filename while the path modal or its error is still open.
    def accepted(text):
        if path_error(text):
            raise RuntimeError(f"Tally rejected folder {folder}; filename was NOT entered")
        return not path_dialog(text) and exact_path_visible(text, folder)
    wait_screen(desk, accepted, 20)


def import_xml(desk, month, name, company, expected):
    if not _tally_on_screen(desk):
        raise RuntimeError("Tally is not on the screen — nothing was imported")
    desk.keyboard.press("Alt+O"); time.sleep(1)
    desk.keyboard.press("t")
    wait_screen(desk, lambda t: "importtransactions" in compact(t))
    if month != import_month(name):
        raise RuntimeError("Upload month does not match requested file date")
    select_import_path(desk, f"{IMPORT_ROOT}\\{month}")
    click_setting(desk, ["File to Import"], value_field=True)
    click_setting(desk, [name], activate=True, ocr_mode=11)
    def filled(text):
        value = compact(text)
        return (name.casefold() in text.casefold() and exact_path_visible(text, f"{IMPORT_ROOT}\\{month}")
                and compact(company) in value and not format_list_open(text)
                and not path_dialog(text) and not path_error(text))
    _, png = wait_screen(desk, filled, 15)
    step("awaiting_import", f"Review company {company}, file {name}, {expected} vouchers. "
         "Reply IMPORT to submit this file, or STOP to cancel. No import submitted yet.", png=png)
    if wait_answer("import") != "IMPORT":
        raise RuntimeError("Import cancelled — no import submitted")
    # Recheck after the owner replies: remote sessions can be changed by another user.
    text, _ = read_screen(desk)
    if not filled(text):
        raise RuntimeError("Import form changed after approval — no import submitted")
    # Persist an uncertainty marker BEFORE the irreversible keystroke. Never retry Accept.
    step("submitting", f"Submitting {name}; do not start another import until its result is checked.")
    desk.keyboard.press("Control+a")
    text, png = wait_screen(desk, lambda t: result_counts(t) is not None, 90)
    counts = result_counts(text)
    if counts["errors"] or counts["ignored"] or counts["created"] + counts["altered"] != expected:
        step("review_required", f"Tally result for {name}: {counts}. Expected {expected} vouchers. "
             "Check Day Book before retrying; a partial import may have occurred.", png=png)
        return
    from datetime import datetime
    dates = re.findall(r"\d{2}-\d{2}-\d{4}", name)
    d1, d2 = [datetime.strptime(v, "%d-%m-%Y").date() for v in dates]
    desk.keyboard.press("Escape")  # close the verified import statistics
    if not daybook_check(desk, company, d1, d2, done=False):
        step("review_required", "Import statistics were returned, but Day Book shows no entries. Check filters and vouchers before retrying.", desk)
        return
    step("import_verified", f"Tally result for {name}: created {counts['created']}, "
         f"altered {counts['altered']}, errors 0, ignored 0. Expected {expected} vouchers.",
         png=png, done=True, evidence={"counts": counts, "filename": name})


def date_visible(text, value):
    tokens = re.findall(r"\b(\d{1,2})[-/ .]([A-Za-z]{3,9}|\d{1,2})[-/ .](\d{2}|\d{4})\b", text)
    for d, m, y in tokens:
        month = int(m) if m.isdigit() else next((i+1 for i, name in enumerate(MON) if name.lower() == m[:3].lower()), 0)
        year = int(y) + (2000 if len(y) == 2 else 0)
        if (int(d), month, year) == (value.day, value.month, value.year):
            return True
    return False


def set_period(desk, d1, d2):
    desk.keyboard.press("Alt+F2")
    wait_screen(desk, lambda t: "period" in t.lower() and "from" in t.lower() and "to" in t.lower())
    replace_field(desk, d1.strftime("%d-%m-%Y")); desk.keyboard.press("Enter")
    replace_field(desk, d2.strftime("%d-%m-%Y")); desk.keyboard.press("Enter")


def gateway(desk, company):
    previous = None
    for _ in range(8):
        text, _ = read_screen(desk)
        if quit_dialog(text):
            text, _ = dismiss_quit(desk)
        low = text.lower()
        overlay = any(word in low for word in ("import transactions", "specify file path", "export configuration",
                                               "day book", "ledger vouchers", "account books", "display more reports"))
        if "gateway of tally" in low and not overlay:
            if compact(company) not in compact(text):
                raise RuntimeError("Gateway is visible but company could not be verified; stopped without pressing Esc")
            return
        fingerprint = compact(text)
        if fingerprint == previous:
            raise RuntimeError("Screen did not change after Esc; stopped before sending another Esc")
        previous = fingerprint
        desk.keyboard.press("Escape"); time.sleep(0.5)
    raise RuntimeError("Could not verify company Gateway before report shortcuts")


def return_to_gateway(desk, company=None):
    close_side_panel(desk)
    if company:
        gateway(desk, company)
    else:
        recover_tally_screen(desk)


def open_report(desk, report, company, d1, d2, ledger=None):
    gateway(desk, company)
    if ledger:
        for key in ("d", "a", "l"):
            desk.keyboard.press(key); time.sleep(0.7)
        wait_screen(desk, lambda t: "ledger" in t.lower())
        remote_type(desk, ledger); desk.keyboard.press("Enter")
    else:
        desk.keyboard.press("k")
    wait_screen(desk, lambda t: compact(report) in compact(t) and compact(company) in compact(t))
    if report == "Day Book" and d1 == d2:
        desk.keyboard.press("F2")
        wait_screen(desk, lambda t: "date" in t.lower())
        replace_field(desk, d1.strftime("%d-%m-%Y")); desk.keyboard.press("Enter")
    else:
        set_period(desk, d1, d2)
    def verified(text):
        return (compact(report) in compact(text) and compact(company) in compact(text)
                and date_visible(text, d1) and date_visible(text, d2)
                and "change period" not in text.lower()
                and (not ledger or compact(ledger) in compact(text)))
    return wait_screen(desk, verified)


def daybook_check(desk, company, d1, d2, done=True):
    text, png = open_report(desk, "Day Book", company, d1, d2)
    # Owner-requested quick check: inspect the dated report once, without opening
    # a voucher filter or paging through unrelated entries. Absence is view-scoped.
    if (not png or "day book" not in text.lower() or compact(company) not in compact(text)
            or not date_visible(text, d1) or not date_visible(text, d2)
            or any(label in text.lower() for label in
                   ("change voucher type", "name of voucher type", "voucher types", "change period", "change date"))):
        raise RuntimeError("Day Book is obscured or its date/company is unreadable; check the screenshot")
    found = bool(re.search(r"\b(?:HSD|MS)\b", text, re.I))
    if found:
        observation = "YES — HSD/MS data found in Day Book."
    else:
        observation = "NO DATA FOUND — no HSD/MS entries visible in this Day Book view."
    step("daybook_checked" if done else "daybook_evidence", f"{company}: {d1:%d/%m/%Y}–{d2:%d/%m/%Y}. " + observation,
         png=png, done=done)
    return found


def click_setting(desk, labels, value_field=False, activate=True, ocr_mode=6):
    """Locate the visible setting, rather than typing through guessed export fields."""
    from PIL import Image
    image = Image.open(io.BytesIO(desk.screenshot(type="png"))).convert("RGB")
    image = image.resize((image.width * 2, image.height * 2))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "settings.png"; image.save(path)
        result = subprocess.run(["tesseract", str(path), "stdout", "--psm", str(ocr_mode), "tsv"],
                                capture_output=True, text=True, timeout=15, check=True)
    rows = {}
    for word in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        if not word.get("text", "").strip():
            continue
        key = tuple(word[k] for k in ("block_num", "par_num", "line_num"))
        rows.setdefault(key, []).append(word)
    matches = []
    for words in rows.values():
        for label in labels:
            parts = label.lower().split()
            for i in range(len(words) - len(parts) + 1):
                group = words[i:i+len(parts)]
                if [compact(w["text"]) for w in group] == [compact(t) for t in parts]:
                    target = group[0]
                    if value_field:
                        values = [w for w in words[i+len(parts):] if w["text"].strip() not in (":", ";", "=")]
                        if not values:
                            continue
                        target = values[0]
                    matches.append((int(target["left"])/2, (int(group[0]["top"])+int(group[0]["height"])/2)/2))
    if len(set(matches)) != 1:
        raise RuntimeError("Could not uniquely locate setting: " + "/".join(labels))
    x, y = matches[0]
    desk.mouse.click(x+4, y)
    if value_field:
        # Clicking the label then Enter used to advance into the backup field.
        # Click the value itself, never Enter, and prove focus stays on that row.
        time.sleep(0.3)
        focused = Image.open(io.BytesIO(desk.screenshot(type="png"))).convert("RGB")
        yellow = 0
        for yy in range(max(0, int(y)-3), min(focused.height, int(y)+4)):
            for xx in range(max(0, int(x)), min(focused.width, int(x)+100)):
                r, g, b = focused.getpixel((xx, yy))
                yellow += r > 170 and g > 130 and b < g * 0.88
        if yellow < 35:
            raise RuntimeError("Requested import field is not visibly focused; stopped before typing: " + labels[0])
    elif activate:
        desk.keyboard.press("Enter"); time.sleep(0.5)


def configure_pdf(desk, pdf_name):
    click_setting(desk, ["File Format"])
    desk.keyboard.type("PDF", delay=40); desk.keyboard.press("Enter")
    click_setting(desk, ["Folder Path", "File Path"])
    replace_field(desk, r"D:\My_Files"); desk.keyboard.press("Enter")
    text, _ = read_screen(desk)
    if path_error(text) or path_dialog(text):
        raise RuntimeError("PDF destination was not accepted")
    click_setting(desk, ["File Name", "Filename"])
    replace_field(desk, pdf_name); desk.keyboard.press("Enter")
    desk.keyboard.press("Control+a")  # accepts export configuration, not vouchers
    wait_screen(desk, lambda t: "export" in t.lower() and "pdf" in t.lower()
                and compact(pdf_name) in compact(t) and compact(r"D:\My_Files") in compact(t))
    desk.keyboard.press("e")


def verify_pdf(data, ledger, company, d1, d2):
    if not data.startswith(b"%PDF-"):
        raise RuntimeError("Downloaded file is not a PDF")
    if importlib.util.find_spec("pypdf") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pypdf==5.1.0"], check=True)
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not (compact(ledger) in compact(text) and compact(company) in compact(text)
            and date_visible(text, d1) and date_visible(text, d2)):
        raise RuntimeError("PDF ledger/company/period could not be verified; file was not sent")


def ledger_pdf(desk, ledger, d1, d2, company):
    text, png = open_report(desk, "Ledger Vouchers", company, d1, d2, ledger=ledger)
    step("ledger", f"Verified ledger view: {ledger}, {company}, {d1:%d/%m/%Y}–{d2:%d/%m/%Y}", png=png)
    desk.keyboard.press("Control+e")
    wait_screen(desk, lambda t: "export" in t.lower())
    desk.keyboard.press("c")
    wait_screen(desk, lambda t: "file format" in t.lower())
    pdf_name = f"{re.sub(r'[^A-Za-z0-9_-]', '_', ledger)}_{d1:%d%m%y}_{d2:%d%m%y}_job{JOB}.pdf"
    configure_pdf(desk, pdf_name)
    side_button(desk, "File Manager")
    fm = next((f for f in desk.frames if _safe(lambda: f.get_by_text("Drop files here").count() > 0, False)), ui(desk))
    fm.get_by_text("My_Files", exact=True).first.dblclick(); time.sleep(2)
    with desk.expect_download(timeout=120000) as dl:
        row = fm.get_by_text(pdf_name, exact=True).first
        row.wait_for(timeout=120000)
        row.hover()
        container = row.locator("xpath=ancestor::*[.//button[contains(@title,'ownload') or contains(@aria-label,'ownload')]][1]")
        button = container.locator("button[title*='ownload'], button[aria-label*='ownload']")
        if button.count() != 1:
            raise RuntimeError("Could not identify the download button for the requested PDF")
        button.click()
    path = dl.value.path()
    data = Path(path).read_bytes()
    verify_pdf(data, ledger, company, d1, d2)
    response = api("/file", method="POST", files={"file": (pdf_name, data, "application/pdf")}).json()
    if not response.get("ok"):
        raise RuntimeError("WhatsApp rejected the PDF; delivery is not confirmed")
    step("pdf", f"Ledger PDF sent: {pdf_name}", None, done=True)


def ensure_runtime():
    """Support the existing Actions workflow without requiring workflow-write permission."""
    if importlib.util.find_spec("PIL") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pillow==11.0.0"], check=True)
    if not shutil.which("tesseract"):
        if os.environ.get("GITHUB_ACTIONS") != "true" or not shutil.which("sudo"):
            raise RuntimeError("Tesseract OCR is required. Install tesseract-ocr before running the agent.")
        subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True, timeout=180)
        subprocess.run(["sudo", "apt-get", "install", "-y", "tesseract-ocr"], check=True, timeout=180)
    if not shutil.which("tesseract"):
        raise RuntimeError("Tesseract OCR installation did not complete")


def main():
    global JOB
    started = time.monotonic()
    if os.environ["JOB_ID"].endswith(":retry-login"):
        job = api("/retry-login", method="POST").json()["job"]
        JOB = job["id"]
    job = api("").json().get("job")
    if job and job["state"] != "starting":
        sys.exit("job already started or finished; refusing to replay it")
    if not job:
        sys.exit("job not found")
    if not api("/claim", method="POST").json().get("ok"):
        sys.exit("job could not be claimed")
    p = job["params"]
    try:
        ensure_runtime()
    except Exception:
        step("error", "Robot setup failed before login. No import was attempted.", failed=True)
        raise
    with sync_playwright() as pw:
        browser = None
        page = desk = None
        company = None
        try:
            browser = pw.chromium.launch()
            ctx = browser.new_context(accept_downloads=True)
            page = login(ctx)
            desk = connect(page)
            if job["kind"] == "import":
                r = api("/xml")
                if r.status_code != 200:
                    step("xml", "XML blocked: " + "; ".join(r.json().get("errors", [])[:5]), failed=True); return
                month = upload_xml(desk, p["name"], r.text)
                company = open_tally(desk)
                import_xml(desk, month, p["name"], company, p["vouchers"])
            else:
                company = open_tally(desk)
                d1, d2 = date.fromisoformat(p["from"]), date.fromisoformat(p["to"])
                if p.get("report") == "daybook":
                    daybook_check(desk, company, d1, d2)
                else:
                    ledger_pdf(desk, p["ledger"], d1, d2, company)
            return_to_gateway(desk, company)
            # Reuse the authenticated desktop for read-only follow-up reports.
            # Leave room inside the existing 25-minute Actions timeout.
            while time.monotonic() - started < 19 * 60:
                anchor = JOB
                idle_until = min(time.monotonic() + 5 * 60, started + 19 * 60)
                follow = None
                while time.monotonic() < idle_until:
                    response = api("/session", method="POST", json={}).json()
                    if not response.get("ok"):
                        break
                    follow = response.get("job")
                    if follow:
                        break
                    time.sleep(3)
                if not follow:
                    api("/session", method="POST", json={"close": True})
                    break
                JOB = follow["id"]
                p = follow["params"]
                close_side_panel(desk)
                step("reuse", f"Using the existing session for {company}; no login repeated.")
                d1, d2 = date.fromisoformat(p["from"]), date.fromisoformat(p["to"])
                if p.get("report") == "daybook":
                    daybook_check(desk, company, d1, d2)
                else:
                    ledger_pdf(desk, p["ledger"], d1, d2, company)
                return_to_gateway(desk, company)
        except Exception as e:
            try:
                step("error", f"Stopped: {type(e).__name__}: {str(e)[:200]}", desk or LAST_DESK, failed=True)
            except Exception:
                print("Failure status could not be delivered (or job already expired); see tally status.", flush=True)
            raise
        finally:
            # Capture/deliver any failure screenshot above before leaving that screen.
            if desk is not None:
                try:
                    return_to_gateway(desk, company)
                except Exception:
                    print("Could not verify Gateway during cleanup; no further keys sent.", flush=True)
            if browser is not None:
                browser.close()


if __name__ == "__main__":
    main()
