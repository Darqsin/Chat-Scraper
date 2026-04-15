from __future__ import annotations

import argparse
import json
import logging
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
    parser = argparse.ArgumentParser(description="Scrape Maricopa County Notice of Trustee Sale leads.")
    parser.add_argument("--start-date", help="MM/DD/YYYY")
    parser.add_argument("--end-date", help="MM/DD/YYYY")
    parser.add_argument("--lookback-days", type=int, default=1, help="Days back when start/end not supplied.")
    parser.add_argument("--document-code", default="NS", help="Document code to search. Default: NS")
    parser.add_argument("--headful", action="store_true", help="Run browser visibly for debugging.")
    parser.add_argument("--slow-mo", type=int, default=0, help="Playwright slow motion in milliseconds.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def resolve_date_range(start_date: Optional[str], end_date: Optional[str], lookback_days: int) -> tuple[str, str]:
    if start_date and end_date:
        _validate_mmddyyyy(start_date)
        _validate_mmddyyyy(end_date)
        return start_date, end_date

    target = date.today() - timedelta(days=lookback_days)
    text = target.strftime("%m/%d/%Y")
    return text, text


def _validate_mmddyyyy(value: str) -> None:
    datetime.strptime(value, "%m/%d/%Y")


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("fetch")

    start_date, end_date = resolve_date_range(args.start_date, args.end_date, args.lookback_days)
    logger.info("Resolved date range: %s -> %s", start_date, end_date)

    downloads_dir = ROOT / "grouped_output" / "pdfs"
    raw_text_dir = ROOT / "parsed_output"
    data_dir = ROOT / "data"
    dashboard_dir = ROOT / "dashboard"

    scraper = MaricopaClerkScraper(
        headless=not args.headful,
        slow_mo_ms=args.slow_mo,
        downloads_dir=downloads_dir,
    )

    results = scraper.scrape(start_date, end_date, document_code=args.document_code)
    logger.info("Scrape returned %s documents.", len(results))

    parsed = []
    for result in results:
        logger.info("Parsing PDF for document %s", result.doc_num)
        parsed_record = parse_record(
            pdf_path=result.pdf_path,
            clerk_url=result.clerk_url,
            pdf_url=result.pdf_url,
            filed=result.filed,
            doc_num=result.doc_num,
            doc_type=result.doc_type,
            cat_label="Notice of Trustee Sale",
            raw_text_dir=raw_text_dir,
        )
        parsed.append(parsed_record)

    payload = build_records_payload(
        parsed,
        source=DEFAULT_SOURCE,
        date_range={"start": start_date, "end": end_date},
    )

    write_json_records(payload, dashboard_dir / "records.json", data_dir / "records.json")
    write_ghl_csv(parsed, data_dir / "ghl_export.csv")
    write_flat_csv(parsed, ROOT / "nts_data.csv")
    write_xlsx(parsed, ROOT / "nts_data.xlsx")

    summary_path = data_dir / "run_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "fetched_at": payload["fetched_at"],
                "date_range": payload["date_range"],
                "total": payload["total"],
                "with_address": payload["with_address"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("Done. Records=%s, with_address=%s", payload["total"], payload["with_address"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
