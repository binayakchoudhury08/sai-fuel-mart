import asyncio, json, os, glob
from playwright.async_api import async_playwright
BASE = "http://127.0.0.1:5075"
ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
async def login(pg):
    await pg.goto(BASE + "/login"); await pg.fill("#u", "boss"); await pg.fill("#p", "Pump@2026x"); await pg.click("button")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                                    args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        # ---- phone
        ctx = await b.new_context(user_agent=ANDROID, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, service_workers="block",
                                  permissions=["camera", "microphone", "geolocation"], geolocation={"latitude": 22.654312, "longitude": 88.876543, "accuracy": 9})
        pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await login(pg)
        await pg.goto(BASE + "/proofs"); await pg.screenshot(path="/tmp/ph_register_before.png")
        print("phone register shows 'Take proof':", await pg.locator("text=Take proof").count() > 0)
        await pg.click(".typebtn:has-text('MS Dip Check')")
        await pg.wait_for_function("document.getElementById('video').videoWidth > 0", timeout=15000)
        print("live camera running in page:", True, "| file picker hidden:", await pg.locator("#fallback").is_hidden())
        await pg.wait_for_function("document.getElementById('gps').textContent.indexOf('GPS') === 0", timeout=10000)
        print("GPS badge:", await pg.inner_text("#gps"))
        await pg.screenshot(path="/tmp/ph_camera.png")
        # record a 3 s video, then take the photo
        await pg.click("#rec"); await pg.wait_for_timeout(3200); await pg.click("#rec")
        await pg.wait_for_selector("#videoinfo:not([hidden])", timeout=10000)
        print("video:", await pg.inner_text("#vlabel"))
        await pg.wait_for_function("document.getElementById('video').videoWidth > 0", timeout=15000)
        await pg.click("#shoot"); await pg.wait_for_selector("#step-save:not([hidden])", timeout=10000)
        await pg.select_option("#status", "Available"); await pg.fill("#note", "MS dip 1240 mm")
        await pg.screenshot(path="/tmp/ph_preview.png", full_page=True)
        await pg.click("#upload"); await pg.wait_for_url("**/proofs?d=*", timeout=20000)
        await pg.screenshot(path="/tmp/ph_register_after.png", full_page=True)
        print("after save → register; card shown:", await pg.locator(".proofcard").count())
        print("JS errors:", errs or "none")
        # ---- desktop must not be able to take proofs
        d = await b.new_context(user_agent=DESKTOP, viewport={"width": 1366, "height": 800}, service_workers="block")
        dp = await d.new_page(); await login(dp); await dp.goto(BASE + "/proofs/new")
        print("PC: camera page refuses:", await dp.locator("text=Open this on your phone").count() > 0, "| no camera:", await dp.locator("#video").count() == 0)
        await dp.goto(BASE + "/proofs"); await dp.screenshot(path="/tmp/pc_register.png")
        await b.close()
asyncio.run(main())
