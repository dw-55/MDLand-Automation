import re
from playwright.sync_api import Page, expect

def test_title(page: Page):
    page.goto("https://www.mdland.com")
    expect(page).to_have_title(re.compile(r"mdland"))
    
def test_labOrder_link(page: Page):
    page.goto("https://www.mdland.com")
    expect(page.locator("text=Start")).to_be_visible()