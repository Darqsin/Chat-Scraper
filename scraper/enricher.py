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

def extract_address(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    bad_patterns = [
        "201 w jefferson",
        "1850 n central",
        "suite",
        "floor",
        "servicer",
        "qualityloan",
        "law group",
        "default services",
        "trustee",
        "sales line",
        "http",
        "www"
    ]

    def is_bad(line):
        l = line.lower()
        return any(p in l for p in bad_patterns)

    def is_street(line):
        return bool(re.search(r"\d{2,5}\s+[A-Za-z]", line))

    def is_city_state(line):
        return bool(re.search(r"[A-Za-z\s]+,\s*[A-Z]{2}\s*\d{5}", line))

    for i, line in enumerate(lines):
        if "street address or identifiable location" in line.lower():
            for j in range(i+1, min(i+6, len(lines)-1)):
                if is_street(lines[j]) and not is_bad(lines[j]):
                    if is_city_state(lines[j+1]):
                        return f"{lines[j]}, {lines[j+1]}"

    for i in range(len(lines)-1):
        if is_street(lines[i]) and is_city_state(lines[i+1]):
            if not is_bad(lines[i]):
                return f"{lines[i]}, {lines[i+1]}"

    return ""

def parse_record(**kwargs):
    pdf_path = kwargs.get("pdf_path")
    text = extract_text(pdf_path)

    return {
        "doc_num": kwargs.get("doc_num",""),
        "owner": "",
        "trustee_name": "",
        "prop_address": extract_address(text),
        "auction_date": "",
    }
