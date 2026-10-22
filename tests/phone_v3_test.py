import asyncio
from playwright.async_api import async_playwright
B = "http://127.0.0.1:5075"
ANDROID = "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                                    args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        ctx = await b.new_context(user_agent=ANDROID, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, service_workers="block",
                                  permissions=["camera", "microphone", "geolocation"], geolocation={"latitude": 22.6543, "longitude": 88.8765, "accuracy": 8})
        pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B + "/login"); await pg.fill("#u", "mgr2"); await pg.fill("#p", "Mgr@123456x"); await pg.click("button")
        # More menu on phone
        await pg.goto(B + "/dashboard"); await pg.click("label[for=more][role=button]")
        vis = await pg.locator(".sheet").is_visible(); tiles = await pg.locator(".sheet .tile").count()
        print("More menu opens on phone:", vis, "| tiles:", tiles); await pg.screenshot(path="/tmp/v3_more.png")
        await pg.locator(".sheet .tile:has-text('Lube stock')").click(); await pg.wait_for_url("**/lube**")
        print("tile navigates and sheet closes:", not await pg.locator(".sheet").is_visible())
        # attendance with live photo
        await pg.goto(B + "/staff"); await pg.screenshot(path="/tmp/v3_att_before.png", full_page=True)
        btn = pg.locator("[data-snap]").first; name = await btn.get_attribute("data-name"); await btn.click()
        await pg.wait_for_function("document.getElementById('camvideo').videoWidth > 0", timeout=15000)
        await pg.click("#camshoot"); await pg.wait_for_function("!document.getElementById('camsave').disabled", timeout=8000)
        await pg.screenshot(path="/tmp/v3_att_cam.png")
        await pg.click("#camsave"); await pg.wait_for_load_state("load"); await pg.wait_for_timeout(1500)
        card = pg.locator(".staffcard", has_text=name)
        print(f"attendance: {name} →", await card.locator(".badge").first.inner_text(), "| photo shown:", await card.locator("img.face").count() == 1)
        await pg.screenshot(path="/tmp/v3_att_after.png", full_page=True)
        # proof with direct upload
        await pg.goto(B + "/proofs"); await pg.click(".typebtn:has-text('HSD Dip Check')")
        await pg.wait_for_function("document.getElementById('video').videoWidth > 0", timeout=15000)
        await pg.click("#shoot"); await pg.wait_for_selector("#step-save:not([hidden])"); await pg.fill("#note", "HSD 1500 mm")
        await pg.click("#upload"); await pg.wait_for_url("**/proofs?d=*", timeout=20000)
        print("proof saved via direct upload; cards:", await pg.locator(".proofcard").count())
        src = await pg.locator(".proofcard img").first.get_attribute("src")
        r = await pg.request.get(B + src, max_redirects=0)
        print("photo link → private signed link:", r.status, "→", (r.headers.get("location") or "")[:60])
        print("JS errors:", errs or "none")
        await b.close()
asyncio.run(main())
