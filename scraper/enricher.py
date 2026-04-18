
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

TRUSTEE_LABELS = ["trustee:", "successor trustee:", "substitute trustee:"]

BAD_WORDS = ["trustor","borrower","beneficiary","attorney","notary"]

DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")

def ocr_pdf(path):
    images = convert_from_path(path)
    text = ""
    for img in images:
        text += pytesseract.image_to_string(img)
    return text

def extract_text(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            if text.strip():
                return text
    except:
        pass
    return ocr_pdf(pdf_path)

def clean_owner(text):
    text = re.split(r"\d{5,}", text)[0]
    return text.strip()

def extract_trustee(text):
    lines = text.split("\n")
    for i,l in enumerate(lines):
        low = l.lower()
        if any(lbl in low for lbl in TRUSTEE_LABELS):
            block = " ".join(lines[i:i+3])
            if not any(b in block.lower() for b in BAD_WORDS):
                return block.strip()
    return ""

def extract_dates(text, filed):
    dates = DATE_RE.findall(text)
    filed_dt = None
    try:
        filed_dt = datetime.strptime(filed, "%Y-%m-%d")
    except:
        pass

    valid = []
    for d in dates:
        try:
            dt = datetime.strptime(d, "%m/%d/%Y")
            if not filed_dt or dt >= filed_dt:
                valid.append(dt.strftime("%Y-%m-%d"))
        except:
            continue

    return valid[0] if valid else ""

def parse_record(**kwargs):
    pdf_path = kwargs.get("pdf_path")
    text = extract_text(pdf_path)

    owner = clean_owner(text)
    trustee = extract_trustee(text)
    auction = extract_dates(text, kwargs.get("filed",""))

    return {
        "doc_num": kwargs.get("doc_num",""),
        "doc_type": kwargs.get("doc_type",""),
        "filed": kwargs.get("filed",""),
        "owner": owner,
        "trustee_name": trustee,
        "auction_date": auction,
        "prop_address": "",
        "flags": [],
        "score": 80
    }
