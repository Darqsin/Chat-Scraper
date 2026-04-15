from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

log = logging.getLogger("clerk_scraper")

PORTAL_BASE = "https://recorder.maricopa.gov"
SEARCH_URL = f"{PORTAL_BASE}/recording/document-search.html"


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

        return self._scrape_browser(document_code=document_code)

    def _scrape_browser(self, document_code="NS"):
        results = []
        seen = set()

        start_mmddyyyy = self._to_mmddyyyy(self.start_date)
        end_mmddyyyy = self._to_mmddyyyy(self.end_date)

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

            log.info("Opening search page")
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)

            self._fill_search_form(page, document_code, start_mmddyyyy, end_mmddyyyy)
            self._submit_search(page)

            self._wait_for_results(page)

            rows = page.locator("table tbody tr")
            row_count = rows.count()
            log.info("Found %s result rows", row_count)

            for i in range(row_count):
                try:
                    row = rows.nth(i)
                    row_text = row.inner_text().strip()
                    if not row_text:
                        continue

                    doc_num = self._extract_doc_num_from_row(row_text)
                    if not doc_num or doc_num in seen:
                        continue

                    filed = self._extract_filed_from_row(row_text)

                    doc_link = row.locator("a").first
                    if doc_link.count() == 0:
                        continue

                    before_pages = len(context.pages)
                    doc_link.click()
                    page.wait_for_timeout(1500)

                    if len(context.pages) <= before_pages:
                        log.warning("No popup for document %s", doc_num)
                        continue

                    popup = context.pages[-1]
                    popup.wait_for_load_state("domcontentloaded", timeout=30000)
                    popup.wait_for_timeout(1500)

                    pdf_url = self._extract_pdf_url_from_popup(popup)

                    if not pdf_url:
                        log.warning("No PDF URL found for %s", doc_num)
                        try:
                            popup.close()
                        except Exception:
                            pass
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
                            "pdf_fallback": popup.url,
                            "pdf_path": str(self.downloads_dir / f"{doc_num}.pdf"),
                            "clerk_url": f"{PORTAL_BASE}/recording/document-details?id={doc_num}",
                            "flags": [],
                            "score": 0,
                        }
                    )
                    seen.add(doc_num)

                    try:
                        popup.close()
                    except Exception:
                        pass

                    page.wait_for_timeout(500)

                except Exception as exc:
                    log.warning("Error processing row %s: %s", i, exc)

            try:
                browser.close()
            except Exception:
                pass

        log.info("Returning %s records", len(results))
        return results

    def _fill_search_form(self, page, document_code, start_date, end_date):
        log.info("Filling search form")

        selected = False
        selects = page.locator("select")
        for i in range(selects.count()):
            sel = selects.nth(i)
            try:
                options_text = sel.inner_text().upper()
                if document_code.upper() in options_text:
                    try:
                        sel.select_option(label=document_code)
                        selected = True
                        break
                    except Exception:
                        try:
                            sel.select_option(value=document_code)
                            selected = True
                            break
                        except Exception:
                            pass
            except Exception:
                continue

        if not selected:
            log.warning("Could not confidently select document code %s", document_code)

        inputs = page.locator("input")
        date_inputs = []

        for i in range(inputs.count()):
            inp = inputs.nth(i)
            try:
                if not inp.is_visible():
                    continue
                typ = (inp.get_attribute("type") or "").lower()
                name = (inp.get_attribute("name") or "").lower()
                placeholder = (inp.get_attribute("placeholder") or "").lower()
                aria = (inp.get_attribute("aria-label") or "").lower()
                bucket = f"{typ} {name} {placeholder} {aria}"

                if "date" in bucket or "begin" in bucket or "end" in bucket:
                    date_inputs.append(inp)
            except Exception:
                continue

        if len(date_inputs) >= 2:
            date_inputs[0].fill(start_date)
            date_inputs[1].fill(end_date)
            return

        visible_text_inputs = []
        for i in range(inputs.count()):
            inp = inputs.nth(i)
            try:
                if not inp.is_visible():
                    continue
                typ = (inp.get_attribute("type") or "text").lower()
                if typ in {"text", "search", "date"}:
                    visible_text_inputs.append(inp)
            except Exception:
                continue

        if len(visible_text_inputs) >= 2:
            visible_text_inputs[-2].fill(start_date)
            visible_text_inputs[-1].fill(end_date)

    def _submit_search(self, page):
        log.info("Submitting search")

        button_selectors = [
            "button:has-text('Search')",
            "button:has-text('SEARCH')",
            "input[type='submit']",
            "button",
        ]

        for sel in button_selectors:
            try:
                locator = page.locator(sel)
                count = locator.count()
                for i in range(count):
                    btn = locator.nth(i)
                    if not btn.is_visible():
                        continue
                    text = ""
                    try:
                        text = btn.inner_text().strip().upper()
                    except Exception:
                        pass
                    if sel == "button" and text not in {"SEARCH", ""}:
                        continue
                    btn.click()
                    page.wait_for_timeout(2000)
                    return
            except Exception:
                continue

        raise RuntimeError("Could not click Search button")

    def _wait_for_results(self, page):
        log.info("Waiting for results")

        for _ in range(20):
            try:
                if page.locator("table tbody tr").count() > 0:
                    return
            except Exception:
                pass
            page.wait_for_timeout(1000)

        raise PlaywrightTimeoutError("Timed out waiting for results table")

    def _extract_pdf_url_from_popup(self, popup):
        selectors = [
            "a:has-text('PDF')",
            "a:has-text('PDF - All pages')",
            "a[href*='.pdf']",
            "a[href*='UnOfficialDocs']",
        ]

        for sel in selectors:
            try:
                links = popup.locator(sel)
                count = links.count()
                for i in range(count):
                    link = links.nth(i)
                    href = link.get_attribute("href")
                    if href:
                        return self._normalize_url(href)

                    before_pages = len(popup.context.pages)
                    link.click()
                    popup.wait_for_timeout(1500)

                    if len(popup.context.pages) > before_pages:
                        pdf_page = popup.context.pages[-1]
                        pdf_url = pdf_page.url
                        try:
                            pdf_page.close()
                        except Exception:
                            pass
                        if pdf_url and pdf_url != "about:blank":
                            return pdf_url
            except Exception:
                continue

        html = popup.content()
        match = re.search(r'https?://[^"\']+\.pdf', html, re.I)
        if match:
            return match.group(0)

        match = re.search(r'(/UnOfficialDocs/[^"\']+\.pdf)', html, re.I)
        if match:
            return self._normalize_url(match.group(1))

        return ""

    def _normalize_url(self, href):
        href = href.strip()
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if href.startswith("/"):
            return f"{PORTAL_BASE}{href}"
        return f"{PORTAL_BASE}/{href.lstrip('/')}"

    def _extract_doc_num_from_row(self, text):
        m = re.search(r"\b(20\d{9,})\b", text)
        return m.group(1) if m else ""

    def _extract_filed_from_row(self, text):
        m = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", text)
        if not m:
            return ""
        try:
            return datetime.strptime(m.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
        except Exception:
            return m.group(1)

    def _coerce_date(self, raw):
        raw = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return raw

    def _to_mmddyyyy(self, raw):
        raw = str(raw).strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
            except ValueError:
                continue
        return raw
