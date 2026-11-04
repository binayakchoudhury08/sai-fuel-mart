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
from pathlib import Path
from datetime import date

import requests
from playwright.sync_api import sync_playwright

SITE, SECRET, JOB = os.environ["SFM_SITE"].rstrip("/"), os.environ["SFM_SECRET"], int(os.environ["JOB_ID"])
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
    month = f"{MON[int(name.split('_to_')[0][-7:-5]) - 1]} {name.split('_to_')[0][-4:]}"
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
    desk.keyboard.press("Escape"); time.sleep(0.5)


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


def import_xml(desk, month, name, company, expected):
    if not _tally_on_screen(desk):
        raise RuntimeError("Tally is not on the screen — nothing was imported")
    desk.keyboard.press("Alt+O"); time.sleep(1)
    desk.keyboard.press("t")
    wait_screen(desk, lambda t: "file format" in t.lower())
    # Do not type the long display label into Tally's format selector.
    desk.keyboard.type("XML", delay=40)
    desk.keyboard.press("Enter"); time.sleep(1)
    for value in (f"{IMPORT_ROOT}\\{month}", name):
        desk.keyboard.press("Home")
        desk.keyboard.press("Shift+End")
        desk.keyboard.type(value, delay=25)
        desk.keyboard.press("Enter"); time.sleep(1)
    def filled(text):
        value = compact(text)
        return (compact(name) in value and compact(f"{IMPORT_ROOT}\\{month}") in value
                and compact(company) in value and "xml" in value
                and "list of file formats" not in text.lower())
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
    step("import_verified", f"Tally result for {name}: created {counts['created']}, "
         f"altered {counts['altered']}, errors 0, ignored 0. Expected {expected} vouchers.",
         png=png, done=True, evidence={"counts": counts, "filename": name})


def ledger_pdf(desk, ledger, d1, d2):
    desk.keyboard.press("Alt+G"); time.sleep(2); desk.keyboard.type("Ledger Vouchers"); desk.keyboard.press("Enter"); time.sleep(2)
    desk.keyboard.type(ledger); time.sleep(1); desk.keyboard.press("Enter"); time.sleep(4)
    desk.keyboard.press("F2"); time.sleep(1)
    desk.keyboard.type(d1.strftime("%d-%m-%Y")); desk.keyboard.press("Enter"); desk.keyboard.type(d2.strftime("%d-%m-%Y")); desk.keyboard.press("Enter"); time.sleep(4)
    step("ledger", f"Ledger {ledger} {d1:%d/%m}–{d2:%d/%m}", desk)
    desk.keyboard.press("Alt+E"); time.sleep(2); desk.keyboard.press("c"); time.sleep(2)   # Export → Current
    pdf_name = f"{ledger.replace(' ', '_')}_{d1:%d%m%y}_{d2:%d%m%y}.pdf"
    desk.keyboard.type("PDF"); desk.keyboard.press("Enter"); desk.keyboard.type(r"D:\My_Files"); desk.keyboard.press("Enter")
    desk.keyboard.type(pdf_name); desk.keyboard.press("Enter"); desk.keyboard.press("Control+a"); time.sleep(10)
    side_button(desk, "File Manager")
    fm = next((f for f in desk.frames if _safe(lambda: f.get_by_text("Drop files here").count() > 0, False)), ui(desk))
    fm.get_by_text("My_Files", exact=True).first.dblclick(); time.sleep(2)
    with desk.expect_download(timeout=120000) as dl:
        row = fm.get_by_text(pdf_name).first
        row.hover()
        fm.locator("button[title*='ownload'], [aria-label*='ownload']").first.click()
    path = dl.value.path()
    api("/file", method="POST", files={"file": (pdf_name, open(path, "rb"), "application/pdf")})
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
    ensure_runtime()
    job = api("").json().get("job")
    if job and job["state"] != "starting":
        sys.exit("job already started or finished; refusing to replay it")
    if not job:
        sys.exit("job not found")
    if not api("/claim", method="POST").json().get("ok"):
        sys.exit("job could not be claimed")
    p = job["params"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(accept_downloads=True)
        page = desk = None
        try:
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
                open_tally(desk)
                ledger_pdf(desk, p["ledger"], date.fromisoformat(p["from"]), date.fromisoformat(p["to"]))
        except Exception as e:
            step("error", f"Stopped: {type(e).__name__}: {str(e)[:200]}", desk or LAST_DESK or page, failed=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
