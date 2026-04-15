"""
clerk_scraper.py  v11 — API-first, fetch.py-compatible

Updates:
- Compatible with fetch.py import: MaricopaClerkScraper
- Compatible with fetch.py call pattern: .scrape(start_date, end_date, document_code=...)
- No required init args
- Accepts unused kwargs like headless=True
- Uses Recorder API directly instead of browser automation
- Outputs real PDF URLs
- Ensures grouped_output/pdfs exists so workflow commit step does not fail
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Optional

import requests

log = logging.getLogger("clerk_scraper")

API_BASE = "https://publicapi.recorder.maricopa.gov"
SEARCH_URL = f"{API_BASE}/documents/search"
PORTAL_BASE = "https://recorder.maricopa.gov"

PAGE_SIZE = 200
MAX_RESULTS = 200

MAX_RETRIES = 3
RETRY_DELAY = 5
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
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{PORTAL_BASE}/recording/document-search-results.html",
        "Origin": PORTAL_BASE,
        "Connection": "keep-alive",
    }
)


class MaricopaClerkScraper:
    def __init__(self, lead_types=None, start_date=None, end_date=None, **kwargs):
        self.lead_types = lead_types or {
            "NS": ("NOTS", "Notice of Trustee Sale"),
        }

        today = datetime.utcnow().strftime("%Y-%m-%d")
        self.start_date = start_date or today
        self.end_date = end_date or today
        self.records: list[dict] = []

        self.base_url = PORTAL_BASE

    def scrape(self, start_date=None, end_date=None, document_code=None):
        if start_date:
            self.start_date = start_date
        if end_date:
            self.end_date = end_date

        if document_code:
            key = str(document_code).strip().upper()
            self.lead_types = {
                key: self.lead_types.get(key, (key, key))
            }

        return asyncio.run(self.run())

    async def run(self) -> list[dict]:
        os.makedirs("grouped_output/pdfs", exist_ok=True)

        try:
            SESSION.get(f"{PORTAL_BASE}/recording/document-search.html", timeout=15)
            log.info("Session warmed up via portal")
        except Exception as exc:
            log.warning(f"Portal warmup failed (non-fatal): {exc}")

        for lead_key, lead_value in self.lead_types.items():
            doc_code = DOC_CODES.get(lead_key, lead_key)
            cat, cat_label = lead_value

            try:
                recs = self._fetch_all(lead_key, doc_code, cat, cat_label)
                self.records.extend(recs)
                log.info(f"{lead_key}: {len(recs)} records")
            except Exception as exc:
                log.error(f"{lead_key} failed: {exc}", exc_info=True)

            time.sleep(REQUEST_DELAY)

        log.info(f"Total records: {len(self.records)}")
        return self.records

    def _fetch_all(self, lead_key, doc_code, cat, cat_label) -> list[dict]:
        all_records = []
        page = 1

        while True:
            params = {
                "documentCode": doc_code,
                "beginDate": self.start_date,
                "endDate": self.end_date,
                "pageSize": PAGE_SIZE,
                "pageNumber": page,
                "maxResults": MAX_RESULTS,
            }

            data = self._get(SEARCH_URL, params)
            if not data:
                break

            results = data.get("searchResults", []) or []
            total = data.get("totalResults", 0) or 0

            log.info(f"Page {page}: {len(results)} rows (total={total})")

            for item in results:
                try:
                    rec = self._item_to_record(item, lead_key, cat, cat_label)
                    if rec:
                        all_records.append(rec)
                except Exception as exc:
                    log.debug(f"Row error: {exc}")

            if len(all_records) >= total or len(results) < PAGE_SIZE:
                break

            page += 1
            time.sleep(REQUEST_DELAY)

        return all_records

    def _item_to_record(
        self, item: dict, lead_key: str, cat: str, cat_label: str
    ) -> Optional[dict]:
        doc_num = str(item.get("recordingNumber", "")).strip()
        if not doc_num:
            return None

        document_code = str(item.get("documentCode", "")).strip() or cat

        return {
            "doc_num": doc_num,
            "doc_type": document_code,
            "filed": _norm_date(str(item.get("recordingDate", "")).strip()),
            "cat": cat,
            "cat_label": cat_label,
            "lead_key": lead_key,
            "owner": item.get("names", "") or "",
            "grantee": "",
            "amount": None,
            "legal": "",
            "prop_address": None,
            "prop_city": None,
            "prop_state": "AZ",
            "prop_zip": None,
            "mail_address": None,
            "mail_city": None,
            "mail_state": None,
            "mail_zip": None,
            "parcel": None,
            "first_name": None,
            "last_name": None,
            "first_name_2": None,
            "last_name_2": None,
            "trustee_name": None,
            "trustee_phone": None,
            "auction_date": None,
            "pdf_url": f"{API_BASE}/documents/{doc_num}/pdf",
            "pdf_fallback": f"{PORTAL_BASE}/recording/document-preview.html?recNum={doc_num}",
            "clerk_url": f"{PORTAL_BASE}/recording/document-details?id={doc_num}",
            "flags": [],
            "score": 0,
        }

    def _get(self, url: str, params: dict) -> Optional[dict]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = SESSION.get(url, params=params, timeout=20)
                if resp.ok:
                    return resp.json()

                log.warning(f"HTTP {resp.status_code}: {resp.text[:200]}")
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
