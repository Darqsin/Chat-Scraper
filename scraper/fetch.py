from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

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
    parser.add_argument("--lookback-days", type=int, default=1)
    parser.add_argument("--document-code", default="NS")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def resolve_date_range(start_date: Optional[str], end_date: Optional[str], lookback_days: int) -> tuple[str, str]:
    if start_date and end_date:
        datetime.strptime(start_date, "%m/%d/%Y")
        datetime.strptime(end_date, "%m/%d/%Y")
        return start_date, end_date

    target = date.today() - timedelta(days=lookback_days)
    text = target.strftime("%m/%d/%Y")
    return text, text


def mmddyyyy_to_yyyymmdd(value: str) -> str:
    return datetime.strptime(value, "%m/%d/%Y").strftime("%Y-%m-%d")


def download_pdf(url: str, save_path: Path, logger: logging.Logger) -> bool:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/pdf,*/*",
        }

        resp = requests.get(url, headers=headers, timeout=45)

        if resp.ok and resp.content and resp.content[:4] == b"%PDF":
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
            return True

        logger.warning("Bad PDF response: %s | status=%s", url, resp.status_code)

    except Exception as exc:
        logger.warning("PDF download failed: %s | %s", url, exc)

    return False


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("fetch")

    start_mm, end_mm = resolve_date_range(args.start_date, args.end_date, args.lookback_days)
    start_api = mmddyyyy_to_yyyymmdd(start_mm)
    end_api = mmddyyyy_to_yyyymmdd(end_mm)

    logger.info("Date range: %s -> %s", start_mm, end_mm)

    downloads_dir = ROOT / "grouped_output" / "pdfs"
    raw_text_dir = ROOT / "parsed_output"
    data_dir = ROOT / "data"
    dashboard_dir = ROOT / "dashboard"

    for d in [downloads_dir, raw_text_dir, data_dir, dashboard_dir]:
        d.mkdir(parents=True, exist_ok=True)

    scraper = MaricopaClerkScraper()
    results = scraper.scrape(start_api, end_api, document_code=args.document_code)

    logger.info("Scraped %s records", len(results))

    parsed = []

    for result in results:
        doc_num = str(result.get("doc_num", "")).strip()
        pdf_url = str(result.get("pdf_url", "")).strip()
        clerk_url = str(result.get("clerk_url", "")).strip()
        filed = str(result.get("filed", "")).strip()
        doc_type = str(result.get("doc_type", "NS")).strip() or "NS"

        if not doc_num:
            continue

        pdf_path = downloads_dir / f"{doc_num}.pdf"
        pdf_success = False

        # 🔽 TRY PDF DOWNLOAD (but do NOT fail pipeline)
        if pdf_url and not pdf_path.exists():
            pdf_success = download_pdf(pdf_url, pdf_path, logger)

        # 🔽 PARSE OR FALLBACK
        try:
            if pdf_success or pdf_path.exists():
                parsed_record = parse_record(
                    pdf_path=pdf_path,
                    clerk_url=clerk_url,
                    pdf_url=pdf_url,
                    filed=filed,
                    doc_num=doc_num,
                    doc_type=doc_type,
                    cat_label="Notice of Trustee Sale",
                    raw_text_dir=raw_text_dir,
                )
            else:
                raise Exception("No valid PDF")

        except Exception as exc:
            logger.warning("Fallback record used for %s: %s", doc_num, exc)

            parsed_record = {
                "doc_num": doc_num,
                "doc_type": doc_type,
                "filed": filed,
                "cat": "NS",
                "cat_label": "Notice of Trustee Sale",
                "owner": "",
                "grantee": "",
                "amount": "",
                "legal": "",
                "prop_address": "",
                "prop_city": "",
                "prop_state": "",
                "prop_zip": "",
                "mail_address": "",
                "mail_city": "",
                "mail_state": "",
                "mail_zip": "",
                "county": "Maricopa",
                "parcel_number": "",
                "original_loan": "",
                "trustee_name": "",
                "trustee_phone": "",
                "auction_date": "",
                "deed_of_trust": "",
                "first_name": "",
                "last_name": "",
                "second_first": "",
                "second_last": "",
                "clerk_url": clerk_url,
                "pdf_url": pdf_url,
                "pdf_path": "",
                "flags": ["no_pdf"],
                "score": 0,
                "raw_text_path": "",
            }

        parsed.append(parsed_record)

    payload = build_records_payload(
        parsed,
        source=DEFAULT_SOURCE,
        date_range={"start": start_mm, "end": end_mm},
    )

    write_json_records(payload, dashboard_dir / "records.json", data_dir / "records.json")
    write_ghl_csv(parsed, data_dir / "ghl_export.csv")
    write_flat_csv(parsed, ROOT / "nts_data.csv")
    write_xlsx(parsed, ROOT / "nts_data.xlsx")

    logger.info("FINAL: %s records | %s with address", payload["total"], payload["with_address"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
