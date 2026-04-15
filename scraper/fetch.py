from __future__ import annotations

import argparse
import json
import logging
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from clerk_scraper import MaricopaClerkScraper
from enricher import parse_record
from exporter import (
    build_records_payload,
    write_flat_csv,
    write_ghl_csv,
    write_json_records,
    write_xlsx,
)

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "scraper.log"
DEFAULT_SOURCE = "https://recorder.maricopa.gov/recording/document-search.html"


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--lookback-days", type=int, default=1)
    parser.add_argument("--document-code", default="NS")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def resolve_date_range(start_date, end_date, lookback_days):
    if start_date and end_date:
        return start_date, end_date

    target = date.today() - timedelta(days=lookback_days)
    text = target.strftime("%m/%d/%Y")
    return text, text


def download_pdf(url: str, save_path: Path) -> bool:
    try:
        response = requests.get(url, timeout=30)
        if response.ok and response.content:
            save_path.write_bytes(response.content)
            return True
    except Exception as e:
        logging.warning(f"PDF download failed: {url} → {e}")
    return False


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("fetch")

    start_date, end_date = resolve_date_range(
        args.start_date, args.end_date, args.lookback_days
    )

    logger.info("Date range: %s → %s", start_date, end_date)

    downloads_dir = ROOT / "grouped_output" / "pdfs"
    raw_text_dir = ROOT / "parsed_output"
    data_dir = ROOT / "data"
    dashboard_dir = ROOT / "dashboard"

    # 🔥 ENSURE FOLDERS EXIST
    downloads_dir.mkdir(parents=True, exist_ok=True)
    raw_text_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    scraper = MaricopaClerkScraper()

    results = scraper.scrape(start_date, end_date, document_code=args.document_code)
    logger.info("Found %s records", len(results))

    parsed = []

    for r in results:
        doc_num = r.get("doc_num") or r.get("recordingNumber")
        pdf_url = r.get("pdf_url")

        if not doc_num or not pdf_url:
            continue

        pdf_path = downloads_dir / f"{doc_num}.pdf"

        # 🔥 DOWNLOAD PDF
        if not pdf_path.exists():
            success = download_pdf(pdf_url, pdf_path)
            if not success:
                logger.warning(f"Skipping {doc_num} (no PDF)")
                continue

        logger.info(f"Processing {doc_num}")

        try:
            parsed_record = parse_record(
                pdf_path=pdf_path,
                clerk_url=r.get("clerk_url", ""),
                pdf_url=pdf_url,
                filed=r.get("filed", ""),
                doc_num=doc_num,
                doc_type=r.get("doc_type", "NS"),
                cat_label="Notice of Trustee Sale",
                raw_text_dir=raw_text_dir,
            )
            parsed.append(parsed_record)
        except Exception as e:
            logger.error(f"Parse failed for {doc_num}: {e}")

    payload = build_records_payload(
        parsed,
        source=DEFAULT_SOURCE,
        date_range={"start": start_date, "end": end_date},
    )

    write_json_records(payload, dashboard_dir / "records.json", data_dir / "records.json")
    write_ghl_csv(parsed, data_dir / "ghl_export.csv")
    write_flat_csv(parsed, ROOT / "nts_data.csv")
    write_xlsx(parsed, ROOT / "nts_data.xlsx")

    (data_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "total": payload["total"],
                "with_address": payload["with_address"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("DONE → %s records (%s with address)", payload["total"], payload["with_address"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
