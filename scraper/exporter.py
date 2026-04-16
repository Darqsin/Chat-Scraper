from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook

ILLEGAL_XLSX_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _clean_excel_value(value):
    if value is None:
        return ""

    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)

    value = str(value)
    value = ILLEGAL_XLSX_CHARS_RE.sub("", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _ensure_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [str(val)]


def _clean_record(record: dict) -> dict:
    cleaned = {}
    for k, v in record.items():
        if k == "flags":
            cleaned[k] = _ensure_list(v)
        else:
            cleaned[k] = _clean_excel_value(v)
    return cleaned


def build_records_payload(records: list[dict], source: str, date_range: dict) -> dict:
    cleaned_records = [_clean_record(r) for r in records]
    with_address = sum(1 for r in cleaned_records if r.get("prop_address"))
    return {
        "fetched_at": __import__("datetime").datetime.utcnow().isoformat(),
        "source": source,
        "date_range": date_range,
        "total": len(cleaned_records),
        "with_address": with_address,
        "records": cleaned_records,
    }


def write_json_records(payload: dict, *paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        clean_payload = {
            **payload,
            "records": [_clean_record(r) for r in payload.get("records", [])],
        }
        path.write_text(json.dumps(clean_payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_flat_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [_clean_record(r) for r in records]

    fieldnames = list(cleaned[0].keys()) if cleaned else []

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in cleaned:
            writer.writerow(row)


def write_xlsx(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [_clean_record(r) for r in records]

    wb = Workbook()
    ws = wb.active
    ws.title = "NTS Leads"

    if not cleaned:
        wb.save(path)
        return

    headers = list(cleaned[0].keys())
    ws.append(headers)

    for r in cleaned:
        row = []
        for h in headers:
            val = r.get(h, "")
            if isinstance(val, list):
                val = ", ".join(val)
            row.append(_clean_excel_value(val))
        ws.append(row)

    wb.save(path)
