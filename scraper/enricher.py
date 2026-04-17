# ENRICHER V4 - 95% PUSH

import re

TRUSTEE_WHITELIST = [
    "CLEAR RECON CORP",
    "MTC FINANCIAL INC",
    "QUALITY LOAN SERVICE CORPORATION",
    "PRESTIGE DEFAULT SERVICES, LLC",
    "WESTERN PROGRESSIVE",
    "AZ TRUSTEE SERVICES",
    "PIONEER TITLE",
    "LEONARD J. MCDONALD"
]

REMOVE_OWNER_PHRASES = [
    "A MARRIED MAN",
    "A MARRIED WOMAN",
    "A SINGLE MAN",
    "A SINGLE WOMAN",
    "UNMARRIED",
    "A SINGLE PERSON",
    "HUSBAND AND WIFE",
    "WITH RIGHTS OF SURVIVORSHIP",
]

def clean_owner(owner):
    if not owner:
        return ""
    owner = owner.upper()
    for phrase in REMOVE_OWNER_PHRASES:
        owner = re.sub(rf"\b{phrase}\b", "", owner, flags=re.I)
    parts = [p.strip() for p in owner.split(" AND ")]
    owner = " AND ".join(dict.fromkeys(parts))
    return owner.strip(", ").title()


def clean_trustee(text):
    if not text:
        return ""
    text_upper = text.upper()
    for t in TRUSTEE_WHITELIST:
        if t in text_upper:
            return t
        if any(word in text_upper for word in t.split()[:2]):
            return t
    return ""


def extract_auction_date(text):
    lines = text.split("\n")
    for line in lines:
        l = line.lower()
        if any(k in l for k in ["sale", "auction", "sold"]):
            m = re.search(r"(\d{1,2}/\d{1,2}/20\d{2})", line)
            if m and int(m.group(1).split("/")[-1]) >= 2026:
                return m.group(1)
            m = re.search(r"([A-Za-z]+\s+\d{1,2},\s+20\d{2})", line)
            if m and int(m.group(1).split()[-1]) >= 2026:
                return m.group(1)
    return ""


def parse_record(text):
    record = {}

    # limit noise (top half of doc)
    text_section = text[:len(text)//2]

    # owner (simple placeholder - assumes already extracted upstream)
    owner_match = re.search(r"([A-Z ,]+)", text_section)
    record["owner"] = clean_owner(owner_match.group(1)) if owner_match else ""

    # trustee
    record["trustee_name"] = clean_trustee(text_section)

    # auction date
    record["auction_date"] = extract_auction_date(text_section)

    return record
