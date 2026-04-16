# REPLACE YOUR ENTIRE FILE WITH THIS

from __future__ import annotations

import re
from typing import Dict


# =========================
# PROPERTY ADDRESS
# =========================

def _extract_property_address(text: str) -> Dict[str, str]:
    text = text or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # --- PRIORITY: Explicit labeled sections (MULTI-LINE FIX)
    for i, line in enumerate(lines):
        if re.search(
            r"(purported street address|street address or identifiable location|property address)",
            line,
            re.I,
        ):
            combined = line

            # grab next line if needed (city/state/zip often on next line)
            if i + 1 < len(lines):
                combined += " " + lines[i + 1]

            addr = _clean_address(combined)

            if _is_valid_property_address(addr):
                return _parse_address(addr)

    # --- SECONDARY: fallback scan
    candidates = []
    for line in lines:
        if re.search(r"\d{3,6} .+\b(AZ|ARIZONA)\b", line, re.I):
            candidates.append(_clean_address(line))

    return _best_address(candidates)


# =========================
# TRUSTEE
# =========================

def _extract_trustee_name(text: str) -> str:
    text = text or ""

    # --- NEW: Attorney fallback (VERY IMPORTANT)
    m = re.search(
        r"The undersigned Trustee,\s*([^,\n]+),\s*Attorney at Law",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()

    patterns = [
        r"Current Trustee.*?\n(.+)",
        r"Trustee.*?\n(.+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue

        value = m.group(1).strip()

        # 🚫 reject garbage trustee values
        if any(x in value.lower() for x in [
            "sale", "objection", "must file", "court", "superior court"
        ]):
            continue

        # 🚫 reject addresses
        if re.search(r"\d{3,5} .+(AZ|CA|TX)", value):
            continue

        if len(value) > 5:
            return value

    return ""


# =========================
# MAILING ADDRESS
# =========================

def _extract_mailing_address(text: str, prop: Dict[str, str]) -> Dict[str, str]:
    candidates = []

    patterns = [
        r"When recorded mail to\s*[:\-]?\s*(.+?)(?:\n|NOTICE)",
        r"Mailing Address\s*[:\-]?\s*(.+?)(?:\n|Property Address)",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            val = _clean_address(m.group(1))

            # 🚫 reject junk firm/title blocks
            if any(x in val.lower() for x in [
                "title no", "tiffany", "bosco",
                "central arts", "floor", "plaza"
            ]):
                continue

            candidates.append(val)

    best = _best_address(candidates)

    if best["address"] and best["address"] != prop.get("address"):
        return best

    return {"address": "", "city": "", "state": "", "zip": ""}


# =========================
# HELPERS
# =========================

def _clean_address(val: str) -> str:
    val = re.sub(r"\s+", " ", val)
    val = re.sub(r"(suite|ste|unit).*", "", val, flags=re.I)
    val = val.strip(" ,;:-")
    return val


def _is_valid_property_address(val: str) -> bool:
    upper = val.upper()

    # 🚫 HARD BLOCK bad addresses
    bad_terms = [
        "COURT", "COURTHOUSE", "SUPERIOR COURT",
        "JEFFERSON",
        "201 W JEFFERSON",
        "201 WEST JEFFERSON",
        "SALE LOCATION",
    ]

    if any(term in upper for term in bad_terms):
        return False

    return bool(
        re.search(
            r"\d{3,6}\s+[A-Z0-9 .'\-]+(?:ST|AVE|RD|DR|LN|CT|BLVD)\b.*\b(AZ|ARIZONA)\b.*\d{5}",
            val,
            re.I,
        )
    )


def _best_address(candidates):
    best = {"address": "", "city": "", "state": "", "zip": ""}
    best_score = -1

    for c in candidates:
        score = 0

        if re.search(r"\d", c):
            score += 1
        if "AZ" in c.upper():
            score += 1
        if re.search(r"\d{5}", c):
            score += 1

        parsed = _parse_address(c)
        if parsed["address"]:
            score += 2

        if score > best_score:
            best = parsed
            best_score = score

    return best


def _parse_address(val: str):
    m = re.search(
        r"(\d{3,6} .+?),?\s+([A-Za-z ]+),?\s+(AZ|Arizona)\s+(\d{5})",
        val,
        re.I,
    )

    if not m:
        return {"address": "", "city": "", "state": "", "zip": ""}

    return {
        "address": m.group(1).strip(),
        "city": m.group(2).strip(),
        "state": "AZ",
        "zip": m.group(4),
    }
