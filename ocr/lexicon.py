# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Recognising the category of an expense from the text of the receipt.

Several sources, without network or model:

* the receipt words each category declares ("nuitée", "benzyna",
  "pedaggio"...), matched with a tolerance for reading mistakes;
* the merchants already classified: the expense history, provided by the
  Odoo module, which learns from the employees' corrections;
* the APE/NAF and MCC activity codes;
* the European brands of OpenStreetMap's Name Suggestion Index.

Receipts come from all over Europe: the text is therefore folded (no
accents, no case, Polish or Nordic letters brought back to the Latin
alphabet) before any comparison. The default words cover French, English,
German, Italian, Spanish, Polish, Dutch and Portuguese.

No dependency on Odoo, like the rest of the package.
"""
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Text folding
# ---------------------------------------------------------------------------

#: Letters that Unicode decomposition does not bring back to the Latin alphabet.
EXTRA_FOLDS = str.maketrans({
    "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d",
    "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ı": "i",
})


def fold(text):
    """Comparable text: lower case, no accents, words separated by one space."""
    text = (text or "").translate(EXTRA_FOLDS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def split_keywords(raw):
    """The words declared on a category: one per line, or separated by commas."""
    keywords = []
    for chunk in re.split(r"[\n,;]+", raw or ""):
        folded = fold(chunk)
        if folded and folded not in keywords:
            keywords.append(folded)
    return keywords


# ---------------------------------------------------------------------------
# Default words, per expense family
# ---------------------------------------------------------------------------

#: Words suggested when the categories are created. They only fill an empty
#: record: after that, the record prevails and everyone adds their own
#: merchants. Brands are listed because they are often the only readable
#: thing in a header.
#:
#: "total" is not listed, although it is a fuel brand: it is also the label
#: of the amount on every receipt.
DEFAULT_KEYWORDS = {
    'lodging': [
        # fr
        "hôtel", "hotel", "nuitée", "nuitées", "taxe de séjour", "chambre",
        "hébergement", "petit déjeuner inclus",
        # en
        "room", "night", "nights", "city tax", "tourist tax", "lodging", "accommodation",
        # de
        "übernachtung", "zimmer", "kurtaxe", "citytax", "beherbergung",
        # it
        "pernottamento", "tassa di soggiorno", "imposta di soggiorno", "albergo", "notti",
        # es
        "alojamiento", "habitación", "hospedaje", "noches",
        # pl
        "nocleg", "noclegi", "zakwaterowanie", "pokój", "doba hotelowa", "opłata miejscowa",
        # nl, pt
        "overnachting", "toeristenbelasting", "alojamento", "dormida",
        # brands
        "airbnb", "booking.com", "ibis", "novotel", "mercure", "campanile", "kyriad",
        "première classe", "b&b hotel", "holiday inn", "best western", "marriott",
        "hilton", "radisson", "motel one", "logis hotels", "appart city",
    ],
    'meal': [
        # fr
        "restaurant", "brasserie", "couverts", "couvert", "menu", "plat du jour",
        # Meal voucher mentions: a receipt that accepts them is a meal.
        "titre restaurant", "titres restaurant", "ticket restaurant", "eligible tr",
        "dessert", "boisson", "boissons", "pizzeria", "traiteur",
        # en
        "table", "guests", "covers", "tip", "gratuity", "food", "beverage",
        # de
        "gaststätte", "speisen", "getränke", "trinkgeld", "bewirtung", "gasthaus",
        # it
        "ristorante", "trattoria", "osteria", "coperto", "coperti", "pizzeria",
        "bevande", "servizio",
        # es
        "comida", "bebidas", "comensales", "propina",
        # pl
        "restauracja", "obiad", "napoje", "danie", "bar mleczny",
        # nl, pt
        "eetcafé", "dranken", "refeição", "bebidas",
        # brands
        "mcdonald", "burger king", "subway", "flunch",
        "courtepaille", "buffalo grill", "hippopotamus", "la croissanterie",
    ],
    'fuel': [
        # fr
        "gazole", "gasoil", "sans plomb", "sp95", "sp98", "sp95 e10", "e85",
        "carburant", "supercarburant", "borne de recharge", "recharge électrique",
        # en
        "diesel", "unleaded", "petrol", "fuel", "charging",
        # de
        "benzin", "kraftstoff", "tankstelle", "super e10", "ladestation",
        # it
        "benzina", "gasolio", "carburante", "rifornimento", "ricarica",
        # es
        "gasolina", "gasóleo", "combustible", "gasolinera",
        # pl
        "benzyna", "olej napędowy", "paliwo", "pb95", "pb 95", "stacja paliw",
        # nl, pt
        "brandstof", "tankstation", "gasóleo", "combustível",
        # brands
        "totalenergies", "esso", "shell", "avia", "agip", "eni", "q8", "aral",
        "orlen", "lotos", "circle k", "tamoil", "repsol", "cepsa", "galp", "omv",
        "ionity", "fastned", "electra", "freshmile",
    ],
    'toll_parking': [
        # fr
        "péage", "autoroute", "autoroutes", "stationnement", "parking", "horodateur",
        # Mentions specific to toll receipts, which often do not print the
        # word "péage" and whose operator initials the OCR readily glues to
        # the next word ("ASFLieu-dit").
        "classe tarif", "classe de véhicule", "gare de péage", "bip&go", "ulys",
        "barrière de vallesque",
        # en
        "toll", "car park",
        # de
        "maut", "autobahn", "parkhaus", "parkplatz", "parkgebühr",
        # it
        "pedaggio", "autostrada", "autostrade", "parcheggio", "sosta",
        # es
        "peaje", "autopista", "aparcamiento",
        # pl
        "opłata za przejazd", "autostrada", "parkowanie", "parking strzeżony",
        # nl, pt
        "parkeren", "portagem", "estacionamento",
        # brands
        "vinci autoroutes", "sanef", "aprr", "asf", "cofiroute", "escota",
        "telepass", "indigo", "effia", "saemes", "q-park", "onepark",
        "interparking", "apcoa", "via verde",
    ],
    'train_air': [
        # fr
        "sncf", "tgv", "inoui", "ouigo", "billet", "voyageur", "carte d'embarquement",
        "bagage",
        # en
        "train", "flight", "boarding pass", "baggage", "airline",
        # de
        "deutsche bahn", "db fernverkehr", "fahrkarte", "fahrschein", "flug",
        # it
        "trenitalia", "italo", "frecciarossa", "biglietto", "treno", "volo",
        # es
        "renfe", "billete", "vuelo", "tarjeta de embarque",
        # pl
        "pkp", "intercity", "bilet", "pociąg",
        # nl, pt
        "treinkaartje", "comboio",
        # brands
        "eurostar", "thalys", "sbb", "obb", "air france", "easyjet", "ryanair",
        "transavia", "lufthansa", "lot polish airlines", "vueling", "wizz air",
        "volotea", "ita airways", "klm", "tap air",
    ],
    'taxi': [
        # fr
        "taxi", "vtc", "course", "ratp", "métro", "tramway", "navigo",
        "titre de transport", "trajet unitaire", "tisseo", "tcl", "ilevia",
        "rtm", "twisto", "divia", "bibus",
        "trottinette", "vélo en libre service",
        # en
        "ride", "cab", "fare",
        # de
        "bvg", "mvg", "straßenbahn", "u-bahn",
        # it
        "corsa", "tram", "metropolitana",
        # es
        "trayecto", "metro de madrid", "tmb",
        # pl
        "przejazd", "ztm", "mpk", "tramwaj",
        # brands
        "uber", "bolt", "free now", "freenow", "heetch", "g7", "cabify",
        "itaxi", "lime", "velib",
    ],
    'car_rental': [
        # fr
        "location de véhicule", "location voiture", "location de voiture", "loueur",
        # en
        "car rental", "rent a car", "rental agreement",
        # de
        "autovermietung", "mietwagen",
        # it
        "noleggio", "autonoleggio",
        # es
        "alquiler de coches", "alquiler",
        # pl
        "wypożyczalnia", "najem samochodu",
        # brands
        "hertz", "avis budget", "europcar", "sixt", "enterprise rent", "ada",
        "getaround", "goldcar", "ucar", "leasys",
    ],
    'telecom': [
        "forfait mobile", "téléphonie", "roaming",
        "sfr", "orange sa", "bouygues telecom", "free mobile", "vodafone", "tim",
        "t-mobile", "telekom", "movistar", "play", "plus gsm",
    ],
}

#: Words added after the first suggestion, per version:
#: ``{'19.0.x.y.z': {family: [words]}}``. The version's migration script adds
#: them to the records already filled, without removing anything.
ADDED_KEYWORDS = {
    # Real receipts the first list did not recognise: an airport hot dog
    # classified as transport, a vending machine coffee, a charger billed in
    # kWh, a flight paid online.
    '19.0.2.3.0': {
        'meal': ["repas", "hot dog", "fricadelle", "sandwich", "kebab",
                 "boulangerie", "viennoiserie", "baguette", "croissant",
                 "café au lait", "cafe au lait", "expresso", "cappuccino"],
        'fuel': ["kwh", "energy tariff", "recharge", "chargement", "charging session"],
        'train_air': ["vol", "vols", "passager", "passagers", "embarquement"],
    },
    # An Italian toll ("PEDAGGIO"), a "B&B" booking receipt.
    '19.0.2.4.1': {
        'toll_parking': ["pedaggio", "casello", "autostrada", "telepass", "maut",
                         "peaje", "autopista", "esattore", "transito",
                         "attestato di transito"],
        'lodging': ["b&b", "bed and breakfast", "bnb"],
    },
    # German airport restaurant: "Aichinger Gastro GmbH ... Tisch 1122".
    # Italian filling station whose company name contains "risto" (restaurant).
    # Spanish restaurant receipts: the table and the waiter, not the word
    # "restaurante" (LosComensales.es receipts, Madrid).
    '19.0.2.8.7': {
        'meal': ["mesa", "camarero", "camarera", "comensal"],
    },
    '19.0.2.4.2': {
        'meal': ["gastro", "tisch"],
        'fuel': ["senza piombo", "pompa", "stazione di servizio", "erogatore"],
    },
}
#: Words removed from one version to the next, same form. The migration only
#: removes these words: the rest of each record is left unchanged.
REMOVED_KEYWORDS = {
    # "Aéroport" is a place, not a purchase: the word classified an airport
    # sandwich (Starbucks, Pokawa) as a plane ticket.
    '19.0.2.4.2': {
        'train_air': ["aeroport", "aéroport"],
    },
}
# Categories created after this version start from these words too.
for _words in ADDED_KEYWORDS.values():
    for _family, _added in _words.items():
        DEFAULT_KEYWORDS[_family] = DEFAULT_KEYWORDS[_family] + [
            word for word in _added if word not in DEFAULT_KEYWORDS[_family]]

#: How to recognise, by its name or reference, the category matching a
#: family. First clue found, first family served.
FAMILY_HINTS = [
    ('lodging', ("heberg", "hotel", "bnb", "lodging", "accommodation", "unterkunft",
                 "ubernacht", "alloggi", "alojam", "hospedaj", "nocleg", "overnacht",
                 "overnatt", "overnatn", "boende")),
    ('taxi', ("taxi", "vtc", "mobilit", "mob")),
    ('train_air', ("train", "avion", "transport", "flight", "air", "bahn", "flug", "zug",
                   "treno", "volo", "tren", "vuelo", "pociag", "trein")),
    ('fuel', ("carbur", "energie", "fuel", "essence", "elec", "kraftstoff", "benzin",
              "tank", "tanken", "paliw", "brandstof", "combust", "drivmedel", "drivstoff",
              "braendstof")),
    ('toll_parking', ("peage", "parking", "park", "toll", "maut", "parkgeb", "parkier",
                      "parken", "pedagg", "peaje", "parcheggi", "aparcam", "parkeren", "parkering")),
    ('car_rental', ("location", "rental", "loc", "mietwagen", "noleggio", "alquiler",
                    "wynajem", "huurauto", "hyrbil", "leiebil", "billeje")),
    ('meal', ("repas", "meal", "food", "restaur", "mahlzeit", "pasto",
              "pasti", "comida", "posilk", "maaltijd", "refeic", "maltid")),
    ('telecom', ("communication", "comm", "telecom", "telephon")),
]
#: Categories no family claims: flat allowances, and business meals, which
#: nothing on the receipt tells apart from an ordinary meal.
FAMILY_EXCLUDED = ("igd", "bareme", "forfait", "invit", "affaire", "kilomet", "mileage",
                   "pauschal", "verpflegungsmehr")


def family_of(*labels):
    """Expense family of a category, from its name and reference."""
    words = fold(" ".join(label for label in labels if label)).split()
    if any(word.startswith(excluded) for word in words for excluded in FAMILY_EXCLUDED):
        return None
    for family, hints in FAMILY_HINTS:
        for hint in hints:
            # Short clue: whole word, plural allowed ("Meals", Odoo's own
            # category), otherwise "air" would match "affaires" and "mob"
            # would match anything.
            if any(word in (hint, hint + "s") if len(hint) <= 4 else word.startswith(hint)
                   for word in words):
                return family
    return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

#: Similarity threshold between a misread word and a declared word.
FUZZY_RATIO = 0.84
#: Below this length, a word is only compared exactly: "eni", "ada", "g7"
#: look like too many things.
FUZZY_MIN_LENGTH = 5
#: Number of lines taken as the header, where the merchant name is printed.
HEADER_LINES = 8
#: Weight of a word found in the header, then elsewhere.
HEADER_WEIGHT = 2.0
BODY_WEIGHT = 1.0
#: Minimum score, and minimum lead over the second category, to decide.
MIN_SCORE = 2.0
MIN_LEAD = 1.5


def similar(first, second):
    """Similarity of two folded strings, from 0 to 1."""
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def resembles(first, second, threshold):
    """Are the two strings at least this similar?

    The quick bounds of ``SequenceMatcher`` rule out the vast majority of
    pairs before the full computation: a receipt has hundreds of words, and
    each category tens of declared words.
    """
    if not first or not second:
        return False
    matcher = SequenceMatcher(None, first, second)
    return (matcher.real_quick_ratio() >= threshold
            and matcher.quick_ratio() >= threshold
            and matcher.ratio() >= threshold)


def _same_start(candidate, keyword):
    """A reading mistake rarely changes the first letter of a short word.

    "Selecta" is 86% similar to "electra": without this check, a coffee
    vending machine is taken for a charger. From eight letters on, the
    similarity alone is enough.
    """
    return len(keyword) >= 8 or candidate[:1] == keyword[:1]


def _same_start(candidate, keyword):
    """A reading mistake rarely changes the first letter of a short word.

    "Selecta" is 86% similar to "electra": without this check, a coffee
    vending machine is taken for a charger. From eight letters on, the
    similarity alone is enough.
    """
    return len(keyword) >= 8 or candidate[:1] == keyword[:1]


def _find(keyword, line_words):
    """Is the declared word in the line? Tolerates a reading mistake."""
    parts = keyword.split()
    size = len(parts)
    for start in range(len(line_words) - size + 1):
        window = line_words[start:start + size]
        candidate = " ".join(window)
        if candidate == keyword:
            return True
        if len(keyword) >= FUZZY_MIN_LENGTH and abs(len(candidate) - len(keyword)) <= 2 \
                and _same_start(candidate, keyword) \
                and resembles(candidate, keyword, FUZZY_RATIO):
            return True
    # Word glued to another by the OCR: "TOTALENERGIESSTATION", "IBISLYON".
    if len(keyword) >= 6 and " " not in keyword:
        return any(keyword in word for word in line_words if len(word) > len(keyword))
    return False


def score_categories(lines, categories):
    """Score of each category on the lines of the receipt.

    ``categories`` maps an identifier to its folded words. A word only
    counts once per category, at its best position: a "PARKING" repeated ten
    times at the bottom of the receipt is worth no more than once.
    """
    folded = [fold(line).split() for line in lines]
    scores = {}
    for category, keywords in categories.items():
        score = 0.0
        for keyword in keywords:
            best = 0.0
            for position, words in enumerate(folded):
                if best >= HEADER_WEIGHT:
                    break
                if _find(keyword, words):
                    # An expression of several words ("classe tarif",
                    # "taxe de séjour") says enough to count as a header word
                    # wherever it is: on a toll receipt, it only appears on
                    # the ninth line.
                    header = position < HEADER_LINES or " " in keyword
                    best = max(best, HEADER_WEIGHT if header else BODY_WEIGHT)
            if best:
                # An expression of several words says more than a single
                # word: its weight is multiplied by 1.25.
                score += best * (1.25 if " " in keyword else 1.0)
        if score:
            scores[category] = score
    return scores


def pick_category(scores):
    """The category that clearly wins, or ``None``.

    Better to suggest nothing than a doubtful category: an empty field gets
    noticed, a wrong category goes through to the accounts.
    """
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < MIN_SCORE or best_score - runner_up < MIN_LEAD:
        return None
    return best


# ---------------------------------------------------------------------------
# Known merchants
# ---------------------------------------------------------------------------

#: Similarity required between a header line and a known merchant.
MERCHANT_RATIO = 0.8
#: Similarity also required between the first words: the shop name.
FIRST_WORD_RATIO = 0.75
#: Minimum length of a merchant that can be matched: below it, too many namesakes.
MERCHANT_MIN_LENGTH = 3


def merchant_key(name):
    """Matching key of a merchant."""
    return fold(name)


def match_merchant(lines, known_keys, max_lines=HEADER_LINES + 4):
    """The known merchant the receipt header points to, or ``None``.

    Compares each line at the top of the receipt, and each group of words of
    that line, to the merchants already met. This finds "Auchan" from
    "Rchan" as soon as an Auchan has been classified once.
    """
    keys = [key for key in known_keys if len(key) >= MERCHANT_MIN_LENGTH]
    if not keys:
        return None
    best = (0.0, None)
    for position, line in enumerate(lines[:max_lines]):
        words = fold(line).split()
        if not words:
            continue
        for key in keys:
            size = len(key.split())
            for start in range(max(len(words) - size + 1, 1)):
                candidate = " ".join(words[start:start + size])
                if candidate == key:
                    ratio = 1.0
                elif len(key) < FUZZY_MIN_LENGTH \
                        or not resembles(candidate, key, MERCHANT_RATIO):
                    continue
                elif not resembles(candidate.split()[0], key.split()[0], FIRST_WORD_RATIO):
                    # "Nord Villefranche d'Orbec" looks like "Dormizz
                    # Villefranche d'Orbec" by the city alone: the shop name,
                    # first, must look alike too.
                    continue
                else:
                    ratio = similar(candidate, key)
                # On a tie, the first lines win.
                ranked = ratio - position * 0.001
                if ratio >= MERCHANT_RATIO and ranked > best[0]:
                    best = (ranked, key)
    return best[1]


# ---------------------------------------------------------------------------
# Activity codes: APE/NAF, NACE, MCC
# ---------------------------------------------------------------------------

#: NAF rev. 2 class (first four digits, those of the European NACE, the
#: Italian ATECO or the German WZ) -> expense family. The classes of NACE
#: rev. 2.1, which gradually replaces the previous one, are listed too where
#: they differ (56.11, 56.12, 56.40).
NAF_FAMILIES = {
    '5510': 'lodging', '5520': 'lodging', '5530': 'lodging', '5590': 'lodging',
    '5610': 'meal', '5611': 'meal', '5612': 'meal', '5621': 'meal',
    '5629': 'meal', '5630': 'meal', '5640': 'meal',
    '4711': 'meal', '4721': 'meal', '4724': 'meal', '1071': 'meal',
    '4730': 'fuel', '3514': 'fuel',
    '5221': 'toll_parking',
    '4910': 'train_air', '5110': 'train_air', '5223': 'train_air', '4939': 'train_air',
    '4931': 'taxi', '4932': 'taxi',
    '7711': 'car_rental',
    '6110': 'telecom', '6120': 'telecom', '6130': 'telecom', '6190': 'telecom',
}

#: MCC codes of card slips -> expense family.
MCC_FAMILIES = {
    7011: 'lodging', 7012: 'lodging', 7032: 'lodging', 7033: 'lodging',
    5812: 'meal', 5813: 'meal', 5814: 'meal', 5411: 'meal', 5462: 'meal', 5499: 'meal',
    5541: 'fuel', 5542: 'fuel', 5552: 'fuel', 5983: 'fuel',
    4784: 'toll_parking', 7523: 'toll_parking',
    4011: 'train_air', 4112: 'train_air', 4511: 'train_air', 4582: 'train_air',
    4131: 'train_air',
    4111: 'taxi', 4121: 'taxi',
    7512: 'car_rental', 7513: 'car_rental', 7519: 'car_rental',
    4812: 'telecom', 4814: 'telecom', 4816: 'telecom',
}
#: MCC ranges reserved for the large brands of a sector.
MCC_RANGES = [
    (3000, 3351, 'train_air'),     # airlines
    (3351, 3501, 'car_rental'),    # car rental companies
    (3501, 4000, 'lodging'),       # hotel chains
]


def activity_family(code):
    """Expense family of an activity code read on the receipt.

    ``code`` is ``"NAF:5610A"`` or ``"MCC:5812"``, as the parser returns it;
    ``None`` if the code designates no known family.
    """
    if not code or ':' not in code:
        return None
    kind, value = code.split(':', 1)
    digits = re.sub(r"\D", "", value)
    if kind == 'NAF':
        return NAF_FAMILIES.get(digits[:4])
    if kind == 'MCC' and len(digits) == 4:
        number = int(digits)
        if number in MCC_FAMILIES:
            return MCC_FAMILIES[number]
        for low, high, family in MCC_RANGES:
            if low <= number < high:
                return family
    return None


# ---------------------------------------------------------------------------
# European brands (OpenStreetMap's Name Suggestion Index)
# ---------------------------------------------------------------------------

BRANDS_FILE = os.path.join(os.path.dirname(__file__), 'brands_europe.json')

#: When a brand belongs to several families (Esso sells fuel and sandwiches,
#: Indigo runs car parks and chargers), the first of this list wins: the shop
#: of a filling station is still a filling station.
BRAND_FAMILY_PRIORITY = ('lodging', 'toll_parking', 'car_rental', 'taxi', 'fuel', 'meal')

#: Brands of fewer than four letters accepted anyway: the others ("ed",
#: "me", "bp", which is also the French abbreviation for a PO box) could be
#: anything.
BRAND_SHORT_ALLOWED = {'q8', 'omv', 'kfc', 'ada', 'eni', 'erg', 'jet', 'ina', 'mol', 'dia'}

#: Brands that are also common words of a receipt, an address or a first
#: name, and are therefore not looked for.
BRAND_STOPWORDS = {
    'total', 'best', 'delta', 'element', 'edition', 'motto', 'greet', 'tribe',
    'petrol', 'power', 'star', 'classic', 'mobile', 'metano', 'sprint', 'pace',
    'loop', 'prim', 'tango', 'zest', 'volta', 'market', 'markant', 'extra',
    'fresh', 'mega', 'maxi', 'prix', 'proxi', 'proxy', 'profi', 'quick',
    'quickly', 'paul', 'plus', 'premier', 'pure', 'sale', 'sigma', 'simply',
    'tempo', 'utile', 'viva', 'welcome', 'notes', 'bingo', 'combi', 'joker',
    'okay', 'lounges', 'rabat', 'claro', 'gala', 'julia', 'alice', 'luca',
    'vincent', 'tommy', 'albert', 'hell', 'junge', 'keim', 'kuhn', 'nomi',
    'odin', 'lupa', 'bonjour', 'familia', 'metro', 'casino', 'kiwi', 'mace',
    'meny', 'happ', 'avec', 'aida', 'ange', 'aroma', 'beta', 'birds', 'boheme',
    'cactus', 'caravan', 'charter', 'cosmo', 'costa', 'daisy', 'diona',
    'everest', 'flop', 'frac', 'gama', 'gaucho', 'ginos', 'giraffe', 'grind',
    'gusto', 'klara', 'lantana', 'leon', 'livio', 'lotok', 'maksi', 'mila',
    'minit', 'novus', 'nobis', 'pinchos', 'pitaya', 'pumpkin', 'putka', 'rapo',
    'ribs', 'rolls', 'roda', 'sedal', 'sehne', 'sonic', 'steiner', 'suma',
    'sumo', 'taro', 'terno', 'thyme', 'tigre', 'torba', 'tossed', 'udon',
    'vidal', 'yolk', 'wasabi', 'haan', 'iduna', 'ingo', 'inver', 'loro', 'maes',
    'moya', 'orkan', 'osprey', 'peut', 'puma', 'safeway', 'tanker', 'toka',
    'vega', 'vito', 'watis', 'witty', 'driv', 'amic', 'argos', 'avanti',
    'atlante', 'blink', 'bliska', 'chipo', 'flaga', 'giap', 'marshal', 'octa',
    'sminn', 'believe', 'believ', 'porsche', 'conrad', 'locke', 'tivoli',
    'unbound', 'bastion', 'velo', 'gira', 'lime', 'dott', 'netto', 'bazaar',
    'grab go', 'shop go', 'go asia', 'lounge', 'glusco', 'emotion',
    'tesla', 'penny', 'norma', 'globi', 'globus', 'hubbox',
    'service', 'station', 'hotel', 'restaurant', 'parking', 'cafe', 'bar',
    'pizza', 'kebab', 'sushi', 'boulangerie', 'bakery', 'tabac', 'presse',
    # Long names, also looked for in the middle of a line, that are words.
    'attendant', 'baguette', 'cappuccino', 'caffeine', 'chopsticks',
    'courtyard', 'enchilada', 'enterprise', 'fabrique', 'graduate', 'insomnia',
    'marathon', 'mercedes', 'national', 'parallel', 'practical', 'recharge',
    'renaissance', 'roadhouse', 'romantik', 'tortilla', 'trademark',
    'basilico', 'pomodoro', 'fantastico', 'spettacolo', 'globales',
    'millennium', 'occidental', 'organico', 'colombus', 'columbus', 'gulliver',
    'daybreak', 'checkers', 'harvester', 'minimart', 'homeslice',
    'freshmarket', 'raiffeisen', 'westfalen', 'rosewood', 'wildwood',
    'tinseltown', 'continente', 'catalonia', 'station service',
}
#: Length (without spaces) from which a brand is distinctive enough to be
#: looked for anywhere in a header line: "... CCIAL V2 Restaurant McDonald's".
BRAND_ANYWHERE_LENGTH = 7

_BRAND_INDEX = None


def _latin_share(name):
    """Share of the letters of a name written in the Latin alphabet."""
    letters = [char for char in name if char.isalpha()]
    if not letters:
        return 0.0
    latin = [char for char in letters if fold(char)]
    return len(latin) / float(len(letters))


def _nicer(name, other):
    """Of two spellings of a brand, the one that displays best."""
    def rank(value):
        # "McDonald's" rather than "MCDONALD'S" or "mcdonald's".
        return (value != value.upper() and value != value.lower(), -len(value))
    return name if rank(name) > rank(other) else other


def brand_index():
    """Brand index, loaded once.

    Form: ``{folded brand: (family, displayed name)}``.
    """
    global _BRAND_INDEX
    if _BRAND_INDEX is not None:
        return _BRAND_INDEX
    index = {}
    try:
        with open(BRANDS_FILE, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    for family in BRAND_FAMILY_PRIORITY:
        for name in data.get(family, ()):
            # Greek, Cyrillic or Asian scripts: the OCR does not read them,
            # and folding them would leave only debris.
            if _latin_share(name) < 0.9:
                continue
            key = fold(name)
            if not key or key in BRAND_STOPWORDS:
                continue
            if len(key.replace(" ", "")) < 4 and key not in BRAND_SHORT_ALLOWED:
                continue
            if key in index:
                known_family, shown = index[key]
                if known_family == family:
                    index[key] = (family, _nicer(name, shown))
                continue
            index[key] = (family, name)
    _BRAND_INDEX = index
    return index


#: Unit price of fuel or of a charge: "2,069 €/L", "0.28 EUR/kWh",
#: "Energy: 23.3170 kWh". A 1.5 L bottle of water has no printed price per
#: litre.
FUEL_UNIT_RE = re.compile(
    r"(?:€|\bEUR)\s*/\s*(?:L|LT|LITRES?|KWH)\b|\b\d+[.,]\d+\s*KWH\b|\bKWH\s*:?\s*\d",
    re.IGNORECASE)


def fuel_unit(lines):
    """The line carrying a price per litre or per kWh, if there is one."""
    return next((line for line in lines if FUEL_UNIT_RE.search(line)), None)


def brand_family(lines, max_lines=HEADER_LINES, max_words=4):
    """``(family, displayed brand)`` from the header, or ``(None, None)``.

    A short brand must open a header line; a long brand can be anywhere in
    it. The comparison is always exact: a base of thousands of names, many
    of which look like words, does not allow approximation.
    """
    index = brand_index()
    for line in lines[:max_lines]:
        words = fold(line).split()
        for start in range(len(words)):
            for size in range(min(max_words, len(words) - start), 0, -1):
                candidate = " ".join(words[start:start + size])
                if candidate not in index:
                    continue
                if start and len(candidate.replace(" ", "")) < BRAND_ANYWHERE_LENGTH:
                    continue
                return index[candidate]
    return None, None
