#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Quick check, without Odoo or a database: syntax and imports.

To run before each update of the module. It catches the errors that only show
when the Odoo registry loads and make the instance unusable:

* a syntax error in a Python file of the module, models included, which only
  Odoo imports;
* an ``ocr`` package that no longer imports, or a missing image processing
  dependency.

Receipt reading is checked by the module's tests (reference receipts) and by
the benchmark, ``tools/bench.py``.

    python3 tools/check.py            # syntax and imports
    python3 tools/check.py --strict   # also requires the image dependencies (Odoo's interpreter)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def check_syntax():
    """Compile every file of the module; return the failures."""
    failures = []
    for folder, _subfolders, names in os.walk(ROOT):
        if os.path.basename(folder) in ('__pycache__', '.git'):
            continue
        for name in sorted(names):
            if not name.endswith('.py'):
                continue
            path = os.path.join(folder, name)
            # Compiled in memory: `py_compile` writes a bytecode file and
            # refuses `/dev/null` since Python 3.14.
            with open(path, encoding='utf-8') as handle:
                source = handle.read()
            try:
                compile(source, path, 'exec')
            except SyntaxError as error:
                failures.append("%s line %s: %s" % (
                    os.path.relpath(path, ROOT), error.lineno, error.msg))
    print("syntax             : %s" % ("OK" if not failures else "FAILED"))
    return failures


def main():
    from ocr import categorize, engines, lexicon, parser, preprocess, types  # noqa: F401
    print("imports            : OK")

    available, message = preprocess.dependencies_status()
    print("image processing   : %s" % message)
    for status in engines.engines_status():
        print("engine %-11s: %s (%s)" % (
            status['code'], "available" if status['available'] else "unavailable",
            status['message']))

    failures = check_syntax()
    if not available and '--strict' in sys.argv:
        failures.append("image processing unavailable")
    if failures:
        print("\nFAILED:\n  %s" % "\n  ".join(failures))
        return 1
    print("\nAll good.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
