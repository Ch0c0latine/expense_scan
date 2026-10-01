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
from collections import Counter

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


# ---------------------------------------------------------------------------
# Words learnt from the receipts filed by the team
# ---------------------------------------------------------------------------

#: A word is learnt for a category when it is on at least this many of its
#: receipts, on this share of them, and almost only on them.
LEARN_MIN_RECEIPTS = 3
LEARN_MIN_SHARE = 0.3
LEARN_MIN_PRECISION = 0.85
#: ...and on the receipts of this many merchants: the words of a single
#: merchant (its name, its street) are already known from the merchant
#: history, and say nothing about the next merchant.
LEARN_MIN_MERCHANTS = 2
#: Below this many receipts in all, the words say nothing: with a single
#: category in use, every word of its receipts would look specific.
LEARN_MIN_CORPUS = 10
#: Words kept per category, the most frequent first.
LEARN_MAX_WORDS = 30
LEARN_MIN_LENGTH = 4

#: Words of every receipt, in the languages met: they point to no category.
COMMON_WORDS = set("""
total totale totaal totalt summe suma summa subtotal sous somme gesamt importe importo montant
amount betrag bedrag prix price preis precio prezzo preco cena net netto brutto brut ttc
tva iva mwst vat btw moms mva ptu dph taxe taxes steuer imposta impuesto
euro euros eur chf gbp usd sek nok dkk pln czk
carte card karte tarjeta carta cartao karta visa mastercard maestro contactless sans contact
debit credit terminal transaction autorisation autorizzazione autorizacion
paiement payment zahlung pago pagamento platnosc reglement especes cash contanti efectivo bargeld
rendu change cambio resto ruckgeld monnaie
merci grazie gracias danke thank thanks obrigado bedankt tack takk dziekujemy visite visita besuch
ticket recu receipt beleg quittung scontrino factura fattura facture invoice rechnung fatura
paragon kvittering documento commerciale simplificada simplifiee
date heure time datum fecha data uhrzeit hora
client cliente kunde customer caisse kasse caja cassa kasa operateur vendeur
article articles artikel articulo articoli qte quantite quantity menge cantidad
siret siren tel telephone telefono telefon www http https email mail adresse address
rue avenue boulevard route place calle avda strasse via piazza
numero number nummer code conserver votre vous nous pour avec dans les des the and for with your
und der die das per con del della los las para por
""".split())


def receipt_words(text, excluded=()):
    """Distinct words of a receipt that may name a category."""
    words = set()
    for word in lexicon.fold(text or "").split():
        if len(word) < LEARN_MIN_LENGTH or not word.isalpha():
            continue
        if word in COMMON_WORDS or word in excluded:
            continue
        words.add(word)
    return words


def learn_words(receipts, excluded=(), known=None):
    """``{category: [words]}`` learnt from receipts already filed.

    ``receipts``: ``[(category, text, merchant, chosen)]``. Only the
    receipts whose category a person chose (``chosen``) teach: a category
    suggested by the scan and kept as is would teach back the words that
    suggested it, mistakes included. All of them count to tell whether a
    word belongs to a single category. ``merchant``: key of the merchant,
    ``None`` when unknown (the unknown ones count as one merchant).
    ``excluded``: words never to learn (names of the employees and of the
    company). ``known``: ``{category: [words]}`` declared by hand; such a
    word is not learnt again, nor for another category.
    """
    declared = {word for words in (known or {}).values() for word in words}
    everywhere, in_category = Counter(), Counter()
    taught, sizes, merchants = {}, Counter(), {}
    for category, text, merchant, chosen in receipts:
        words = receipt_words(text, excluded)
        everywhere.update(words)
        in_category.update((category, word) for word in words)
        if not chosen:
            continue
        sizes[category] += 1
        taught.setdefault(category, Counter()).update(words)
        for word in words:
            merchants.setdefault((category, word), set()).add(merchant)
    if len(receipts) < LEARN_MIN_CORPUS:
        return {}
    learnt = {}
    for category, counts in taught.items():
        words = [word for word, count in counts.items()
                 if count >= LEARN_MIN_RECEIPTS
                 and count >= LEARN_MIN_SHARE * sizes[category]
                 and in_category[category, word] >= LEARN_MIN_PRECISION * everywhere[word]
                 and len(merchants[category, word]) >= LEARN_MIN_MERCHANTS
                 and word not in declared]
        words.sort(key=lambda word: (-counts[word], word))
        if words:
            learnt[category] = words[:LEARN_MAX_WORDS]
    return learnt
