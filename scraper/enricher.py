from __future__ import annotations

import re
from typing import Dict


def parse_record(record: Dict) -> Dict:
    """
    Main entry point used by fetch.py
    """
    text = record.get("text", "") or ""

    record["owner"] = _extract_owner(text)
    record["trustee_name"] = _extract_trustee_name(text)
    record["prop_address"] = _extract_property_address(text)

    return record


# ----------------------------
# OWNER
# ----------------------------
def _extract_owner(text: str) -> str:
    patterns = [
        r"Grantor\(s\):(.*?)(?:\n|$)",
        r"Trustor\(s\):(.*?)(?:\n|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            value = m.group(1).strip()
            if len(value) > 5:
                return _clean_text(value)

    return ""


# ----------------------------
# TRUSTEE
# ----------------------------
def _extract_trustee_name(text: str) -> str:
    patterns = [
        r"Current Trustee.*?\n(.+)",
        r"Trustee.*?\n(.+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            value = m.group(1).strip()

            # reject addresses
            if re.search(r"\d{3,5} .+(AZ|CA|TX)", value):
                continue

            # reject obvious junk
            if any(x in value.lower() for x in ["street", "road", "avenue", "suite"]):
                continue

            if len(value) > 5:
                return _clean_text(value)

    return ""


# ----------------------------
# PROPERTY ADDRESS
# ----------------------------
def _extract_property_address(text: str) -> str:
    candidates = []

    patterns = [
        r"Property Address[:\s]*(.*?)(?:\n|$)",
        r"Commonly known as[:\s]*(.*?)(?:\n|$)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            candidates.append(_clean_text(m.group(1)))

    # fallback: find AZ addresses
    for line in (text or "").splitlines():
        if re.search(r"\d{2,6} .+\b(AZ|ARIZONA)\b", line, re.I):
            candidates.append(_clean_text(line))

    # pick best candidate
    for c in candidates:
        if len(c) > 10:
            return c

    return ""


# ----------------------------
# CLEAN TEXT
# ----------------------------
def _clean_text(value: str) -> str:
    if not value:
        return ""

    value = value.replace("\n", " ").replace("\r", " ").strip()
    value = re.sub(r"\s{2,}", " ", value)
    return value
