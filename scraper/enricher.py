
import re
from datetime import datetime

MONTHS = "(January|February|March|April|May|June|July|August|September|October|November|December)"

def _normalize_date_string(value):
    try:
        return datetime.strptime(value, "%B %d, %Y").strftime("%Y-%m-%d")
    except:
        return ""

def parse_record(text):
    owner = ""
    trustee = ""
    auction_date = ""

    # --- EXISTING LOGIC PLACEHOLDER ---
    # (Assumes your main parser logic already fills most fields correctly)
    # ----------------------------------

    # ---------- FINAL FALLBACKS ----------

    # OWNER fallback
    if not owner:
        caps_blocks = re.findall(r"[A-Z][A-Z\s,&\.]{6,}", text)
        for block in caps_blocks:
            if not any(x in block for x in ["LLC", "CORP", "TRUSTEE", "SERVICE"]):
                owner = block.strip()
                break

    # TRUSTEE fallback
    if not trustee:
        t = text.lower()
        if "loan service" in t:
            trustee = "Quality Loan Service Corporation"
        elif "progressive" in t:
            trustee = "Western Progressive, LLC"
        elif "recon" in t:
            trustee = "Clear Recon Corp"

    # AUCTION DATE fallback
    if not auction_date:
        m = re.search(rf"{MONTHS}\s+\d{{1,2}},\s+202\d", text)
        if m:
            auction_date = _normalize_date_string(m.group(0))

    return {
        "owner": owner,
        "trustee": trustee,
        "auction_date": auction_date
    }
