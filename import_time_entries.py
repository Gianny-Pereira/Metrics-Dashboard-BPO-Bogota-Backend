"""
Import TimeEntries.xlsx by sending it to the /api/import endpoint.

Usage:
    python import_time_entries.py                          # default file & URL
    python import_time_entries.py --file path/to/file.xlsx
    python import_time_entries.py --url http://localhost:5000
"""

import argparse
import sys
import requests

DEFAULT_FILE = "TimeEntries.xlsx"
DEFAULT_URL = "http://localhost:5000"


def main():
    parser = argparse.ArgumentParser(description="Import TimeEntries.xlsx into the BPO dashboard.")
    parser.add_argument("--file", default=DEFAULT_FILE, help="Path to the Excel file (default: TimeEntries.xlsx)")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL of the Flask API (default: http://localhost:5000)")
    args = parser.parse_args()

    endpoint = f"{args.url.rstrip('/')}/api/import"

    print(f"Importing {args.file!r} → {endpoint}")

    try:
        with open(args.file, "rb") as f:
            response = requests.post(endpoint, files={"file": (args.file, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)
    except requests.ConnectionError:
        print(f"ERROR: Could not connect to {endpoint}. Is the Flask server running?", file=sys.stderr)
        sys.exit(1)

    try:
        data = response.json()
    except Exception:
        print(f"ERROR: Unexpected response ({response.status_code}):\n{response.text}", file=sys.stderr)
        sys.exit(1)

    if "error" in data:
        print("Errror api" , data)
        print(f"ERROR: {data['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"Imported : {data.get('imported', 0)}")
    print(f"Skipped  : {data.get('skipped', 0)}")

    errors = data.get("errors", [])
    if errors:
        print(f"Warnings ({len(errors)}):")
        for err in errors:
            print(f"  - {err}")

    sys.exit(0 if response.status_code in (200, 207) else 1)


if __name__ == "__main__":
    main()
