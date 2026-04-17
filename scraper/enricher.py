from __future__ import annotations

import logging
import re
from datetime import datetime
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
DATE_TEXT_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.I,
)
PARCEL_RE = re.compile(
    r"\b(?:APN|A\.P\.N\.|PARCEL\s*(?:NO|NUMBER)?|TAX\s*PARCEL\s*NUMBER|ASSESSOR(?:'S)?\s*(?:NO|NUMBER)?)[:\s#-]*([A-Z0-9-]{6,})",
    re.I,
)
DOC_NUM_RE = re.compile(r"\b20\d{9,}\b")

TRUSTEE_WHITELIST = {
    "CLEAR RECON": ("CLEAR RECON CORP", "(866) 931-0036"),
    "CLEAR RECON CORP": ("CLEAR RECON CORP", "(866) 931-0036"),
    "MTC FINANCIAL": ("MTC FINANCIAL INC", "(949) 252-8300"),
    "MTC FINANCIAL INC": ("MTC FINANCIAL INC", "(949) 252-8300"),
    "TRUSTEE CORPS": ("MTC FINANCIAL INC", "(949) 252-8300"),
    "QUALITY LOAN SERVICE": ("QUALITY LOAN SERVICE CORPORATION", "(866) 645-7711"),
    "QUALITY LOAN SERVICE CORPORATION": ("QUALITY LOAN SERVICE CORPORATION", "(866) 645-7711"),
    "PRESTIGE DEFAULT SERVICES": ("PRESTIGE DEFAULT SERVICES, LLC", "(949) 427-2010"),
    "PRESTIGE DEFAULT SERVICES, LLC": ("PRESTIGE DEFAULT SERVICES, LLC", "(949) 427-2010"),
    "LEONARD J. MCDONALD": ("LEONARD J. MCDONALD", "(602) 255-6035"),
    "LEONARD J MCDONALD": ("LEONARD J. MCDONALD", "(602) 255-6035"),
    "PRIME RECON": ("PRIME RECON LLC", "(888) 725-4142"),
    "PRIME RECON LLC": ("PRIME RECON LLC", "(888) 725-4142"),
    "(888) 725-4142": ("PRIME RECON LLC", "(888) 725-4142"),
    "RONALD B. HERB": ("RONALD B. HERB", "(602) 488-1349"),
    "RONALD HERB": ("RONALD B. HERB", "(602) 488-1349"),
    "RONALDHERB@GMAIL.COM": ("RONALD B. HERB", "(602) 488-1349"),
    "(602) 488-1349": ("RONALD B. HERB", "(602) 488-1349"),
    "LICENSED REAL ESTATE BROKER IN ARIZONA": ("RONALD B. HERB", "(602) 488-1349"),
    "WESTERN PROGRESSIVE": ("WESTERN PROGRESSIVE", ""),
    "AZ TRUSTEE SERVICES": ("AZ TRUSTEE SERVICES", ""),
    "PIONEER TITLE": ("PIONEER TITLE", ""),
}

OWNER_REMOVE_PATTERNS = [
    r"\bA MARRIED MAN\b",
    r"\bA MARRIED WOMAN\b",
    r"\bAN UNMARRIED MAN\b",
    r"\bAN UNMARRIED WOMAN\b",
    r"\bUNMARRIED\b",
    r"\bA SINGLE MAN\b",
    r"\bA SINGLE WOMAN\b",
    r"\bA SINGLE PERSON\b",
    r"\bHUSBAND AND WIFE\b",
    r"\bWIFE AND HUSBAND\b",
    r"\bAS COMMUNITY PROPERTY\b",
    r"\bAS HIS SOLE AND SEPARATE PROPERTY\b",
    r"\bAS HER SOLE AND SEPARATE PROPERTY\b",
    r"\bSOLE AND SEPARATE PROPERTY\b",
    r"\bAS JOINT TENANTS WITH RIGHT OF SURVIVORSHIP\b",
    r"\bWITH RIGHT OF SURVIVORSHIP\b",
    r"\bWITH RIGHTS OF SURVIVORSHIP\b",
    r"\bCOMMUNITY PROPERTY WITH RIGHTS OF SURVIVORSHIP\b",
]

BAD_OWNER_EXACT = {
    "AN UNMARRIED WOMAN",
    "AN UNMARRIED MAN",
    "A SINGLE MAN",
    "A SINGLE WOMAN",
    "A SINGLE PERSON",
    "NUMBER",
    "NUMBERS",
    "IDENTIFIABLE",
    "",
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



def _normalize_date_string(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def _extract_deed_of_trust_date(text: str) -> str:
    if not text:
        return ""

    compact = re.sub(r"[ \t]+", " ", text)
    compact = re.sub(r"\n{2,}", "\n", compact)

    patterns = [
        r"Deed of Trust recorded on[:\-\s]{0,20}([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"Deed of Trust recorded on[:\-\s]{0,20}(\d{1,2}/\d{1,2}/\d{4})",
        r"Deed of Trust .*? recorded .*? on[:\-\s]{0,20}([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"Deed of Trust .*? recorded .*? on[:\-\s]{0,20}(\d{1,2}/\d{1,2}/\d{4})",
        r"recorded on[:\-\s]{0,20}([A-Za-z]+\s+\d{1,2},\s+\d{4}).{0,80}?Deed of Trust",
        r"recorded on[:\-\s]{0,20}(\d{1,2}/\d{1,2}/\d{4}).{0,80}?Deed of Trust",
    ]
    for pat in patterns:
        m = re.search(pat, compact, re.I | re.S)
        if m:
            return _normalize_date_string(m.group(1))

    lines = [line.strip() for line in compact.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        block = " ".join(lines[max(0, i-1):i+2])
        if "deed of trust" in block.lower() and "recorded" in block.lower():
            m = DATE_NUMERIC_RE.search(block)
            if m:
                return _normalize_date_string(m.group(0))
            m2 = DATE_TEXT_RE.search(block)
            if m2:
                return _normalize_date_string(m2.group(0))

    return ""


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
    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    owner = _clean_owner(_extract_owner(text))
    if _is_bad_owner(owner):
        flags.append("owner_suspect")
        owner = ""

    trustee_name, trustee_phone = _extract_trustee(text)

    auction_date = _normalize_date_string(_extract_auction_date(text))
    if not auction_date:
        flags.append("auction_date_missing")

    prop = _extract_property_address(text)
    parcel_number = _clean_parcel_number(_extract_parcel_number(text))
    if not parcel_number:
        flags.append("parcel_missing")

    amount = _find_first(MONEY_RE, text)
    deed_of_trust = _extract_deed_of_trust_date(text)

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
    for pat in OWNER_REMOVE_PATTERNS:
        value = re.sub(pat, "", value, flags=re.I)
    value = re.sub(r"\bAND MAN AND\b", "AND", value, flags=re.I)
    value = re.sub(r"\bAND WOMAN AND\b", "AND", value, flags=re.I)
    value = re.sub(r"\bAND\s*,\s*AND\b", "AND", value, flags=re.I)
    value = re.sub(r"\bAS\s*$", "", value, flags=re.I)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r",\s*,", ", ", value)
    value = re.sub(r",\s+AND\b", " AND", value, flags=re.I)
    value = re.sub(r"\bAND\s+AND\b", "AND", value, flags=re.I)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" ,;:-")
    parts = [p.strip() for p in re.split(r"\bAND\b", value) if p.strip()]
    if parts:
        deduped = []
        seen = set()
        for p in parts:
            pu = p.upper()
            if pu not in seen:
                deduped.append(p)
                seen.add(pu)
        value = " AND ".join(deduped)
    return value.strip(" ,;:-")


def _is_bad_owner(value: str) -> bool:
    v = (value or "").strip()
    vu = v.upper()
    if vu in BAD_OWNER_EXACT:
        return True
    if len(v) < 5:
        return True
    bad_starts = [
        "/GRANTOR",
        "IN FAVOR OF",
        "IN WHICH",
        "AS OF THE RECORDING",
        "THAT CERTAIN",
        "UNDER THAT CERTAIN",
        "(AS OF THE RECORDING",
    ]
    return any(vu.startswith(x) for x in bad_starts)


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


def _extract_trustee(text: str) -> tuple[str, str]:
    text_u = (text or "").upper()

    priority_keys = [
        "QUALITY LOAN SERVICE CORPORATION",
        "QUALITY LOAN SERVICE",
        "PRESTIGE DEFAULT SERVICES, LLC",
        "PRESTIGE DEFAULT SERVICES",
        "CLEAR RECON CORP",
        "CLEAR RECON",
        "MTC FINANCIAL INC",
        "MTC FINANCIAL",
        "TRUSTEE CORPS",
        "LEONARD J. MCDONALD",
        "LEONARD J MCDONALD",
        "PRIME RECON LLC",
        "PRIME RECON",
        "(888) 725-4142",
        "RONALD B. HERB",
        "RONALD HERB",
        "RONALDHERB@GMAIL.COM",
        "(602) 488-1349",
        "LICENSED REAL ESTATE BROKER IN ARIZONA",
    ]
    for key in priority_keys:
        if key in text_u:
            return TRUSTEE_WHITELIST[key]

    for key, (name, phone) in TRUSTEE_WHITELIST.items():
        if key in text_u:
            return name, phone
    return "", ""

def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _clean_parcel_number(parcel: str) -> str:
    p = (parcel or "").strip().upper()
    if p in {"NUMBER", "NUMBERS", "IDENTIFIABLE", ""}:
        return ""
    m = re.search(r"(\d{3}-\d{2}-\d{3}[A-Z]?)", p)
    if m:
        return m.group(1)
    return ""


def _extract_auction_date(text: str) -> str:
    if not text:
        return ""

    def _year_ok(s: str) -> bool:
        m = re.search(r"(20\d{2})", s)
        return bool(m and int(m.group(1)) >= 2026)

    compact = re.sub(r"[ \t]+", " ", text)
    compact = re.sub(r"\n{2,}", "\n", compact)

    patterns = [
        r"(?:Sale Date(?: and Time)?|Auction Date|Date of Sale)[:\-\s]{0,20}([A-Za-z]+\s+\d{1,2},\s+20\d{2})",
        r"(?:Sale Date(?: and Time)?|Auction Date|Date of Sale)[:\-\s]{0,20}(\d{1,2}/\d{1,2}/20\d{2})",
        r"(?:will be sold on|to be sold on|sale to be held on|sale will be held on)[^\n]{0,100}?([A-Za-z]+\s+\d{1,2},\s+20\d{2})",
        r"(?:will be sold on|to be sold on|sale to be held on|sale will be held on)[^\n]{0,100}?(\d{1,2}/\d{1,2}/20\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, compact, re.I | re.S)
        if m:
            candidate = m.group(1).strip()
            if _year_ok(candidate):
                return candidate

    lines = [line.strip() for line in compact.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        block = " ".join(lines[i:i+3])
        l = block.lower()
        if any(k in l for k in ["sale", "auction", "sold"]):
            m = DATE_NUMERIC_RE.search(block)
            if m and _year_ok(m.group(0)):
                return m.group(0)
            m2 = DATE_TEXT_RE.search(block)
            if m2 and _year_ok(m2.group(0)):
                return m2.group(0)

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
            if parsed["address"] and not _is_bad_property_address(parsed):
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
        if parsed["address"] and parsed["zip"] and not _is_bad_property_address(parsed):
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
    city = tagged.get("PlaceName", "").replace(",", "").strip()
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
