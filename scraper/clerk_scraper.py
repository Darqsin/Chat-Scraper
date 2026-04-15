from playwright.sync_api import sync_playwright
import time


class MaricopaClerkScraper:
    def __init__(self):
        self.base_url = "https://recorder.maricopa.gov/recording/document-search.html"

    def scrape(self, start_date, end_date, document_code="NS"):
        results = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            page.goto(self.base_url, timeout=60000)

            # ---- SET SEARCH FILTERS ----
            page.wait_for_selector("select")

            # Document Type
            page.select_option("select", document_code)

            # Date inputs
            page.fill("input[name='startDate']", start_date)
            page.fill("input[name='endDate']", end_date)

            # Click search
            page.click("button:has-text('Search')")

            # Wait for results
            page.wait_for_selector("table")

            rows = page.query_selector_all("table tbody tr")

            for row in rows:
                try:
                    doc_link = row.query_selector("a")
                    if not doc_link:
                        continue

                    doc_num = doc_link.inner_text().strip()

                    # Open popup
                    with page.expect_popup() as popup_info:
                        doc_link.click()

                    popup = popup_info.value

                    popup.wait_for_load_state()

                    # ---- CLICK PDF ----
                    pdf_link = popup.query_selector("a[href*='pdf']")
                    if not pdf_link:
                        popup.close()
                        continue

                    with popup.expect_popup() as pdf_popup_info:
                        pdf_link.click()

                    pdf_page = pdf_popup_info.value
                    pdf_page.wait_for_load_state()

                    pdf_url = pdf_page.url

                    # Close PDF tab
                    pdf_page.close()
                    popup.close()

                    # Extract filed date (optional)
                    cells = row.query_selector_all("td")
                    filed = cells[1].inner_text().strip() if len(cells) > 1 else ""

                    results.append({
                        "doc_num": doc_num,
                        "pdf_url": pdf_url,
                        "clerk_url": self.base_url,
                        "filed": filed,
                        "doc_type": "NS"
                    })

                    time.sleep(0.5)

                except Exception as e:
                    print(f"Error processing row: {e}")
                    continue

            browser.close()

        return results
