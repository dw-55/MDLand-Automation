import os
from dotenv import load_dotenv

load_dotenv()

def login(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.goto(
        "https://login.mdland.com/login_central.aspx",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_selector("#id")
    page.fill("#id", os.getenv("MDLAND_USERNAME"))
    page.fill("#password", os.getenv("MDLAND_PASSWORD"))
    page.click("#butlogin", no_wait_after=True)
    page.wait_for_timeout(2000)
    dismiss_change_password_prompt(page)
    page.wait_for_url("**/clinic_main.aspx")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector("#mainmenubutton")
    return browser, page

def dismiss_change_password_prompt(page):
    change_pwd_frame = page.frame_locator("#changePWDFrame")
    change_later_button = change_pwd_frame.locator("text=Change Later")
    if change_later_button.count():
        change_later_button.click()
