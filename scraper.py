"""
EOA Flowmeter Platform — Meter Data Scraper
============================================
Paginates through the internal API and writes all readings for a given
device to a CSV file.

Usage:
    python scrape_meter.py

Requirements:
    pip install requests

Notes:
    - The Bearer token expires. If you get a 401, log in to the platform
      again, open DevTools → Network → copy the new Authorization header.
    - PAGE_SIZE=100 is a safe default; try 500 if the server allows it.
    - SLEEP_BETWEEN_PAGES adds a small delay to avoid hammering the server.
"""

import csv
import time
import sys
import requests
import os
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE_URL   = "https://collect.flowmeter.tech/prod-api/system/data/list"
DEVICE_ID  = "202405101899"          # change per meter
TOKEN      = os.getenv('JWT')

PAGE_SIZE            = 100           # rows per request
SLEEP_BETWEEN_PAGES  = 0.3          # seconds — be gentle on the server
OUTPUT_FILE          = f"meter_{DEVICE_ID}.csv"

# ── HEADERS ───────────────────────────────────────────────────────────────────

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept":        "application/json",
}

# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch_page(page_num: int) -> dict:
    params = {
        "pageNum":  page_num,
        "pageSize": PAGE_SIZE,
        "deviceId": DEVICE_ID,
    }
    resp = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=15)

    if resp.status_code == 401:
        print("ERROR: 401 Unauthorized — your token has expired.")
        print("Log in to the platform, copy a fresh Bearer token, and update TOKEN in this script.")
        sys.exit(1)

    resp.raise_for_status()
    return resp.json()


def scrape_all() -> list[dict]:
    print(f"Fetching page 1 to discover total record count …")
    first = fetch_page(1)

    rows_key  = "rows"
    total_key = "total"

    if rows_key not in first:
        print(f"Unexpected response structure. Keys found: {list(first.keys())}")
        print("Full response:", first)
        sys.exit(1)

    total = first.get(total_key, "?")
    print(f"  → {total} total records for device {DEVICE_ID}")

    all_rows = [r for r in first[rows_key] if "error" not in r]

    import math
    total_pages = math.ceil(total / PAGE_SIZE) if isinstance(total, int) else 9999

    consecutive_empty = 0
    for page in range(2, total_pages + 1):
        print(f"  Fetching page {page}/{total_pages} …", end="\r")
        data = fetch_page(page)
        batch = [r for r in data.get(rows_key, []) if "error" not in r]
        if not batch:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                print(f"\n  3 consecutive empty/error pages — done.")
                break
        else:
            consecutive_empty = 0
            all_rows.extend(batch)
        time.sleep(SLEEP_BETWEEN_PAGES)

    print(f"\nFetched {len(all_rows)} records total.")
    return all_rows


# ── WRITE CSV ─────────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: str):
    if not rows:
        print("No rows to write.")
        return

    # Collect all fieldnames across every row (some rows have extra fields)
    fieldnames = list(dict.fromkeys(k for row in rows for k in row.keys()))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved → {path}  ({len(rows)} rows, {len(fieldnames)} columns)")
    print(f"Columns: {', '.join(fieldnames)}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    rows = scrape_all()
    write_csv(rows, OUTPUT_FILE)