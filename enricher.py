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

TRUSTEE_PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})"
)
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
        return asdict(
            ParsedRecord(
                doc_num=doc_num,
                doc_type=doc_type,
                filed=filed,
                cat="NS",
                cat_label=cat_label,
                clerk_url=clerk_url,
                pdf_url=pdf_url,
                pdf_path="",
                flags=["no_pdf"],
                score=0,
            )
        )

    text, text_flags = extract_text_from_pdf(pdf_path)
    flags.extend(text_flags)

    if not text.strip():
        flags.append("no_text_extracted")

    doc_num = doc_num or _find_first(DOC_NUM_RE, text)
    owner = _clean_owner(_extract_owner(text))
    if _is_bad_owner(owner):
        owner = ""
        flags.append("owner_suspect")

    trustee_name = _extract_trustee_name(text)
    if _is_bad_trustee_candidate(trustee_name):
        trustee_name = ""
        flags.append("trustee_suspect")

    prop = _extract_property_address(text)

    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    amount = _find_first(MONEY_RE, text)
    trustee_phone = _find_first(TRUSTEE_PHONE_RE, text)
    auction_date = _extract_auction_date(text)
    parcel_number = _extract_parcel_number(text)
    deed_of_trust = _find_first(DOC_NUM_RE, text)

    if not owner:
        flags.append("owner_missing")
    if not trustee_name:
        flags.append("trustee_missing")
    if not auction_date:
        flags.append("auction_date_missing")

    score = 0
    if prop.get("address"):
        score += 30
    if owner:
        score += 20
    if trustee_name:
        score += 10

    return asdict(
        ParsedRecord(
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
    )


def extract_text_from_pdf(pdf_path: str | Path, dpi: int = 250) -> tuple[str, list[str]]:
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

    return _clean_text(text), flags


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


def _normalize_one_line(value: str) -> str:
    value = value.replace("\f", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,;:-")


def _clean_owner(value: str) -> str:
    if not value:
        return ""
    value = value.replace("\n", " ").strip()
    value = re.sub(r"\(.*?\)", "", value)
    value = re.sub(r"\b(as shown on the deed of trust)\b", "", value, flags=re.I)
    value = re.sub(r"\b(an|a)\s+arizona\s+.*", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,;:-")


def _is_bad_owner(value: str) -> bool:
    v = (value or "").strip()
    vu = v.upper()

    if len(v) < 6:
        return True

    bad_starts = [
        "/GRANTOR",
        "IN FAVOR OF",
        "IN WHICH",
        "AS OF THE RECORDING",
        "THAT CERTAIN",
        "UNDER THAT CERTAIN",
    ]
    if any(vu.startswith(x) for x in bad_starts):
        return True

    bad_exact = {
        "AN UNMARRIED WOMAN",
        "AN UNMARRIED MAN",
        "A SINGLE MAN",
        "A SINGLE WOMAN",
        "NUMBER",
        "NUMBERS",
        "IDENTIFIABLE",
    }
    if vu in bad_exact:
        return True

    if vu.endswith(" AS"):
        return True

    return False


def _extract_owner(text: str) -> str:
    text = text or ""

    patterns = [
        r"Original Trustor(?:'s)?(?: Name and Address)?[:\-]?\s*(.+?)(?:Current Trustee|Trustee|Beneficiary|Sale Date|NOTICE OF TRUSTEE|$)",
        r"Trustor(?:s)?[:\-]?\s*(.+?)(?:Trustee|Beneficiary|Property Address|Sale Date|$)",
        r"Grantor(?:s)?[:\-]?\s*(.+?)(?:Trustee|Beneficiary|Property Address|Sale Date|$)",
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


def _is_bad_trustee_candidate(value: str) -> bool:
    v = (value or "").upper().strip()
    if not v:
        return False

    bad_phrases = [
        "DEED OF TRUST",
        "TRUST DATED",
        "RECORDED ON",
        "INSTRUMENT NO",
        "INSTRUMENT #",
        "LOAN NUMBER",
        "LOAN NO",
        "TS#",
        "RECORDS OF MARICOPA COUNTY",
        "SUBJECT DEED OF TRUST",
        "ASSIGNMENT OF RENTS",
    ]

    if any(p in v for p in bad_phrases):
        return True

    if v in {"CORPS", "NUMBER", "NUMBERS", "IDENTIFIABLE"}:
        return True

    return False


def _extract_trustee_name(text: str) -> str:
    if not text:
        return ""

    m = re.search(
        r"The undersigned Trustee,\s*([^,\n]+),\s*Attorney at Law",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: list[str] = []

    for i, line in enumerate(lines):
        if re.search(r"trustee", line, re.I):
            same_line = re.sub(r"(?i).*trustee[s]?:?", "", line).strip(" ,;:-")
            if same_line:
                candidates.append(same_line)

            for j in range(1, 3):
                if i + j < len(lines):
                    candidates.append(lines[i + j])

    clean: list[str] = []
    for c in candidates:
        c = _normalize_one_line(c)
        if re.search(r"\d{3,5} .+(AZ|ARIZONA|CA|TX|NV|NM)", c, re.I):
            continue
        if any(x in c.lower() for x in ["street", "road", "avenue", "suite", "phoenix", "az"]):
            continue
        if any(x in c.lower() for x in ["sale", "objection", "must file", "court order", "superior court"]):
            continue
        if len(c) > 4:
            clean.append(c)

    if clean:
        for c in clean:
            if not _is_bad_trustee_candidate(c):
                return c

    return ""


def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _extract_auction_date(text: str) -> str:
    if not text:
        return ""

    patterns = [
        r"(?:Sale Date and Time|Sale Date|Auction Date|Date of Sale)[:\-]?\s*(.+?)(?:\n|Sale Location|Location|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            value = m.group(1).strip()
            date_match = DATE_NUMERIC_RE.search(value)
            if date_match:
                return date_match.group(0)

    return ""


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
                if _is_bad_property_address(parsed):
                    continue
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
        r"\d{3,6}\s+[A-Za-z0-9 .'\-#/]+,\s*[A-Za-z .]+,\s*(?:AZ|Arizona)\s*\d{5}(?:-\d{4})?",
        text,
        re.I,
    )
    candidates.extend(_clean_address(x) for x in fallback_matches if x)

    for c in candidates:
        if len(c) < 15:
            continue
        parsed = _parse_address(c)
        if parsed["address"] and parsed["zip"]:
            if _is_bad_property_address(parsed):
                continue
            return parsed

    return {"address": "", "city": "", "state": "", "zip": ""}


def _clean_address(val: str) -> str:
    val = val or ""
    val = val.replace("\f", " ")
    val = re.sub(r"Tax Parcel.*", "", val, flags=re.I)
    val = re.sub(r"Parcel.*", "", val, flags=re.I)
    val = re.sub(r"APN.*", "", val, flags=re.I)
    val = re.sub(r"\s+", " ", val)
    return val.strip(" ,;:-")


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


def _is_bad_property_address(parsed: dict[str, str]) -> bool:
    address = (parsed.get("address") or "").upper()
    city = (parsed.get("city") or "").upper()
    zip_code = (parsed.get("zip") or "").strip()

    if "JEFFERSON" in address and zip_code in {"85003", "85004"}:
        return True
    if address in {"201 W JEFFERSON", "201 W JEFFERSON STREET", "201 WEST JEFFERSON"}:
        return True
    if city == "PHOENIX" and zip_code == "85003" and "JEFFERSON" in address:
        return True

    return False
