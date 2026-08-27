import argparse
import csv
import json
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_SUFFIX = ".pdf"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587


@dataclass(frozen=True)
class Employee:
    name: str
    email: str


@dataclass(frozen=True)
class PaystubMatch:
    pdf_path: Path
    title: str
    employee: Employee


def normalize_name(value):
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def load_employee_map(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        return load_employee_map_json(path)
    if path.suffix.lower() == ".csv":
        return load_employee_map_csv(path)
    raise ValueError("Employee map must be a .csv or .json file")


def load_env_file(path):
    path = Path(path)
    if not path.exists():
        return

    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            os.environ.setdefault(key, value)


def load_employee_map_json(path):
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    employees = []
    if isinstance(data, dict):
        iterable = data.items()
        for name, email in iterable:
            employees.append(Employee(name=str(name).strip(), email=str(email).strip()))
    elif isinstance(data, list):
        for item in data:
            employees.append(
                Employee(
                    name=str(item.get("name", "")).strip(),
                    email=str(item.get("email", "")).strip(),
                )
            )
    else:
        raise ValueError("JSON employee map must be an object or a list of objects")

    return validate_employees(employees)


def load_employee_map_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required_columns = {"name", "email"}
        if not reader.fieldnames or not required_columns.issubset(reader.fieldnames):
            raise ValueError("CSV employee map must include name,email columns")

        employees = [
            Employee(name=row["name"].strip(), email=row["email"].strip())
            for row in reader
        ]

    return validate_employees(employees)


def validate_employees(employees):
    valid = []
    seen = set()
    for employee in employees:
        normalized = normalize_name(employee.name)
        if not employee.name or not employee.email:
            raise ValueError("Every employee entry must include name and email")
        if normalized in seen:
            raise ValueError(f"Duplicate employee name after normalization: {employee.name}")
        seen.add(normalized)
        valid.append(employee)
    return valid


def get_pdf_title(pdf_path):
    metadata_title = get_pdf_metadata_title(pdf_path)
    if metadata_title:
        return metadata_title

    first_page_title = get_pdf_first_page_title(pdf_path)
    if first_page_title:
        return first_page_title

    return pdf_path.stem


def get_pdf_metadata_title(pdf_path):
    PdfReader = get_pdf_reader()
    if PdfReader is None:
        return None

    try:
        reader = PdfReader(str(pdf_path))
        title = reader.metadata.title if reader.metadata else None
    except Exception as error:
        print(f"Could not read PDF metadata from {pdf_path}: {error}")
        return None

    return clean_title(title)


def get_pdf_first_page_title(pdf_path):
    PdfReader = get_pdf_reader()
    if PdfReader is None:
        return None

    try:
        reader = PdfReader(str(pdf_path))
        if not reader.pages:
            return None
        text = reader.pages[0].extract_text() or ""
    except Exception as error:
        print(f"Could not read PDF text from {pdf_path}: {error}")
        return None

    lines = [clean_title(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None

    for line in lines[:12]:
        if "paystub" in line.lower() or "pay stub" in line.lower():
            return line

    return lines[0]


def get_pdf_reader():
    try:
        from pypdf import PdfReader

        return PdfReader
    except ImportError:
        pass

    try:
        from PyPDF2 import PdfReader

        return PdfReader
    except ImportError:
        return None


def clean_title(value):
    if not value:
        return None
    value = str(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def find_employee_for_title(title, employees):
    normalized_title = normalize_name(strip_common_paystub_words(title))
    exact_matches = [
        employee
        for employee in employees
        if normalize_name(employee.name) == normalized_title
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    contained_matches = [
        employee
        for employee in employees
        if name_is_in_title(employee.name, normalized_title)
    ]
    if len(contained_matches) == 1:
        return contained_matches[0]
    if len(contained_matches) > 1:
        names = ", ".join(employee.name for employee in contained_matches)
        raise ValueError(f"Title matched multiple employees: {title} -> {names}")

    parsed_name = parse_name_from_title(title)
    if parsed_name:
        parsed_normalized = normalize_name(parsed_name)
        parsed_matches = [
            employee
            for employee in employees
            if normalize_name(employee.name) == parsed_normalized
        ]
        if len(parsed_matches) == 1:
            return parsed_matches[0]

    raise ValueError(f"No employee email match found for title: {title}")


def strip_common_paystub_words(title):
    value = title or ""
    value = re.sub(r"\b(paystub|pay stub|pay statement|earnings statement)\b", " ", value, flags=re.I)
    value = re.sub(r"\b(employee|name|for|period|date)\b", " ", value, flags=re.I)
    return value


def name_is_in_title(name, normalized_title):
    normalized_name = normalize_name(name)
    if not normalized_name:
        return False
    return re.search(rf"\b{re.escape(normalized_name)}\b", normalized_title) is not None


def parse_name_from_title(title):
    patterns = [
        r"\bpay\s*stub\s*[-:]\s*(?P<name>[a-z ,.'-]+)",
        r"\bpaystub\s*[-:]\s*(?P<name>[a-z ,.'-]+)",
        r"\bearnings\s+statement\s+for\s+(?P<name>[a-z ,.'-]+)",
        r"\bemployee\s+name\s*[:\-]\s*(?P<name>[a-z ,.'-]+)",
        r"\bname\s*[:\-]\s*(?P<name>[a-z ,.'-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, title or "", flags=re.I)
        if match:
            name = re.sub(r"\s+", " ", match.group("name")).strip(" ,.-")
            if name:
                return name
    return None


def find_paystub_matches(pdf_dir, employees):
    pdf_dir = Path(pdf_dir)
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF folder does not exist: {pdf_dir}")

    matches = []
    for pdf_path in sorted(pdf_dir.iterdir()):
        if pdf_path.suffix.lower() != PDF_SUFFIX:
            continue
        title = get_pdf_title(pdf_path)
        employee = find_employee_for_title(title, employees)
        matches.append(PaystubMatch(pdf_path=pdf_path, title=title, employee=employee))
    return matches


def build_email(match, sender):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = match.employee.email
    message["Subject"] = f"Paystub - {match.employee.name}"
    message.set_content(
        f"Hello {match.employee.name},\n\n"
        "Attached is your paystub.\n\n"
        "Thank you."
    )

    with open(match.pdf_path, "rb") as file:
        message.add_attachment(
            file.read(),
            maintype="application",
            subtype="pdf",
            filename=match.pdf_path.name,
        )

    return message


def send_paystub_email(match, smtp_host, smtp_port, sender, password):
    message = build_email(match, sender)
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)


def get_required_env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def parse_args():
    parser = argparse.ArgumentParser(
        description="Email paystub PDFs to employees based on the paystub title."
    )
    parser.add_argument("pdf_dir", help="Folder containing paystub PDF files")
    parser.add_argument(
        "employee_map",
        help="CSV with name,email columns or JSON mapping names to emails",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send emails. Without this flag, the script only prints matches.",
    )
    parser.add_argument(
        "--env-file",
        default=PROJECT_ROOT / ".env",
        help="Environment file with EMAIL_USERNAME and EMAIL_PASSWORD.",
    )
    parser.add_argument(
        "--smtp-host",
        default=os.getenv("SMTP_HOST", DEFAULT_SMTP_HOST),
        help=f"SMTP host. Defaults to {DEFAULT_SMTP_HOST} or SMTP_HOST.",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=int(os.getenv("SMTP_PORT", DEFAULT_SMTP_PORT)),
        help=f"SMTP port. Defaults to {DEFAULT_SMTP_PORT} or SMTP_PORT.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    load_env_file(args.env_file)
    employees = load_employee_map(args.employee_map)
    if get_pdf_reader() is None:
        print("Install pypdf or PyPDF2 to read PDF titles/text. Falling back to filenames.")
    matches = find_paystub_matches(args.pdf_dir, employees)

    if not matches:
        print("No PDF paystubs found.")
        return

    for match in matches:
        print(
            f"{match.pdf_path.name}: title='{match.title}' -> "
            f"{match.employee.name} <{match.employee.email}>"
        )

    if not args.send:
        print("Dry run only. Add --send to email the matched paystubs.")
        return

    sender = get_required_env("EMAIL_USERNAME")
    password = get_required_env("EMAIL_PASSWORD")
    for match in matches:
        send_paystub_email(
            match=match,
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            sender=sender,
            password=password,
        )
        print(f"Sent {match.pdf_path.name} to {match.employee.email}")


if __name__ == "__main__":
    main()
