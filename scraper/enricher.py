
import re
from datetime import datetime

MONTHS = "(January|February|March|April|May|June|July|August|September|October|November|December)"

TRUSTEE_WHITELIST = {
    "quality loan service": "Quality Loan Service Corporation",
    "western progressive": "Western Progressive, LLC",
    "aztec foreclosure": "Aztec Foreclosure Corporation",
    "clear recon": "Clear Recon Corp",
    "mccarthy holthus": "McCarthy & Holthus LLP",
}

def normalize_date(text):
    m = re.search(rf"{MONTHS}\s+\d{{1,2}},\s+202\d", text, re.I)
    if m:
        try:
            return datetime.strptime(m.group(0), "%B %d, %Y").strftime("%Y-%m-%d")
        except:
            return ""
    return ""

def extract_trustee(text):
    t = text.lower()
    for k,v in TRUSTEE_WHITELIST.items():
        if k in t:
            return v
    return ""

def extract_owner(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i,l in enumerate(lines):
        if any(x in l.lower() for x in ["trustor","grantor","borrower","owner"]):
            block = " ".join(lines[i:i+3])
            m = re.search(r"([A-Z][A-Z ,.&]+)", block)
            if m:
                val = m.group(1)
                if len(val) > 5:
                    return val
    return ""

def parse_record(text):
    return {
        "owner": extract_owner(text),
        "trustee": extract_trustee(text),
        "auction_date": normalize_date(text),
        "raw_text": text[:200]
    }
