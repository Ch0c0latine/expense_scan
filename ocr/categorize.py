# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Category clues given by a receipt, without depending on Odoo.

The model adds the merchant history, which lives in the database, to these
clues. Everything else (receipt words, activity code, unit price, brand) is
computed here, so that the benchmark (``tools/bench.py``) replays exactly
the module's behaviour.

Categories are designated by any key: the product id in Odoo, its code in
the benchmark.
"""
from . import lexicon

#: Weight of an activity code (APE, MCC, or SIRET found in Sirene): 4.
#: Enough to beat a single receipt word (two points) by at least 1.5: the
#: code decides on its own, unless the receipt words clearly say something
#: else (a hotel restaurant is still a meal).
CODE_WEIGHT = 4.0
#: Weight of a known brand at the top of the receipt: 3, more than a header
#: word. At 2, a stray word ("route", "airport") was enough to leave a KFC or
#: an airport Starbucks without a category.
BRAND_WEIGHT = 3.0
#: Weight of a price per litre or per kWh: that of a brand. A charge paid at
#: a supermarket charger ("ALDI") is still a charge.
UNIT_WEIGHT = 3.0

#: Kinds of clue, as returned by :func:`score`.
WORDS, ACTIVITY, SIRET, UNIT, BRAND = 'words', 'activity', 'siret', 'unit', 'brand'


def score(lines, keyword_categories, family_keys, activity=None, naf_of=None):
    """Score of each category, and the reason for that score.

    ``lines``: the receipt text, line by line. ``keyword_categories``:
    ``{key: words}`` of the categories that words designate. ``family_keys``:
    ``{family: key}``, the category of each expense family (hotel, meal,
    fuel...). ``activity``: the printed activity code, ``"APE:5610A"``.
    ``naf_of``: called, when no code is printed, to find the code from the
    SIRET (a query, hence lazy).

    Returns ``(scores, reasons, brand)``: ``reasons`` maps each key to its
    ``(weight, (kind, detail))``, and ``brand`` is the brand recognised at the
    top of the receipt, or ``None``.
    """
    scores = lexicon.score_categories(lines, keyword_categories)
    reasons = {key: [(value, (WORDS, None))] for key, value in scores.items()}

    def add(family, weight, reason):
        key = family_keys.get(family)
        if key is not None:
            scores[key] = scores.get(key, 0.0) + weight
            reasons.setdefault(key, []).append((weight, reason))

    family = lexicon.activity_family(activity)
    if family:
        add(family, CODE_WEIGHT, (ACTIVITY, activity.split(':', 1)[1]))
    else:
        naf = naf_of() if naf_of else None
        naf_family = lexicon.activity_family('NAF:%s' % naf) if naf else None
        if naf_family:
            add(naf_family, CODE_WEIGHT, (SIRET, naf))

    if lexicon.fuel_unit(lines):
        add('fuel', UNIT_WEIGHT, (UNIT, None))

    brand_family, brand = lexicon.brand_family(lines)
    if brand_family:
        add(brand_family, BRAND_WEIGHT, (BRAND, brand))
    return scores, reasons, brand if brand_family else None
