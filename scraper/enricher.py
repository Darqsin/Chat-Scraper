# FINAL FIXED ENRICHER

from __future__ import annotations
import re
from datetime import datetime
import pdfplumber
import pytesseract
from pdf2image import convert_from_path

DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")

def ocr_pdf(path):
    images = convert_from_path(path)
    return "\n".join(pytesseract.image_to_string(img) for img in images)

def extract_text(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            if text.strip():
                return text
    except:
        pass
    return ocr_pdf(pdf_path)

BAD = [
    "201 w jefferson","1850 n central","camino del rio","gillette ave",
    "suite","floor","servicer","qualityloan","law group","default services",
    "trustee","sales line","http","www","will occur","courthouse","superior court"
]

def bad(l): return any(x in l.lower() for x in BAD)

def street(l): return bool(re.search(r"\d{2,5}\s+[A-Za-z]", l))

def city(l): return bool(re.search(r"[A-Za-z\s]+,\s*[A-Z]{2}\s*\d{5}", l))

def clean(l):
    l = re.sub(r"street address.*?:\s*", "", l, flags=re.I)
    l = re.sub(r"purported street address:\s*", "", l, flags=re.I)
    return l.strip()

def extract_address(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    for i,l in enumerate(lines):
        if "street address" in l.lower():
            for j in range(i+1, min(i+6,len(lines)-1)):
                if street(lines[j]) and city(lines[j+1]) and not bad(lines[j]):
                    return f"{clean(lines[j])}, {clean(lines[j+1])}"

    for i in range(len(lines)-1):
        if street(lines[i]) and city(lines[i+1]):
            if not bad(lines[i]) and not bad(lines[i+1]):
                return f"{clean(lines[i])}, {clean(lines[i+1])}"

    return ""

def extract_owner(text):
    for line in text.split("\n"):
        if "original trustor" in line.lower():
            return line.strip()
    return ""

def extract_trustee(text):
    for line in text.split("\n"):
        if "trustee" in line.lower() and not bad(line):
            return line.strip()
    return ""

def extract_auction_date(text):
    for m in DATE_RE.findall(text):
        try:
            d = datetime.strptime(m,"%m/%d/%Y")
            if d.year >= 2026:
                return d.strftime("%Y-%m-%d")
        except: pass
    return ""

def parse_record(**kwargs):
    text = extract_text(kwargs.get("pdf_path"))
    return {
        "doc_num": kwargs.get("doc_num",""),
        "owner": extract_owner(text),
        "trustee_name": extract_trustee(text),
        "prop_address": extract_address(text),
        "auction_date": extract_auction_date(text),
    }
