
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
EMAIL_RE = re.compile(r"\b[\w.\-+]+@[\w.\-]+\.\w+\b", re.I)

TRUSTEE_WHITELIST = {
    "CLEAR RECON CORP": {"aliases": ["CLEAR RECON CORP", "CLEAR RECON CORP."]},
    "MTC FINANCIAL INC": {"aliases": ["MTC FINANCIAL INC", "MTC FINANCIAL, INC.", "TRUSTEE CORPS", "TRUSTEE CORPS/MTC FINANCIAL", "CORPS"]},
    "QUALITY LOAN SERVICE CORPORATION": {"aliases": ["QUALITY LOAN SERVICE CORPORATION", "QUALITY LOAN SERVICE CORP"]},
    "PRESTIGE DEFAULT SERVICES, LLC": {"aliases": ["PRESTIGE DEFAULT SERVICES, LLC", "PRESTIGE DEFAULT SERVICES"]},
    "WESTERN PROGRESSIVE": {"aliases": ["WESTERN PROGRESSIVE", "WESTERN PROGRESSIVE - ARIZONA, INC.", "WESTERN PROGRESSIVE, LLC"]},
    "AZ TRUSTEE SERVICES": {"aliases": ["AZ TRUSTEE SERVICES", "ARIZONA TRUSTEE SERVICES"]},
    "PIONEER TITLE": {"aliases": ["PIONEER TITLE", "PIONEER TITLE AGENCY"]},
    "LEONARD J. MCDONALD": {"aliases": ["LEONARD J. MCDONALD"]},
}
TRUSTEE_ALIASES = {alias.upper(): canon for canon, meta in TRUSTEE_WHITELIST.items() for alias in meta["aliases"]}

OWNER_REMOVE_PHRASES = [
    r"\bA MARRIED MAN\b",
    r"\bA MARRIED WOMAN\b",
    r"\bAN UNMARRIED MAN\b",
    r"\bAN UNMARRIED WOMAN\b",
    r"\bA SINGLE MAN\b",
    r"\bA SINGLE WOMAN\b",
    r"\bA SINGLE PERSON\b",
    r"\bUNMARRIED\b",
    r"\bHUSBAND AND WIFE\b",
    r"\bWIFE AND HUSBAND\b",
    r"\bAS COMMUNITY PROPERTY\b",
    r"\bAS HIS SOLE AND SEPARATE PROPERTY\b",
    r"\bAS HER SOLE AND SEPARATE PROPERTY\b",
    r"\bAS THEIR SOLE AND SEPARATE PROPERTY\b",
    r"\bAS JOINT TENANTS WITH RIGHT OF SURVIVORSHIP\b",
    r"\bWITH RIGHTS? OF SURVIVORSHIP\b",
    r"\bAS JOINT TENANTS\b",
    r"\bSOLE AND SEPARATE PROPERTY\b",
    r"\bCOMMUNITY PROPERTY\b",
]
BAD_OWNER_EXACT = {
    "A MARRIED MAN", "A MARRIED WOMAN", "AN UNMARRIED MAN", "AN UNMARRIED WOMAN",
    "A SINGLE MAN", "A SINGLE WOMAN", "A SINGLE PERSON", "UNMARRIED",
    "NUMBER", "NUMBERS", "IDENTIFIABLE"
}
BAD_OWNER_PREFIXES = [
    "/GRANTOR", "IN FAVOR OF", "IN WHICH", "AS OF THE RECORDING",
    "THAT CERTAIN", "UNDER THAT CERTAIN", "NOTICE OF TRUSTEE"
]
BAD_TRUSTEE_PHRASES = [
    "CURRENT BENEFICIARY", "ORIGINAL TRUSTOR", "TRUSTEE’S PHONE", "TRUSTEE'S PHONE",
    "THE FOLLOWING INFORMATION IS PROVIDED", "COMMON DESIGNATION", "TO THE HIGHEST BIDDER",
    "NOTICE! IF YOU BELIEVE", "SALE OR IF", "LICENSED REAL ESTATE BROKER IN ARIZONA",
    "HEREIN QUALIFIES AS TRUSTEE", "TRUST DATED", "DEED OF TRUST", "RECORDED ON",
    "INSTRUMENT NO", "INSTRUMENT #", "ASSIGNMENT OF RENTS", "LOAN NUMBER", "LOAN NO", "TS#"
]


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
    if not owner:
        flags.append("owner_missing")

    trustee_name = _extract_trustee_name(text)
    if trustee_name:
        trustee_phone = _extract_trustee_phone(text, trustee_name)
    else:
        trustee_phone = ""

    prop = _extract_property_address(text)

    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    amount = _find_first(MONEY_RE, text)
    auction_date = _extract_auction_date(text)
    if not auction_date:
        flags.append("auction_date_missing")

    parcel_number = _clean_parcel_number(_extract_parcel_number(text))
    if not parcel_number:
        flags.append("parcel_missing")

    deed_of_trust = _extract_deed_of_trust(text)

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
    for pattern in OWNER_REMOVE_PHRASES:
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r",\s*,+", ", ", value)
    value = re.sub(r"\s{2,}", " ", value)
    value = value.strip(" ,;:-")
    value = re.sub(r"\bAND\s+AND\b", "AND", value, flags=re.I)
    value = re.sub(r",\s*AND\s+", " AND ", value, flags=re.I)
    value = re.sub(r"\s+,", ",", value).strip(" ,;:-")
    return value


def _is_bad_owner(value: str) -> bool:
    if not value:
        return True
    v = _normalize_one_line(value)
    vu = v.upper()
    if len(v) < 5:
        return True
    if vu in BAD_OWNER_EXACT:
        return True
    if any(vu.startswith(x) for x in BAD_OWNER_PREFIXES):
        return True
    if vu.endswith(" AS"):
        return True
    if "NOTICE OF TRUSTEE" in vu:
        return True
    return False


def _extract_owner(text: str) -> str:
    text = text or ""

    patterns = [
        r"Original Trustor(?:'s)?(?: Name and Address)?[:\-]?\s*(.+?)(?:Current Trustee|Successor Trustee|Trustee|Beneficiary|Sale Date|NOTICE OF TRUSTEE|$)",
        r"Trustor(?:s)?[:\-]?\s*(.+?)(?:Current Trustee|Successor Trustee|Trustee|Beneficiary|Property Address|Sale Date|$)",
        r"Grantor(?:s)?[:\-]?\s*(.+?)(?:Current Trustee|Successor Trustee|Trustee|Beneficiary|Property Address|Sale Date|$)",
        r"Borrower[:\-]?\s*(.+?)(?:Current Trustee|Successor Trustee|Trustee|Beneficiary|Property Address|Sale Date|$)",
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
    if not text:
        return ""

    # Hard whitelist first: if any alias is anywhere in text, return canonical name.
    text_upper = text.upper()
    for alias, canon in sorted(TRUSTEE_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in text_upper:
            return canon

    # Special exact attorney/trustee style.
    m = re.search(r"The undersigned Trustee,\s*([^,\n]+),\s*Attorney at Law", text, re.I)
    if m:
        cand = _normalize_one_line(m.group(1))
        if cand.upper() in TRUSTEE_ALIASES:
            return TRUSTEE_ALIASES[cand.upper()]
        if cand.upper() == "LEONARD J. MCDONALD":
            return "LEONARD J. MCDONALD"

    # No fallback guessing beyond whitelist.
    return ""


def _extract_trustee_phone(text: str, trustee_name: str) -> str:
    if not trustee_name:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    trustee_upper = trustee_name.upper()

    # Search near trustee alias/name lines first.
    best = ""
    for i, line in enumerate(lines):
        line_u = line.upper()
        if trustee_upper in line_u or any(alias in line_u for alias, canon in TRUSTEE_ALIASES.items() if canon == trustee_name):
            window = " ".join(lines[max(0, i - 1): min(len(lines), i + 3)])
            m = TRUSTEE_PHONE_RE.search(window)
            if m:
                phone = _normalize_phone(m.group(0))
                if _is_valid_phone(phone):
                    return phone

    # fallback to whole text only for valid formatted phone numbers
    for m in TRUSTEE_PHONE_RE.finditer(text):
        phone = _normalize_phone(m.group(0))
        if _is_valid_phone(phone):
            best = phone
            break
    return best


def _normalize_phone(phone: str) -> str:
    phone = re.sub(r"[^\d]", "", phone or "")
    if len(phone) == 11 and phone.startswith("1"):
        phone = phone[1:]
    if len(phone) != 10:
        return ""
    return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"


def _is_valid_phone(phone: str) -> bool:
    if not phone:
        return False
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) != 10:
        return False
    if digits.startswith(("000", "001", "002", "003", "004", "005", "024")):
        return False
    return True


def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _clean_parcel_number(parcel: str) -> str:
    if not parcel:
        return ""
    p = parcel.strip().upper()
    if p in {"NUMBER", "NUMBERS", "IDENTIFIABLE"}:
        return ""
    p = re.sub(r"[^A-Z0-9-]", "", p)
    # Collapse duplicated APN like 105-65-525105-65-525
    dup = re.match(r"^(\d{3}-\d{2}-\d{3}[A-Z]?)(\1)$", p)
    if dup:
        p = dup.group(1)
    # Prefer first valid APN-looking token
    m = re.search(r"\d{3}-\d{2}-\d{3}[A-Z]?", p)
    return m.group(0) if m else ""


def _extract_auction_date(text: str) -> str:
    if not text:
        return ""

    patterns = [
        r"(?:Sale Date and Time|Sale Date|Auction Date|Date of Sale)[:\-]?\s*(.+?)(?:\n|Sale Location|Location|$)",
        r"will be sold on[:\-]?\s*(.+?)(?:\n|at the hours?|at |Sale Location|$)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            value = m.group(1).strip()
            date_match = DATE_NUMERIC_RE.search(value)
            if date_match:
                date_text = date_match.group(0)
                year = int(date_text.split("/")[-1])
                if year >= 2026:
                    return date_text
    return ""


def _extract_deed_of_trust(text: str) -> str:
    # prefer instrument number style references before first raw doc number
    patterns = [
        r"Instrument\s*(?:No\.?|#)\s*(20\d{9,})",
        r"recorded on .*?Instrument\s*(?:No\.?|#)\s*(20\d{9,})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1).strip()
    return _find_first(DOC_NUM_RE, text)


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

    city = city.replace(",", "").strip()

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
