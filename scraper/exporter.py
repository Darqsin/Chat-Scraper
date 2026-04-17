from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook

ILLEGAL_XLSX_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _clean_excel_value(value: Any) -> Any:
    if value is None:
        return ""

    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)

    value = str(value)
    value = ILLEGAL_XLSX_CHARS_RE.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\f", " ")
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _ensure_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    return [str(val)]


def _clean_record(record: dict) -> dict:
    cleaned: dict[str, Any] = {}
    for key, value in record.items():
        if key == "flags":
            cleaned[key] = _ensure_list(value)
        else:
            cleaned[key] = _clean_excel_value(value)
    return cleaned


def build_records_payload(records: list[dict], source: str, date_range: dict) -> dict:
    cleaned_records = [_clean_record(r) for r in records]
    with_address = sum(1 for r in cleaned_records if r.get("prop_address"))
    return {
        "fetched_at": datetime.utcnow().isoformat(),
        "source": source,
        "date_range": date_range,
        "total": len(cleaned_records),
        "with_address": with_address,
        "records": cleaned_records,
    }


def write_json_records(payload: dict, *paths: Path) -> None:
    clean_payload = {
        **payload,
        "records": [_clean_record(r) for r in payload.get("records", [])],
    }

    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(clean_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def write_flat_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [_clean_record(r) for r in records]

    fieldnames = [
        "doc_num",
        "doc_type",
        "filed",
        "cat",
        "cat_label",
        "owner",
        "grantee",
        "amount",
        "legal",
        "prop_address",
        "prop_city",
        "prop_state",
        "prop_zip",
        "mail_address",
        "mail_city",
        "mail_state",
        "mail_zip",
        "county",
        "parcel_number",
        "original_loan",
        "trustee_name",
        "trustee_phone",
        "auction_date",
        "deed_of_trust",
        "first_name",
        "last_name",
        "second_first",
        "second_last",
        "clerk_url",
        "pdf_url",
        "pdf_path",
        "flags",
        "score",
        "raw_text_path",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in cleaned:
            output = dict(row)
            output["flags"] = ", ".join(_ensure_list(output.get("flags")))
            writer.writerow(output)


def write_ghl_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [_clean_record(r) for r in records]

    fieldnames = [
        "Deed of Trust",
        "First Name",
        "Last Name",
        "2nd First",
        "2nd Last",
        "Street Address",
        "City",
        "State",
        "Postal Code",
        "Property Address",
        "Property City",
        "Property State",
        "Property Postal Code",
        "County",
        "Parcel Number",
        "Original Loan",
        "Estimated Value",
        "Equity",
        "Trustee Name",
        "Trustee Phone",
        "Auction Date",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in cleaned:
            writer.writerow(
                {
                    "Deed of Trust": r.get("deed_of_trust", ""),
                    "First Name": r.get("first_name", ""),
                    "Last Name": r.get("last_name", ""),
                    "2nd First": r.get("second_first", ""),
                    "2nd Last": r.get("second_last", ""),
                    "Street Address": r.get("mail_address", ""),
                    "City": r.get("mail_city", ""),
                    "State": r.get("mail_state", ""),
                    "Postal Code": r.get("mail_zip", ""),
                    "Property Address": r.get("prop_address", ""),
                    "Property City": r.get("prop_city", ""),
                    "Property State": r.get("prop_state", ""),
                    "Property Postal Code": r.get("prop_zip", ""),
                    "County": r.get("county", "Maricopa"),
                    "Parcel Number": r.get("parcel_number", ""),
                    "Original Loan": r.get("original_loan", r.get("amount", "")),
                    "Estimated Value": "",
                    "Equity": "",
                    "Trustee Name": r.get("trustee_name", r.get("grantee", "")),
                    "Trustee Phone": r.get("trustee_phone", ""),
                    "Auction Date": r.get("auction_date", ""),
                }
            )


def write_xlsx(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [_clean_record(r) for r in records]

    wb = Workbook()
    ws = wb.active
    ws.title = "NTS Leads"

    headers = [
        "doc_num",
        "doc_type",
        "filed",
        "cat",
        "cat_label",
        "owner",
        "grantee",
        "amount",
        "legal",
        "prop_address",
        "prop_city",
        "prop_state",
        "prop_zip",
        "mail_address",
        "mail_city",
        "mail_state",
        "mail_zip",
        "county",
        "parcel_number",
        "original_loan",
        "trustee_name",
        "trustee_phone",
        "auction_date",
        "deed_of_trust",
        "first_name",
        "last_name",
        "second_first",
        "second_last",
        "clerk_url",
        "pdf_url",
        "pdf_path",
        "flags",
        "score",
        "raw_text_path",
    ]

    ws.append(headers)

    for r in cleaned:
        row = []
        for header in headers:
            value = r.get(header, "")
            if isinstance(value, list):
                value = ", ".join(_ensure_list(value))
            row.append(_clean_excel_value(value))
        ws.append(row)

    for column in ws.columns:
        max_len = 0
        col_letter = column[0].column_letter
        for cell in column:
            try:
                length = len(str(cell.value)) if cell.value is not None else 0
                if length > max_len:
                    max_len = length
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 60)

    wb.save(path)
