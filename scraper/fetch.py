from __future__ import annotations

import argparse
import logging
import time
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

PDF_SESSION = requests.Session()
PDF_SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
)

PDF_TIMEOUT = 60
PDF_RETRIES = 3
PDF_RETRY_STATUS = {403, 408, 429, 500, 502, 503, 504}


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


def build_pdf_candidates(url: str, doc_num: str) -> list[str]:
    candidates: list[str] = []
    if url:
        candidates.append(url)

    if doc_num:
        candidates.extend(
            [
                f"https://legacy.recorder.maricopa.gov/UnOfficialDocs/pdf/{doc_num}.pdf",
                f"https://legacy.recorder.maricopa.gov/UnOfficialDocs/PDF/{doc_num}.pdf",
                f"https://recorder.maricopa.gov/UnOfficialDocs/pdf/{doc_num}.pdf",
                f"https://recorder.maricopa.gov/UnOfficialDocs/PDF/{doc_num}.pdf",
            ]
        )

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def is_pdf_response(content: bytes, content_type: str) -> bool:
    if not content:
        return False
    header = content[:32].lstrip()
    if header.startswith(b"%PDF"):
        return True
    return "pdf" in (content_type or "").lower() and len(content) > 1000


def fetch_candidate(candidate: str, clerk_url: str, logger: logging.Logger, doc_num: str) -> tuple[bool, bytes, str, int]:
    headers = {
        "Referer": clerk_url or DEFAULT_SOURCE,
        "Accept": "application/pdf,application/octet-stream,*/*",
    }

    last_status = 0
    last_content_type = ""

    for attempt in range(1, PDF_RETRIES + 1):
        try:
            resp = PDF_SESSION.get(
                candidate,
                headers=headers,
                timeout=PDF_TIMEOUT,
                allow_redirects=True,
            )
            last_status = resp.status_code
            last_content_type = resp.headers.get("Content-Type", "")
            content = resp.content or b""

            if resp.ok and is_pdf_response(content, last_content_type):
                return True, content, last_content_type, resp.status_code

            logger.warning(
                "Bad PDF response | doc=%s | attempt=%s/%s | status=%s | type=%s | url=%s",
                doc_num,
                attempt,
                PDF_RETRIES,
                resp.status_code,
                last_content_type or "n/a",
                candidate,
            )

            if resp.status_code not in PDF_RETRY_STATUS:
                break

        except Exception as exc:
            logger.warning(
                "PDF request failed | doc=%s | attempt=%s/%s | url=%s | err=%s",
                doc_num,
                attempt,
                PDF_RETRIES,
                candidate,
                exc,
            )

        if attempt < PDF_RETRIES:
            time.sleep(1.5 * attempt)

    return False, b"", last_content_type, last_status


def download_pdf(
    url: str,
    save_path: Path,
    logger: logging.Logger,
    doc_num: str = "",
    clerk_url: str = "",
) -> tuple[bool, str]:
    candidates = build_pdf_candidates(url, doc_num)
    if not candidates:
        return False, "pdf_missing"

    save_path.parent.mkdir(parents=True, exist_ok=True)

    saw_block = False
    saw_non_pdf = False

    for candidate in candidates:
        ok, content, content_type, status = fetch_candidate(candidate, clerk_url, logger, doc_num)
        if ok:
            save_path.write_bytes(content)
            logger.info("PDF downloaded | doc=%s | url=%s", doc_num, candidate)
            return True, ""

        if status in {403, 429}:
            saw_block = True
        if content_type and "pdf" not in content_type.lower():
            saw_non_pdf = True

    if saw_block:
        return False, "pdf_blocked"
    if saw_non_pdf:
        return False, "pdf_non_pdf_response"
    return False, "pdf_missing"


def fallback_record(doc_num: str, doc_type: str, filed: str, clerk_url: str, pdf_url: str, flags: list[str]) -> dict:
    return {
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
        "flags": flags,
        "score": 0,
        "raw_text_path": "",
    }


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

    for directory in [downloads_dir, raw_text_dir, data_dir, dashboard_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    scraper = MaricopaClerkScraper()
    results = scraper.scrape(start_api, end_api, document_code=args.document_code)

    logger.info("Scraped %s records", len(results))

    parsed: list[dict] = []
    download_success = 0
    download_fail = 0

    for idx, result in enumerate(results, start=1):
        doc_num = str(result.get("doc_num", "")).strip()
        pdf_url = str(result.get("pdf_url", "")).strip()
        clerk_url = str(result.get("clerk_url", "")).strip()
        filed = str(result.get("filed", "")).strip()
        doc_type = str(result.get("doc_type", "NS")).strip() or "NS"

        if not doc_num:
            logger.warning("Skipping record %s: missing doc_num", idx)
            continue

        pdf_path = downloads_dir / f"{doc_num}.pdf"
        pdf_success = False
        pdf_fail_reason = ""

        if pdf_path.exists() and pdf_path.stat().st_size > 1000:
            pdf_success = True
        else:
            pdf_success, pdf_fail_reason = download_pdf(
                pdf_url,
                pdf_path,
                logger,
                doc_num=doc_num,
                clerk_url=clerk_url,
            )

        if pdf_success:
            download_success += 1
        else:
            download_fail += 1

        try:
            if pdf_success or (pdf_path.exists() and pdf_path.stat().st_size > 1000):
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
                raise RuntimeError(pdf_fail_reason or "No valid PDF")

        except Exception as exc:
            logger.warning("Fallback record used for %s: %s", doc_num, exc)
            parsed_record = fallback_record(
                doc_num=doc_num,
                doc_type=doc_type,
                filed=filed,
                clerk_url=clerk_url,
                pdf_url=pdf_url,
                flags=[pdf_fail_reason or "no_pdf"],
            )

        parsed.append(parsed_record)

        if idx % 25 == 0:
            logger.info(
                "Progress: %s/%s | pdf_ok=%s | pdf_fail=%s",
                idx,
                len(results),
                download_success,
                download_fail,
            )

    payload = build_records_payload(
        parsed,
        source=DEFAULT_SOURCE,
        date_range={"start": start_mm, "end": end_mm},
    )

    write_json_records(payload, dashboard_dir / "records.json", data_dir / "records.json")
    write_ghl_csv(parsed, data_dir / "ghl_export.csv")
    write_flat_csv(parsed, ROOT / "nts_data.csv")
    write_xlsx(parsed, ROOT / "nts_data.xlsx")

    logger.info(
        "FINAL: %s records | %s with address | pdf_ok=%s | pdf_fail=%s",
        payload["total"],
        payload.get("with_address", 0),
        download_success,
        download_fail,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
