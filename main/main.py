import os
import re
import smtplib
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.mdland import login

load_dotenv()

LAB_REPORTS_DIR = PROJECT_ROOT / "labReports"


def get_int_env(name, default):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def send_email(pdf_path, patient_name, dob):
    sender = os.getenv("EMAIL_USERNAME")
    password = os.getenv("EMAIL_PASSWORD")
    recipient = os.getenv("TEST_EMAIL_RECIPIENT")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"Lab Report - {patient_name} DOB: {dob}"

    body = """To Whom It May Concern,

Please contact the patient to schedule an appointment.

The patient is Chinese-speaking only !

Thank you."""
    msg.attach(MIMEText(body, "plain"))

    with open(pdf_path, "rb") as f:
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", f'attachment; filename="{pdf_path}"')
        msg.attach(attachment)

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
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
    report_page.wait_for_load_state("domcontentloaded")
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
        LAB_REPORTS_DIR.mkdir(exist_ok=True)
        pdf_path = LAB_REPORTS_DIR / f"lab_report_{patient_name}_{report_id}.pdf"
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes[-1])
        print(f"Saved {len(pdf_bytes[-1])} bytes to {pdf_path}")
        send_email(pdf_path, patient_name, dob)
    else:
        print("No PDF response captured")
        print(f"Report page URL: {report_page.url}")
        print(f"Print popup URL: {print_page.url}")


def run():
    with sync_playwright() as playwright:
        browser, page = login(playwright)
        print(page.url)
        page.evaluate("loadLabOrderList()")
        frame = page.frame(name="workarea0")
        frame.select_option("select[name='labtype']", value="1104")
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
            popup_page = popup_info.value

            content = popup_page.inner_text("body")
            match = re.search(r"DOB:\s*(\d{2}/\d{2}/\d{4})", content)
            dob = None
            if match:
                dob = match.group(1)
            save_pdf_from_print_flow(popup_page, report_id, patient_name, dob)

        input("Press Enter to close the browser...")


if __name__ == "__main__":
    run()
