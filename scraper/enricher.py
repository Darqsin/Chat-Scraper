from __future__ import annotations

import logging
import re
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

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
STREET_ONLY_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9 .'\-#/]+\s(?:AVE|AVENUE|ST|STREET|RD|ROAD|DR|DRIVE|LN|LANE|BLVD|BOULEVARD|CT|COURT|CIR|CIRCLE|PL|PLACE|PKWY|PARKWAY|TRL|TRAIL|WAY)\b(?:\s+(?:UNIT|STE|APT|#)\s*\w+)?",
    re.I,
)

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
    "PIONEER TITLE": ("PIONEER TITLE AGENCY, INC.", ""),
    "NESTOR SOLUTIONS": ("NESTOR SOLUTIONS, LLC", ""),
    "AMERICA WEST LENDER SERVICES": ("AMERICA WEST LENDER SERVICES, LLC", ""),
    "STATEWIDE FORECLOSURE SERVICES": ("STATEWIDE FORECLOSURE SERVICES, INC.", ""),
    "VYLLA SOLUTIONS": ("VYLLA SOLUTIONS, LLC", ""),
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
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _parse_date(value: str) -> Optional[datetime]:
    v = _normalize_date_string(value)
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return None


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = text.replace("\f", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
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


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _line_is_header_junk(line: str) -> bool:
    u = line.upper()
    if not line.strip():
        return True
    junk_terms = [
        "UNOFFICIAL",
        "DOCUMENT",
        "RECORDING REQUESTED",
        "WHEN RECORDED",
        "RETURN TO",
        "MAIL TO",
        "THIS INFORMATION WAS RECORDED AT REQUEST OF",
        "NAME, ADDRESS",
        "TELEPHONE NUMBER OF TRUSTEE",
        "AS OF RECORDING",
        "NOTICE OF SALE",
        "PAGE ",
        "TS#:",
        "ORDER #:",
        "ATTN:",
        "PHONE:",
        "TELEPHONE:",
        "SALES INFORMATION",
        "ARIZONA DEPARTMENT OF",
        "MOUNTAIN STANDARD TIME",
    ]
    if any(term in u for term in junk_terms):
        return True
    if re.fullmatch(r"[\W_]+", line):
        return True
    if len(u) <= 3:
        return True
    return False


def _looks_like_company(line: str) -> bool:
    u = line.upper()
    company_terms = [
        "LLC", "L.L.C", "INC", "CORP", "CORPORATION", "SERVICES", "SERVICE",
        "SOLUTIONS", "TITLE", "RECON", "FINANCIAL", "DEFAULT", "LAW", "LLP",
        "P.A.", "AGENCY", "BOSCO", "TRUSTEE", "PIONEER", "NESTOR", "VYLLA",
        "STATEWIDE", "QUALITY", "CLEAR RECON", "AMERICA WEST", "MTC", "PRIME RECON",
    ]
    return any(term in u for term in company_terms)


def _looks_like_person_or_owner(line: str) -> bool:
    if not line:
        return False
    u = line.upper()
    if _line_is_header_junk(line):
        return False
    if re.search(r"\b\d{3,6}\b", line) and STREET_ONLY_RE.search(line):
        return False
    if re.search(r"\b(AZ|ARIZONA|CALIFORNIA|NEVADA|TEXAS)\b", u):
        return False
    if re.search(r"\bP\.?O\.?\s*BOX\b", u):
        return False
    if _looks_like_company(line):
        return False
    words = re.findall(r"[A-Z][A-Za-z'.-]+", line)
    return len(words) >= 2


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
    value = re.sub(r"\b\d{5,}\b.*$", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,;:-")


def _clean_owner_noise(value: str) -> str:
    v = _clean_owner(value)
    if not v:
        return ""
    v = re.sub(r"^.*?NAME AND ADDRESS[:\s]*", "", v, flags=re.I).strip(" ,;:-")
    v = re.sub(r"^.*?STATED NAME AND ADDRESS[:\s]*", "", v, flags=re.I).strip(" ,;:-")
    v = re.sub(r"^['’]S\s+NAME\s+AND\s+ADDRESS[:\s]*", "", v, flags=re.I).strip(" ,;:-")
    v = re.sub(r"^AS SHOWN ON THE DEED OF TRUST[:\s-]*", "", v, flags=re.I).strip(" ,;:-")
    v = re.sub(r"\bWHEREAS,.*$", "", v, flags=re.I).strip(" ,;:-")
    v = re.sub(r"\s+", " ", v).strip(" ,;:-")
    return v


def _is_bad_owner(value: str) -> bool:
    v = (value or "").strip()
    vu = v.upper()
    if vu in BAD_OWNER_EXACT:
        return True
    if len(v) < 5:
        return True
    if "(AS SHOWN ON THE DEED OF TRUST)" in vu:
        return True
    bad_starts = [
        "/GRANTOR",
        "IN FAVOR OF",
        "IN WHICH",
        "AS OF THE RECORDING",
        "THAT CERTAIN",
        "UNDER THAT CERTAIN",
        "(AS OF THE RECORDING",
        "RECORDING REQUESTED",
        "WHEN RECORDED",
        "UNOFFICIAL",
        "P.O. BOX",
        "NOTICE TO POTENTIAL BIDDERS",
        "COUNTY OF",
    ]
    if any(vu.startswith(x) for x in bad_starts):
        return True
    if re.search(r"\b(CAMINO DEL RIO|SAN DIEGO, CA|HTTP://|WWW\.|DEFAULT SERVICES DEPARTMENT|TRUST CREATED BY SAID DEED OF TRUST)\b", vu):
        return True
    if re.search(r"\b\d{1,6}\s+\w+", v) and re.search(r"\b(AZ|CA|ARIZONA|CALIFORNIA)\b", vu):
        return True
    return False


def _extract_block_after_labels(text: str, labels: list[str], max_lines: int = 8) -> list[str]:
    lines = _lines(text)
    for i, line in enumerate(lines):
        low = line.lower()
        if any(label.lower() in low for label in labels):
            block = []
            for j in range(i + 1, min(len(lines), i + 1 + max_lines)):
                ln = lines[j].strip()
                if not ln:
                    continue
                if any(stop in ln.lower() for stop in [
                    "notice of trustee sale", "sale date", "auction date",
                    "date of sale", "property address", "street address",
                    "apn", "a.p.n", "legal description", "beneficiary",
                    "current trustee", "successor trustee", "substitute trustee"
                ]) and block:
                    break
                block.append(ln)
            if block:
                return block
    return []


def _extract_owner(text: str) -> str:
    block = _extract_block_after_labels(
        text,
        [
            "name and address of original trustor",
            "original trustor",
            "trustor",
            "grantor",
            "borrower",
            "owner of record",
        ],
        max_lines=10,
    )
    for line in block:
        line = _clean_owner_noise(line)
        if _looks_like_person_or_owner(line) and not _is_bad_owner(line):
            return line

    patterns = [
        r"Original Trustor(?:'s)?(?: Name and Address)?[:\-]?\s*(.+?)(?:Current Trustee|Substitute Trustee|Trustee|Beneficiary|Sale Date|Auction Date|NOTICE OF TRUSTEE|$)",
        r"Trustor(?:s)?[:\-]?\s*(.+?)(?:Current Trustee|Substitute Trustee|Trustee|Beneficiary|Property Address|Sale Date|Auction Date|$)",
        r"Grantor(?:s)?[:\-]?\s*(.+?)(?:Current Trustee|Substitute Trustee|Trustee|Beneficiary|Property Address|Sale Date|Auction Date|$)",
        r"Borrower[:\-]?\s*(.+?)(?:Current Trustee|Substitute Trustee|Trustee|Beneficiary|Property Address|Sale Date|Auction Date|$)",
        r"Owner(?: of Record)?[:\-]?\s*(.+?)(?:Current Trustee|Substitute Trustee|Trustee|Beneficiary|Property Address|Sale Date|Auction Date|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I | re.S)
        if m:
            for piece in re.split(r"[\n|]+", m.group(1)):
                value = _clean_owner_noise(piece)
                if _looks_like_person_or_owner(value) and not _is_bad_owner(value):
                    return value
    return ""


def _extract_trustee(text: str) -> tuple[str, str]:
    text_u = (text or "").upper()

    candidate_name, candidate_phone = "", ""
    for key, (name, phone) in TRUSTEE_WHITELIST.items():
        if key in text_u:
            candidate_name, candidate_phone = name, phone
            break

    block = _extract_block_after_labels(
        text,
        [
            "NAME, ADDRESS & TELEPHONE NUMBER OF TRUSTEE",
            "NAME, ADDRESS AND TELEPHONE NUMBER OF TRUSTEE",
            "NAME AND ADDRESS OF TRUSTEE",
            "CURRENT TRUSTEE",
            "SUCCESSOR TRUSTEE",
            "SUBSTITUTE TRUSTEE",
            "NAME AND ADDRESS OF CURRENT SUCCESSOR TRUSTEE",
        ],
        max_lines=10,
    )

    phone = ""
    block_joined = " ".join(block)
    m_phone = TRUSTEE_PHONE_RE.search(block_joined)
    if m_phone:
        phone = m_phone.group(0)

    for line in block:
        clean = _normalize_one_line(re.sub(r"^[^:]*:\s*", "", line))
        if _line_is_header_junk(clean):
            continue
        u = clean.upper()
        if "AS OF RECORDING" in u:
            continue
        if STREET_ONLY_RE.search(clean):
            continue
        if re.search(r"\b(AZ|ARIZONA|CALIFORNIA|NEVADA|TEXAS)\b", u):
            continue
        if _looks_like_company(clean):
            for key, (name, wl_phone) in TRUSTEE_WHITELIST.items():
                if key in u:
                    return name, phone or wl_phone
            return clean, phone

    for line in block:
        clean = _normalize_one_line(re.sub(r"^[^:]*:\s*", "", line))
        if _line_is_header_junk(clean):
            continue
        if re.search(r"\b(AZ|ARIZONA|CALIFORNIA|NEVADA|TEXAS)\b", clean.upper()):
            continue
        if STREET_ONLY_RE.search(clean):
            continue
        if re.search(r"\b[A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+){1,3}\b", clean):
            return clean, phone

    return candidate_name, candidate_phone or phone


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


def _extract_auction_date(text: str, filed: str = "") -> str:
    filed_dt = _parse_date(filed)
    min_dt = _parse_date("2026-01-01")

    candidates: list[tuple[int, str]] = []
    lines = _lines(text)
    for i, line in enumerate(lines):
        block = " ".join(lines[max(0, i - 1): min(len(lines), i + 4)])
        low = block.lower()
        if any(term in low for term in ["sale date", "auction date", "date of sale", "will occur", "will be sold", "sale will be held", "to be sold on", "public auction"]):
            for m in DATE_NUMERIC_RE.finditer(block):
                candidates.append((i, m.group(0)))
            for m in DATE_TEXT_RE.finditer(block):
                candidates.append((i, m.group(0)))

    seen = set()
    for _, raw in sorted(candidates, key=lambda x: x[0]):
        norm = _normalize_date_string(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        dt = _parse_date(norm)
        if not dt:
            continue
        if min_dt and dt < min_dt:
            continue
        if filed_dt and dt < filed_dt:
            continue
        return norm

    return ""


def _extract_deed_of_trust_date(text: str) -> str:
    compact = re.sub(r"[ \t]+", " ", text or "")
    compact = re.sub(r"\n{2,}", "\n", compact)
    patterns = [
        r"Deed of Trust recorded on[:\-\s]{0,20}([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"Deed of Trust recorded on[:\-\s]{0,20}(\d{1,2}/\d{1,2}/\d{4})",
        r"Deed of Trust .*? recorded .*? on[:\-\s]{0,20}([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        r"Deed of Trust .*? recorded .*? on[:\-\s]{0,20}(\d{1,2}/\d{1,2}/\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, compact, re.I | re.S)
        if m:
            return _normalize_date_string(m.group(1))
    return ""


def _extract_property_address(text: str) -> dict[str, str]:
    lines = _lines(text)

    def parse_city_state_zip(line: str) -> tuple[str, str, str]:
        line = _normalize_one_line(line)
        m = re.search(r"([A-Za-z .'-]+),?\s+(AZ|Arizona)\s+(\d{5}(?:-\d{4})?)", line, re.I)
        if not m:
            return "", "", ""
        city = m.group(1).strip(" ,")
        return city, "AZ", m.group(3)[:5]

    label_terms = [
        "street address or identifiable location",
        "the street address/location of the real property described above is purported to be",
        "purported street address",
        "property address",
        "common designation",
    ]

    for i, line in enumerate(lines):
        if any(term in line.lower() for term in label_terms):
            block = lines[i + 1: i + 6]
            street = ""
            city = ""
            state = ""
            zipcode = ""
            for idx, ln in enumerate(block):
                ln_clean = _normalize_one_line(ln)
                if not street:
                    m_street = STREET_ONLY_RE.search(ln_clean)
                    if m_street:
                        street = m_street.group(0).strip(" ,")
                        continue
                c, s, z = parse_city_state_zip(ln_clean)
                if c:
                    city, state, zipcode = c, s, z
                    if not street and idx > 0:
                        maybe_prev = _normalize_one_line(block[idx - 1])
                        m_prev = STREET_ONLY_RE.search(maybe_prev)
                        if m_prev:
                            street = m_prev.group(0).strip(" ,")
                    break
            if street:
                candidate = {"address": street, "city": city, "state": state, "zip": zipcode}
                if not _is_bad_property_address(candidate):
                    return candidate

    full = re.search(
        r"(\d{1,6}\s+[A-Za-z0-9 .'\-#/]+),\s*([A-Za-z .'-]+),?\s+(AZ|Arizona)\s+(\d{5}(?:-\d{4})?)",
        text or "",
        re.I,
    )
    if full:
        candidate = {
            "address": _normalize_one_line(full.group(1)),
            "city": full.group(2).strip(" ,"),
            "state": "AZ",
            "zip": full.group(4)[:5],
        }
        if not _is_bad_property_address(candidate):
            return candidate

    return {"address": "", "city": "", "state": "", "zip": ""}




def _cleanup_owner_final(value: str) -> str:
    v = _clean_owner_noise(value)
    if not v:
        return ""
    # reject obvious non-owner lines
    if re.search(r"\b(CAMINO DEL RIO|SAN DIEGO|HTTP|WWW\.|P\.?O\.?\s*BOX|CARE OF/SERVICER|DEFAULT SERVICES DEPARTMENT)\b", v, re.I):
        return ""
    if re.search(r"\b(COUNTY OF|NOTICE TO POTENTIAL BIDDERS|TRUST CREATED BY SAID DEED OF TRUST)\b", v, re.I):
        return ""
    # trim trailing qualifiers
    trims = [
        r"\b,\s*NOT AS TENANTS.*$",
        r"\b,\s*NOT\b.*$",
        r"\b,\s*AS JOINT.*$",
        r"\b,\s*AS COMMUNITY.*$",
        r"\b,\s*WITH RIGHT.*$",
        r"\b,\s*COMMUNITY\b.*$",
        r"\b,\s*MAN\b.*$",
        r"\b,\s*WOMAN\b.*$",
        r"\b,\s*HUSBAND\b.*$",
        r"\b,\s*WIFE\b.*$",
        r"\bAND\s*$",
        r"\bAS\s*$",
    ]
    for pat in trims:
        v = re.sub(pat, "", v, flags=re.I).strip(" ,;:-")
    v = v.replace("AN PERSON", "AN INDIVIDUAL")
    v = v.replace(" Carbaja! ", " Carbajal ")
    return v.strip(" ,;:-")


def _is_bad_trustee_text(value: str) -> bool:
    v = _normalize_one_line(value)
    if not v:
        return True
    u = v.upper()
    bad_terms = [
        "HTTP://", "WWW.", "PLEASE BE ADVISED", "IF THE SALE IS SET ASIDE",
        "NOTARY PUBLIC", "ON THIS", "CARE OF/SERVICER", "TWENTY FOURTH FLOOR",
        "DEFAULT SERVICES DEPARTMENT", "ATTORNEY AT LAW, TRUSTEE, IS REGULATED",
        "THE TRUSTEE SHALL NOT", "PURCHASER", "HIGHEST BIDDER",
    ]
    if any(term in u for term in bad_terms):
        return True
    if re.search(r"\b(CAMINO DEL RIO|SAN DIEGO, CA)\b", u):
        return True
    return False


def _cleanup_trustee_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _is_bad_property_address(parsed: dict[str, str]) -> bool:
    address = (parsed.get("address") or "").upper().strip()
    city = (parsed.get("city") or "").upper().strip()
    zip_code = (parsed.get("zip") or "").strip()
    if address in {"201 W JEFFERSON", "201 W JEFFERSON STREET", "201 WEST JEFFERSON", "201 WEST JEFFERSON STREET"}:
        return True
    if city == "PHOENIX" and zip_code == "85003" and "JEFFERSON" in address:
        return True
    if address.startswith("1850 N CENTRAL AVE"):
        return True
    return False

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

    owner = _cleanup_owner_final(_extract_owner(text))
    if _is_bad_owner(owner):
        flags.append("owner_suspect")
        owner = ""

    trustee_name, trustee_phone = _extract_trustee(text)
    trustee_phone = _cleanup_trustee_phone(trustee_phone)
    if not trustee_name or _is_bad_trustee_text(trustee_name):
        trustee_name = ""
        trustee_phone = ""
        flags.append("trustee_suspect")

    auction_date = _extract_auction_date(text, filed=filed)
    if not auction_date:
        flags.append("auction_date_missing")

    prop = _extract_property_address(text)
    if not prop.get("address"):
        flags.append("address_missing")

    parcel_number = _clean_parcel_number(_extract_parcel_number(text))
    if not parcel_number:
        flags.append("parcel_missing")

    amount = _find_first(MONEY_RE, text)
    deed_of_trust = _extract_deed_of_trust_date(text)
    if deed_of_trust and auction_date and deed_of_trust >= auction_date:
        deed_of_trust = ""

    score = 0
    if prop.get("address"):
        score += 30
    if owner:
        score += 20
    if trustee_name:
        score += 10
    if auction_date:
        score += 10
    if parcel_number:
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
            flags=sorted(set(flags)),
            score=score,
            raw_text_path=str(raw_text_path),
        )
    )
