import re
import pdfplumber
import logging

log = logging.getLogger("enricher")


# =========================
# MAIN ENTRY (REQUIRED)
# =========================

def parse_record(record):
    try:
        pdf_path = record.get("pdf_path")

        if not pdf_path:
            record["flags"] = ["no_pdf"]
            return record

        text = extract_text(pdf_path)

        if not text:
            record["flags"] = ["no_pdf"]
            return record

        # Extract fields
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
        log.warning(f"Failed to process {record.get('doc_num')}: {e}")
        record["flags"] = ["error"]
        return record


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
    try:
        match = re.search(
            r"Property Address[:\s]*(.+?\d{5})",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            return clean(match.group(1))
    except:
        pass
    return None


def extract_trustee(text):
    try:
        match = re.search(
            r"Trustee[:\s]*(.+)",
            text,
            re.IGNORECASE,
        )
        if match:
            return clean(match.group(1))
    except:
        pass
    return None


def extract_auction_date(text):
    try:
        match = re.search(
            r"Sale Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
    except:
        pass
    return None


# =========================
# HELPERS
# =========================

def clean(value):
    return re.sub(r"\s+", " ", value).strip()
