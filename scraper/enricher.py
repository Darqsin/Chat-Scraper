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

    if not prop["address"]:
        flags.append("missing_property_address")
    if not owner:
        flags.append("missing_owner")
    if not trustee_name:
        flags.append("missing_trustee")
    if not auction_date:
        flags.append("missing_auction_date")

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


def extract_text_from_pdf(pdf_path: str | Path, dpi: int = 250) -> tuple[str, list[str], list[str]]:
    pdf_path = Path(pdf_path)
    flags: list[str] = []
    page_texts: list[str] = []

    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    page_texts.append(text)
    except Exception as exc:
        flags.append(f"pdfplumber_failed:{type(exc).__name__}")
        LOGGER.warning("pdfplumber failed for %s: %s", pdf_path, exc)

    if sum(len(t.strip()) for t in page_texts) < 50:
        flags.append("ocr_fallback")
        page_texts = []
        try:
            images = convert_from_path(str(pdf_path), dpi=dpi)
            for image in images:
                page_texts.append(pytesseract.image_to_string(image))
        except Exception as exc:
            flags.append(f"ocr_failed:{type(exc).__name__}")
            LOGGER.warning("OCR failed for %s: %s", pdf_path, exc)

    cleaned_pages = [_clean_text(t) for t in page_texts if t and t.strip()]
    full_text = "\n".join(cleaned_pages)
    return full_text, cleaned_pages, flags


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _find_first(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text or "")
    return m.group(1).strip() if m and m.lastindex else (m.group(0).strip() if m else "")


def _extract_owner(text: str) -> str:
    patterns = [
        r"Original Trustor(?:'s)?(?:\s*Name and Address)?\s*[:\-]?\s*(.+?)(?:\n(?:Current Trustee|Trustee|Beneficiary|Name and Address of Beneficiary|Sale Date|TS#|NOTICE OF TRUSTEE))",
        r"NAME AND ADDRESS OF ORIGINAL TRUSTOR.*?\n(.+?)(?:\n(?:NAME AND ADDRESS OF BENEFICIARY|CURRENT TRUSTEE|NOTICE OF TRUSTEE))",
        r"Trustor(?:s)?\s*[:\-]\s*(.+?)(?:\n(?:Trustee|Beneficiary|Property Address|Sale Date))",
        r"Name of Trustor\s*[:\-]?\s*(.+?)(?:\n(?:Trustee|Beneficiary|Property Address|Sale Date))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            return _normalize_name_line(m.group(1))
    return ""


def _extract_trustee_name(text: str) -> str:
    m = re.search(
        r"The undersigned Trustee,\s*([^,\n]+),\s*Attorney at Law",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()

    patterns = [
        r"Current Trustee(?:'s)?(?:\s*Name and Address)?\s*[:\-]?\s*(.+?)(?:\n(?:Phone|Telephone|Name of Trustee's Regulator|This sale|Sale Date))",
        r"Trustee\s*[:\-]?\s*(.+?)(?:\n(?:Phone|Telephone|Address|Sale Date|Name of Trustee's Regulator))",
        r"NAME, ADDRESS\s*&\s*TELEPHONE NUMBER OF TRUSTEE.*?\n(.+?)(?:\n(?:Name of Trustee's Regulator|State Bar|Dated this))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if not m:
            continue

        value = _normalize_name_line(m.group(1))
        if not value:
            continue

        if any(x in value.lower() for x in [
            "sale", "objection", "must file", "court order", "superior court"
        ]):
            continue

        if re.search(r"\d{3,5}\s+.+\b(AZ|CA|TX|NV|NM)\b", value, re.I):
            continue

        if any(x in value.lower() for x in [
            "suite", "avenue", "road", "street", "drive", "lane", "boulevard"
        ]):
            continue

        if len(value) > 5:
            return value
    # 🔥 Fallback: detect law firms / trustee names
    m = re.search(r"\b([A-Z][A-Za-z&., ]+(?:LLP|LLC|P\.A\.|LAW FIRM|ATTORNEY))\b", text)
    if m:
    return m.group(1).strip()
    
    return ""


def _extract_auction_date(text: str) -> str:
    patterns = [
        r"(?:Sale Date and Time|Sale Date|Auction Date|Date of Sale)\s*[:\-]?\s*(.+?)(?:\n|Sale Location|Location)",
        r"on\s+(%s)" % DATE_LONG_RE.pattern,
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            value = m.group(1).strip() if m.lastindex else m.group(0).strip()
            long_match = DATE_LONG_RE.search(value)
            if long_match:
                return long_match.group(0)
            num_match = DATE_NUMERIC_RE.search(value)
            if num_match:
                return num_match.group(0)
            return _clean_text(value)

    m = DATE_LONG_RE.search(text or "")
    if m:
        return m.group(0)

    m = DATE_NUMERIC_RE.search(text or "")
    return m.group(0) if m else ""


def _extract_parcel_number(text: str) -> str:
    m = PARCEL_RE.search(text or "")
    return m.group(1).strip() if m else ""


def _extract_deed_of_trust(text: str) -> str:
    patterns = [
        r"Deed of Trust(?: recorded)?\s*(?:as|at|being)?\s*(?:Instrument|Document|No\.?|Number)?\s*[:#\-]?\s*(20\d{9,})",
        r"pursuant to that certain Deed of Trust.*?(20\d{9,})",
        r"Instrument No\.?\s*(20\d{9,})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            return m.group(1).strip()
    return ""


def _extract_legal(text: str) -> str:
    patterns = [
        r"Legal Description\s*[:\-]?\s*(.+?)(?:\n\s*(?:APN|A\.P\.N\.|Parcel|Tax Parcel Number|Original Principal Balance|Property Address|Purported Street Address|Street address or identifiable location)|$)",
        r"(LOT\s+\d+.+?)(?:\n\s*(?:APN|A\.P\.N\.|Parcel|Tax Parcel Number|Original Principal Balance|Property Address|Purported Street Address)|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            return _clean_text(m.group(1))[:1000]
    return ""


def _extract_property_address(text: str) -> dict[str, str]:
    text = text or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    candidates: list[str] = []

    for i, line in enumerate(lines):
        if re.search(
            r"(purported street address|street address or identifiable location|property address)",
            line,
            re.I,
        ):
            combined = line
            if i + 1 < len(lines):
                combined += " " + lines[i + 1]

            combined = _clean_address(combined)
            combined = re.sub(
                r"^(Purported Street Address|Street address or identifiable location|Property Address)\s*[:\-]?\s*",
                "",
                combined,
                flags=re.I,
            )
            if _is_valid_property_address(combined):
                return _parse_address(combined)

    patterns = [
        r"street address is purported to be[:\-]?\s*(.+?)(?:\n|\.|Tax Parcel|APN)",
        r"is purported to be[:\-]?\s*(.+?)(?:\n|\.|Tax Parcel|APN)",
        r"Purported Street Address\s*[:\-]?\s*(.+?)(?:\n|Tax Parcel Number|Original Principal Balance)",
        r"Street address or identifiable location\s*[:\-]?\s*(.+?)(?:\n|A\.P\.N\.|APN|Original Principal Balance)",
        r"Property Address\s*[:\-]?\s*(.+?)(?:\n|Parcel|APN|Tax|Original Principal Balance)",
        r"Commonly known as\s*[:\-]?\s*(.+?)(?:\n|Parcel|APN|Tax)",
        r"Property\s+located\s+at\s+(.+?)(?:\n|Parcel|APN|Tax)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            candidate = _clean_text(m.group(1))
            candidate = candidate.split("\n")[0]
            candidate = re.sub(r"(Suite|Ste|Unit).*", "", candidate, flags=re.I)
            candidate = re.sub(r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER).*$", "", candidate, flags=re.I)
            candidate = re.sub(r"\d{1,2}:\d{2}\s*(AM|PM).*$", "", candidate, flags=re.I)
            candidate = candidate.strip(" ,;")
            candidates.append(candidate)

    line_candidates = [
        line.strip()
        for line in (text or "").splitlines()
        if re.search(r"\d{2,6} .+(AZ|ARIZONA)\b.*\d{5}", line, re.I)
    ]
    candidates.extend(line_candidates)

    return _best_address(candidates, property_mode=True)


def _extract_mailing_address(text: str, prop: dict[str, str]) -> dict[str, str]:
    candidates: list[str] = []
    patterns = [
        r"When recorded mail to\s*[:\-]?\s*(.+?)(?:\n(?:TS No|Order No|Notice of Trustee|NOTICE OF TRUSTEE))",
        r"Mailing Address\s*[:\-]?\s*(.+?)(?:\n|Property Address|Trustee)",
        r"Send notice to\s*[:\-]?\s*(.+?)(?:\n|Property Address|Trustee)",
        r"Address of Trustor\s*[:\-]?\s*(.+?)(?:\n|Property Address|Trustee)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            candidate = _clean_text(m.group(1))
            if any(x in candidate.lower() for x in [
                "llp", "loan", "title no", "tiffany", "bosco",
                "central arts", "plaza", "floor", "suite", "ste",
                "trustee", "attorney"
            ]):
                continue
            candidates.append(candidate)

    best = _best_address(candidates, property_mode=False)
    if best["address"] and best["address"].upper() != prop.get("address", "").upper():
        return best

    return {"address": "", "city": "", "state": "", "zip": ""}


def _clean_address(val: str) -> str:
    val = re.sub(r"\s+", " ", val)
    val = re.sub(r"(suite|ste|unit).*", "", val, flags=re.I)
    val = val.strip(" ,;:-")
    return val


def _is_valid_property_address(val: str) -> bool:
    upper = val.upper()

    bad_terms = [
        "85003", "85004",
        "COURT", "COURTHOUSE", "SUPERIOR COURT",
        "JEFFERSON", "SALE LOCATION",
        "201 W JEFFERSON", "201 WEST JEFFERSON",
        "LOCATED AT 201 WEST JEFFERSON",
    ]
    if "JEFFERSON" in upper and re.search(r"\b8500[34]\b", upper):
    return False
    if any(term in upper for term in bad_terms):
        return False

    return bool(
        re.search(
            r"\d{3,6}\s+[A-Z0-9 .'\-]+(?:ST|STREET|AVE|AVENUE|RD|ROAD|DR|DRIVE|LN|LANE|CT|COURT|PL|PLACE|BLVD|BOULEVARD|WAY)\b.*\b(AZ|ARIZONA)\b.*\d{5}",
            val,
            re.I,
        )
    )


def _best_address(candidates: list[str], property_mode: bool = True) -> dict[str, str]:
    best = {"address": "", "city": "", "state": "", "zip": ""}
    best_score = -1

    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,;")
        if len(candidate) < 8:
            continue

        if property_mode and not _is_valid_property_address(candidate):
            continue

        score = 0
        if re.search(r"\d", candidate):
            score += 1
        if re.search(r"\bAZ\b|\bARIZONA\b", candidate, re.I):
            score += 1
        if re.search(r"\b\d{5}(?:-\d{4})?\b", candidate):
            score += 1

        parsed = _parse_address(candidate)
        if parsed["address"]:
            score += 2

        if score > best_score:
            best = parsed if parsed["address"] else best
            best_score = score

    return best


def _parse_address(val: str) -> dict[str, str]:
    try:
        tagged, _ = usaddress.tag(val)
    except Exception:
        return {
            "address": val if _looks_like_address(val) else "",
            "city": "",
            "state": "",
            "zip": "",
        }

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
        "USPSBoxType",
        "USPSBoxID",
        "BuildingName",
    ]:
        if key in tagged:
            street_parts.append(tagged[key])

    street = " ".join(street_parts).strip()
    city = tagged.get("PlaceName", "").strip()
    state = tagged.get("StateName", "").strip()
    zipcode = tagged.get("ZipCode", "").strip()

    if zipcode and len(zipcode) > 5:
        zipcode = zipcode[:5]

    if state.lower() == "arizona":
        state = "AZ"

    return {"address": street, "city": city, "state": state, "zip": zipcode}


def _looks_like_address(value: str) -> bool:
    return bool(re.search(r"\d{2,6}\s+[A-Z0-9]", value, re.I))


def _normalize_name_line(value: str) -> str:
    value = _clean_text(value)
    lines = [x.strip(" ,;:-") for x in value.split("\n") if x.strip()]
    if not lines:
        return ""
    return lines[0]


def _split_owner_names(owner: str) -> tuple[str, str, str, str]:
    if not owner:
        return "", "", "", ""

    parts = re.split(r"\s*(?:&| AND |/|\n)\s*", owner, maxsplit=1, flags=re.I)
    primary = parts[0].strip(" ,;")
    secondary = parts[1].strip(" ,;") if len(parts) > 1 else ""

    p_first, p_last = _split_single_name(primary)
    s_first, s_last = _split_single_name(secondary)
    return p_first, p_last, s_first, s_last


def _split_single_name(name: str) -> tuple[str, str]:
    if not name:
        return "", ""
    tokens = [t for t in re.split(r"\s+", name.strip()) if t]
    if len(tokens) == 1:
        return tokens[0].title(), ""
    return tokens[0].title(), tokens[-1].title()


def _score_record(
    prop: dict[str, str],
    mail: dict[str, str],
    owner: str,
    trustee_name: str,
    auction_date: str,
    original_loan: str,
) -> int:
    score = 0
    if prop.get("address"):
        score += 30
    if prop.get("zip"):
        score += 10
    if mail.get("address"):
        score += 15
    if owner:
        score += 20
    if trustee_name:
        score += 10
    if auction_date:
        score += 10
    if original_loan:
        score += 5
    return min(score, 100)
