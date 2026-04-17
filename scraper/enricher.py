# CLEANED ENRICHER V3

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
    for phrase in REMOVE_OWNER_PHRASES:
        owner = re.sub(rf"\b{phrase}\b", "", owner, flags=re.I)
    owner = re.sub(r",\s*$", "", owner).strip()
    return owner


def clean_trustee(name):
    if not name:
        return ""
    name_upper = name.upper()
    for t in TRUSTEE_WHITELIST:
        if t in name_upper:
            return t
    return ""


def is_valid_phone(p):
    if not p:
        return ""
    if re.match(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$", p):
        return p
    return ""


def clean_parcel(parcel):
    if not parcel:
        return ""
    if not re.match(r"\d{3}-\d{2}-\d{3}", parcel):
        return ""
    return parcel


def clean_auction_date(date):
    if not date:
        return ""
    if "2026" in date or "2027" in date:
        return date
    return ""
