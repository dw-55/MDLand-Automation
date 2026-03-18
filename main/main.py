from itertools import count
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

def login():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://login.mdland.com/login_central.aspx")
        print(page.title())
        page.fill("#id", os.getenv("MDLAND_USERNAME"))
        page.fill("#password", os.getenv("MDLAND_PASSWORD"))
        page.click("#butlogin")
        page.wait_for_url("**/clinic_main.aspx")
        page.wait_for_load_state("domcontentloaded")
        print(page.url)
        page.evaluate("loadLabOrderList()")
        frame = page.frame(name="workarea0")
        frame.select_option("select[name='labtype']", value = "1104")
        # page.wait_for_timeout(2000)

        # page.wait_for_function("() => document.querySelector('iframe[name=\"labFrame\"]').contentDocument.body.innerHTML.includes('reportdiv')")
        lab_frame = page.frame(name="labFrame")
        count = lab_frame.locator("a[id^='reportdiv_']").count()
        # content = lab_frame.content()
        # print("reportdiv found:", "reportdiv" in content)
        # print("Requisition found:", "Requisition" in content)

        # for f in page.frames:
        #     if "reportdiv" in f.content():
        #         print(f"FOUND IT! Frame name: {f.name} | url: {f.url}")

        with page.expect_popup() as popup_info:
            lab_frame.locator("a[id^='reportdiv_']").first.scroll_into_view_if_needed()
            lab_frame.locator("a[id^='reportdiv_']").first.click()
        popup_page = popup_info.value
        popup_page.wait_for_load_state("domcontentloaded")

        with popup_page.expect_popup() as print_popup_info:
            popup_page.click("a[href=\"javascript:showep('Print')\"]")
        print_page = print_popup_info.value
        print_page.wait_for_load_state("domcontentloaded")
        print_page.pdf(path="lab_report.pdf")

        # popup_page.click("a[href=\"javascript:showep('Print')\"]")
        # popup_page.pdf(path="lab_report.pdf")

        input("Press Enter to close the browser...")
        
        # browser.close()

if __name__ == "__main__":
    login()