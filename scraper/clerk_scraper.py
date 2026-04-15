"""
clerk_scraper.py  v10 — FINAL STABLE

- Compatible with fetch.py (scrape() method added)
- No required init args
- Fixes base_url error
- Uses API (fast + reliable)
- Outputs real PDF URLs (not PNG preview)
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

import requests

log = logging.getLogger("clerk_scraper")

API_BASE    = "https://publicapi.recorder.maricopa.gov"
SEARCH_URL  = f"{API_BASE}/documents/search"
PORTAL_BASE = "https://recorder.maricopa.gov"

PAGE_SIZE   = 200
MAX_RESULTS = 200

MAX_RETRIES   = 3
RETRY_DELAY   = 5
REQUEST_DELAY = 0.5

DOC_CODES = {
    "NS": "NS",
    "FL": "FL",
    "SL": "SL",
    "DE": "DE",
    "PD": "PD",
    "PJ": "PJ",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":      "Mozilla/5.0",
    "Accept":          "application/json, text/plain, */*",
    "Referer":         f"{PORTAL_BASE}/recording/document-search-results.html",
    "Origin":          PORTAL_BASE,
    "Connection":      "keep-alive",
})


class MaricopaClerkScraper:

    def __init__(self, lead_types=None, start_date=None, end_date=None, **kwargs):

        # default lead types
        self.lead_types = lead_types or {
            "NS": ("preforeclosure", "Notice of Trustee Sale"),
            "FL": ("foreclosure", "Foreclosure"),
            "DE": ("default", "Notice of Default"),
        }

        today = datetime.now().strftime("%Y-%m-%d")

        self.start_date = start_date or today
        self.end_date   = end_date   or today

        self.records: list[dict] = []

        # fix for GitHub error
        self.base_url = PORTAL_BASE

    async def run(self) -> list[dict]:

        # warm up session
        try:
            SESSION.get(f"{PORTAL_BASE}/recording/document-search.html", timeout=15)
            log.info("Session warmed up")
        except Exception as e:
            log.warning(f"Warmup failed: {e}")

        for lead_key in self.lead_types:
            doc_code       = DOC_CODES.get(lead_key)
            cat, cat_label = self.lead_types[lead_key]

            if not doc_code:
                continue

            try:
                recs = self._fetch_all(lead_key, doc_code, cat, cat_label)
                self.records.extend(recs)
                log.info(f"{lead_key}: {len(recs)} records")
            except Exception as exc:
                log.error(f"{lead_key} failed: {exc}", exc_info=True)

            time.sleep(REQUEST_DELAY)

        log.info(f"TOTAL: {len(self.records)} records")
        return self.records

    def scrape(self, start_date=None, end_date=None, document_code=None):
        if start_date:
            self.start_date = start_date
        if end_date:
            self.end_date = end_date

        if document_code:
            self.lead_types = {
                document_code: self.lead_types.get(
                    document_code,
                    (document_code, document_code)
                )
            }

        return asyncio.run(self.run())

    def _fetch_all(self, lead_key, doc_code, cat, cat_label) -> list[dict]:
        all_records = []
        page = 1

        while True:
            params = {
                "documentCode": doc_code,
                "beginDate":    self.start_date,
                "endDate":      self.end_date,
                "pageSize":     PAGE_SIZE,
                "pageNumber":   page,
                "maxResults":   MAX_RESULTS,
            }

            data = self._get(SEARCH_URL, params)
            if not data:
                break

            results = data.get("searchResults", [])
            total   = data.get("totalResults", 0)

            for item in results:
                rec = self._item_to_record(item, lead_key, cat, cat_label)
                if rec:
                    all_records.append(rec)

            if len(all_records) >= total or len(results) < PAGE_SIZE:
                break

            page += 1
            time.sleep(REQUEST_DELAY)

        return all_records

    def _item_to_record(self, item: dict, lead_key, cat, cat_label) -> Optional[dict]:
        doc_num = str(item.get("recordingNumber", "")).strip()
        if not doc_num:
            return None

        pdf_url = f"{API_BASE}/documents/{doc_num}/pdf"
        preview_url = f"{PORTAL_BASE}/recording/document-preview.html?recNum={doc_num}"

        return {
            "doc_num":       doc_num,
            "doc_type":      item.get("documentCode", cat),
            "filed":         _norm_date(item.get("recordingDate", "")),
            "cat":           cat,
            "cat_label":     cat_label,
            "lead_key":      lead_key,

            "owner":         item.get("names", "") or "",
            "grantee":       "",

            "amount":        None,
            "legal":         "",

            "prop_address":  None,
            "prop_city":     None,
            "prop_state":    "AZ",
            "prop_zip":      None,

            "mail_address":  None,
            "mail_city":     None,
            "mail_state":    None,
            "mail_zip":      None,

            "parcel":        None,

            "first_name":    None,
            "last_name":     None,
            "first_name_2":  None,
            "last_name_2":   None,

            "trustee_name":  None,
            "trustee_phone": None,

            "auction_date":  None,

            "pdf_url":       pdf_url,
            "pdf_fallback":  preview_url,

            "clerk_url":     f"{PORTAL_BASE}/recording/document-details?id={doc_num}",

            "flags": [],
            "score": 0,
        }

    def _get(self, url: str, params: dict) -> Optional[dict]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = SESSION.get(url, params=params, timeout=20)
                if resp.ok:
                    return resp.json()
                log.warning(f"HTTP {resp.status_code}")
            except Exception as exc:
                log.warning(f"Attempt {attempt} failed: {exc}")

            time.sleep(RETRY_DELAY * attempt)

        return None


def _norm_date(raw: str) -> str:
    raw = raw.strip().replace("-", "/")
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw
