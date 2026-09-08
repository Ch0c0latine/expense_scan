#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contrôle rapide du paquet ``ocr``, sans Odoo ni base de données.

À lancer **avant** chaque mise à jour du module. Il attrape en une seconde
les erreurs qui, sinon, ne se manifestent qu'au chargement du registre
Odoo — et laissent alors l'instance entière hors service :

* erreurs de syntaxe et d'import ;
* erreurs qui n'apparaissent qu'à l'exécution du module, comme un champ de
  dataclass muni d'une valeur par défaut placé avant un champ qui n'en a
  pas ;
* dépendances de traitement d'image absentes ou incapables de se charger.

Il vérifie aussi que le parseur lit correctement un ticket de référence,
ce qui signale toute régression dans les expressions de reconnaissance.

    python3 tools/check_ocr.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr import engines, parser, preprocess, types  # noqa: E402

#: Ticket de péage réel, avec ses points de conduite et son prix HT piège.
REFERENCE_RECEIPT = """
ASF Lieu-dit Gaussens BP 40037
Date .07/09/26 .PAMIERS
PRIX HT.......5,67 euros TVA 20,00%....1,13 euros
PRIX TTC......6,80 euros
Paiement....6,80 E ..CB
"""
EXPECTED_TOTAL = 6.80
EXPECTED_TAX = 1.13


def words_from_text(text, line_height=20.0, char_width=9.0):
    """Fabrique des mots situés à partir d'un ticket écrit en texte."""
    words = []
    for row, line in enumerate(text.strip().splitlines()):
        column = 0
        for chunk in line.split(" "):
            if chunk:
                words.append(types.OcrWord(
                    text=chunk,
                    score=0.95,
                    left=column * char_width,
                    top=row * line_height,
                    right=(column + len(chunk)) * char_width,
                    bottom=row * line_height + line_height * 0.7,
                ))
            column += len(chunk) + 1
    return words


def main():
    print("imports            : OK")

    available, message = preprocess.dependencies_status()
    print("traitement image   : %s" % message)

    for status in engines.engines_status():
        print("moteur %-11s: %s" % (
            status['code'],
            "%s (%s)" % ("disponible" if status['available'] else "indisponible",
                         status['message'])))

    result = parser.parse(words_from_text(REFERENCE_RECEIPT))
    print("--- ticket de référence ---")
    for name in ('merchant', 'date', 'time', 'total', 'tax_rate', 'tax_amount'):
        print("  %-11s %-14s confiance %.2f" % (
            name, result.value(name), result.confidence(name)))
    print("  à vérifier  %s" % (", ".join(parser.fields_to_check(result)) or "rien"))

    failures = []
    if result.value('total') != EXPECTED_TOTAL:
        failures.append("total lu %s au lieu de %s" % (result.value('total'), EXPECTED_TOTAL))
    if result.value('tax_amount') != EXPECTED_TAX:
        failures.append("TVA lue %s au lieu de %s" % (result.value('tax_amount'), EXPECTED_TAX))
    if not available:
        failures.append("traitement d'image indisponible")

    if failures:
        print("\nÉCHEC : %s" % " / ".join(failures))
        return 1
    print("\nTout est conforme.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
