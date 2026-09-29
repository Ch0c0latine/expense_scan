# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Indices de catégorie fournis par un ticket, sans dépendance à Odoo.

Le modèle ajoute à ces indices l'historique des enseignes, qui vit en base.
Tout le reste (mots du ticket, code d'activité, prix à l'unité, marque) est
calculé ici, pour que le banc d'évaluation (``tools/bench.py``) rejoue
exactement le comportement du module.

Les catégories sont désignées par une clé quelconque : l'identifiant du
produit dans Odoo, son code dans le banc.
"""
from . import lexicon

#: Poids d'un code d'activité (APE, MCC, ou SIRET retrouvé dans Sirene) : 4.
#: C'est assez pour l'emporter d'au moins 1,5 sur un mot isolé du ticket (deux
#: points) : le code est décisif à lui seul, sauf si les mots du ticket
#: indiquent nettement autre chose (le restaurant d'un hôtel reste un repas).
CODE_WEIGHT = 4.0
#: Poids d'une marque connue en tête du ticket : 3, soit plus qu'un mot
#: d'en-tête. À 2, un mot égaré (« route », « aéroport ») suffisait à laisser
#: sans catégorie un KFC ou un Starbucks d'aérogare.
BRAND_WEIGHT = 3.0
#: Poids d'un prix au litre ou au kWh : celui d'une marque. Une recharge
#: payée à la borne d'un supermarché (« ALDI ») reste une recharge.
UNIT_WEIGHT = 3.0

#: Natures d'indice, telles que les rend :func:`score`.
WORDS, ACTIVITY, SIRET, UNIT, BRAND = 'words', 'activity', 'siret', 'unit', 'brand'


def score(lines, keyword_categories, family_keys, activity=None, naf_of=None):
    """Score de chaque catégorie, et la raison de ce score.

    ``lines`` : le texte du ticket, ligne à ligne. ``keyword_categories`` :
    ``{clé: mots}`` des catégories que des mots désignent. ``family_keys`` :
    ``{famille: clé}``, la catégorie de chaque famille de frais (hôtel,
    repas, carburant…). ``activity`` : le code d'activité imprimé,
    ``"APE:5610A"``. ``naf_of`` : appelée, s'il n'y a pas de code imprimé,
    pour retrouver le code d'après le SIRET (une requête, donc paresseuse).

    Renvoie ``(scores, raisons, marque)`` : ``raisons`` associe à chaque
    clé ses ``(poids, (nature, détail))``, et ``marque`` est la marque
    reconnue en tête du ticket, ou ``None``.
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
