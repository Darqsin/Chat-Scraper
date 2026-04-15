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
    parser.add_argument("--lookback-days", type=int, default=1, help="Days back when start/end not supplied.")
    parser.add_argument("--document-code", default="NS", help="Document code to search. Default: NS")
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


def mmddyyyy_to_yyyymmdd(value: str) -> str:
    return datetime.strptime(value, "%m/%d/%Y").strftime("%Y-%m-%d")


def download_pdf(url: str, save_path: Path, logger: logging.Logger) -> bool:
    try:
        resp = requests.get(url, timeout=45)
        content_type = (resp.headers.get("content-type") or "").lower()

        if resp.ok and resp.content and ("pdf" in content_type or resp.content[:4] == b"%PDF"):
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
            return True

        logger.warning("Bad PDF response for %s | status=%s | content-type=%s", url, resp.status_code, content_type)
    except Exception as exc:
        logger.warning("PDF download failed for %s: %s", url, exc)

    return False


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger("fetch")

    start_date_mmddyyyy, end_date_mmddyyyy = resolve_date_range(
        args.start_date, args.end_date, args.lookback_days
    )

    logger.info("Resolved date range: %s -> %s", start_date_mmddyyyy, end_date_mmddyyyy)

    # API scraper needs YYYY-MM-DD
    start_date_api = mmddyyyy_to_yyyymmdd(start_date_mmddyyyy)
    end_date_api = mmddyyyy_to_yyyymmdd(end_date_mmddyyyy)

    logger.info("API date range: %s -> %s", start_date_api, end_date_api)

    downloads_dir = ROOT / "grouped_output" / "pdfs"
    raw_text_dir = ROOT / "parsed_output"
    data_dir = ROOT / "data"
    dashboard_dir = ROOT / "dashboard"

    downloads_dir.mkdir(parents=True, exist_ok=True)
    raw_text_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    # keep Git happy even on zero-result days
    keep_file = downloads_dir / ".keep"
    if not keep_file.exists():
        keep_file.write_text("", encoding="utf-8")

    scraper = MaricopaClerkScraper()
    results = scraper.scrape(start_date_api, end_date_api, document_code=args.document_code)

    logger.info("Scrape returned %s documents.", len(results))

    parsed = []
    for result in results:
        doc_num = str(result.get("doc_num", "")).strip()
        pdf_url = str(result.get("pdf_url", "")).strip()
        clerk_url = str(result.get("clerk_url", "")).strip()
        filed = str(result.get("filed", "")).strip()
        doc_type = str(result.get("doc_type", "NS")).strip() or "NS"

        if not doc_num or not pdf_url:
            logger.warning("Skipping incomplete result: %s", result)
            continue

        pdf_path = downloads_dir / f"{doc_num}.pdf"

        if not pdf_path.exists():
            ok = download_pdf(pdf_url, pdf_path, logger)
            if not ok:
                fallback = str(result.get("pdf_fallback", "")).strip()
                if fallback:
                    logger.warning("Primary PDF failed for %s, fallback exists but is not used as PDF: %s", doc_num, fallback)
                logger.warning("Skipping document %s because PDF was not downloaded.", doc_num)
                continue

        logger.info("Parsing PDF for document %s", doc_num)

        try:
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
            parsed.append(parsed_record)
        except Exception as exc:
            logger.exception("Failed parsing %s: %s", doc_num, exc)

    payload = build_records_payload(
        parsed,
        source=DEFAULT_SOURCE,
        date_range={"start": start_date_mmddyyyy, "end": end_date_mmddyyyy},
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
