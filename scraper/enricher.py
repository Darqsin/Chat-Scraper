from __future__ import annotations
import re
from datetime import datetime
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

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

def extract_block(text, start_label):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if start_label.lower() in line.lower():
            return lines[i+1:i+6]
    return []

def extract_owner(text):
    block = extract_block(text, "Name and address of original trustor")
    return block[0].strip() if block else ""

def extract_trustee(text):
    block = extract_block(text, "NAME, ADDRESS & TELEPHONE NUMBER OF TRUSTEE")
    return block[0].strip() if block else ""

def extract_address(text):
    block = extract_block(text, "Street address or identifiable location")
    if len(block) >= 2:
        street = block[0].strip()
        city = block[1].strip()
        return f"{street}, {city}"
    return ""

def extract_auction_date(text):
    matches = DATE_RE.findall(text)
    for d in matches:
        try:
            dt = datetime.strptime(d, "%m/%d/%Y")
            if dt.year >= 2026:
                return dt.strftime("%Y-%m-%d")
        except:
            continue
    return ""

def parse_record(**kwargs):
    pdf_path = kwargs.get("pdf_path")
    text = extract_text(pdf_path)

    return {
        "doc_num": kwargs.get("doc_num",""),
        "doc_type": kwargs.get("doc_type",""),
        "filed": kwargs.get("filed",""),
        "owner": extract_owner(text),
        "trustee_name": extract_trustee(text),
        "prop_address": extract_address(text),
        "auction_date": extract_auction_date(text),
        "flags": [],
        "score": 90
    }
