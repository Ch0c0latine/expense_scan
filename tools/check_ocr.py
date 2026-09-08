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

#: Tickets de référence : chacun a fait remonter un bug une fois, et le
#: garder ici évite de le revoir. Les valeurs attendues portent sur ce qui
#: finit en comptabilité — total et TVA — car c'est là que l'erreur coûte.
REFERENCE_RECEIPTS = [
    {
        'name': "péage ASF (prix HT piège, TVA sur une ligne)",
        'text': """
ASF Lieu-dit Gaussens BP 40037
Date .07/09/26 .PAMIERS
PRIX HT.......5,67 euros TVA 20,00%....1,13 euros
PRIX TTC......6,80 euros
Paiement....6,80 E ..CB
""",
        'total': 6.80,
        'tax_amount': 1.13,
        'tax_rate': 20.0,
        'tax_rate_max': 20.0,
    },
    {
        'name': "restaurant multi-taux (TVA 10 % et 20 %, trois colonnes)",
        'text': """
LES 3 BRASSEURS
1 x Formule du jour 29,03
1 x Biere 4,07
TOTAL 33,10 EUR
TVA 10 % 26,39 2,64 29,03
TVA 20 % 3,39 0,68 4,07
CB EMV 33,10
""",
        'total': 33.10,
        # 2,64 + 0,68 : la somme des taxes, surtout pas celle des TTC.
        'tax_amount': 3.32,
        # Plusieurs taux : aucun ne vaut pour la dépense entière, on n'en
        # impose pas ; le plus élevé sert de plafond au contrôle.
        'tax_rate': None,
        'tax_rate_max': 20.0,
    },
    {
        'name': "ticket de carte bancaire (aucune TVA à reporter)",
        'text': """
CREDIT AGRICOLE
CB CONTACT A0000000031010
MONTANT 45,00 EUR
DEBIT
MERCI AU REVOIR
""",
        'total': 45.00,
        'tax_amount': None,
        'tax_rate': None,
        'tax_rate_max': None,
    },
]


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


def check_receipt(case):
    """Analyse un ticket de référence et renvoie ses écarts."""
    result = parser.parse(words_from_text(case['text']))
    print("--- %s ---" % case['name'])
    for name in ('merchant', 'date', 'time', 'total', 'tax_rate', 'tax_rate_max', 'tax_amount'):
        print("  %-11s %-14s confiance %.2f" % (
            name, result.value(name), result.confidence(name)))
    print("  à vérifier  %s" % (", ".join(parser.fields_to_check(result)) or "rien"))

    failures = []
    for field in ('total', 'tax_amount', 'tax_rate', 'tax_rate_max'):
        if field not in case:
            continue
        read = result.value(field)
        if read != case[field]:
            failures.append("%s : %s lu au lieu de %s" % (case['name'], read, case[field]))
    return failures


def main():
    print("imports            : OK")

    available, message = preprocess.dependencies_status()
    print("traitement image   : %s" % message)

    for status in engines.engines_status():
        print("moteur %-11s: %s" % (
            status['code'],
            "%s (%s)" % ("disponible" if status['available'] else "indisponible",
                         status['message'])))

    failures = []
    for case in REFERENCE_RECEIPTS:
        failures.extend(check_receipt(case))
    if not available:
        failures.append("traitement d'image indisponible")

    if failures:
        print("\nÉCHEC :\n  %s" % "\n  ".join(failures))
        return 1
    print("\nTout est conforme.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
