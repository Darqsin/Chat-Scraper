from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook


def _ensure_path(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _normalize_record(record: Any) -> dict[str, Any]:
    if is_dataclass(record):
        return asdict(record)
    if isinstance(record, dict):
        return record
    raise TypeError(f"Unsupported record type: {type(record)!r}")


def build_records_payload(records: list[Any], source: str, date_range: dict[str, str]) -> dict[str, Any]:
    normalized = [_normalize_record(r) for r in records]
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "date_range": date_range,
        "total": len(normalized),
        "with_address": sum(1 for r in normalized if r.get("prop_address")),
        "records": [
            {
                "doc_num": r.get("doc_num", ""),
                "doc_type": r.get("doc_type", ""),
                "filed": r.get("filed", ""),
                "cat": r.get("cat", ""),
                "cat_label": r.get("cat_label", ""),
                "owner": r.get("owner", ""),
                "grantee": r.get("grantee", ""),
                "amount": r.get("amount", ""),
                "legal": r.get("legal", ""),
                "prop_address": r.get("prop_address", ""),
                "prop_city": r.get("prop_city", ""),
                "prop_state": r.get("prop_state", ""),
                "prop_zip": r.get("prop_zip", ""),
                "mail_address": r.get("mail_address", ""),
                "mail_city": r.get("mail_city", ""),
                "mail_state": r.get("mail_state", ""),
                "mail_zip": r.get("mail_zip", ""),
                "clerk_url": r.get("clerk_url", ""),
                "flags": r.get("flags", []),
                "score": r.get("score", 0),
            }
            for r in normalized
        ],
    }


def write_json_records(payload: dict[str, Any], *paths: str | Path) -> None:
    for path in paths:
        p = _ensure_path(path)
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_ghl_csv(records: list[Any], path: str | Path) -> None:
    p = _ensure_path(path)
    normalized = [_normalize_record(r) for r in records]
    headers = [
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
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for r in normalized:
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
                    "Original Loan": r.get("original_loan", ""),
                    "Estimated Value": "",
                    "Equity": "",
                    "Trustee Name": r.get("trustee_name", ""),
                    "Trustee Phone": r.get("trustee_phone", ""),
                    "Auction Date": r.get("auction_date", ""),
                }
            )


def write_flat_csv(records: list[Any], path: str | Path) -> None:
    p = _ensure_path(path)
    normalized = [_normalize_record(r) for r in records]
    if not normalized:
        headers = [
            "doc_num", "doc_type", "filed", "cat", "cat_label", "owner", "grantee", "amount",
            "legal", "prop_address", "prop_city", "prop_state", "prop_zip", "mail_address", "mail_city",
            "mail_state", "mail_zip", "county", "parcel_number", "original_loan", "trustee_name",
            "trustee_phone", "auction_date", "deed_of_trust", "first_name", "last_name", "second_first",
            "second_last", "clerk_url", "pdf_url", "pdf_path", "flags", "score", "raw_text_path",
        ]
    else:
        headers = list(normalized[0].keys())

    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        for row in normalized:
            row = row.copy()
            if isinstance(row.get("flags"), list):
                row["flags"] = ";".join(row["flags"])
            writer.writerow(row)


def write_xlsx(records: list[Any], path: str | Path) -> None:
    p = _ensure_path(path)
    normalized = [_normalize_record(r) for r in records]
    wb = Workbook()
    ws = wb.active
    ws.title = "NTS Data"

    headers = [
        "Deed of Trust", "First Name", "Last Name", "2nd First", "2nd Last",
        "Street Address", "City", "State", "Postal Code", "Property Address",
        "Property City", "Property State", "Property Postal Code", "County",
        "Parcel Number", "Original Loan", "Trustee Name", "Trustee Phone", "Auction Date",
        "Doc Number", "Doc Type", "Filed", "Owner", "Legal", "Score", "Flags",
        "Clerk URL", "PDF URL", "PDF Path",
    ]
    ws.append(headers)

    for r in normalized:
        ws.append(
            [
                r.get("deed_of_trust", ""), r.get("first_name", ""), r.get("last_name", ""),
                r.get("second_first", ""), r.get("second_last", ""), r.get("mail_address", ""),
                r.get("mail_city", ""), r.get("mail_state", ""), r.get("mail_zip", ""),
                r.get("prop_address", ""), r.get("prop_city", ""), r.get("prop_state", ""),
                r.get("prop_zip", ""), r.get("county", "Maricopa"), r.get("parcel_number", ""),
                r.get("original_loan", ""), r.get("trustee_name", ""), r.get("trustee_phone", ""),
                r.get("auction_date", ""), r.get("doc_num", ""), r.get("doc_type", ""),
                r.get("filed", ""), r.get("owner", ""), r.get("legal", ""), r.get("score", 0),
                ";".join(r.get("flags", [])) if isinstance(r.get("flags"), list) else r.get("flags", ""),
                r.get("clerk_url", ""), r.get("pdf_url", ""), r.get("pdf_path", ""),
            ]
        )
    wb.save(p)
