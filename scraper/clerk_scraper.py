from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

from playwright.sync_api import BrowserContext, Page, sync_playwright

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://recorder.maricopa.gov/recording/document-search.html"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class SearchResult:
    doc_num: str
    doc_type: str
    filed: str
    clerk_url: str
    pdf_url: str
    pdf_path: str
    meta: dict[str, Any]


class MaricopaClerkScraper:
    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 45_000,
        slow_mo_ms: int = 0,
        downloads_dir: str | Path = "grouped_output/pdfs",
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.slow_mo_ms = slow_mo_ms
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

    def scrape(self, beginning_date: str, end_date: str, document_code: str = "NS") -> list[SearchResult]:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
            context = browser.new_context(
                accept_downloads=True,
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 1080},
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            try:
                self._open_search(page)
                self._run_search(page, beginning_date, end_date, document_code)
                results = self._extract_results(context, page)
                return results
            finally:
                context.close()
                browser.close()

    def _open_search(self, page: Page) -> None:
        LOGGER.info("Opening clerk portal: %s", BASE_URL)
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("input, select", timeout=self.timeout_ms)
        
    def _run_search(self, page: Page, beginning_date: str, end_date: str, document_code: str) -> None:
        LOGGER.info("Running search for code=%s, dates=%s -> %s", document_code, beginning_date, end_date)

        self._fill_first_existing(
            page,
            [
                "input[placeholder*='BEGINNING DATE']",
                "input[aria-label*='BEGINNING DATE']",
                "input[name*='begin' i]",
                "input[id*='begin' i]",
            ],
            beginning_date,
        )
        self._fill_first_existing(
            page,
            [
                "input[placeholder*='END DATE']",
                "input[aria-label*='END DATE']",
                "input[name*='end' i]",
                "input[id*='end' i]",
            ],
            end_date,
        )

        self._select_document_code(page, document_code)

        clicked = False
        search_candidates = [
            page.get_by_role("button", name=re.compile(r"^SEARCH$", re.I)).nth(2),
            page.locator("button:has-text('SEARCH')").nth(2),
            page.locator("input[type='submit'][value*='SEARCH' i]").nth(2),
        ]
        for candidate in search_candidates:
            try:
                candidate.click()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Could not click the main document search button.")

        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2_000)
        page.wait_for_load_state("networkidle")

    def _select_document_code(self, page: Page, document_code: str) -> None:
        select_candidates = [
            "select[name*='documentCode' i]",
            "select[id*='documentCode' i]",
            "select[aria-label*='DOCUMENT CODE' i]",
            "select:near(:text('DOCUMENT CODE'))",
        ]

        for selector in select_candidates:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.select_option(value=document_code)
                LOGGER.info("Selected document code by value using selector: %s", selector)
                return
            except Exception:
                try:
                    locator = page.locator(selector).first
                    locator.select_option(label=re.compile(r"^(NS|NOTS|Notice of Trustee Sale)", re.I))
                    LOGGER.info("Selected document code by label using selector: %s", selector)
                    return
                except Exception:
                    continue

        # Final fallback: try raw JS across all selects.
        js = """
        (code) => {
          const selects = Array.from(document.querySelectorAll('select'));
          for (const s of selects) {
            const opt = Array.from(s.options).find(o =>
              (o.value || '').trim().toUpperCase() === code.toUpperCase() ||
              (o.textContent || '').toUpperCase().includes('NOTICE OF TRUSTEE') ||
              (o.textContent || '').trim().toUpperCase() === 'NOTS'
            );
            if (opt) {
              s.value = opt.value;
              s.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }
          }
          return false;
        }
        """
        ok = page.evaluate(js, document_code)
        if not ok:
            raise RuntimeError("Could not locate/select the document code dropdown.")

    def _extract_results(self, context: BrowserContext, page: Page) -> list[SearchResult]:
        LOGGER.info("Collecting search results...")
        rows = self._extract_result_rows_from_dom(page)
        LOGGER.info("Found %s result rows.", len(rows))

        results: list[SearchResult] = []
        for idx, row in enumerate(rows, start=1):
            clerk_url = row.get("clerk_url", "")
            if not clerk_url:
                continue
            try:
                result = self._process_result(context, clerk_url, row, idx)
                if result:
                    results.append(result)
            except Exception as exc:
                LOGGER.exception("Failed processing result %s (%s): %s", idx, clerk_url, exc)
        return results

    def _extract_result_rows_from_dom(self, page: Page) -> list[dict[str, Any]]:
        js = """
        () => {
          const abs = (href) => href ? new URL(href, location.href).toString() : '';
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          const out = [];
          const seen = new Set();
          for (const a of anchors) {
            const href = a.getAttribute('href') || '';
            const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
            const nearby = (a.closest('tr, li, div')?.innerText || '').replace(/\s+/g, ' ').trim();
            const combo = `${text} ${nearby} ${href}`.toUpperCase();
            if (!combo) continue;
            const docNumMatch = combo.match(/\b20\d{9,}\b/);
            const looksRelevant = /NOTICE OF TRUSTEE|NOTS|NS\b/.test(combo) || /DETAIL|DOCUMENT|VIEW/.test(combo);
            if (!looksRelevant || !docNumMatch) continue;
            const absolute = abs(href);
            if (!absolute || seen.has(absolute)) continue;
            seen.add(absolute);
            out.push({
              doc_num: docNumMatch[0],
              row_text: nearby || text,
              clerk_url: absolute,
            });
          }
          return out;
        }
        """
        rows = page.evaluate(js)
        if rows:
            return rows

        # Fallback if site renders no anchors at evaluation time.
        html = page.content()
        matches = re.findall(r"https?://[^\s\"']+", html)
        out = []
        for href in matches:
            if "record" in href.lower() or "document" in href.lower():
                out.append({"doc_num": "", "row_text": "", "clerk_url": href})
        return out

    def _process_result(
        self,
        context: BrowserContext,
        clerk_url: str,
        seed_meta: dict[str, Any],
        row_number: int,
    ) -> Optional[SearchResult]:
        detail = context.new_page()
        detail.set_default_timeout(self.timeout_ms)
        try:
            detail.goto(clerk_url, wait_until="domcontentloaded")
            detail.wait_for_load_state("networkidle")
            meta = self._extract_detail_meta(detail, seed_meta)
            pdf_url = self._find_pdf_url(detail)
            pdf_path = self._download_pdf(context, pdf_url, meta.get("doc_num") or seed_meta.get("doc_num") or str(row_number))
            return SearchResult(
                doc_num=meta.get("doc_num") or seed_meta.get("doc_num") or "",
                doc_type=meta.get("doc_type") or "NS",
                filed=meta.get("filed") or "",
                clerk_url=clerk_url,
                pdf_url=pdf_url,
                pdf_path=str(pdf_path),
                meta=meta,
            )
        finally:
            detail.close()

    def _extract_detail_meta(self, page: Page, seed_meta: dict[str, Any]) -> dict[str, Any]:
        text = re.sub(r"\s+", " ", page.locator("body").inner_text()).strip()
        meta = dict(seed_meta)

        doc_num = self._search_first(r"\b20\d{9,}\b", text)
        filed = self._search_first(
            r"(?:RECORDED|FILED|RECORDING DATE|RECORD DATE)\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{2,4})",
            text,
            group=1,
        )
        doc_type = self._search_first(
            r"(NOTICE OF TRUSTEE SALE|NOTS|NS)",
            text,
            group=1,
        )

        if doc_num:
            meta["doc_num"] = doc_num
        if filed:
            meta["filed"] = filed
        if doc_type:
            meta["doc_type"] = doc_type
        meta["detail_text"] = text
        return meta

    def _find_pdf_url(self, page: Page) -> str:
        # Direct anchors first.
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(a => ({href: a.href, text: (a.textContent || '').trim()}))",
        )
        for item in hrefs:
            href = item.get("href", "")
            text = item.get("text", "")
            combo = f"{text} {href}".upper()
            if ".PDF" in combo or " PDF" in combo or "DOWNLOAD" in combo:
                return href

        # Look for iframe/object/embed.
        for selector in ["iframe", "embed", "object"]:
            try:
                src = page.locator(selector).first.get_attribute("src")
                if src and ".pdf" in src.lower():
                    return urljoin(page.url, src)
            except Exception:
                pass

        # JavaScript fallback.
        pdf_url = page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll('a[href], iframe[src], embed[src], object[data]'));
              for (const node of nodes) {
                const candidate = node.href || node.src || node.data || '';
                if (candidate.toLowerCase().includes('.pdf')) return new URL(candidate, location.href).toString();
              }
              return '';
            }
            """
        )
        if pdf_url:
            return pdf_url
        raise RuntimeError(f"Could not find a PDF URL on detail page: {page.url}")

    def _download_pdf(self, context: BrowserContext, pdf_url: str, doc_num: str) -> Path:
        safe_doc = re.sub(r"[^0-9A-Za-z_.-]+", "_", doc_num) or f"doc_{int(time.time())}"
        target_path = self.downloads_dir / f"{safe_doc}.pdf"

        # If direct open works, download bytes through the browser context request API.
        response = context.request.get(pdf_url)
        if not response.ok:
            raise RuntimeError(f"PDF request failed ({response.status}) for {pdf_url}")
        body = response.body()
        if not body:
            raise RuntimeError(f"Empty PDF response for {pdf_url}")
        target_path.write_bytes(body)
        LOGGER.info("Saved PDF: %s", target_path)
        return target_path

    @staticmethod
    def _fill_first_existing(page: Page, selectors: list[str], value: str) -> None:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                locator.click()
                locator.fill("")
                locator.type(value, delay=10)
                return
            except Exception:
                continue
        raise RuntimeError(f"Could not locate date input for value: {value}")

    @staticmethod
    def _search_first(pattern: str, text: str, group: int = 0) -> str:
        match = re.search(pattern, text, re.I)
        if not match:
            return ""
        return (match.group(group) or "").strip()


def results_to_dicts(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
