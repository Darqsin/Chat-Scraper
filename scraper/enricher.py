import re
import pdfplumber
import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

log = logging.getLogger("enricher")

DOWNLOAD_DIR = Path("grouped_output/pdfs")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def parse_record(record):
    try:
        doc_num = record.get("doc_num")
        fallback = record.get("pdf_fallback")
        pdf_path = DOWNLOAD_DIR / f"{doc_num}.pdf"

        # ✅ Download via network capture
        if not pdf_path.exists():
            if not download_pdf_via_network(fallback, pdf_path):
                record["flags"] = ["no_pdf"]
                return record

        text = extract_text(pdf_path)

        if not text:
            record["flags"] = ["no_pdf"]
            return record

        record["prop_address"] = extract_property_address(text)
        record["trustee_name"] = extract_trustee(text)
        record["auction_date"] = extract_auction_date(text)

        flags = []

        if not record["prop_address"]:
            flags.append("no_address")

        if not record["trustee_name"]:
            flags.append("no_trustee")

        record["flags"] = flags

        return record

    except Exception as e:
        log.warning(f"Failed {record.get('doc_num')}: {e}")
        record["flags"] = ["error"]
        return record


# =========================
# 🔥 NETWORK CAPTURE DOWNLOAD
# =========================

def download_pdf_via_network(url, save_path):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            pdf_bytes = None

            def handle_response(response):
                nonlocal pdf_bytes
                if "pdf" in response.url and response.status == 200:
                    try:
                        pdf_bytes = response.body()
                    except:
                        pass

            page.on("response", handle_response)

            page.goto(url, timeout=60000)
            page.wait_for_timeout(5000)

            browser.close()

            if pdf_bytes:
                with open(save_path, "wb") as f:
                    f.write(pdf_bytes)
                return True

            return False

    except Exception as e:
        log.warning(f"Network capture failed: {e}")
        return False


# =========================
# TEXT EXTRACTION
# =========================

def extract_text(pdf_path):
    try:
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += "\n" + t
        return text
    except Exception as e:
        log.warning(f"PDF read failed: {pdf_path} → {e}")
        return None


# =========================
# FIELD EXTRACTION
# =========================

def extract_property_address(text):
    patterns = [
        r"Property Address[:\s]*(.+?\d{5})",
        r"\b\d{3,5}\s+[A-Z0-9\s]+(?:ST|AVE|RD|DR|LN|BLVD|WAY|CT)[^\n]+AZ\s+\d{5}",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean(match.group(0))

    return None


def extract_trustee(text):
    match = re.search(r"Trustee[:\s]*(.+)", text, re.IGNORECASE)
    if match:
        return clean(match.group(1))
    return None


def extract_auction_date(text):
    match = re.search(r"Sale Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def clean(value):
    return re.sub(r"\s+", " ", value).strip()
