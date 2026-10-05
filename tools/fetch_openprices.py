#!/usr/bin/env python3
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Build a corpus of receipts per country from Open Prices (Open Food Facts).

The receipt photos of Open Prices are published under the CC-BY-SA 4.0
licence, the data under ODbL. Each receipt carries the shop's country, the
purchase date and sometimes the total entered by the contributor: they serve
as the expected answer to measure the reading, country by country.

The photos stay local: they may show names or card numbers. Never add them to
the repository.

Usage:

    python3 tools/fetch_openprices.py FOLDER --per-country 80 --countries FR DE BE

Produces ``FOLDER/<country>/<id>.<ext>`` and ``FOLDER/truth.jsonl`` (one line
per receipt: id, country, date, total, currency, image, contributor). A file
already there is not downloaded again.
"""
import argparse
import collections
import json
import os
import sys
import time
import urllib.request

API = "https://prices.openfoodfacts.org/api/v1/proofs?type=RECEIPT&size=100&page=%d"
IMAGES = "https://prices.openfoodfacts.org/img/"
USER_AGENT = "expense_scan-corpus/1.0 (https://github.com/TTC-Technologies/expense_scan)"


def get(url, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=120)


def proofs():
    """All the receipts of Open Prices, page by page."""
    page = 1
    while True:
        with get(API % page) as response:
            data = json.load(response)
        yield from data["items"]
        if page >= data.get("pages", 0):
            return
        page += 1
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder")
    parser.add_argument("--per-country", type=int, default=80)
    parser.add_argument("--countries", nargs="+", required=True)
    parser.add_argument("--max-mb", type=float, default=8.0)
    args = parser.parse_args()
    wanted = {code.upper() for code in args.countries}

    candidates = collections.defaultdict(list)
    for proof in proofs():
        country = (proof.get("location") or {}).get("osm_address_country_code")
        if country in wanted and proof.get("date") and proof.get("file_path"):
            candidates[country].append(proof)

    os.makedirs(args.folder, exist_ok=True)
    truth_path = os.path.join(args.folder, "truth.jsonl")
    known = set()
    if os.path.exists(truth_path):
        with open(truth_path, encoding="utf-8") as existing:
            known = {json.loads(line)["id"] for line in existing if line.strip()}

    kept = collections.Counter()
    size = 0
    with open(truth_path, "a", encoding="utf-8") as truth:
        for country in sorted(wanted):
            # Receipts with a known total first: they also measure the
            # reading of the amount.
            ranked = sorted(candidates[country],
                            key=lambda p: (p.get("receipt_price_total") is None, -p["id"]))
            folder = os.path.join(args.folder, country)
            os.makedirs(folder, exist_ok=True)
            for proof in ranked:
                if kept[country] >= args.per_country:
                    break
                if proof["id"] in known:
                    kept[country] += 1
                    continue
                url = IMAGES + proof["file_path"]
                try:
                    with get(url, method="HEAD") as head:
                        length = int(head.headers.get("Content-Length") or 0)
                    if length > args.max_mb * 1024 * 1024:
                        continue
                    extension = os.path.splitext(proof["file_path"])[1]
                    target = os.path.join(folder, "%d%s" % (proof["id"], extension))
                    with get(url) as response, open(target, "wb") as output:
                        output.write(response.read())
                except OSError as error:
                    print("%s %s: %s" % (country, proof["id"], error), file=sys.stderr)
                    continue
                size += length
                kept[country] += 1
                truth.write(json.dumps({
                    "id": proof["id"],
                    "country": country,
                    "date": proof.get("date"),
                    "total": proof.get("receipt_price_total"),
                    "currency": proof.get("currency"),
                    "file": os.path.relpath(target, args.folder).replace(os.sep, "/"),
                    "source": url,
                    "owner": proof.get("owner"),
                    "license": "CC-BY-SA-4.0",
                }, ensure_ascii=False) + "\n")
                truth.flush()
                time.sleep(0.1)
            print("%s: %d receipts" % (country, kept[country]), flush=True)
    print("downloaded: %.0f MB" % (size / 1024 / 1024))


if __name__ == "__main__":
    main()
