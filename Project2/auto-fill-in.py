import os
import re
import smtplib
import sys
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shared.mdland import login

TEST_DATE_FROM = "06/12/2026"
TEST_DATE_TO = "06/13/2026"
TARGET_LAB_PATTERNS = {
    "Quest": "Quest Lab Report",
    "Sherman": "Sherman Abrams Labs",
    "Sunrise": "Sunrise",
    "BrLab": "BrLab",
    "MainStreet": "MainStreet",
}
TARGET_TEST_NAME = "CHOLESTEROL, TOTAL"
TARGET_TEST_NAME_UPPER = TARGET_TEST_NAME.upper()
CHOLESTEROL_TEST_NAMES = {"CHOLESTEROL, TOTAL", "CHOLESTEROL"}
HDL_TEST_NAMES = {"HDL CHOLESTEROL, TOTAL", "HDL CHOLESTEROL", "HDL CHOL., DIRECT"}
LDL_TEST_NAMES = {"LDL (CALCULATED)", "LDL-CHOLESTEROL", "LDL CHOL, CALCULATED", "LDL CHOLESTEROL"}
TRIGLYCERIDES_TEST_NAMES = {"TRIGLYCERIDES"}
HEMOGLOBIN_A1C_TEST_NAMES = {"HEMOGLOBIN A1C"}
EGFR_TEST_NAMES = {"EGFR", "E-GFR", "EGFR (CKD-EPI EQUATION)"}
URINE_AC_TEST_NAMES = {
    "CREATININE, RANDOM URINE",
    "ALBUMIN/CREATININE RATIO, RANDOM URINE",
    "CALC ALBUMIN/CREAT, RND",
    "ALBUM/CREAT RATIO, URINE",
}
AST_TEST_NAMES = {"ASPARTATE AMINOTRANSFERASE (AST)", "AST", "AST(SGOT)"}
ALT_TEST_NAMES = {"ALANINE AMINOTRANSFERASE (ALT)", "ALT", "ALT(SGPT)"}
PLATELET_TEST_NAMES = {"PLATELETS", "PLATELET COUNT"}
FIB4_TEST_NAMES = {"FIB 4 INDEX", "FIB-4"}
VITAMIN_D_TEST_NAMES = {"VITAMIN D 25 HYDROXY", "VITAMIN D,25-OH,TOTAL,IA", "VITAMIN D", "25OH, VITAMIN D"}
WBC_TEST_NAMES = {"WHITE BLOOD COUNT", "WHITE BLOOD CELL COUNT", "WBC"}
PSA_TEST_NAMES = {"TOTAL PSA", "PSA, TOTAL", "PSA", "PSA (ROCHE ECLIA)"}
AFP_TEST_NAMES = {"AFP TUMOR MARKER", "ALPHA FETOPROTEIN, TUMOR MARKER", "AFP, TUMOR MARKER"}
TSH_TEST_NAMES = {
    "THYROID-STIMULATING HORMONE",
    "THYROID-STIMULATING HORMONE (TSH)",
    "TSH W/REFLEX TO FT4",
    "TSH,ULTRASENSITIVE",
    "TSH W/RFX",
    "TSH W/RFX TO FREE T4",
}
T4_TEST_NAMES = {"FREE T4", "FREE T4 (THYROXINE)", "T4, FREE", "FREE T-4", "THYROXINE, FREE (FT4)"}


def normalize_test_label(text):
    text = (text or "").replace("\xa0", " ").upper().strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^A-Z0-9]+", "", text)
    return text


def row_matches_test_name(test_name, test_names):
    normalized_test_name = normalize_test_label(test_name)
    for candidate in test_names:
        normalized_candidate = normalize_test_label(candidate)
        if normalized_candidate and normalized_candidate in normalized_test_name:
            return True
    return False


def extract_lab_row_values(row):
    cells = row.locator("td")
    cell_count = cells.count()
    if cell_count < 5:
        return None

    test_name = cells.nth(2).inner_text().strip()
    abn_text = cells.nth(3).inner_text().strip()
    result_text = cells.nth(4).inner_text().strip()

    if not test_name:
        return None

    return test_name, abn_text, result_text


def find_notes_frame(page):
    for frame in page.frames:
        description_box = frame.locator("#description")
        try:
            if description_box.is_visible(timeout=1000):
                return frame
        except PlaywrightTimeoutError:
            continue
    return None


def find_lab_report_frame(page):
    for frame in page.frames:
        if frame.name == "labpic":
            return frame

    for frame in page.frames:
        try:
            if "ov_labreport.aspx" in frame.url:
                return frame
        except Exception:
            continue
    return None


def ensure_report_view(review_frame):
    view_title = review_frame.locator("#spanViewTitle").first
    try:
        view_title.wait_for(state="visible", timeout=2000)
    except PlaywrightTimeoutError:
        print("Report/Compact view toggle not found")
        return

    current_view_label = view_title.inner_text().strip()
    if current_view_label == "Compact View":
        report_view_button = review_frame.locator("#btnReportView").first
        report_view_button.click()
        review_frame.wait_for_timeout(2000)
        print("Switched from Compact View to Report View")
    else:
        print(f"View already set to: {current_view_label}")


def apply_test_date_range(frame):
    from_input = frame.locator("input[name='listFrom']")
    to_input = frame.locator("input[name='listTo']")

    from_input.wait_for(state="visible")
    to_input.wait_for(state="visible")
    from_input.click()
    from_input.press("Meta+A")
    from_input.type(TEST_DATE_FROM)
    from_input.press("Tab")
    to_input.click()
    to_input.press("Meta+A")
    to_input.type(TEST_DATE_TO)
    to_input.press("Tab")
    print(f"Applied inbox date range: {TEST_DATE_FROM} to {TEST_DATE_TO}")
    print(f"MDLand date fields show: {from_input.input_value()} to {to_input.input_value()}")

def refresh_inbox_list(frame):
    refresh_button = frame.locator("div[onclick='labFrameletsGo(1)']").first
    refresh_button.wait_for(state="visible")
    refresh_button.click()
    frame.wait_for_timeout(1000)
    frame.evaluate("labFrameletsGo(1)")
    print("Clicked Refresh and triggered labFrameletsGo(1)")


def ensure_final_status_filter_unchecked(frame):
    final_checkbox = frame.locator("input[name='orderStatusFilter'][value='Final']").first
    final_checkbox.wait_for(state="visible")
    final_checked = frame.evaluate(
        """
        () => {
            const checkbox = document.querySelector("input[name='orderStatusFilter'][value='Final']");
            return checkbox ? checkbox.checked : null;
        }
        """
    )
    if final_checked:
        frame.evaluate(
            """
            () => {
                const checkbox = document.querySelector("input[name='orderStatusFilter'][value='Final']");
                if (checkbox && checkbox.checked) {
                    checkbox.click();
                }
            }
            """
        )
        frame.wait_for_timeout(500)
        final_checked = frame.evaluate(
            """
            () => {
                const checkbox = document.querySelector("input[name='orderStatusFilter'][value='Final']");
                return checkbox ? checkbox.checked : null;
            }
            """
        )
        print(f"Unchecked Final status filter, checked={final_checked}")
    else:
        print("Final status filter was already unchecked")


def build_lab_value_note(review_frame, label, test_names):
    rows = review_frame.locator("tr")
    row_count = rows.count()
    print(f"Scanning {row_count} table rows for {label}")

    for i in range(row_count):
        row = rows.nth(i)
        row_values = extract_lab_row_values(row)
        if row_values is None:
            continue
        test_name, abn_text, result_text = row_values
        if not row_matches_test_name(test_name, test_names):
            continue

        if not result_text:
            continue

        lab_value_note = f"{label} {result_text}"
        abn_upper = abn_text.upper()
        if abn_upper == "H" or "ABOVE HIGH NORMAL" in abn_upper:
            lab_value_note += " (H)"
        elif abn_upper == "L" or "ABNORMAL LOW" in abn_upper:
            lab_value_note += " (L)"

        print(
            f"Found cholesterol row: test={test_name}, abn={abn_text}, result={result_text}"
        )
        return lab_value_note

    frame_text = review_frame.locator("body").inner_text()
    if any(test_name in frame_text.upper() for test_name in test_names):
        print(
            f"{label} exists somewhere on the review page, but no matching row with a result was parsed."
        )

    return None


def build_ast_alt_note(review_frame):
    rows = review_frame.locator("tr")
    row_count = rows.count()
    print(f"Scanning {row_count} table rows for AST/ALT")

    ast_note = None
    alt_note = None
    ast_abnormal = False
    alt_abnormal = False

    for i in range(row_count):
        row = rows.nth(i)
        row_values = extract_lab_row_values(row)
        if row_values is None:
            continue
        test_name, abn_text, result_text = row_values

        if not result_text:
            continue

        suffix = ""
        abn_upper = abn_text.upper()
        if abn_upper == "H" or "ABOVE HIGH NORMAL" in abn_upper:
            suffix = " (H)"
        elif abn_upper == "L" or "ABNORMAL LOW" in abn_upper:
            suffix = " (L)"

        if row_matches_test_name(test_name, AST_TEST_NAMES) and ast_note is None:
            ast_note = f"{result_text}{suffix}"
            ast_abnormal = bool(suffix)
            print(f"Found AST row: test={test_name}, abn={abn_text}, result={result_text}")
        elif row_matches_test_name(test_name, ALT_TEST_NAMES) and alt_note is None:
            alt_note = f"{result_text}{suffix}"
            alt_abnormal = bool(suffix)
            print(f"Found ALT row: test={test_name}, abn={abn_text}, result={result_text}")

        if ast_note and alt_note:
            if ast_abnormal or alt_abnormal:
                return f"AST/ALT {ast_note} / {alt_note}"
            return None

    return None


def build_platelet_note(review_frame):
    rows = review_frame.locator("tr")
    row_count = rows.count()
    print(f"Scanning {row_count} table rows for PLT")

    for i in range(row_count):
        row = rows.nth(i)
        row_values = extract_lab_row_values(row)
        if row_values is None:
            continue
        test_name, abn_text, result_text = row_values
        if not row_matches_test_name(test_name, PLATELET_TEST_NAMES):
            continue
        if not result_text:
            continue

        suffix = ""
        abn_upper = abn_text.upper()
        if abn_upper == "H" or "ABOVE HIGH NORMAL" in abn_upper:
            suffix = " (H)"
        elif abn_upper == "L" or "ABNORMAL LOW" in abn_upper:
            suffix = " (L)"

        print(f"Found PLT row: test={test_name}, abn={abn_text}, result={result_text}")
        if suffix:
            return f"PLT {result_text}{suffix}"
        return None

    return None


def build_wbc_note(review_frame):
    rows = review_frame.locator("tr")
    row_count = rows.count()
    print(f"Scanning {row_count} table rows for WBC")

    for i in range(row_count):
        row = rows.nth(i)
        row_values = extract_lab_row_values(row)
        if row_values is None:
            continue
        test_name, abn_text, result_text = row_values
        if not row_matches_test_name(test_name, WBC_TEST_NAMES):
            continue
        if not result_text:
            continue

        suffix = ""
        abn_upper = abn_text.upper()
        if abn_upper == "H" or "ABOVE HIGH NORMAL" in abn_upper:
            suffix = " (H)"
        elif abn_upper == "L" or "ABNORMAL LOW" in abn_upper:
            suffix = " (L)"

        print(f"Found WBC row: test={test_name}, abn={abn_text}, result={result_text}")
        if suffix:
            return f"WBC {result_text}{suffix}"
        return None

    return None


def build_psa_note(review_frame):
    rows = review_frame.locator("tr")
    row_count = rows.count()
    print(f"Scanning {row_count} table rows for PSA")

    for i in range(row_count):
        row = rows.nth(i)
        row_values = extract_lab_row_values(row)
        if row_values is None:
            continue
        test_name, abn_text, result_text = row_values
        if not row_matches_test_name(test_name, PSA_TEST_NAMES):
            continue
        if not result_text:
            continue

        suffix = ""
        abn_upper = abn_text.upper()
        if abn_upper == "H" or "ABOVE HIGH NORMAL" in abn_upper:
            suffix = " (H)"
        elif abn_upper == "L" or "ABNORMAL LOW" in abn_upper:
            suffix = " (L)"

        print(f"Found PSA row: test={test_name}, abn={abn_text}, result={result_text}")
        if suffix:
            return f"PSA {result_text}{suffix}"
        return None

    return None


def build_tsh_t4_note(review_frame):
    rows = review_frame.locator("tr")
    row_count = rows.count()
    print(f"Scanning {row_count} table rows for TSH/T4")

    tsh_note = None
    t4_note = None

    for i in range(row_count):
        row = rows.nth(i)
        row_values = extract_lab_row_values(row)
        if row_values is None:
            continue
        test_name, abn_text, result_text = row_values
        if not result_text:
            continue

        suffix = ""
        abn_upper = abn_text.upper()
        if abn_upper == "H" or "ABOVE HIGH NORMAL" in abn_upper:
            suffix = " (H)"
        elif abn_upper == "L" or "ABNORMAL LOW" in abn_upper:
            suffix = " (L)"

        if row_matches_test_name(test_name, TSH_TEST_NAMES) and tsh_note is None:
            tsh_note = f"{result_text}{suffix}"
            print(f"Found TSH row: test={test_name}, abn={abn_text}, result={result_text}")
        elif row_matches_test_name(test_name, T4_TEST_NAMES) and t4_note is None:
            t4_note = f"{result_text}{suffix}"
            print(f"Found T4 row: test={test_name}, abn={abn_text}, result={result_text}")

        if tsh_note and t4_note:
            return f"TSH/T4 {tsh_note} / {t4_note}"

    return None


def build_fib4_note(review_frame):
    return build_lab_value_note(review_frame, "fib-4", FIB4_TEST_NAMES)


def build_vitamin_d_note(review_frame):
    return build_lab_value_note(review_frame, "vit d", VITAMIN_D_TEST_NAMES)


def build_cholesterol_note(review_frame):
    return build_lab_value_note(review_frame, "chol", CHOLESTEROL_TEST_NAMES)


def build_hdl_note(review_frame):
    return build_lab_value_note(review_frame, "HDL", HDL_TEST_NAMES)


def build_ldl_note(review_frame):
    return build_lab_value_note(review_frame, "LDL", LDL_TEST_NAMES)


def build_triglycerides_note(review_frame):
    return build_lab_value_note(review_frame, "TG", TRIGLYCERIDES_TEST_NAMES)


def build_a1c_note(review_frame):
    return build_lab_value_note(review_frame, "A1c", HEMOGLOBIN_A1C_TEST_NAMES)


def build_egfr_note(review_frame):
    return build_lab_value_note(review_frame, "GFR", EGFR_TEST_NAMES)


def build_urine_ac_note(review_frame):
    return build_lab_value_note(review_frame, "urine A/C", URINE_AC_TEST_NAMES)


def build_afp_note(review_frame):
    return build_lab_value_note(review_frame, "AFP", AFP_TEST_NAMES)


def extract_date_of_service(target_lab_link):
    inbox_row = target_lab_link.locator("xpath=ancestor::tr[1]")
    dos_input = inbox_row.locator("input[name='DOS']").first
    raw_dos = dos_input.get_attribute("value") or ""

    date_of_service = ""
    if raw_dos:
        date_of_service = raw_dos.split(" ")[0]

    if not date_of_service:
        cells = inbox_row.locator("td.tableBillPA")
        cell_count = cells.count()
        if cell_count >= 8:
            date_of_service = cells.nth(7).inner_text().replace("\xa0", " ").strip()

    print(f"Date of service: {date_of_service}")
    return date_of_service


def classify_lab_name(link_text):
    normalized_text = link_text.strip().upper()

    for canonical_name, pattern in TARGET_LAB_PATTERNS.items():
        if pattern.upper() in normalized_text:
            return canonical_name

    return None


def collect_lab_items(lab_frame):
    inbox_rows = lab_frame.locator("tr[id^='trInboxItem']")
    row_count = inbox_rows.count()
    items = []

    for i in range(row_count):
        row = inbox_rows.nth(i)
        links = row.locator("a")
        if links.count() == 0:
            continue

        lab_link = links.first
        link_text = lab_link.inner_text().strip()
        lab_name = classify_lab_name(link_text)
        if lab_name is None:
            continue

        row_id = row.get_attribute("id")
        if not row_id:
            continue

        date_of_service = extract_date_of_service(lab_link)
        items.append(
            {
                "row_id": row_id,
                "lab_name": lab_name,
                "date_of_service": date_of_service,
            }
        )

    print(f"Collected {len(items)} inbox items for labs: {', '.join(sorted(TARGET_LAB_PATTERNS))}")
    return items


def run():
    with sync_playwright() as playwright:
        browser, page = login(playwright)
        print(page.url)
        page.evaluate("loadInboxList()")
        page.wait_for_timeout(2000)

        frame = page.frame(name="workarea0")
        filter_select = frame.locator("#DLFilterSetSettings")
        filter_select.wait_for(state="visible")
        filter_select.select_option(value="lab result")
        print("Selected inbox filter: lab result")
        apply_test_date_range(frame)
        ensure_final_status_filter_unchecked(frame)
        refresh_inbox_list(frame)
        page.wait_for_timeout(2000)

        lab_frame = page.frame(name="labFrame")
        lab_items = collect_lab_items(lab_frame)

        for item in lab_items:
            row = lab_frame.locator(f"tr#{item['row_id']}")
            target_lab_link = row.locator("a", has_text=item["lab_name"]).first
            target_lab_link.wait_for(state="visible")
            target_lab_link.click()
            print(f"Opened {item['lab_name']} item from row {item['row_id']}")
            page.wait_for_timeout(3000)

            notes_frame = find_notes_frame(page)
            if notes_frame is None:
                raise RuntimeError(
                    f"Could not find a visible #description textarea after clicking {item['lab_name']}."
                )

            lab_report_frame = find_lab_report_frame(page)
            if lab_report_frame is None:
                raise RuntimeError(
                    f"Could not find the lab report iframe after clicking {item['lab_name']}."
                )

            ensure_report_view(notes_frame)
            description_text = item["date_of_service"]
            if item["lab_name"] != "MainStreet":
                description_text = f"{description_text} Lab: "
            cholesterol_note = build_cholesterol_note(lab_report_frame)
            hdl_note = build_hdl_note(lab_report_frame)
            ldl_note = build_ldl_note(lab_report_frame)
            triglycerides_note = build_triglycerides_note(lab_report_frame)
            a1c_note = build_a1c_note(lab_report_frame)
            egfr_note = build_egfr_note(lab_report_frame)
            urine_ac_note = build_urine_ac_note(lab_report_frame)
            afp_note = build_afp_note(lab_report_frame)
            ast_alt_note = build_ast_alt_note(lab_report_frame)
            platelet_note = build_platelet_note(lab_report_frame)
            fib4_note = build_fib4_note(lab_report_frame)
            vitamin_d_note = build_vitamin_d_note(lab_report_frame)
            wbc_note = build_wbc_note(lab_report_frame)
            psa_note = build_psa_note(lab_report_frame)
            tsh_t4_note = build_tsh_t4_note(lab_report_frame)
            if cholesterol_note:
                description_text = f"{description_text}{cholesterol_note}"
            if hdl_note:
                separator = ", " if cholesterol_note else ""
                description_text = f"{description_text}{separator}{hdl_note}"
            if ldl_note:
                separator = ", " if (cholesterol_note or hdl_note) else ""
                description_text = f"{description_text}{separator}{ldl_note}"
            if triglycerides_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note) else ""
                description_text = f"{description_text}{separator}{triglycerides_note}"
            if a1c_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note) else ""
                description_text = f"{description_text}{separator}{a1c_note}"
            if egfr_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note) else ""
                description_text = f"{description_text}{separator}{egfr_note}"
            if urine_ac_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note) else ""
                description_text = f"{description_text}{separator}{urine_ac_note}"
            if afp_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note) else ""
                description_text = f"{description_text}{separator}{afp_note}"
            if ast_alt_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note) else ""
                description_text = f"{description_text}{separator}{ast_alt_note}"
            if platelet_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note or ast_alt_note) else ""
                description_text = f"{description_text}{separator}{platelet_note}"
            if fib4_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note or ast_alt_note or platelet_note) else ""
                description_text = f"{description_text}{separator}{fib4_note}"
            if vitamin_d_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note or ast_alt_note or platelet_note or fib4_note) else ""
                description_text = f"{description_text}{separator}{vitamin_d_note}"
            if wbc_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note or ast_alt_note or platelet_note or fib4_note or vitamin_d_note) else ""
                description_text = f"{description_text}{separator}{wbc_note}"
            if psa_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note or ast_alt_note or platelet_note or fib4_note or vitamin_d_note or wbc_note) else ""
                description_text = f"{description_text}{separator}{psa_note}"
            if tsh_t4_note:
                separator = ", " if (cholesterol_note or hdl_note or ldl_note or triglycerides_note or a1c_note or egfr_note or urine_ac_note or afp_note or ast_alt_note or platelet_note or fib4_note or vitamin_d_note or wbc_note or psa_note) else ""
                description_text = f"{description_text}{separator}{tsh_t4_note}"
            description_box = notes_frame.locator("#description")
            description_box.wait_for(state="visible")
            description_box.click()
            description_box.fill("")
            description_box.fill(description_text)
            print(f"Entered note: {description_text}")

            save_notes_button = notes_frame.locator("div[onclick='saveNotes()']").first
            save_notes_button.wait_for(state="visible")
            save_notes_button.click()
            print(f"Clicked Save Notes for {item['lab_name']} on {item['date_of_service']}")

            close_button = notes_frame.locator("div[onclick='closeMe();']").first
            close_button.wait_for(state="visible")
            close_button.click()
            print(f"Closed review tab for {item['lab_name']}")
            page.wait_for_timeout(1500)

        input("Press Enter to close the browser...")

if __name__ == "__main__":
    run()
