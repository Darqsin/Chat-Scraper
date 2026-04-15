from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

log = logging.getLogger("clerk_scraper")

PORTAL_BASE = "https://recorder.maricopa.gov"
RESULTS_PAGE = f"{PORTAL_BASE}/recording/document-search-results.html"
LEGACY_PDF_BASE = "https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf"


class MaricopaClerkScraper:
    def __init__(
        self,
        lead_types=None,
        start_date=None,
        end_date=None,
        headless=True,
        slow_mo_ms=0,
        downloads_dir="grouped_output/pdfs",
        **kwargs,
    ):
        self.lead_types = lead_types or {
            "NS": ("NOTS", "Notice of Trustee Sale"),
        }
        self.start_date = start_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.end_date = end_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.headless = headless
        self.slow_mo_ms = int(slow_mo_ms or 0)
        self.downloads_dir = Path(downloads_dir)

    def scrape(self, start_date=None, end_date=None, document_code="NS"):
        if start_date:
            self.start_date = self._coerce_date(start_date)
        if end_date:
            self.end_date = self._coerce_date(end_date)

        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        keep = self.downloads_dir / ".keep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

        return self._scrape_results_page(document_code=document_code)

    def _scrape_results_page(self, document_code="NS"):
        results = []
        seen = set()

        results_url = self._build_results_url(
            document_code=document_code,
            begin_date=self.start_date,
            end_date=self.end_date,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                    "--single-process",
                ],
            )

            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            log.info("Opening results page directly: %s", results_url)
            page.goto(results_url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(5000)

            # Wait for the results summary or any anchor with a recording number
            self._wait_for_results(page)

            # Pull only visible anchors that look like recording numbers
            anchors = page.locator("a")
            anchor_count = anchors.count()
            log.info("Found %s anchors on page", anchor_count)

            for i in range(anchor_count):
                try:
                    a = anchors.nth(i)
                    if not a.is_visible():
                        continue

                    text = (a.inner_text() or "").strip()
                    if not re.fullmatch(r"20\d{9,}", text):
                        continue

                    doc_num = text
                    if doc_num in seen:
                        continue

                    filed = self._extract_filed_for_doc(page, doc_num)

                    results.append(
                        {
                            "doc_num": doc_num,
                            "doc_type": "N/TR SALE",
                            "filed": filed,
                            "cat": "NS",
                            "cat_label": "Notice of Trustee Sale",
                            "lead_key": "NS",
                            "owner": "",
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
                            "pdf_url": f"{LEGACY_PDF_BASE}/{doc_num}.pdf",
                            "pdf_fallback": f"{PORTAL_BASE}/recording/document-preview.html?recNum={doc_num}",
                            "pdf_path": str(self.downloads_dir / f"{doc_num}.pdf"),
                            "clerk_url": f"{PORTAL_BASE}/recording/document-details?id={doc_num}",
                            "flags": [],
                            "score": 0,
                        }
                    )
                    seen.add(doc_num)

                except Exception as exc:
                    log.warning("Error processing anchor %s: %s", i, exc)

            try:
                browser.close()
            except Exception:
                pass

        log.info("Returning %s records", len(results))
        return results

    def _build_results_url(self, document_code: str, begin_date: str, end_date: str) -> str:
        return (
            f"{RESULTS_PAGE}"
            f"?lastNames="
            f"&firstNames="
            f"&middleNames="
            f"&documentTypeSelector=code"
            f"&documentCode={document_code}"
            f"&beginDate={begin_date}"
            f"&endDate={end_date}"
        )

    def _wait_for_results(self, page) -> None:
        for _ in range(20):
            try:
                html = page.content()
                if "Showing" in html and re.search(r"20\d{9,}", html):
                    return
            except Exception:
                pass
            page.wait_for_timeout(1000)

    def _extract_filed_for_doc(self, page, doc_num: str) -> str:
        try:
            html = page.content()
            idx = html.find(doc_num)
            if idx == -1:
                return ""
            window = html[max(0, idx - 800): idx + 800]
            m = re.search(r"\b(\d{1,2}-\d{1,2}-\d{4}|\d{2}/\d{2}/\d{4})\b", window)
            if not m:
                return ""
            raw = m.group(1)
            for fmt in ("%m-%d-%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            return raw
        except Exception:
            return ""

    def _coerce_date(self, raw: str) -> str:
        raw = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return raw
