#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Contrôle rapide, sans Odoo ni base de données : syntaxe et imports.

À lancer avant chaque mise à jour du module. Il détecte les erreurs qui ne
se manifestent qu'au chargement du registre Odoo et rendent l'instance
inutilisable :

* faute de syntaxe dans un fichier Python du module, y compris les modèles,
  que seul Odoo importe ;
* paquet ``ocr`` qui ne s'importe plus, ou dépendance de traitement d'image
  absente.

La lecture des tickets se vérifie par les tests du module (tickets de
référence) et par le banc d'évaluation, ``tools/bench.py``.

    python3 tools/check.py            # syntaxe et imports
    python3 tools/check.py --strict   # exige aussi les dépendances d'image (interpréteur d'Odoo)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def check_syntax():
    """Compile tous les fichiers du module ; renvoie les échecs."""
    failures = []
    for folder, _subfolders, names in os.walk(ROOT):
        if os.path.basename(folder) in ('__pycache__', '.git'):
            continue
        for name in sorted(names):
            if not name.endswith('.py'):
                continue
            path = os.path.join(folder, name)
            # Compilation en mémoire : `py_compile` écrit un fichier de bytecode
            # et refuse `/dev/null` depuis Python 3.14.
            with open(path, encoding='utf-8') as handle:
                source = handle.read()
            try:
                compile(source, path, 'exec')
            except SyntaxError as error:
                failures.append("%s ligne %s : %s" % (
                    os.path.relpath(path, ROOT), error.lineno, error.msg))
    print("syntaxe            : %s" % ("OK" if not failures else "ÉCHEC"))
    return failures


def main():
    from ocr import categorize, engines, lexicon, parser, preprocess, types  # noqa: F401
    print("imports            : OK")

    available, message = preprocess.dependencies_status()
    print("traitement image   : %s" % message)
    for status in engines.engines_status():
        print("moteur %-11s: %s (%s)" % (
            status['code'], "disponible" if status['available'] else "indisponible",
            status['message']))

    failures = check_syntax()
    if not available and '--strict' in sys.argv:
        failures.append("traitement d'image indisponible")
    if failures:
        print("\nÉCHEC :\n  %s" % "\n  ".join(failures))
        return 1
    print("\nTout est conforme.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
