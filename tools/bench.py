#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Benchmark: replays the parsing on text already read, without Odoo.

A full run through Odoo takes 35 to 60 minutes for 700 receipts, almost all
of it OCR. Most changes are about the parsing (dates, totals, VAT, merchant,
category), not the reading. The benchmark only replays the parsing, on a
snapshot of the words read: a few seconds, with a file by file report of
what changed.

Neither the receipts, nor the snapshot, nor the truth tables are versioned
(personal data). The repository only holds the tool.

    # 1. produce the snapshot (to redo when the OCR reading changes):
    #    put the receipts in /tmp/expense_scan_corpus, create
    #    /tmp/expense_scan_snapshot, run the module's tests;
    # 2. measure, and keep the measure as the reference:
    python3 tools/bench.py SNAPSHOT --truth TRUTH --save before.json
    # 3. after a change, compare:
    python3 tools/bench.py SNAPSHOT --truth TRUTH --baseline before.json
    # Open Prices receipts (tools/fetch_openprices.py), named
    # "OP-<country>_<id>.<ext>" in the corpus: results per country
    python3 tools/bench.py SNAPSHOT --open-prices truth.jsonl

The benchmark judges what the parser reads (``tax``: the VAT read, before the
application's rules such as a category without VAT or a foreign currency, so
stricter than what Odoo writes); its measures compare with each other, not
with the log.

The truth is the table of an expense report, one line per expense
(``report01.txt`` for the receipts ``report01_p003.jpg``). Without it, the
benchmark only says whether a result changed, not whether it is right.

Exit code: 1 if a right result became wrong, 0 otherwise.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr import categorize, lexicon, parser  # noqa: E402
from ocr.types import OcrWord  # noqa: E402

# ---------------------------------------------------------------------------
# Truth
# ---------------------------------------------------------------------------

NUM = r"-?\d{1,3}(?:[  ]\d{3})*,\d\d"
ROW = re.compile(r"^\s*(\d\d/\d\d/\d{4})\s+(%s)\s+((?:%s\s+)*)(\S.*?)\s+(%s) ([A-Z]{3})\b"
                 % (NUM, NUM, NUM))
LOOSE_TOTAL = re.compile(r"(%s) ([A-Z]{3})\b" % NUM)

#: Expense type of the table -> code(s) of the matching category.
TYPE_MAP = {
    "HOTEL": ("HEBERGEMENT",), "RESTAURANT": ("REPAS",), "PARKING": ("PARK",),
    "PEAGE": ("PARK",), "TAXI_TRANSPORT": ("MOB_URB", "TRANSPORT"), "TRAIN": ("TRANSPORT",),
    "AVION": ("TRANSPORT",), "TRANSPORT": ("TRANSPORT", "MOB_URB"),
    "LOCATION_VEHICULE": ("LOC",), "LOCATION": ("LOC",), "CARBURANT": ("ENERGIE",),
    "TELEPHONE": ("COMM",), "INVITATION": ("INVITATION",), "REPAS_MIDI": ("REPAS",),
    "PANIER_REPAS": ("REPAS",), "TRAIN_PLANE": ("TRANSPORT",),
    "LOCATION_DE_VEHICULE": ("LOC",),
}


def num(text):
    return float(text.replace(" ", "").replace(" ", "").replace(",", "."))


def expected_codes(kind):
    folded = kind.upper()
    return next((codes for key, codes in TYPE_MAP.items() if folded.startswith(key)), None)


def load_truth(directory):
    """``{report: (rows, loose amounts)}`` from the tables of the report."""
    truth = {}
    for path in glob.glob(os.path.join(directory, "*.txt")):
        rows, loose = [], []
        with open(path, encoding="latin-1") as handle:
            for line in handle:
                match = ROW.match(line)
                if match:
                    taxes = [num(t) for t in re.findall(NUM, match.group(3))]
                    rows.append(dict(
                        date="-".join(reversed(match.group(1).split("/"))),
                        tax=round(sum(taxes), 2), ttc=num(match.group(5)),
                        type=re.sub(r"\s+%s$" % NUM, "", match.group(4)).strip(),
                        currency=match.group(6)))
                elif not line.lstrip().startswith("Total"):
                    loose += [num(m.group(1)) for m in LOOSE_TOTAL.finditer(line)]
        truth[os.path.basename(path)[:-4]] = (rows, loose)
    return truth


#: A date, in one of the receipt forms: "20.08.2026", "06/26/26",
#: "2026-08-20", "29 januari 2026". Used to recognise a receipt whose photo
#: shows no date (often cut off by the contributor).
DATE_NUMERIC = re.compile(r"\b\d{1,4}\s?[./-]\s?\d{1,2}\s?[./-]\s?\d{2,4}")
DATE_WORDS = re.compile(r"\b\d{1,2}\.?\s+([A-Za-zÀ-ÿ]{3,10})\.?\s+\d{2,4}\b")


def has_date(text):
    """True if the text holds a date; "1,00 Kom 39" is not one."""
    if DATE_NUMERIC.search(text):
        return True
    return any(parser._month_number(match.group(1))
               for match in DATE_WORDS.finditer(text))

#: Prefix of the receipts taken from Open Prices in the snapshot:
#: "OP-DE_12345.webp" (country, receipt id).
OPEN_PRICES_PREFIX = "OP-"


def load_open_prices(path):
    """``{"OP-<country>_<id>": row}`` from ``truth.jsonl`` (tools/fetch_openprices.py)."""
    truth = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                truth["%s%s_%d" % (OPEN_PRICES_PREFIX, row["country"], row["id"])] = row
    return truth


def judge_open_prices(name, got, truth):
    """Date, currency and, when known, total of an Open Prices receipt."""
    row = truth.get(os.path.splitext(name)[0])
    if row is None:
        return {}
    verdict = {"currency": (got["currency"] or "EUR") == (row["currency"] or "EUR")}
    # Without any date on the photo, the date entered by the contributor
    # cannot be read: the receipt is not counted.
    if got.get("date_visible", True):
        # The Open Prices date is entered by the contributor, often on the
        # day of the upload: two days apart are accepted.
        verdict["date"] = bool(got["date"]) and abs(
            (date.fromisoformat(got["date"]) - date.fromisoformat(row["date"])).days) <= 2
    if row.get("total"):
        verdict["total"] = abs((got["total"] or 0) - row["total"]) < 0.015
    return verdict


def print_by_country(verdicts):
    """Success rate per country, for the Open Prices receipts."""
    by_country = collections.defaultdict(collections.Counter)
    for name, verdict in verdicts.items():
        if not name.startswith(OPEN_PRICES_PREFIX):
            continue
        country = name[len(OPEN_PRICES_PREFIX):].split("_")[0]
        by_country[country]["receipts"] += 1
        by_country[country]["no date"] += "date" not in verdict
        for field, ok in verdict.items():
            by_country[country][field, "n"] += 1
            by_country[country][field, "ok"] += ok
    if not by_country:
        return
    print("\nOpen Prices, per country:")
    print("  ctry receipts     date          total         currency  no visible date")
    for country in sorted(by_country, key=lambda c: -by_country[c]["receipts"]):
        counts = by_country[country]
        cells = []
        for field in ("date", "total", "currency"):
            n, ok = counts[field, "n"], counts[field, "ok"]
            cells.append("%3d/%-3d %3.0f %%" % (ok, n, 100.0 * ok / n) if n else "      -     ")
        print("  %-4s %8d  %s   %5d" % (country, counts["receipts"], "   ".join(cells),
                                        counts["no date"]))


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def load_snapshot(directory):
    """``(meta, {name: snapshot})``."""
    with open(os.path.join(directory, "_meta.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    items = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        if os.path.basename(path) == "_meta.json":
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        items[data["name"]] = data
    return meta, items


def replay(item, meta):
    """What the parsing gets from a receipt: fields read and category."""
    words = [OcrWord(text=w[0], score=w[1], left=w[2], top=w[3], right=w[4], bottom=w[5],
                     angle=w[6]) for w in item["words"]]
    result = parser.parse(
        words, today=date.fromisoformat(item["today"]),
        max_age_days=item["max_age_days"] or 730,
        default_currency=item["default_currency"] or "EUR", buyers=item["buyers"],
        country=item.get("country"))
    lines = [line.text for line in result.lines]
    scores, _reasons, _brand = categorize.score(
        lines, meta["keyword_categories"], meta["family_keys"],
        activity=result.value("activity"), naf_of=lambda: item.get("naf"))
    picked = lexicon.pick_category(scores)
    when = result.value("date")
    return {
        "total": result.value("total"), "tax": result.value("tax_amount"),
        "rate": result.value("tax_rate"), "date": when.isoformat() if when else None,
        "time": str(result.value("time")) if result.value("time") else None,
        "currency": result.value("currency"), "merchant": result.value("merchant"),
        "category": picked,
        "date_visible": has_date("\n".join(lines)),
    }


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

def judge(name, got, truth):
    """What is right in ``got``, according to the truth; ``{}`` without truth."""
    rows, loose = truth.get(name.split("_")[0], (None, None))
    if rows is None:
        return {}
    total = got["total"] or 0
    ttcs = [r["ttc"] for r in rows] + loose
    matching = [r for r in rows if abs(r["ttc"] - total) < 0.015]
    row = next((r for r in matching if r["date"] == got["date"]), matching[0] if matching else None)
    verdict = {
        "total": any(abs(t - total) < 0.015 for t in ttcs),
        "date": any(r["date"] == got["date"] for r in rows),
    }
    if row:
        verdict["tax"] = abs(row["tax"] - (got["tax"] or 0)) < 0.025
        codes = expected_codes(row["type"])
        if codes:
            verdict["category"] = got["category"] in codes
    return verdict


def recall(results, truth):
    """Check that each expense of the table with a receipt has an image with the right total."""
    found = collections.Counter()
    lost = []
    for note, (rows, _loose) in sorted(truth.items()):
        read = [got for name, got in results.items() if name.split("_")[0] == note]
        for row in rows:
            if not expected_codes(row["type"]) or row["ttc"] <= 0:
                continue  # flat rates, allowances: no receipt
            found["rows"] += 1
            hit = any(abs((got["total"] or 0) - row["ttc"]) < 0.015 for got in read)
            found["found"] += hit
            if not hit:
                lost.append("%s %s %s %.2f %s" % (
                    note, row["date"], row["type"], row["ttc"], row["currency"]))
    return found, lost


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

FIELDS = ("total", "date", "tax", "category")
LABELS = {"total": "total", "date": "date", "tax": "VAT", "category": "category"}


def summarize(verdicts):
    counts = collections.Counter()
    for verdict in verdicts.values():
        for field, ok in verdict.items():
            counts[field, "n"] += 1
            counts[field, "ok"] += ok
    return counts


def print_summary(results, verdicts, truth, seconds):
    counts = summarize(verdicts)
    print("files replayed: %d in %.1f s" % (len(results), seconds))
    if not truth:
        return
    for field in FIELDS:
        n, ok = counts[field, "n"], counts[field, "ok"]
        print("  %-10s %4d / %-4d %5.1f %%" % (LABELS[field], ok, n, 100.0 * ok / max(n, 1)))
    found, _lost = recall(results, truth)
    print("  expenses with a receipt found: %d / %d (%.1f %%)" % (
        found["found"], found["rows"], 100.0 * found["found"] / max(found["rows"], 1)))


def compare(baseline, results, verdicts):
    """Differences with the reference measure, file by file."""
    regressions, gains, changes = [], [], []
    for name, got in sorted(results.items()):
        before = baseline.get(name)
        if not before:
            continue
        for field in FIELDS + ("merchant", "currency"):
            old, new = before["got"].get(field), got.get(field)
            if old == new:
                continue
            was = before["verdict"].get(field)
            now = verdicts.get(name, {}).get(field)
            line = "%-24s %-9s %r -> %r" % (name, LABELS.get(field, field), old, new)
            if was and now is False:
                regressions.append(line)
            elif was is False and now:
                gains.append(line)
            else:
                changes.append(line)
    return regressions, gains, changes


def print_comparison(baseline, results, verdicts):
    regressions, gains, changes = compare(baseline, results, verdicts)
    for title, lines in (("REGRESSIONS (right -> wrong)", regressions), ("gains (wrong -> right)", gains),
                         ("other differences", changes)):
        print("\n%s: %d" % (title, len(lines)))
        for line in lines[:60]:
            print("  " + line)
        if len(lines) > 60:
            print("  ... and %d more" % (len(lines) - 60))
    return len(regressions)


def print_fidelity(items, results):
    """Compare the replay with what Odoo had written; a gap points to a stale snapshot."""
    differ = collections.Counter()
    for name, item in items.items():
        odoo, got = item.get("odoo") or {}, results[name]
        if odoo.get("total") is not None and abs((got["total"] or 0) - odoo["total"]) > 0.005:
            differ["total"] += 1
        # Without a date read, Odoo sets today's: it is not a gap.
        if got["date"] and odoo.get("date") and odoo["date"] != got["date"]:
            differ["date"] += 1
        if odoo.get("category") and got["category"] and odoo["category"] != got["category"]:
            differ["category"] += 1
    print("\ngaps with Odoo (merchant history, retouches included): %s" % (
        dict(differ) or "none"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argp = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    argp.add_argument("snapshot", help="snapshot folder (words read)")
    argp.add_argument("--truth", help="folder of the truth tables")
    argp.add_argument("--open-prices", help="truth.jsonl of the Open Prices receipts")
    argp.add_argument("--save", help="write the measure to this file")
    argp.add_argument("--baseline", help="reference measure to compare with")
    argp.add_argument("--only", help="only replay the files whose name contains this text")
    args = argp.parse_args()

    meta, items = load_snapshot(args.snapshot)
    if args.only:
        items = {name: item for name, item in items.items() if args.only in name}
    truth = load_truth(args.truth) if args.truth else {}

    started = time.time()
    results = {name: replay(item, meta) for name, item in items.items()}
    seconds = time.time() - started
    verdicts = {name: judge(name, got, truth) for name, got in results.items()}
    if args.open_prices:
        open_prices = load_open_prices(args.open_prices)
        for name, got in results.items():
            if name.startswith(OPEN_PRICES_PREFIX):
                verdicts[name] = judge_open_prices(name, got, open_prices)

    print_summary(results, verdicts, truth, seconds)
    print_by_country(verdicts)
    print_fidelity(items, results)
    regressions = 0
    if args.baseline:
        with open(args.baseline, encoding="utf-8") as handle:
            regressions = print_comparison(json.load(handle), results, verdicts)
    if args.save:
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump({name: {"got": got, "verdict": verdicts[name]}
                       for name, got in results.items()}, handle, ensure_ascii=False)
        print("\nmeasure saved: %s" % args.save)
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
