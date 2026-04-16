import re
import pdfplumber
import logging
import requests
from pathlib import Path

log = logging.getLogger("enricher")

DOWNLOAD_DIR = Path("grouped_output/pdfs")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# MAIN ENTRY
# =========================

def parse_record(record):
    try:
        doc_num = record.get("doc_num")
        pdf_path = DOWNLOAD_DIR / f"{doc_num}.pdf"

        # ✅ STEP 1: GET REAL PDF URL
        pdf_url = get_real_pdf_url(record)

        if not pdf_url:
            record["flags"] = ["no_pdf"]
            return record

        # ✅ STEP 2: DOWNLOAD
        if not pdf_path.exists():
            if not download_pdf(pdf_url, pdf_path):
                record["flags"] = ["no_pdf"]
                return record

        # ✅ STEP 3: PARSE
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
# GET REAL PDF URL
# =========================

def get_real_pdf_url(record):
    try:
        fallback = record.get("pdf_fallback")

        if not fallback:
            return None

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://recorder.maricopa.gov/"
        }

        r = requests.get(fallback, headers=headers, timeout=20)

        if r.status_code != 200:
            return None

        # Look for actual PDF link in page
        match = re.search(r'href="([^"]+\.pdf)"', r.text)

        if match:
            url = match.group(1)

            if url.startswith("/"):
                return "https://recorder.maricopa.gov" + url

            return url

    except Exception as e:
        log.warning(f"PDF URL extract failed: {e}")

    return None


# =========================
# DOWNLOAD
# =========================

def download_pdf(url, path):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://recorder.maricopa.gov/"
        }

        r = requests.get(url, headers=headers, timeout=20)

        if r.status_code == 200 and r.content:
            with open(path, "wb") as f:
                f.write(r.content)
            return True

        log.warning(f"Download failed {url} → {r.status_code}")
        return False

    except Exception as e:
        log.warning(f"Download error {url} → {e}")
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
        r"Property Address[:\s]*(.+?Arizona\s+\d{5})",
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


# =========================
# HELPERS
# =========================

def clean(value):
    return re.sub(r"\s+", " ", value).strip()
