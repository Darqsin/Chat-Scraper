from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pdfplumber
import pytesseract
import usaddress
from pdf2image import convert_from_path

LOGGER = logging.getLogger("enricher")

TRUSTEE_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})")
MONEY_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?")
DATE_NUMERIC_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
PARCEL_RE = re.compile(
    r"\b(?:APN|PARCEL\s*(?:NO|NUMBER)?|TAX\s*PARCEL\s*NUMBER)[:\s#-]*([A-Z0-9-]{6,})",
    re.I,
)
DOC_NUM_RE = re.compile(r"\b20\d{9,}\b")


@dataclass
class ParsedRecord:
    doc_num: str = ""
    doc_type: str = ""
    filed: str = ""
    cat: str = "NS"
    cat_label: str = "Notice of Trustee Sale"
    owner: str = ""
    grantee: str = ""
    amount: str = ""
    legal: str = ""
    prop_address: str = ""
    prop_city: str = ""
    prop_state: str = ""
    prop_zip: str = ""
    mail_address: str = ""
    mail_city: str = ""
    mail_state: str = ""
    mail_zip: str = ""
    county: str = "Maricopa"
    parcel_number: str = ""
    original_loan: str = ""
    trustee_name: str = ""
    trustee_phone: str = ""
    auction_date: str = ""
    deed_of_trust: str = ""
    first_name: str = ""
    last_name: str = ""
    second_first: str = ""
    second_last: str = ""
    clerk_url: str = ""
    pdf_url: str = ""
    pdf_path: str = ""
    flags: list[str] | None = None
    score: int = 0
    raw_text_path: str = ""


def parse_record(
    pdf_path: str | Path,
    clerk_url: str,
    pdf_url: str,
    filed: str = "",
    doc_num: str = "",
    doc_type: str = "NS",
    cat_label: str = "Notice of Trustee Sale",
    raw_text_dir: str | Path = "parsed_output",
) -> dict[str, Any]:

    raw_text_dir = Path(raw_text_dir)
    raw_text_dir.mkdir(parents=True, exist_ok=True)

    flags: list[str] = []
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        return asdict(ParsedRecord(flags=["no_pdf"], score=0))

    text, _, text_flags = extract_text_from_pdf(pdf_path)
    flags.extend(text_flags)

    doc_num = doc_num or _find_first(DOC_NUM_RE, text)
    owner = _extract_owner(text)
    trustee_name = _extract_trustee_name(text)

    prop = _extract_property_address(text)

    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    score = 0
    if prop["address"]:
        score += 30
    if owner:
        score += 20
    if trustee_name:
        score += 10

    rec = ParsedRecord(
        doc_num=doc_num,
        doc_type=doc_type,
        filed=filed,
        owner=owner,
        grantee=trustee_name,
        amount=_find_first(MONEY_RE, text),
        prop_address=prop["address"],
        prop_city=prop["city"],
        prop_state=prop["state"],
        prop_zip=prop["zip"],
        trustee_name=trustee_name,
        trustee_phone=_find_first(TRUSTEE_PHONE_RE, text),
        auction_date=_find_first(DATE_NUMERIC_RE, text),
        clerk_url=clerk_url,
        pdf_url=pdf_url,
        pdf_path=str(pdf_path),
        flags=flags,
        score=score,
        raw_text_path=str(raw_text_path),
    )

    return asdict(rec)


def extract_text_from_pdf(pdf_path, dpi=250):
    flags = []
    text = ""

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
    except:
        flags.append("pdfplumber_failed")

    if len(text.strip()) < 50:
        flags.append("ocr_fallback")
        try:
            images = convert_from_path(str(pdf_path), dpi=dpi)
            text = ""
            for img in images:
                text += pytesseract.image_to_string(img) + "\n"
        except:
            flags.append("ocr_failed")

    return text, [], flags


def _find_first(pattern, text):
    m = pattern.search(text or "")
    return m.group(1).strip() if m and m.lastindex else (m.group(0) if m else "")


# -------------------------
# 🔥 OWNER (OCR SAFE)
# -------------------------

def _extract_owner(text: str) -> str:
    m = re.search(r"Trustor[:\-]?\s*(.+?)(?:Trustee|Beneficiary|$)", text or "", re.I | re.S)
    if not m:
        return ""

    value = m.group(1).strip()

    # remove address junk
    value = re.sub(r"\d{3,5} .+", "", value)

    return value if len(value) > 5 else ""


# -------------------------
# 🔥 TRUSTEE (FILTERS COURTHOUSE)
# -------------------------

def _extract_trustee_name(text: str) -> str:
    m = re.search(r"Trustee[:\-]?\s*(.+?)(?:Trustor|Beneficiary|$)", text or "", re.I | re.S)
    if not m:
        return ""

    value = m.group(1).strip()

    # 🚫 remove courthouse / addresses
    if re.search(r"\d{3,5} .+(AZ|CA|TX)", value):
        return ""

    if any(x in value.lower() for x in ["street", "avenue", "road", "suite"]):
        return ""

    return value if len(value) > 5 else ""


# -------------------------
# 🔥 ADDRESS (FIXED)
# -------------------------

def _extract_property_address(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for i in range(len(lines) - 1):
        l1 = lines[i]
        l2 = lines[i + 1]

        if re.search(r"\d{3,6} .+", l1) and re.search(r"(AZ|Arizona).*\d{5}", l2, re.I):
            return _parse_address(f"{l1}, {l2}")

    # fallback
    matches = re.findall(r"\d{3,6} .+?(?:AZ|Arizona)\s*\d{5}", text, re.I)

    for m in matches:
        if len(m) < 15:
            continue
        parsed = _parse_address(m)
        if parsed["zip"]:
            return parsed

    return {"address": "", "city": "", "state": "", "zip": ""}


def _parse_address(val: str) -> dict:
    try:
        tagged, _ = usaddress.tag(val)
    except:
        return {"address": "", "city": "", "state": "", "zip": ""}

    return {
        "address": val.split(",")[0],
        "city": tagged.get("PlaceName", ""),
        "state": tagged.get("StateName", ""),
        "zip": tagged.get("ZipCode", "")[:5],
    }
