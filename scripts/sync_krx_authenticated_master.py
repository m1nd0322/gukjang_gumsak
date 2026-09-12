"""Fetch KRX ETF metadata without persisting credentials or session cookies."""

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import requests


BASE = "https://data.krx.co.kr"
LOGIN = BASE + "/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
DATA = BASE + "/comm/bldAttendant/getJsonData.cmd"


def credentials(path):
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if len(lines) != 2:
        raise ValueError("Credential file must contain account and password on two lines")
    values = []
    for line in lines:
        match = re.match(r"^[\w \uac00-\ud7a3-]+\s*[:=]\s*(.+)$", line)
        values.append(match.group(1).strip() if match else line)
    if not all(values):
        raise ValueError("Empty credential field")
    return dict(zip(("mbrId", "pw"), values))


def fetch_master(account_file):
    with requests.Session() as session:
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + "/contents/MDC/COMS/client/view/login.jsp?site=mdc",
        })
        session.get(session.headers["Referer"], timeout=15).raise_for_status()
        params = credentials(account_file)
        # KRX allows only one session; the user authorized automatic re-login.
        params["skipDup"] = "Y"
        response = session.post(LOGIN, data=params, timeout=15, allow_redirects=False)
        response.raise_for_status()
        result = response.json()
        if result.get("_error_code") != "CD001" or not result.get("MBR_NO"):
            raise ValueError("KRX authentication unsuccessful; no automatic retry")
        session.headers["Referer"] = (
            BASE + "/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030101"
        )
        response = session.post(DATA, data={
            "bld": "dbms/MDC/STAT/standard/MDCSTAT04601",
            "locale": "ko_KR", "share": "1", "csvxls_isNo": "false",
        }, timeout=20, allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("output"), list) or not payload["output"]:
            raise ValueError("KRX returned no ETF metadata")
        return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-file", type=Path,
                        default=Path("/Users/songhear/APIs/krx_login_account.txt"))
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--master", type=Path,
                        default=Path("data/krx/etf_authenticated_master.json"))
    parser.add_argument("--inventory", type=Path,
                        default=Path("data/krx/etf_inventory_enriched.csv"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/krx/etf_inventory_authenticated.csv"))
    parser.add_argument("--report", type=Path,
                        default=Path("reports/krx_authenticated_metadata.json"))
    args = parser.parse_args()
    if not args.cached:
        payload = fetch_master(args.account_file)
        args.master.parent.mkdir(parents=True, exist_ok=True)
        args.master.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    payload = json.loads(args.master.read_text())
    master = {}
    for entry in payload["output"]:
        ticker = entry["ISU_SRT_CD"].strip()
        if ticker in master:
            raise ValueError("Duplicate ticker in KRX metadata")
        master[ticker] = entry
    digest = hashlib.sha256(args.master.read_bytes()).hexdigest()
    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not {"ticker", "listing_date"}.issubset(fields):
        raise ValueError("Inventory requires ticker and listing_date columns")
    for field in ("listing_date_source", "listing_date_artifact", "listing_date_sha256"):
        if field not in fields:
            fields.append(field)
    filled = 0
    conflicts = []
    unresolved = []
    for row in rows:
        entry = master.get(row["ticker"])
        if entry:
            listing_date = date.fromisoformat(entry["LIST_DD"].replace("/", "-")).isoformat()
            if row["listing_date"] and row["listing_date"] != listing_date:
                conflicts.append({"ticker": row["ticker"],
                                  "existing": row["listing_date"], "krx": listing_date})
            elif not row["listing_date"]:
                row["listing_date"] = listing_date
                row["listing_date_source"] = DATA + "?bld=dbms/MDC/STAT/standard/MDCSTAT04601"
                row["listing_date_artifact"] = str(args.master)
                row["listing_date_sha256"] = digest
                row["source"] = row["listing_date_source"]
                if "source" not in fields:
                    fields.append("source")
                filled += 1
        if not row["listing_date"]:
            unresolved.append(row["ticker"])
    if conflicts:
        raise ValueError("Listing-date conflicts found; refusing to overwrite inventory")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    report = {
        "status": "incomplete" if unresolved else "metadata_complete_only",
        "inventory_total": len(rows), "krx_master_rows": len(master),
        "listing_dates_added": filled,
        "listing_dates_available": len(rows) - len(unresolved),
        "listing_dates_missing": len(unresolved), "unresolved_tickers": unresolved,
        "master_sha256": digest, "output": str(args.output),
        "survivorship_bias_resolved": False,
        "note": "Current metadata is not historical selection or redemption evidence",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key != "unresolved_tickers"}))
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
