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
    r"\b(?:APN|A\.P\.N\.|PARCEL\s*(?:NO|NUMBER)?|TAX\s*PARCEL\s*NUMBER|ASSESSOR(?:'S)?\s*(?:NO|NUMBER)?)[:\s#-]*([A-Z0-9-]{6,})",
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
        rec = ParsedRecord(
            doc_num=doc_num,
            doc_type=doc_type,
            filed=filed,
            cat="NS",
            cat_label=cat_label,
            clerk_url=clerk_url,
            pdf_url=pdf_url,
            pdf_path=str(pdf_path),
            flags=["no_pdf"],
            score=0,
        )
        return asdict(rec)

    text, _, text_flags = extract_text_from_pdf(pdf_path)
    flags.extend(text_flags)

    if not text:
        flags.append("no_text_extracted")

    doc_num = doc_num or _find_first(DOC_NUM_RE, text)
    owner = _extract_owner(text)
    trustee_name = _extract_trustee_name(text)
    prop = _extract_property_address(text)

    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    amount = _find_first(MONEY_RE, text)
    trustee_phone = _find_first(TRUSTEE_PHONE_RE, text)
    auction_date = _find_first(DATE_NUMERIC_RE, text)
    parcel_number = _extract_parcel_number(text)
    deed_of_trust = _find_first(DOC_NUM_RE, text)

    score = 0
    if prop.get("address"):
        score += 30
    if owner:
        score += 20
    if trustee_name:
        score += 10

    rec = ParsedRecord(
        doc_num=doc_num,
        doc_type=doc_type,
        filed=filed,
        cat="NS",
        cat_label=cat_label,
        owner=owner,
        grantee=trustee_name,
        amount=amount,
        legal="",
        prop_address=prop.get("address", ""),
        prop_city=prop.get("city", ""),
        prop_state=prop.get("state", ""),
        prop_zip=prop.get("zip", ""),
        mail_address="",
        mail_city="",
        mail_state="",
        mail_zip="",
        county="Maricopa",
        parcel_number=parcel_number,
        original_loan=amount,
        trustee_name=trustee_name,
        trustee_phone=trustee_phone,
        auction_date=auction_date,
        deed_of_trust=deed_of_trust,
        first_name="",
        last_name="",
        second_first="",
        second_last="",
        clerk_url=clerk_url,
        pdf_url=pdf_url,
        pdf_path=str(pdf_path),
        flags=flags,
        score=score,
        raw_text_path=str(raw_text_path),
    )
    return asdict(rec)


def extract_text_from_pdf(pdf_path: str | Path, dpi: int = 250):
    flags: list[str] = []
    text = ""

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
    except Exception as exc:
        LOGGER.warning("pdfplumber failed for %s: %s", pdf_path, exc)
        flags.append("pdfplumber_failed")

    if len(text.strip()) < 50:
        flags.append("ocr_fallback")
        try:
            images = convert_from_path(str(pdf_path), dpi=dpi)
            text = ""
            for img in images:
                text += pytesseract.image_to_string(img) + "\n"
        except Exception as exc:
            LOGGER.warning("OCR failed for %s: %s", pdf_path, exc)
            flags.append("ocr_failed")

    text = _clean_text(text)
    return text, [], flags


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = text.replace("\f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _find_first(pattern, text: str) -> str:
    m = pattern.search(text or "")
    if not m:
        return ""
    if m.lastindex:
        return (m.group(1) or "").strip()
    return (m.group(0) or "").strip()


def _extract_owner(text: str) -> str:
    text = text or ""

    patterns = [
        r"Original Trustor(?:'s)?(?: Name and Address)?[:\-]?\s*(.+?)(?:Current Trustee|Trustee|Beneficiary|Sale Date|NOTICE OF TRUSTEE|$)",
        r"Trustor(?:s)?[:\-]?\s*(.+?)(?:Trustee|Beneficiary|Property Address|Sale Date|$)",
        r"Borrower[:\-]?\s*(.+?)(?:Trustee|Beneficiary|Property Address|Sale Date|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            value = _normalize_one_line(m.group(1))
            value = re.sub(r"\b\d{3,6}\s+.*", "", value).strip(" ,;:-")
            if len(value) > 5:
                return value

    return ""


def _extract_trustee_name(text: str) -> str:
    text = text or ""

    m = re.search(
        r"The undersigned Trustee,\s*([^,\n]+),\s*Attorney at Law",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()

    patterns = [
        r"Current Trustee(?:'s)?(?: Name and Address)?[:\-]?\s*(.+?)(?:Phone|Telephone|Sale Date|$)",
        r"Trustee[:\-]?\s*(.+?)(?:Phone|Telephone|Sale Date|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if not m:
            continue

        value = _normalize_one_line(m.group(1))
        lower = value.lower()

        if any(x in lower for x in ["sale", "objection", "must file", "court order", "superior court"]):
            continue
        if re.search(r"\d{3,5}\s+.+\b(AZ|CA|TX|NV|NM)\b", value, re.I):
            continue
        if any(x in lower for x in ["street", "avenue", "road", "suite", "lane", "drive", "boulevard"]):
            continue
        if len(value) > 5:
            return value

    return ""


def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _extract_property_address(text: str) -> dict[str, str]:
    text = text or ""

    multiline_patterns = [
        r"The street address/location of the real property described above is purported to be:\s*([^\n]+)\s+([A-Za-z .]+,\s*(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
        r"Street address or identifiable location:\s*([^\n]+)\s+([A-Za-z .]+,\s*(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
        r"Purported Street Address[:\-]?\s*([^\n]+)\s+([A-Za-z .]+,\s*(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
    ]

    for pattern in multiline_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            combined = _clean_address(f"{m.group(1)}, {m.group(2)}")
            parsed = _parse_address(combined)
            if parsed["address"]:
                return parsed

    single_patterns = [
        r"The street address is purported to be[:\-]?\s*(.+?(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
        r"The street address/location of the real property described above is purported to be[:\-]?\s*(.+?(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
        r"Street address or identifiable location[:\-]?\s*(.+?(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
        r"Purported Street Address[:\-]?\s*(.+?(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
        r"Property Address[:\-]?\s*(.+?(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?)",
    ]

    candidates: list[str] = []

    for pattern in single_patterns:
        for m in re.finditer(pattern, text, re.I):
            candidate = _clean_address(m.group(1))
            if candidate:
                candidates.append(candidate)

    fallback_matches = re.findall(
        r"\d{3,6}\s+[A-Za-z0-9 .'\-#]+,\s*[A-Za-z .]+,\s*(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?",
        text,
        re.I,
    )
    candidates.extend(_clean_address(x) for x in fallback_matches if x)

    return _best_address(candidates)


def _clean_address(val: str) -> str:
    val = val or ""
    val = val.replace("\f", " ")
    val = re.sub(r"Tax Parcel.*", "", val, flags=re.I)
    val = re.sub(r"Parcel.*", "", val, flags=re.I)
    val = re.sub(r"APN.*", "", val, flags=re.I)
    val = re.sub(r"\s+", " ", val)
    return val.strip(" ,;:-")


def _best_address(candidates: list[str]) -> dict[str, str]:
    for c in candidates:
        if len(c) < 15:
            continue
        parsed = _parse_address(c)
        if parsed["address"] and parsed["zip"]:
            return parsed

    return {"address": "", "city": "", "state": "", "zip": ""}


def _parse_address(val: str) -> dict[str, str]:
    try:
        tagged, _ = usaddress.tag(val)
    except Exception:
        return {"address": "", "city": "", "state": "", "zip": ""}

    street_parts = []
    for key in [
        "AddressNumber",
        "StreetNamePreDirectional",
        "StreetNamePreType",
        "StreetName",
        "StreetNamePostType",
        "StreetNamePostDirectional",
        "OccupancyType",
        "OccupancyIdentifier",
    ]:
        if key in tagged:
            street_parts.append(tagged[key])

    street = " ".join(street_parts).strip()
    city = tagged.get("PlaceName", "").strip()
    state = tagged.get("StateName", "").strip()
    zipcode = tagged.get("ZipCode", "").strip()

    if state.lower() == "arizona":
        state = "AZ"
    if len(zipcode) > 5:
        zipcode = zipcode[:5]

    return {
        "address": street,
        "city": city,
        "state": state,
        "zip": zipcode,
    }


def _normalize_one_line(value: str) -> str:
    value = value.replace("\f", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()
