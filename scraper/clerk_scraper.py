"""
clerk_scraper.py  v14 — API first, browser fallback

Updates:
- API search first
- If API returns zero, fallback to Playwright browser search on recorder portal
- Compatible with current fetch.py
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("clerk_scraper")

API_BASE = "https://publicapi.recorder.maricopa.gov"
SEARCH_URL = f"{API_BASE}/documents/search"
PORTAL_BASE = "https://recorder.maricopa.gov"
SEARCH_PAGE = f"{PORTAL_BASE}/recording/document-search.html"

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
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
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
        self.start_date = start_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.end_date = end_date or datetime.utcnow().strftime("%Y-%m-%d")
        self.records: list[dict] = []

        self.downloads_dir = Path(kwargs.get("downloads_dir", "grouped_output/pdfs"))
        self.base_url = PORTAL_BASE
        self.headless = kwargs.get("headless", True)
        self.slow_mo_ms = int(kwargs.get("slow_mo_ms", 0) or 0)

    def scrape(self, start_date=None, end_date=None, document_code=None):
        if start_date:
            self.start_date = _coerce_date(start_date)
        if end_date:
            self.end_date = _coerce_date(end_date)

        if document_code:
            key = str(document_code).strip().upper()
            existing = self.lead_types.get(key)
            if existing:
                self.lead_types = {key: existing}
            else:
                self.lead_types = {key: (key, key)}

        return asyncio.run(self.run())

    async def run(self) -> list[dict]:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        keep = self.downloads_dir / ".keep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")

        try:
            SESSION.get(SEARCH_PAGE, timeout=15)
            log.info("Session warmed up via portal")
        except Exception as exc:
            log.warning(f"Portal warmup failed (non-fatal): {exc}")

        self.records = []

        for lead_key in self.lead_types:
            doc_code = DOC_CODES.get(lead_key, lead_key)
            cat, cat_label = self.lead_types[lead_key]
            log.info(f"Scraping {lead_key} ({cat_label})")

            recs = []
            try:
                recs = self._fetch_all_api(lead_key, doc_code, cat, cat_label)
                log.info(f"API returned {len(recs)} records for {lead_key}")
            except Exception as exc:
                log.warning(f"API failed for {lead_key}: {exc}")

            if not recs:
                try:
                    recs = await self._fetch_all_browser(lead_key, doc_code, cat, cat_label)
                    log.info(f"Browser returned {len(recs)} records for {lead_key}")
                except Exception as exc:
                    log.error(f"Browser fallback failed for {lead_key}: {exc}", exc_info=True)

            self.records.extend(recs)
            time.sleep(REQUEST_DELAY)

        log.info(f"Total records: {len(self.records)}")
        return self.records

    def _fetch_all_api(self, lead_key, doc_code, cat, cat_label) -> list[dict]:
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
            if data is None:
                break

            results = data.get("searchResults", [])
            total = data.get("totalResults", 0)
            log.info(f"API page {page}: {len(results)} rows (total={total})")

            for item in results:
                try:
                    rec = self._item_to_record_api(item, lead_key, cat, cat_label)
                    if rec:
                        all_records.append(rec)
                except Exception as exc:
                    log.debug(f"API row error: {exc}")

            if len(all_records) >= total or len(results) < PAGE_SIZE:
                break

            page += 1
            time.sleep(REQUEST_DELAY)

        return all_records

    async def _fetch_all_browser(self, lead_key, doc_code, cat, cat_label) -> list[dict]:
        from playwright.async_api import async_playwright

        mmdd_start = _yyyy_mm_dd_to_mmddyyyy(self.start_date)
        mmdd_end = _yyyy_mm_dd_to_mmddyyyy(self.end_date)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto(SEARCH_PAGE, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)

            await self._fill_search_form(page, doc_code, mmdd_start, mmdd_end)
            await self._submit_search(page)

            await page.wait_for_timeout(5000)

            records = await self._extract_results_from_page(page, lead_key, cat, cat_label)

            # sometimes results open in same tab with delayed render
            if not records:
                await page.wait_for_timeout(5000)
                records = await self._extract_results_from_page(page, lead_key, cat, cat_label)

            await browser.close()
            return records

    async def _fill_search_form(self, page, doc_code: str, begin_date: str, end_date: str) -> None:
        # Document code
        filled = False
        code_selectors = [
            "select",
            "[role='combobox']",
            "input[placeholder*='Document Code' i]",
        ]

        for sel in code_selectors:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                for i in range(count):
                    item = locator.nth(i)
                    try:
                        tag = await item.evaluate("(el) => el.tagName.toLowerCase()")
                    except Exception:
                        continue

                    if tag == "select":
                        options_text = (await item.inner_text()).upper()
                        if doc_code.upper() in options_text:
                            await item.select_option(label=doc_code)
                            filled = True
                            break
                        try:
                            await item.select_option(value=doc_code)
                            filled = True
                            break
                        except Exception:
                            pass
                    else:
                        await item.fill(doc_code)
                        filled = True
                        break
                if filled:
                    break
            except Exception:
                continue

        # Date fields
        await self._fill_date_fields(page, begin_date, end_date)

    async def _fill_date_fields(self, page, begin_date: str, end_date: str) -> None:
        inputs = page.locator("input")
        count = await inputs.count()

        date_like = []
        for i in range(count):
            inp = inputs.nth(i)
            try:
                placeholder = (await inp.get_attribute("placeholder") or "").upper()
                aria = (await inp.get_attribute("aria-label") or "").upper()
                name = (await inp.get_attribute("name") or "").upper()
                val = f"{placeholder} {aria} {name}"
                if "DATE" in val or "BEGIN" in val or "END" in val:
                    date_like.append(inp)
            except Exception:
                continue

        if len(date_like) >= 2:
            await date_like[0].fill(begin_date)
            await date_like[1].fill(end_date)
            return

        # fallback: fill last two visible text/date inputs
        visible_inputs = []
        for i in range(count):
            inp = inputs.nth(i)
            try:
                typ = (await inp.get_attribute("type") or "text").lower()
                if typ in {"text", "date", "search"} and await inp.is_visible():
                    visible_inputs.append(inp)
            except Exception:
                continue

        if len(visible_inputs) >= 2:
            await visible_inputs[-2].fill(begin_date)
            await visible_inputs[-1].fill(end_date)

    async def _submit_search(self, page) -> None:
        # Click the search button in the general document search section.
        for sel in [
            "button:has-text('SEARCH')",
            "input[type='submit']",
            "button",
        ]:
            try:
                buttons = page.locator(sel)
                count = await buttons.count()
                for i in range(count):
                    btn = buttons.nth(i)
                    txt = ""
                    try:
                        txt = ((await btn.inner_text()) or "").strip().upper()
                    except Exception:
                        pass
                    if sel == "button" and txt != "SEARCH":
                        continue
                    if await btn.is_visible():
                        await btn.click()
                        return
            except Exception:
                continue

    async def _extract_results_from_page(self, page, lead_key, cat, cat_label) -> list[dict]:
        html = await page.content()

        # Preferred extraction from links with recording numbers
        rec_nums = sorted(set(re.findall(r"recNum=(20\d{9,})", html)))
        if not rec_nums:
            rec_nums = sorted(set(re.findall(r"\b(20\d{9,})\b", html)))

        filed_map = self._extract_filed_dates_from_html(html)

        records = []
        for doc_num in rec_nums:
            records.append(
                {
                    "doc_num": doc_num,
                    "doc_type": lead_key,
                    "filed": filed_map.get(doc_num, self.end_date),
                    "cat": cat,
                    "cat_label": cat_label,
                    "lead_key": lead_key,
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
                    "pdf_url": f"{API_BASE}/documents/{doc_num}/pdf",
                    "pdf_fallback": f"{PORTAL_BASE}/recording/document-preview.html?recNum={doc_num}",
                    "pdf_path": str(self.downloads_dir / f"{doc_num}.pdf"),
                    "clerk_url": f"{PORTAL_BASE}/recording/document-details?id={doc_num}",
                    "flags": [],
                    "score": 0,
                }
            )

        return records

    def _extract_filed_dates_from_html(self, html: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        # capture a nearby mm/dd/yyyy around a recording number
        for m in re.finditer(r"(20\d{9,})", html):
            doc_num = m.group(1)
            window_start = max(0, m.start() - 200)
            window_end = min(len(html), m.end() + 200)
            window = html[window_start:window_end]
            dm = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", window)
            if dm:
                try:
                    mapping[doc_num] = datetime.strptime(dm.group(1), "%m/%d/%Y").strftime("%Y-%m-%d")
                except Exception:
                    mapping[doc_num] = self.end_date
        return mapping

    def _item_to_record_api(self, item: dict, lead_key, cat, cat_label) -> Optional[dict]:
        doc_num = str(item.get("recordingNumber", "")).strip()
        if not doc_num:
            return None

        pdf_path = self.downloads_dir / f"{doc_num}.pdf"

        return {
            "doc_num": doc_num,
            "doc_type": item.get("documentCode", cat),
            "filed": _norm_date(item.get("recordingDate", "")),
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
            "pdf_path": str(pdf_path),
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
                log.warning(f"  HTTP {resp.status_code} — {resp.text[:200]}")
            except Exception as exc:
                log.warning(f"  Attempt {attempt} failed: {exc}")
            time.sleep(RETRY_DELAY * attempt)
        return None


def _coerce_date(raw: str) -> str:
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _yyyy_mm_dd_to_mmddyyyy(raw: str) -> str:
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return raw


def _norm_date(raw: str) -> str:
    raw = str(raw).strip().replace("-", "/")
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw
