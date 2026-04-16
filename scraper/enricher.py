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
DATE_LONG_RE = re.compile(
    r"(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2},\s+\d{4}",
    re.I,
)
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
        flags.append("no_pdf")
        rec = ParsedRecord(
            doc_num=doc_num,
            doc_type=doc_type,
            filed=filed,
            cat="NS",
            cat_label=cat_label,
            clerk_url=clerk_url,
            pdf_url=pdf_url,
            pdf_path=str(pdf_path),
            flags=flags,
            score=0,
        )
        return asdict(rec)

    text, _, text_flags = extract_text_from_pdf(pdf_path)
    flags.extend(text_flags)

    if not text:
        flags.append("no_text_extracted")

    doc_num = doc_num or _find_first(DOC_NUM_RE, text)
    owner = _extract_owner(text)
    deed_of_trust = _extract_deed_of_trust(text)
    original_loan = _find_first(MONEY_RE, text)
    trustee_name = _extract_trustee_name(text)
    trustee_phone = _find_first(TRUSTEE_PHONE_RE, text)
    auction_date = _extract_auction_date(text)
    parcel_number = _extract_parcel_number(text)
    legal = _extract_legal(text)

    prop = _extract_property_address(text)
    mail = _extract_mailing_address(text, prop)
    first_name, last_name, second_first, second_last = _split_owner_names(owner)

    raw_text_path = raw_text_dir / f"{doc_num or pdf_path.stem}.txt"
    raw_text_path.write_text(text or "", encoding="utf-8")

    score = _score_record(prop, mail, owner, trustee_name, auction_date, original_loan)

    rec = ParsedRecord(
        doc_num=doc_num,
        doc_type=doc_type,
        filed=filed,
        cat="NS",
        cat_label=cat_label,
        owner=owner,
        grantee=trustee_name,
        amount=original_loan,
        legal=legal,
        prop_address=prop["address"],
        prop_city=prop["city"],
        prop_state=prop["state"],
        prop_zip=prop["zip"],
        mail_address=mail["address"],
        mail_city=mail["city"],
        mail_state=mail["state"],
        mail_zip=mail["zip"],
        county="Maricopa",
        parcel_number=parcel_number,
        original_loan=original_loan,
        trustee_name=trustee_name,
        trustee_phone=trustee_phone,
        auction_date=auction_date,
        deed_of_trust=deed_of_trust,
        first_name=first_name,
        last_name=last_name,
        second_first=second_first,
        second_last=second_last,
        clerk_url=clerk_url,
        pdf_url=pdf_url,
        pdf_path=str(pdf_path),
        flags=flags,
        score=score,
        raw_text_path=str(raw_text_path),
    )
    return asdict(rec)


def extract_text_from_pdf(pdf_path: str | Path, dpi: int = 250):
    flags = []
    text = ""

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text += t + "\n"
    except Exception:
        flags.append("pdfplumber_failed")

    if len(text.strip()) < 50:
        flags.append("ocr_fallback")
        try:
            images = convert_from_path(str(pdf_path), dpi=dpi)
            text = ""
            for img in images:
                text += pytesseract.image_to_string(img) + "\n"
        except Exception:
            flags.append("ocr_failed")

    return _clean_text(text), [], flags


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _find_first(pattern, text):
    m = pattern.search(text or "")
    return m.group(1).strip() if m and m.lastindex else (m.group(0).strip() if m else "")


# -------------------------
# 🔥 ADDRESS FIXES START HERE
# -------------------------

def _clean_address(val: str) -> str:
    val = re.sub(r"Tax Parcel.*", "", val, flags=re.I)
    val = re.sub(r"Parcel.*", "", val, flags=re.I)
    val = re.sub(r"\s+", " ", val or "")
    return val.strip(" ,;:-")


def _extract_property_address(text: str) -> dict[str, str]:
    text = text or ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 🔥 Multi-line address detection
    for i in range(len(lines) - 1):
        l1 = lines[i]
        l2 = lines[i + 1]

        if re.search(r"\d{3,6} .+", l1) and re.search(r"(AZ|Arizona).*\d{5}", l2, re.I):
            combined = _clean_address(f"{l1}, {l2}")
            parsed = _parse_address(combined)
            if parsed["address"]:
                return parsed

    # 🔥 Pattern fallback
    patterns = [
        r"purported to be[:\-]?\s*(.+?)(?:\n|\.|Tax Parcel|APN)",
        r"street address is purported to be[:\-]?\s*(.+?)(?:\n|\.|Tax Parcel|APN)",
    ]

    candidates = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            candidates.append(_clean_address(m.group(1)))

    return _best_address(candidates)


def _best_address(candidates):
    for c in candidates:
        parsed = _parse_address(c)
        if parsed["address"]:
            return parsed

    return {"address": "", "city": "", "state": "", "zip": ""}


def _parse_address(val: str) -> dict[str, str]:
    try:
        tagged, _ = usaddress.tag(val)
    except Exception:
        return {"address": "", "city": "", "state": "", "zip": ""}

    return {
        "address": val.split(",")[0],
        "city": tagged.get("PlaceName", ""),
        "state": tagged.get("StateName", ""),
        "zip": tagged.get("ZipCode", "")[:5],
    }


# -------------------------
# OTHER EXTRACTORS (UNCHANGED)
# -------------------------

def _extract_owner(text: str) -> str:
    m = re.search(r"Trustor.*?\n(.+)", text or "", re.I)
    return m.group(1).strip() if m else ""


def _extract_trustee_name(text: str) -> str:
    m = re.search(r"Trustee.*?\n(.+)", text or "", re.I)
    return m.group(1).strip() if m else ""


def _extract_auction_date(text: str) -> str:
    m = DATE_NUMERIC_RE.search(text or "")
    return m.group(0) if m else ""


def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    return m.group(1) if m else ""


def _extract_deed_of_trust(text: str) -> str:
    m = DOC_NUM_RE.search(text or "")
    return m.group(0) if m else ""


def _extract_legal(text: str) -> str:
    return ""


def _extract_mailing_address(text: str, prop: dict[str, str]):
    return {"address": "", "city": "", "state": "", "zip": ""}


def _split_owner_names(owner: str):
    if not owner:
        return "", "", "", ""
    parts = owner.split()
    return parts[0], parts[-1], "", ""


def _score_record(prop, mail, owner, trustee, auction, loan):
    score = 0
    if prop.get("address"):
        score += 30
    if owner:
        score += 20
    return score
