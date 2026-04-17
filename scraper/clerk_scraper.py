from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("clerk_scraper")

API_BASE = "https://publicapi.recorder.maricopa.gov"
SEARCH_URL = f"{API_BASE}/documents/search"
PORTAL_BASE = "https://recorder.maricopa.gov"
LEGACY_PDF_BASE = "https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf"

PAGE_SIZE = 200
MAX_RESULTS = 200

MAX_RETRIES = 3
RETRY_DELAY = 5
REQUEST_DELAY = 0.5

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
        "Referer": f"{PORTAL_BASE}/recording/document-search-results.html",
        "Origin": PORTAL_BASE,
    }
)


class MaricopaClerkScraper:
    def __init__(self, lead_types=None, start_date=None, end_date=None, **kwargs):
        self.lead_types = lead_types or {
            "NS": ("NOTS", "Notice of Trustee Sale"),
        }
        self.start_date = start_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.end_date = end_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.records: list[dict] = []

        self.downloads_dir = Path(kwargs.get("downloads_dir", "grouped_output/pdfs"))

    def scrape(self, start_date=None, end_date=None, document_code=None):
        if start_date:
            self.start_date = _coerce_date(start_date)
        if end_date:
            self.end_date = _coerce_date(end_date)

        return asyncio.run(self.run())

    async def run(self) -> list[dict]:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.records = []

        recs = self._fetch_all()
        self.records.extend(recs)

        log.info(f"Total records: {len(self.records)}")
        return self.records

    def _fetch_all(self) -> list[dict]:
        all_records = []
        page = 1

        while True:
            params = {
                "documentCode": "NS",
                "beginDate": self.start_date,
                "endDate": self.end_date,
                "pageSize": PAGE_SIZE,
                "pageNumber": page,
                "maxResults": MAX_RESULTS,
            }

            data = self._get(SEARCH_URL, params)
            if not data:
                break

            results = data.get("searchResults", [])
            total = data.get("totalResults", 0)

            log.info(f"Page {page}: {len(results)} records")

            for item in results:
                rec = self._item_to_record(item)
                if rec:
                    all_records.append(rec)

            if len(all_records) >= total or len(results) < PAGE_SIZE:
                break

            page += 1

        return all_records

    def _item_to_record(self, item: dict) -> Optional[dict]:
        doc_num = str(item.get("recordingNumber", "")).strip()
        if not doc_num:
            return None

        pdf_url = f"{LEGACY_PDF_BASE}/{doc_num}.pdf"
        pdf_path = self.downloads_dir / f"{doc_num}.pdf"

        downloaded = self._download_pdf(pdf_url, pdf_path)

        flags = []
        if not downloaded:
            flags.append("no_pdf")

        return {
            "doc_num": doc_num,
            "doc_type": item.get("documentCode", ""),
            "filed": _norm_date(item.get("recordingDate", "")),
            "owner": item.get("names", "") or "",
            "pdf_url": pdf_url,
            "pdf_path": str(pdf_path) if downloaded else "",
            "flags": flags,
            "score": 0,
        }

    def _download_pdf(self, url: str, path: Path) -> bool:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = SESSION.get(url, timeout=20)

                if resp.status_code == 200 and resp.content.startswith(b"%PDF"):
                    path.write_bytes(resp.content)
                    return True

            except Exception as e:
                log.warning(f"Download error {url}: {e}")

            time.sleep(RETRY_DELAY * attempt)

        return False

    def _get(self, url: str, params: dict) -> Optional[dict]:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = SESSION.get(url, params=params, timeout=20)
                if resp.ok:
                    return resp.json()
            except Exception as e:
                log.warning(f"API error: {e}")

            time.sleep(RETRY_DELAY * attempt)

        return None


def _coerce_date(raw: str) -> str:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except:
            pass
    return raw


def _norm_date(raw: str) -> str:
    raw = str(raw).replace("-", "/")
    for fmt in ("%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except:
            pass
    return raw
