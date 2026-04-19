from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_path

# Uncomment if running locally on Windows and Tesseract is not on PATH.
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
APN_RE = re.compile(r"\b(?:APN|A\.P\.N\.|Parcel(?:\s+No\.?|\s+Number)?)[\s:#-]*([0-9\-]{6,20})\b", re.I)
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?([2-9][0-9]{2})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})")
MONEY_RE = re.compile(r"\$\s?([0-9,]+(?:\.\d{2})?)")
ZIP_RE = re.compile(r"\bAZ\s+(\d{5})(?:-\d{4})?\b", re.I)
AZ_CITY_STATE_ZIP_RE = re.compile(r"\b([A-Za-z][A-Za-z .'-]+),\s*AZ\s+(\d{5})(?:-\d{4})?\b", re.I)
FULL_ADDRESS_RE = re.compile(
    r"\b(\d{1,6}\s+[A-Za-z0-9#./' -]+?(?:AVE|AVENUE|ST|STREET|RD|ROAD|DR|DRIVE|LN|LANE|CT|COURT|PL|PLACE|WAY|BLVD|BOULEVARD|PKWY|PARKWAY|CIR|CIRCLE|TER|TERRACE|HWY|HIGHWAY|TRL|TRAIL)\b(?:\s+(?:APT|UNIT|LOT|STE|SUITE)\s*[A-Za-z0-9-]+)?)\s*,?\s*([A-Za-z][A-Za-z .'-]+),\s*AZ\s+(\d{5})(?:-\d{4})?\b",
    re.I,
)

BAD_ADDRESS_TERMS = [
    "201 w jefferson",
    "superior court",
    "courthouse",
    "camino del rio",
    "gillette ave",
    "recording requested by",
    "return to",
    "mail to",
    "attention:",
    "sales line",
    "www.",
    "http",
    "trustee",
    "servicer",
    "law group",
    "default services",
    "quality loan",
    "barrett daffin",
    "mccarthy holthus",
    "aztec foreclosure",
    "western progressive",
    "suite ",
    " floor",
]

TRUSTEE_BAD_TERMS = [
    "trustor",
    "beneficiary",
    "deed of trust",
    "sales line",
    "telephone",
    "phone",
    "www.",
    "http",
    "auction",
    "courthouse",
    "recorded",
    "when recorded mail to",
    "return to",
]

KNOWN_TRUSTEES = [
    "quality loan service corporation",
    "mccarthy holthus",
    "barrett daffin",
    "western progressive",
    "aztec foreclosure corporation",
    "clear recon corp",
    "national default servicing corporation",
    "trustee corps",
    "prestige default services",
]

OWNER_LABELS = [
    "original trustor",
    "trustor",
    "borrower",
    "grantor",
]

AUCTION_CONTEXT_TERMS = [
    "date of sale",
    "sale date",
    "auction date",
    "sale will be held",
    "sale to be held",
    "trustee's sale",
    "trustee sale",
]


def normalize_space(value: str) -> str:
    value = value.replace("\x00", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)
    return value.strip()


def clean_field(value: str) -> str:
    value = normalize_space(value)
    value = re.sub(r"^[,:;\-\s]+", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,;:-")


def split_lines(text: str) -> list[str]:
    return [clean_field(line) for line in text.splitlines() if clean_field(line)]


def write_raw_text(text: str, raw_text_dir: Optional[Path], doc_num: str) -> str:
    if not raw_text_dir or not doc_num:
        return ""
    raw_text_dir.mkdir(parents=True, exist_ok=True)
    out = raw_text_dir / f"{doc_num}.txt"
    out.write_text(text, encoding="utf-8")
    return str(out)


def ocr_pdf(path: Path) -> str:
    images = convert_from_path(path)
    chunks: list[str] = []
    for img in images:
        chunks.append(pytesseract.image_to_string(img))
    return "\n".join(chunks)


def extract_text(pdf_path: Path) -> tuple[str, bool]:
    text_parts: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text() or ""
                if extracted.strip():
                    text_parts.append(extracted)
    except Exception:
        text_parts = []

    pdf_text = normalize_space("\n".join(text_parts))
    if len(pdf_text) >= 400:
        return pdf_text, False

    ocr_text = normalize_space(ocr_pdf(pdf_path))
    if len(ocr_text) > len(pdf_text):
        return ocr_text, True

    return pdf_text or ocr_text, bool(ocr_text and not pdf_text)


def is_bad_address_line(value: str) -> bool:
    lower = value.lower()
    return any(term in lower for term in BAD_ADDRESS_TERMS)


def normalize_address_line(value: str) -> str:
    value = re.sub(r"(?i)^(street|property|purported)\s+address\s*[:\-]?\s*", "", value)
    value = re.sub(r"(?i)^common\s+address\s*[:\-]?\s*", "", value)
    return clean_field(value)


def looks_like_street(value: str) -> bool:
    value = normalize_address_line(value)
    if not re.search(r"\b\d{1,6}\b", value):
        return False
    if not re.search(r"\b(?:AVE|AVENUE|ST|STREET|RD|ROAD|DR|DRIVE|LN|LANE|CT|COURT|PL|PLACE|WAY|BLVD|BOULEVARD|PKWY|PARKWAY|CIR|CIRCLE|TER|TERRACE|HWY|HIGHWAY|TRL|TRAIL)\b", value, re.I):
        return False
    return not is_bad_address_line(value)


def looks_like_city_state_zip(value: str) -> bool:
    return bool(AZ_CITY_STATE_ZIP_RE.search(value))


def title_name(value: str) -> str:
    words = []
    for word in clean_field(value).split():
        if word.isupper() and len(word) > 1:
            words.append(word.title())
        else:
            words.append(word)
    return " ".join(words).strip()


def compact_name(value: str) -> str:
    value = clean_field(value)
    value = re.sub(r"(?i)\bwhose address is.*$", "", value)
    value = re.sub(r"(?i)\bwhose street address is.*$", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return title_name(value)


def split_person_names(full_name: str) -> tuple[str, str, str, str]:
    cleaned = compact_name(full_name)
    if not cleaned:
        return "", "", "", ""

    parts = [p.strip() for p in re.split(r"\s+(?:and|&)\s+", cleaned, maxsplit=1) if p.strip()]

    def split_one(name: str) -> tuple[str, str]:
        tokens = [t for t in name.split() if t]
        if not tokens:
            return "", ""
        if len(tokens) == 1:
            return tokens[0], ""
        return " ".join(tokens[:-1]), tokens[-1]

    first_name, last_name = split_one(parts[0])
    second_first, second_last = ("", "")
    if len(parts) > 1:
        second_first, second_last = split_one(parts[1])
    return first_name, last_name, second_first, second_last


def extract_owner(text: str, lines: list[str]) -> str:
    patterns = [
        re.compile(r"(?i)original\s+trustor\s*[:\-]\s*(.+)"),
        re.compile(r"(?i)trustor\s*[:\-]\s*(.+)"),
        re.compile(r"(?i)borrower\s*[:\-]\s*(.+)"),
    ]

    for line in lines:
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                value = compact_name(match.group(1))
                if value and "trustee" not in value.lower():
                    return value

    for idx, line in enumerate(lines):
        lower = line.lower()
        if any(label in lower for label in OWNER_LABELS):
            joined = line
            if idx + 1 < len(lines) and len(line.split()) <= 4:
                joined = f"{line} {lines[idx + 1]}"
            joined = re.sub(r"(?i)^.*?(original\s+trustor|trustor|borrower|grantor)\s*[:\-]?\s*", "", joined)
            value = compact_name(joined)
            if value and "trustee" not in value.lower():
                return value

    return ""


def score_trustee_candidate(value: str) -> int:
    lower = value.lower()
    score = 0
    if any(term in lower for term in TRUSTEE_BAD_TERMS):
        score -= 5
    if "successor trustee" in lower:
        score += 5
    elif re.search(r"\btrustee\b", lower):
        score += 3
    if any(name in lower for name in KNOWN_TRUSTEES):
        score += 6
    if PHONE_RE.search(value):
        score -= 2
    if len(value) > 120:
        score -= 2
    if "," in value:
        score += 1
    return score


def extract_trustee(text: str, lines: list[str]) -> str:
    candidates: list[tuple[int, str]] = []

    explicit_patterns = [
        re.compile(r"(?i)successor\s+trustee\s*[:\-]\s*(.+)"),
        re.compile(r"(?i)trustee\s*[:\-]\s*(.+)"),
    ]

    for line in lines:
        for pattern in explicit_patterns:
            match = pattern.search(line)
            if match:
                candidate = compact_name(match.group(1))
                if candidate:
                    candidates.append((score_trustee_candidate(candidate) + 3, candidate))

        if "trustee" in line.lower():
            cleaned = compact_name(re.sub(r"(?i)^.*?trustee\s*[:\-]?\s*", "", line))
            if cleaned and cleaned.lower() != line.lower():
                candidates.append((score_trustee_candidate(cleaned), cleaned))

        if any(name in line.lower() for name in KNOWN_TRUSTEES):
            candidates.append((score_trustee_candidate(line) + 2, compact_name(line)))

    if not candidates:
        return ""

    candidates = [(score, value) for score, value in candidates if score >= 3 and value]
    if not candidates:
        return ""

    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    return candidates[0][1]


def candidate_address_pairs(lines: list[str]) -> list[tuple[int, str, str, str, str]]:
    candidates: list[tuple[int, str, str, str, str]] = []

    for idx, line in enumerate(lines):
        lower = line.lower()

        for match in FULL_ADDRESS_RE.finditer(line):
            street, city, zip_code = match.groups()
            street = normalize_address_line(street)
            if not is_bad_address_line(street):
                score = 10
                if "property" in lower or "street address" in lower or "purported" in lower:
                    score += 5
                candidates.append((score, street, title_name(city), "AZ", zip_code))

        if idx + 1 < len(lines):
            street = normalize_address_line(line)
            city_line = clean_field(lines[idx + 1])
            if looks_like_street(street) and looks_like_city_state_zip(city_line):
                city_match = AZ_CITY_STATE_ZIP_RE.search(city_line)
                if city_match and not is_bad_address_line(street) and not is_bad_address_line(city_line):
                    city, zip_code = city_match.groups()
                    score = 8
                    if "street address" in lower or "property" in lower or "purported" in lower:
                        score += 5
                    if idx > 0 and any(token in lines[idx - 1].lower() for token in ["street address", "property address", "purported"]):
                        score += 4
                    candidates.append((score, street, title_name(city), "AZ", zip_code))

    return candidates


def extract_address(text: str, lines: list[str]) -> tuple[str, str, str, str]:
    candidates = candidate_address_pairs(lines)
    if not candidates:
        return "", "", "", ""

    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    _, street, city, state, zip_code = candidates[0]
    return street, city, state, zip_code


def extract_mailing_address(lines: list[str], prop_address: str) -> tuple[str, str, str, str]:
    for idx, line in enumerate(lines[:-1]):
        lower = line.lower()
        if "mail to" in lower or "return to" in lower or "when recorded mail to" in lower:
            street = normalize_address_line(lines[idx + 1])
            city_line = clean_field(lines[idx + 2]) if idx + 2 < len(lines) else ""
            if looks_like_street(street) and looks_like_city_state_zip(city_line):
                city_match = AZ_CITY_STATE_ZIP_RE.search(city_line)
                if city_match and street.lower() != prop_address.lower():
                    city, zip_code = city_match.groups()
                    return street, title_name(city), "AZ", zip_code
    return "", "", "", ""


def extract_parcel_number(text: str) -> str:
    match = APN_RE.search(text)
    return clean_field(match.group(1)) if match else ""


def extract_phone(text: str) -> str:
    match = PHONE_RE.search(text)
    if not match:
        return ""
    return f"({match.group(1)}) {match.group(2)}-{match.group(3)}"


def extract_amount(text: str) -> str:
    match = MONEY_RE.search(text)
    return match.group(1) if match else ""


def extract_auction_date(text: str, lines: list[str]) -> str:
    candidates: list[datetime] = []

    for idx, line in enumerate(lines):
        window = " ".join(lines[max(0, idx - 1): min(len(lines), idx + 2)])
        lower = window.lower()
        if not any(term in lower for term in AUCTION_CONTEXT_TERMS):
            continue

        for match in DATE_RE.findall(window):
            try:
                dt = datetime.strptime(match, "%m/%d/%Y")
                if 2024 <= dt.year <= 2035:
                    candidates.append(dt)
            except ValueError:
                continue

    if not candidates:
        for match in DATE_RE.findall(text):
            try:
                dt = datetime.strptime(match, "%m/%d/%Y")
                if 2024 <= dt.year <= 2035:
                    candidates.append(dt)
            except ValueError:
                continue

    if not candidates:
        return ""

    candidates.sort()
    return candidates[0].strftime("%Y-%m-%d")


def extract_deed_of_trust(text: str) -> str:
    patterns = [
        re.compile(r"(?i)deed of trust(?:\s+dated)?\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})"),
        re.compile(r"(?i)recorded\s+(\d{1,2}/\d{1,2}/\d{4}).{0,40}?deed of trust"),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return datetime.strptime(match.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                return match.group(1)
    return ""


def build_flags(*, used_ocr: bool, owner: str, trustee_name: str, prop_address: str, parcel_number: str, auction_date: str) -> list[str]:
    flags: list[str] = []
    if used_ocr:
        flags.append("ocr_used")
    if not owner:
        flags.append("missing_owner")
    if not trustee_name:
        flags.append("missing_trustee")
    if not prop_address:
        flags.append("missing_address")
    if not parcel_number:
        flags.append("missing_apn")
    if not auction_date:
        flags.append("missing_auction_date")
    return flags


def score_record(owner: str, trustee_name: str, prop_address: str, parcel_number: str, auction_date: str) -> int:
    score = 0
    if owner:
        score += 25
    if trustee_name:
        score += 20
    if prop_address:
        score += 30
    if parcel_number:
        score += 15
    if auction_date:
        score += 10
    return score


def parse_record(**kwargs):
    pdf_path = Path(kwargs.get("pdf_path"))
    raw_text_dir = kwargs.get("raw_text_dir")
    if raw_text_dir and not isinstance(raw_text_dir, Path):
        raw_text_dir = Path(raw_text_dir)

    text, used_ocr = extract_text(pdf_path)
    lines = split_lines(text)

    owner = extract_owner(text, lines)
    trustee_name = extract_trustee(text, lines)
    prop_address, prop_city, prop_state, prop_zip = extract_address(text, lines)
    mail_address, mail_city, mail_state, mail_zip = extract_mailing_address(lines, prop_address)
    parcel_number = extract_parcel_number(text)
    auction_date = extract_auction_date(text, lines)
    trustee_phone = extract_phone(text)
    amount = extract_amount(text)
    deed_of_trust = extract_deed_of_trust(text)
    raw_text_path = write_raw_text(text, raw_text_dir, kwargs.get("doc_num", ""))

    first_name, last_name, second_first, second_last = split_person_names(owner)
    flags = build_flags(
        used_ocr=used_ocr,
        owner=owner,
        trustee_name=trustee_name,
        prop_address=prop_address,
        parcel_number=parcel_number,
        auction_date=auction_date,
    )
    score = score_record(owner, trustee_name, prop_address, parcel_number, auction_date)

    return {
        "doc_num": kwargs.get("doc_num", ""),
        "doc_type": kwargs.get("doc_type", "NS"),
        "filed": kwargs.get("filed", ""),
        "cat": "NS",
        "cat_label": kwargs.get("cat_label", "Notice of Trustee Sale"),
        "owner": owner,
        "grantee": "",
        "amount": amount,
        "legal": "",
        "prop_address": prop_address,
        "prop_city": prop_city,
        "prop_state": prop_state,
        "prop_zip": prop_zip,
        "mail_address": mail_address,
        "mail_city": mail_city,
        "mail_state": mail_state,
        "mail_zip": mail_zip,
        "county": "Maricopa",
        "parcel_number": parcel_number,
        "original_loan": "",
        "trustee_name": trustee_name,
        "trustee_phone": trustee_phone,
        "auction_date": auction_date,
        "deed_of_trust": deed_of_trust,
        "first_name": first_name,
        "last_name": last_name,
        "second_first": second_first,
        "second_last": second_last,
        "clerk_url": kwargs.get("clerk_url", ""),
        "pdf_url": kwargs.get("pdf_url", ""),
        "pdf_path": str(pdf_path),
        "flags": flags,
        "score": score,
        "raw_text_path": raw_text_path,
    }
