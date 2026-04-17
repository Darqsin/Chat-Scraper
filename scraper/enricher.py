from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
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
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
MONEY_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?")
DATE_NUMERIC_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
PARCEL_RE = re.compile(
    r"\b(?:APN|A\.P\.N\.|PARCEL\s*(?:NO|NUMBER)?|TAX\s*PARCEL\s*NUMBER|ASSESSOR(?:'S)?\s*(?:NO|NUMBER)?)[:\s#-]*([A-Z0-9-]{6,})",
    re.I,
)
DOC_NUM_RE = re.compile(r"\b20\d{9,}\b")

TRUSTEE_WHITELIST = [
    "QUALITY LOAN SERVICE CORPORATION",
    "MTC FINANCIAL INC",
    "MTC FINANCIAL INC. DBA TRUSTEE CORPS",
    "TRUSTEE CORPS",
    "CLEAR RECON CORP",
    "CLEAR RECON CORP.",
    "WESTERN PROGRESSIVE - ARIZONA",
    "WESTERN PROGRESSIVE, LLC",
    "AZ TRUSTEE SERVICES LLC",
    "AZ TRUSTEE SERVICES, LLC",
    "NATIONWIDE TRUSTEE SERVICES, INC.",
    "NATIONWIDE TRUSTEE SERVICES INC",
    "PRESTIGE DEFAULT SERVICES, LLC",
    "PRESTIGE DEFAULT SERVICES LLC",
    "FAY SERVICING, LLC",
    "T.D. SERVICE COMPANY",
    "TD SERVICE COMPANY",
    "LEONARD J. MCDONALD",
    "RONALD E. HERBERS",
    "RONALD E HERBERS",
]

TRUSTEE_BAD_PHRASES = [
    "CURRENT BENEFICIARY",
    "ORIGINAL TRUSTOR",
    "COMMON DESIGNATION",
    "TO THE HIGHEST BIDDER",
    "TRUSTEE'S PHONE",
    "TRUSTEE’S PHONE",
    "THE FOLLOWING INFORMATION IS PROVIDED",
    "A.R.S. SECTION 33-808",
    "NO LATER THAN 5:00 P.M.",
    "LAST BUSINESS DAY",
    "TRUST DATED",
    "DEED OF TRUST",
    "RECORDED ON",
    "INSTRUMENT NO",
    "INSTRUMENT #",
    "LOAN NUMBER",
    "LOAN NO",
    "TS#",
    "SALE DATE",
    "AUCTION DATE",
    "PROPERTY ADDRESS",
    "STREET ADDRESS",
    "GILLETTE AVE",
    "PHOENIX, AZ",
    "ARIZONA 85234",
]

OWNER_STATUS_PHRASES = [
    r",?\s+a single man\b",
    r",?\s+a single woman\b",
    r",?\s+an unmarried man\b",
    r",?\s+an unmarried woman\b",
    r",?\s+single man\b",
    r",?\s+single woman\b",
    r",?\s+unmarried man\b",
    r",?\s+unmarried woman\b",
    r",?\s+husband and wife\b",
    r",?\s+wife and husband\b",
    r",?\s+as community property\b",
    r",?\s+as community property with rights? of survivorship\b",
    r",?\s+as joint tenants with rights? of survivorship\b",
    r",?\s+as his sole and separate property\b",
    r",?\s+as her sole and separate property\b",
    r",?\s+as their sole and separate property\b",
    r",?\s+sole and separate property\b",
]

KNOWN_CITY_FIXES = {
    "SUN, CITY": "SUN CITY",
    "QUEEN, CREEK": "QUEEN CREEK",
    "EL, MIRAGE": "EL MIRAGE",
}


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
        flags.append("owner_suspect")
        owner = ""

    trustee_name = _extract_trustee_name(text)
    if not trustee_name:
        flags.append("trustee_missing")

    prop = _extract_property_address(text)

    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    amount = _find_first(MONEY_RE, text)
    trustee_phone = _extract_trustee_phone(text)
    auction_date = _extract_auction_date(text)
    parcel_number = _extract_parcel_number(text)
    deed_of_trust = _extract_deed_of_trust(text, doc_num)

    if not owner:
        flags.append("owner_missing")
    if not auction_date:
        flags.append("auction_date_missing")
    if not parcel_number:
        flags.append("parcel_missing")

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
    text = text.replace("’", "'")
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


def _smart_title_name(value: str) -> str:
    if not value:
        return ""
    if value.isupper():
        return value.title()
    return value


def _clean_owner(value: str) -> str:
    if not value:
        return ""
    value = value.replace("\n", " ").strip()
    value = re.sub(r"\(.*?\)", "", value)
    value = re.sub(r"\b(as shown on the deed of trust)\b", "", value, flags=re.I)
    value = re.sub(r"\b(an|a)\s+arizona\s+.*", "", value, flags=re.I)
    value = re.sub(r"^[/: ]*(grantor|trustor|borrower)s?[:\-]?\s*", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" ,;:-")

    for pattern in OWNER_STATUS_PHRASES:
        value = re.sub(pattern, "", value, flags=re.I)

    value = re.sub(r"\s+and\s+and\s+", " and ", value, flags=re.I)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r",\s*,+", ", ", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = value.strip(" ,;:-")
    return _smart_title_name(value)


def _is_bad_owner(value: str) -> bool:
    if not value:
        return True

    vu = value.upper().strip()

    bad_starts = [
        "/GRANTOR",
        "GRANTOR:",
        "IN FAVOR OF",
        "IN WHICH",
        "AS OF THE RECORDING",
        "THAT CERTAIN",
        "UNDER THAT CERTAIN",
        "CURRENT BENEFICIARY",
        "ORIGINAL TRUSTOR",
    ]
    if any(vu.startswith(x) for x in bad_starts):
        return True

    bad_exact = {
        "AN UNMARRIED WOMAN",
        "AN UNMARRIED MAN",
        "A SINGLE MAN",
        "A SINGLE WOMAN",
        "SINGLE MAN",
        "SINGLE WOMAN",
        "UNMARRIED MAN",
        "UNMARRIED WOMAN",
        "NUMBER",
        "NUMBERS",
        "IDENTIFIABLE",
    }
    if vu in bad_exact:
        return True

    if vu.endswith(" AS"):
        return True

    return len(vu) < 4


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


def _normalize_for_match(value: str) -> str:
    value = value.upper()
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _extract_trustee_name(text: str) -> str:
    if not text:
        return ""

    normalized_text = _normalize_for_match(text)

    for trustee in TRUSTEE_WHITELIST:
        trustee_norm = _normalize_for_match(trustee)
        if trustee_norm in normalized_text:
            return trustee

    m = re.search(
        r"The undersigned Trustee,\s*([^,\n]+),\s*Attorney at Law",
        text,
        re.I,
    )
    if m:
        candidate = _clean_trustee_candidate(m.group(1))
        if _is_good_trustee_candidate(candidate):
            return candidate

    patterns = [
        r"Current Trustee[:\-]?\s*([^\n]+)",
        r"Successor Trustee[:\-]?\s*([^\n]+)",
        r"Substitute Trustee[:\-]?\s*([^\n]+)",
        r"Trustee[:\-]?\s*([^\n]+)",
        r"/Agent[:\-]?\s*([^\n]+)",
        r"/A gent[:\-]?\s*([^\n]+)",
    ]

    candidates: list[str] = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            candidates.append(m.group(1))

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if re.search(r"\b(trustee|successor trustee|substitute trustee|/agent)\b", line, re.I):
            cleaned_line = re.sub(r"(?i).*\b(trustee|successor trustee|substitute trustee|/agent|/a gent)\b[:\-]?", "", line).strip(" ,;:-")
            if cleaned_line:
                candidates.append(cleaned_line)
            for j in range(1, 3):
                if i + j < len(lines):
                    candidates.append(lines[i + j])

    for candidate in candidates:
        cleaned = _clean_trustee_candidate(candidate)
        if _is_good_trustee_candidate(cleaned):
            return cleaned

    return ""


def _clean_trustee_candidate(value: str) -> str:
    value = _normalize_one_line(value)
    value = value.strip(" /")
    value = re.sub(r"^(Agent|A gent)[:\-]?\s*", "", value, flags=re.I)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" ,;:-")


def _is_good_trustee_candidate(value: str) -> bool:
    if not value:
        return False

    vu = value.upper()

    if EMAIL_RE.search(value):
        return False
    if TRUSTEE_PHONE_RE.search(value):
        return False
    if re.search(r"\d{3,5}\s+\w+", value):
        return False
    if any(bad in vu for bad in TRUSTEE_BAD_PHRASES):
        return False
    if len(vu) < 4:
        return False

    good_tokens = [
        "TRUSTEE",
        "CORP",
        "CORPORATION",
        "INC",
        "INC.",
        "LLC",
        "COMPANY",
        "SERVICE",
        "SERVICES",
        "RECON",
        "MCDONALD",
        "HERBERS",
        "QUALITY",
        "MTC",
        "CLEAR",
        "WESTERN",
        "PRESTIGE",
        "NATIONWIDE",
        "AZ TRUSTEE",
    ]
    if any(token in vu for token in good_tokens):
        return True

    if re.fullmatch(r"[A-Z][A-Z .,'\-]{4,}", value):
        return True

    if re.fullmatch(r"[A-Za-z][A-Za-z .,'\-]{4,}", value):
        return True

    return False


def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    if not m:
        return ""

    parcel = m.group(1).strip().upper()
    parcel = re.sub(r"[^A-Z0-9\-]", "", parcel)

    if parcel in {"NUMBER", "NUMBERS", "IDENTIFIABLE"}:
        return ""

    parcel = re.sub(r"(\d{3}-\d{2}-\d{3})(\1)+", r"\1", parcel)
    m_clean = re.match(r"^\d{3}-\d{2}-\d{3}[A-Z]?$", parcel)
    return m_clean.group(0) if m_clean else ""


def _extract_auction_date(text: str) -> str:
    if not text:
        return ""

    patterns = [
        r"(?:Sale Date and Time|Sale Date|Auction Date|Date of Sale)[:\-]?\s*(.+?)(?:\n|Sale Location|Location|Place of Sale|$)",
        r"(?:The sale will be made at public auction on)[:\-]?\s*(.+?)(?:\n|at\s+\d{1,2}:\d{2}|$)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            value = m.group(1).strip()
            for date_match in DATE_NUMERIC_RE.finditer(value):
                if _is_valid_auction_date(date_match.group(0)):
                    return date_match.group(0)

    return ""


def _is_valid_auction_date(value: str) -> bool:
    try:
        dt = datetime.strptime(value, "%m/%d/%Y")
    except ValueError:
        try:
            dt = datetime.strptime(value, "%m/%d/%y")
        except ValueError:
            return False

    return dt.year >= 2026


def _extract_deed_of_trust(text: str, doc_num: str = "") -> str:
    candidates = DOC_NUM_RE.findall(text or "")
    for cand in candidates:
        if cand != doc_num:
            return cand
    return doc_num


def _extract_trustee_phone(text: str) -> str:
    for match in TRUSTEE_PHONE_RE.finditer(text or ""):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) != 10:
            continue
        if digits.startswith("20"):
            continue
        return raw
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

    city = KNOWN_CITY_FIXES.get(city.upper(), city)

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
