#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Banc d'évaluation : rejoue l'analyse sur des textes déjà lus, sans Odoo.

Un passage complet par Odoo prend de 35 à 60 minutes pour 700 justificatifs,
presque tout en OCR. La plupart des changements portent sur l'analyse (dates,
totaux, TVA, enseigne, catégorie) et non sur la lecture. Le banc rejoue
uniquement l'analyse, sur un instantané des mots lus : quelques secondes,
avec un compte rendu fichier par fichier de ce qui a changé.

Ni les justificatifs, ni l'instantané, ni les tableaux de vérité ne sont
versionnés (données personnelles). Le dépôt ne contient que l'outil.

    # 1. produire l'instantané (à refaire quand la lecture OCR change) :
    #    déposer les justificatifs dans /tmp/expense_scan_corpus, créer
    #    /tmp/expense_scan_snapshot, lancer les tests du module ;
    # 2. mesurer, et garder la mesure comme référence :
    python3 tools/bench.py SNAPSHOT --truth VERITE --save avant.json
    # 3. après un changement, comparer :
    python3 tools/bench.py SNAPSHOT --truth VERITE --baseline avant.json
    # tickets d'Open Prices (tools/fetch_openprices.py), nommés
    # « OP-<pays>_<id>.<ext> » dans le corpus : résultats par pays
    python3 tools/bench.py SNAPSHOT --open-prices truth.jsonl

Le banc juge ce que lit l'analyseur (``tax`` : la TVA lue, avant les règles
de l'application comme catégorie sans TVA ou devise étrangère, donc plus
sévère que ce qu'Odoo écrit) ; ses mesures se comparent entre elles, pas au
journal.

La vérité est le tableau d'une note de frais, une ligne par dépense
(``vsa01.txt`` pour les justificatifs ``vsa01_p003.jpg``). Sans elle, le
banc indique seulement si un résultat a changé, pas s'il est juste.

Code de sortie : 1 si un résultat juste est devenu faux, 0 sinon.
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
# Vérité
# ---------------------------------------------------------------------------

NUM = r"-?\d{1,3}(?:[  ]\d{3})*,\d\d"
ROW = re.compile(r"^\s*(\d\d/\d\d/\d{4})\s+(%s)\s+((?:%s\s+)*)(\S.*?)\s+(%s) ([A-Z]{3})\b"
                 % (NUM, NUM, NUM))
LOOSE_TOTAL = re.compile(r"(%s) ([A-Z]{3})\b" % NUM)

#: Type de frais du tableau -> code(s) de la catégorie correspondante.
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
    """``{note: (lignes, montants isolés)}`` d'après les tableaux de la note."""
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


#: Une date, sous l'une des formes des tickets : « 20.08.2026 », « 06/26/26 »,
#: « 2026-08-20 », « 29 januari 2026 ». Sert à reconnaître un ticket dont la
#: photo ne montre aucune date (souvent coupée par le contributeur).
DATE_LIKE = re.compile(
    r"\b\d{1,4}\s?[./-]\s?\d{1,2}\s?[./-]\s?\d{2,4}\b"
    r"|\b\d{1,2}\.?\s+[A-Za-zÀ-ÿ]{3,10}\.?\s+\d{2,4}\b")

#: Préfixe des justificatifs tirés d'Open Prices dans l'instantané :
#: « OP-DE_12345.webp » (pays, identifiant du ticket).
OPEN_PRICES_PREFIX = "OP-"


def load_open_prices(path):
    """``{"OP-<pays>_<id>": ligne}`` d'après ``truth.jsonl`` (tools/fetch_openprices.py)."""
    truth = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                truth["%s%s_%d" % (OPEN_PRICES_PREFIX, row["country"], row["id"])] = row
    return truth


def judge_open_prices(name, got, truth):
    """Date, devise et, s'il est connu, total d'un ticket d'Open Prices."""
    row = truth.get(os.path.splitext(name)[0])
    if row is None:
        return {}
    verdict = {"currency": (got["currency"] or "EUR") == (row["currency"] or "EUR")}
    # Sans aucune date sur la photo, la date saisie par le contributeur ne
    # peut pas être lue : le ticket n'est pas compté.
    if got.get("date_visible", True):
        # La date d'Open Prices est saisie par le contributeur, souvent le
        # jour de l'envoi : deux jours d'écart sont admis.
        verdict["date"] = bool(got["date"]) and abs(
            (date.fromisoformat(got["date"]) - date.fromisoformat(row["date"])).days) <= 2
    if row.get("total"):
        verdict["total"] = abs((got["total"] or 0) - row["total"]) < 0.015
    return verdict


def print_by_country(verdicts):
    """Taux de réussite par pays, pour les tickets d'Open Prices."""
    by_country = collections.defaultdict(collections.Counter)
    for name, verdict in verdicts.items():
        if not name.startswith(OPEN_PRICES_PREFIX):
            continue
        country = name[len(OPEN_PRICES_PREFIX):].split("_")[0]
        by_country[country]["tickets"] += 1
        by_country[country]["sans date"] += "date" not in verdict
        for field, ok in verdict.items():
            by_country[country][field, "n"] += 1
            by_country[country][field, "ok"] += ok
    if not by_country:
        return
    print("\nOpen Prices, par pays :")
    print("  pays tickets      date          total         devise    sans date visible")
    for country in sorted(by_country, key=lambda c: -by_country[c]["tickets"]):
        counts = by_country[country]
        cells = []
        for field in ("date", "total", "currency"):
            n, ok = counts[field, "n"], counts[field, "ok"]
            cells.append("%3d/%-3d %3.0f %%" % (ok, n, 100.0 * ok / n) if n else "      -     ")
        print("  %-4s %7d   %s   %5d" % (country, counts["tickets"], "   ".join(cells),
                                         counts["sans date"]))


# ---------------------------------------------------------------------------
# Rejeu
# ---------------------------------------------------------------------------

def load_snapshot(directory):
    """``(méta, {nom: instantané})``."""
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
    """Ce que l'analyse tire d'un justificatif : champs lus et catégorie."""
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
        "date_visible": bool(DATE_LIKE.search("\n".join(lines))),
    }


# ---------------------------------------------------------------------------
# Jugement
# ---------------------------------------------------------------------------

def judge(name, got, truth):
    """Ce qui est juste dans ``got``, d'après la vérité ; ``{}`` sans vérité."""
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
    """Vérifie que chaque dépense à justificatif du tableau a une image au bon total."""
    found = collections.Counter()
    lost = []
    for note, (rows, _loose) in sorted(truth.items()):
        read = [got for name, got in results.items() if name.split("_")[0] == note]
        for row in rows:
            if not expected_codes(row["type"]) or row["ttc"] <= 0:
                continue  # forfaits, indemnités : pas de justificatif
            found["lignes"] += 1
            hit = any(abs((got["total"] or 0) - row["ttc"]) < 0.015 for got in read)
            found["trouvées"] += hit
            if not hit:
                lost.append("%s %s %s %.2f %s" % (
                    note, row["date"], row["type"], row["ttc"], row["currency"]))
    return found, lost


# ---------------------------------------------------------------------------
# Compte rendu
# ---------------------------------------------------------------------------

FIELDS = ("total", "date", "tax", "category")
LABELS = {"total": "total", "date": "date", "tax": "TVA", "category": "catégorie"}


def summarize(verdicts):
    counts = collections.Counter()
    for verdict in verdicts.values():
        for field, ok in verdict.items():
            counts[field, "n"] += 1
            counts[field, "ok"] += ok
    return counts


def print_summary(results, verdicts, truth, seconds):
    counts = summarize(verdicts)
    print("fichiers rejoués : %d en %.1f s" % (len(results), seconds))
    if not truth:
        return
    for field in FIELDS:
        n, ok = counts[field, "n"], counts[field, "ok"]
        print("  %-10s %4d / %-4d %5.1f %%" % (LABELS[field], ok, n, 100.0 * ok / max(n, 1)))
    found, _lost = recall(results, truth)
    print("  dépenses à justificatif retrouvées : %d / %d (%.1f %%)" % (
        found["trouvées"], found["lignes"], 100.0 * found["trouvées"] / max(found["lignes"], 1)))


def compare(baseline, results, verdicts):
    """Différences avec la mesure de référence, fichier par fichier."""
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
    for title, lines in (("RÉGRESSIONS (juste -> faux)", regressions), ("gains (faux -> juste)", gains),
                         ("autres différences", changes)):
        print("\n%s : %d" % (title, len(lines)))
        for line in lines[:60]:
            print("  " + line)
        if len(lines) > 60:
            print("  … et %d autres" % (len(lines) - 60))
    return len(regressions)


def print_fidelity(items, results):
    """Compare le rejeu à ce qu'Odoo avait écrit ; un écart signale un instantané périmé."""
    differ = collections.Counter()
    for name, item in items.items():
        odoo, got = item.get("odoo") or {}, results[name]
        if odoo.get("total") is not None and abs((got["total"] or 0) - odoo["total"]) > 0.005:
            differ["total"] += 1
        # Sans date lue, Odoo met celle du jour : ce n'est pas un écart.
        if got["date"] and odoo.get("date") and odoo["date"] != got["date"]:
            differ["date"] += 1
        if odoo.get("category") and got["category"] and odoo["category"] != got["category"]:
            differ["catégorie"] += 1
    print("\nécarts avec Odoo (historique des enseignes, retouches comprises) : %s" % (
        dict(differ) or "aucun"))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    argp = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    argp.add_argument("snapshot", help="dossier de l'instantané (mots lus)")
    argp.add_argument("--truth", help="dossier des tableaux de vérité")
    argp.add_argument("--open-prices", help="truth.jsonl des tickets d'Open Prices")
    argp.add_argument("--save", help="écrit la mesure dans ce fichier")
    argp.add_argument("--baseline", help="mesure de référence à comparer")
    argp.add_argument("--only", help="ne rejouer que les fichiers dont le nom contient ce texte")
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
        print("\nmesure enregistrée : %s" % args.save)
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
