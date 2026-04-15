from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

log = logging.getLogger("clerk_scraper")

PORTAL_BASE = "https://recorder.maricopa.gov"
SEARCH_PAGE = f"{PORTAL_BASE}/recording/document-search.html"
RESULTS_PAGE = f"{PORTAL_BASE}/recording/document-search-results.html"


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

        return asyncio.run(self.run(document_code=document_code))

    async def run(self, document_code="NS"):
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

            log.info("Opening results page directly")
            page.goto(results_url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)

            doc_nums = self._extract_doc_numbers(page)
            log.info("Found %s document numbers", len(doc_nums))

            for doc_num in doc_nums:
                if doc_num in seen:
                    continue

                try:
                    filed = self._extract_filed_for_doc(page, doc_num)

                    if not self._open_doc_modal(page, doc_num):
                        log.warning("Could not open modal for %s", doc_num)
                        continue

                    pdf_url = self._extract_pdf_url_from_modal(page)
                    if not pdf_url:
                        log.warning("No PDF URL found in modal for %s", doc_num)
                        self._close_modal(page)
                        continue

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
                            "pdf_url": pdf_url,
                            "pdf_fallback": f"{PORTAL_BASE}/recording/document-preview.html?recNum={doc_num}",
                            "pdf_path": str(self.downloads_dir / f"{doc_num}.pdf"),
                            "clerk_url": f"{PORTAL_BASE}/recording/document-details?id={doc_num}",
                            "flags": [],
                            "score": 0,
                        }
                    )
                    seen.add(doc_num)

                    self._close_modal(page)
                    page.wait_for_timeout(400)

                except Exception as exc:
                    log.warning("Error processing %s: %s", doc_num, exc)
                    try:
                        self._close_modal(page)
                    except Exception:
                        pass

            try:
                browser.close()
            except Exception:
                pass

        log.info("Returning %s records", len(results))
        return results

    def _build_results_url(self, document_code: str, begin_date: str, end_date: str) -> str:
        # Matches the live URL pattern from your screenshot.
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

    def _extract_doc_numbers(self, page) -> list[str]:
        html = page.content()
        nums = re.findall(r"\b(20\d{9,})\b", html)
        # keep stable order, dedupe
        seen = set()
        ordered = []
        for n in nums:
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        return ordered

    def _extract_filed_for_doc(self, page, doc_num: str) -> str:
        # Pull a nearby date from the HTML around the doc number.
        html = page.content()
        idx = html.find(doc_num)
        if idx == -1:
            return ""
        window = html[max(0, idx - 500): idx + 500]
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

    def _open_doc_modal(self, page, doc_num: str) -> bool:
        # Gray number is usually an anchor/button with the recording number text.
        candidates = [
            page.locator(f"a:has-text('{doc_num}')").first,
            page.locator(f"button:has-text('{doc_num}')").first,
            page.locator(f"text={doc_num}").first,
        ]

        for locator in candidates:
            try:
                if locator.count() > 0 and locator.is_visible():
                    locator.click()
                    if self._wait_for_modal(page):
                        return True
            except Exception:
                continue

        return False

    def _wait_for_modal(self, page) -> bool:
        selectors = [
            "text=Document details",
            "text=Preview Unofficial Document",
            "a:has-text('PDF - All pages')",
        ]
        for _ in range(10):
            for sel in selectors:
                try:
                    if page.locator(sel).count() > 0 and page.locator(sel).first.is_visible():
                        return True
                except Exception:
                    continue
            page.wait_for_timeout(500)
        return False

    def _extract_pdf_url_from_modal(self, page) -> str:
        # Best case: href already exists in the modal.
        selectors = [
            "a:has-text('PDF - All pages')",
            "a:has-text('PDF')",
            "a[href*='.pdf']",
            "a[href*='UnOfficialDocs']",
        ]

        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() == 0:
                    continue

                for i in range(loc.count()):
                    link = loc.nth(i)
                    href = link.get_attribute("href")
                    if href:
                        return self._normalize_url(href)
            except Exception:
                continue

        # Fallback: look in modal HTML
        html = page.content()

        # full absolute PDF URL
        m = re.search(r'https?://[^"\']+\.pdf', html, re.I)
        if m:
            return m.group(0)

        # legacy relative PDF path
        m = re.search(r'(/UnOfficialDocs/[^"\']+\.pdf)', html, re.I)
        if m:
            return self._normalize_url(m.group(1))

        # sometimes the doc number is enough to build the real URL
        m = re.search(r"\b(20\d{9,})\b", html)
        if m:
            return f"https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf/{m.group(1)}.pdf"

        return ""

    def _close_modal(self, page) -> None:
        selectors = [
            "button[aria-label='Close']",
            "button:has-text('×')",
            "text=×",
        ]

        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click()
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue

        # Esc fallback
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            pass

    def _normalize_url(self, href: str) -> str:
        href = href.strip()
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if href.startswith("/"):
            if href.lower().startswith("/unofficialdocs"):
                return f"https://legacy.recorder.maricopa.gov{href}"
            return f"{PORTAL_BASE}{href}"
        if href.lower().startswith("unofficialdocs"):
            return f"https://legacy.recorder.maricopa.gov/{href}"
        return f"{PORTAL_BASE}/{href.lstrip('/')}"

    def _coerce_date(self, raw: str) -> str:
        raw = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return raw
