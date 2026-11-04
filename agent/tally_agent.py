"""Tally Cloud robot (runs on GitHub Actions). Every step reports to the website, which sends it to the owner's
WhatsApp with a screenshot. The secure PIN and the company name come from the owner on WhatsApp, at the moment needed.

Steps (import):  login (email, password, PIN) → Connect → File Manager → D:\\My_Applications\\SFM_TALLY_IMPORT\\<MON YYYY>
→ upload the XML → open TallyPrime → company (asked on WhatsApp) → O: Import → Transactions → path + file → Accept.
Steps (ledger):  login → Connect → TallyPrime → company → Go To Ledger Vouchers → ledger + period → export PDF to D:
→ download it from File Manager → send to WhatsApp.
"""
import base64
import os
import sys
import time
from datetime import date

import requests
from playwright.sync_api import sync_playwright

SITE, SECRET, JOB = os.environ["SFM_SITE"].rstrip("/"), os.environ["SFM_SECRET"], int(os.environ["JOB_ID"])
H = {"x-cron-secret": SECRET}
LOGIN = "https://in.cloudaccess.tallysolutions.com/login"
IMPORT_ROOT = r"D:\My_Applications\SFM_TALLY_IMPORT"
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def api(path, **kw):
    r = requests.request(kw.pop("method", "GET"), f"{SITE}/api/tally/job/{JOB}{path}", headers=H, timeout=60, **kw)
    return r


def step(name, message, page=None, done=False, failed=False):
    png = None
    if page is not None:
        try:
            png = base64.b64encode(page.screenshot(type="png")).decode()
        except Exception:
            png = None
    api("/step", method="POST", json={"step": name, "message": message, "png": png, "done": done, "failed": failed})
    print(f"[{name}] {message}", flush=True)


def wait_answer(field, minutes=10):
    end = time.time() + minutes * 60
    while time.time() < end:
        r = api(f"/wait/{field}").json()
        if r.get("state") == "ok":
            return r["answer"]
        if r.get("state") in ("expired", "missing"):
            break
        time.sleep(3)
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
    step("login", "Login successful", page)
    return page


def connect(page):
    with page.context.expect_page(timeout=600000) as newp:
        page.get_by_text("Connect", exact=True).click()
    desk = newp.value
    desk.wait_for_load_state("domcontentloaded")
    desk.set_viewport_size({"width": 1600, "height": 900})
    for _ in range(100):                                    # the virtual computer takes up to ~5 minutes to start
        if desk.locator("canvas").count() and "cloudaccess" in desk.url:
            break
        time.sleep(3)
    time.sleep(10)
    dismiss_popups(desk)
    step("connect", "Virtual computer connected", desk)
    return desk


def dismiss_popups(desk):
    """Tally Cloud shows "Auto-shutdown on timeout" / "Scheduled shutdown" alerts after connecting — press OK."""
    for _ in range(4):
        hit = False
        for label in ("Okay", "OK", "Ok"):
            b = desk.get_by_role("button", name=label)
            if b.count():
                try:
                    b.first.click(timeout=2000); hit = True; time.sleep(1)
                except Exception:
                    pass
        if not hit:
            desk.mouse.click(800, 450); desk.keyboard.press("Enter"); time.sleep(1)
            break


SIDE = {"power": 0, "fullscreen": 1, "notifications": 2, "File Manager": 3}


def side_button(desk, title):
    """The side panel opens with the « arrow at the right edge; its buttons have no text until hovered, so they are
    clicked by position (File Manager = 4th, ~90 px apart)."""
    vw, vh = desk.viewport_size["width"], desk.viewport_size["height"]
    btn = desk.locator(f"[title='{title}'], [aria-label='{title}']")
    if not btn.count():
        desk.mouse.click(vw - 55, vh // 2); time.sleep(1.5)          # «  open the panel
        btn = desk.locator(f"[title='{title}'], [aria-label='{title}']")
    if btn.count():
        btn.first.click()
    else:
        desk.mouse.click(vw - 65, 55 + 90 * SIDE[title]); time.sleep(2)
    if not desk.get_by_text("File Manager").count() and title == "File Manager":
        raise RuntimeError("the File Manager did not open")


def upload_xml(desk, name, xml):
    side_button(desk, "File Manager")
    fm = desk
    month = f"{MON[int(name.split('_to_')[0][-7:-5]) - 1]} {name.split('_to_')[0][-4:]}"
    for part in ("My_Applications", "SFM_TALLY_IMPORT"):
        fm.get_by_text(part, exact=True).first.dblclick(); time.sleep(2)
    if not fm.get_by_text(month, exact=True).count():          # new month: create the folder
        fm.locator("button[title*='older'], button[aria-label*='older']").first.click()
        fm.keyboard.type(month); fm.keyboard.press("Enter"); time.sleep(2)
    fm.get_by_text(month, exact=True).first.dblclick(); time.sleep(2)
    path = f"/tmp/{name}"
    open(path, "w", encoding="utf8").write(xml)
    fm.locator("input[type=file]").first.set_input_files(path)
    fm.get_by_text("Upload", exact=False).last.click()
    fm.get_by_text(name).first.wait_for(timeout=120000)
    step("upload", f"Uploaded to {IMPORT_ROOT}\\{month}\\{name}", fm)
    desk.keyboard.press("Escape")
    return month


def open_tally(desk):
    desk.mouse.dblclick(80, 270)                               # TallyPrime icon on the virtual desktop
    time.sleep(25)
    step("tally", "TallyPrime opened", desk)
    company = wait_answer("company")
    desk.keyboard.type(company); time.sleep(2); desk.keyboard.press("Enter"); time.sleep(8)
    step("company", f"Company opened: {company}", desk)


def import_xml(desk, month, name):
    desk.keyboard.press("Alt+O"); time.sleep(2)                 # O: Import
    desk.keyboard.press("t"); time.sleep(3)                     # Transactions
    for value in ("XML (Data Interchange)", f"{IMPORT_ROOT}\\{month}", name, "Yes"):
        desk.keyboard.type(value); desk.keyboard.press("Enter"); time.sleep(1.5)
    desk.keyboard.press("Control+a"); time.sleep(20)            # A: Accept
    step("import", f"Imported {name} — check the counts on the screenshot", desk, done=True)


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
    desk.get_by_text("My_Files", exact=True).first.dblclick(); time.sleep(2)
    with desk.expect_download(timeout=120000) as dl:
        row = desk.get_by_text(pdf_name).first
        row.hover()
        desk.locator("button[title*='ownload'], [aria-label*='ownload']").first.click()
    path = dl.value.path()
    api("/file", method="POST", files={"file": (pdf_name, open(path, "rb"), "application/pdf")})
    step("pdf", f"Ledger PDF sent: {pdf_name}", None, done=True)


def main():
    job = api("").json().get("job")
    if not job:
        sys.exit("job not found")
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
                open_tally(desk)
                import_xml(desk, month, p["name"])
            else:
                open_tally(desk)
                ledger_pdf(desk, p["ledger"], date.fromisoformat(p["from"]), date.fromisoformat(p["to"]))
        except Exception as e:
            step("error", f"Stopped: {type(e).__name__}: {str(e)[:200]}", desk or page, failed=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
