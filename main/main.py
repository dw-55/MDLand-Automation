import os
import re
import smtplib
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

load_dotenv()


def get_int_env(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def dismiss_change_password_prompt(page):
    change_pwd_frame = page.frame_locator("#changePWDFrame")
    change_later_button = change_pwd_frame.locator("text=Change Later")

    if change_later_button.count():
        change_later_button.click()

def send_email(pdf_path, patient_name, dob):
    sender = os.getenv("EMAIL_USERNAME")
    password = os.getenv("EMAIL_PASSWORD")
    recipient = os.getenv("TEST_EMAIL_RECIPIENT")

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = f"Lab Report - {patient_name} DOB: {dob}"

    body = """To Whom It May Concern,

Please contact the patient to schedule an appointment.

The patient is Chinese-speaking only !

Thank you."""
    # add body text
    msg.attach(MIMEText(body, 'plain'))

    # attach the PDF
    with open(pdf_path, 'rb') as f:
        attachment = MIMEBase('application', 'octet-stream')
        attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header('Content-Disposition', f'attachment; filename="{pdf_path}"')
        msg.attach(attachment)

    # send it
    with smtplib.SMTP('smtp.gmail.com', 587) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())



def apply_date_filter(frame):
    start_override = os.getenv("MDLAND_DATE_FROM", "").strip()
    end_override = os.getenv("MDLAND_DATE_TO", "").strip()
    lookback_days = get_int_env("MDLAND_LOOKBACK_DAYS", 0)

    if start_override and end_override:
        start_date = start_override
        end_date = end_override
    elif lookback_days > 0:
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%m/%d/%Y")
        end_date = datetime.now().strftime("%m/%d/%Y")
    else:
        print("Using MDLand's existing date filters")
        return

    from_input = frame.locator("input[name='listFrom']")
    to_input = frame.locator("input[name='listTo']")
    from_input.wait_for(state="visible")
    to_input.wait_for(state="visible")
    from_input.fill(start_date)
    to_input.fill(end_date)
    from_input.press("Tab")
    to_input.press("Tab")
    print(f"Applied date filter: {start_date} to {end_date}")

def save_pdf_from_print_flow(report_page, report_id, patient_name, dob):
    report_page.wait_for_load_state("domcontentloaded")  # just this is enough
    report_page.bring_to_front()

    pdf_bytes = []

    def handle_response(response):
        content_type = response.headers.get("content-type", "").lower()
        if response.status == 200 and "application/pdf" in content_type:
            pdf_bytes.append(response.body())
            print(f"Captured PDF response from {response.url}")

    report_page.context.on("response", handle_response)

    print_button = report_page.locator("a[href=\"javascript:showep('Print')\"]")
    print_button.wait_for(state="visible")

    with report_page.expect_popup() as print_popup_info:
        print_button.click()
    print_page = print_popup_info.value
    print_page.wait_for_load_state("domcontentloaded")
    print_page.wait_for_timeout(3000)

    if pdf_bytes:
        filename = f"lab_report_{patient_name}_{report_id}.pdf" 
        with open(filename, "wb") as f:
            f.write(pdf_bytes[-1])
        print(f"Saved {len(pdf_bytes[-1])} bytes to {filename}")
        send_email(filename, patient_name, dob)
    else:
        print("No PDF response captured")
        print(f"Report page URL: {report_page.url}")
        print(f"Print popup URL: {print_page.url}")


def login():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://login.mdland.com/login_central.aspx")
        print(page.title())
        page.fill("#id", os.getenv("MDLAND_USERNAME"))
        page.fill("#password", os.getenv("MDLAND_PASSWORD"))
        page.click("#butlogin")
        page.wait_for_timeout(2000)
        dismiss_change_password_prompt(page)
        page.wait_for_url("**/clinic_main.aspx")
        page.wait_for_load_state("domcontentloaded")
        print(page.url)
        page.evaluate("loadLabOrderList()")
        frame = page.frame(name="workarea0")
        frame.select_option("select[name='labtype']", value = "1104")
        apply_date_filter(frame)
        frame.locator("text=Refresh").click()
        page.wait_for_timeout(2000)

        lab_frame = page.frame(name="labFrame")
        requisition_links = lab_frame.locator("a[id^='reportdiv_']")
        requisition_links.first.wait_for(state="visible")

        report_count = requisition_links.count()
        
        print(f"Found {report_count} requisition links")

        for i in range(report_count):
            element = requisition_links.nth(i)
            full_id = element.get_attribute("id")
            report_id = full_id.split("_")[1]
            patient_name = lab_frame.locator(f"#span_patientname_{i}").inner_text().strip()

            with page.expect_popup() as popup_info:
                element.scroll_into_view_if_needed()
                element.click()
                # lab_frame.locator("a[id^='reportdiv_']").nth(i).scroll_into_view_if_needed()
                # lab_frame.locator("a[id^='reportdiv_']").nth(i).click() 
            popup_page = popup_info.value

            content = popup_page.inner_text("body")
            match = re.search(r"DOB:\s*(\d{2}/\d{2}/\d{4})", content)
            dob = None
            if match:
                dob = match.group(1)
            save_pdf_from_print_flow(popup_page, report_id, patient_name, dob)


        input("Press Enter to close the browser...")


if __name__ == "__main__":
    login()
