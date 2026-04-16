import json
import logging
from pathlib import Path
from datetime import datetime

from clerk_scraper import ClerkScraper
from enricher import parse_record

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("fetch")

OUTPUT_FILE = Path("records.json")
PDF_DIR = Path("pdfs")

def run():
    scraper = ClerkScraper()

    results = scraper.run(document_code="NT")  # Notice of Trustee

    records = []
    with_address = 0

    for r in results:
        try:
            parsed = parse_record(
                pdf_path=r.get("pdf_path"),
                clerk_url=r.get("clerk_url", ""),
                pdf_url=r.get("pdf_url", ""),
                filed=r.get("filed", ""),
                doc_num=r.get("doc_num", ""),
            )

        except Exception as e:
            LOGGER.warning(f"Fallback record used for {r.get('doc_num')}: {e}")
            parsed = r

        if parsed.get("prop_address"):
            with_address += 1

        records.append(parsed)

    output = {
        "fetched_at": datetime.utcnow().isoformat(),
        "total": len(records),
        "with_address": with_address,
        "records": records,
    }

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))

    LOGGER.info(f"FINAL: {len(records)} records | {with_address} with address")


if __name__ == "__main__":
    run()
