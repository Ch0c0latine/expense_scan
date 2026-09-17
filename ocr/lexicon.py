# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Reconnaissance de la catégorie d'une dépense à partir du texte du ticket.

Plusieurs sources, sans réseau ni modèle :

* les **mots du ticket** que chaque catégorie déclare (« nuitée »,
  « benzyna », « pedaggio »…), rapprochés avec une tolérance aux fautes de
  lecture ;
* les **enseignes déjà classées** : l'historique des dépenses, que le module
  Odoo fournit, et qui apprend des corrections des salariés ;
* les **codes d'activité** APE/NAF et MCC ;
* les **marques européennes** du Name Suggestion Index d'OpenStreetMap.

Les tickets arrivent de toute l'Europe : le texte est donc replié — sans
accent, sans casse, lettres polonaises ou nordiques ramenées à l'alphabet
latin — avant toute comparaison, et les mots par défaut couvrent le
français, l'anglais, l'allemand, l'italien, l'espagnol, le polonais, le
néerlandais et le portugais.

Aucune dépendance à Odoo, comme le reste du paquet.
"""
import json
import os
import re
import unicodedata
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Repliement du texte
# ---------------------------------------------------------------------------

#: Lettres que la décomposition Unicode ne ramène pas à l'alphabet latin.
EXTRA_FOLDS = str.maketrans({
    "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "đ": "d", "Đ": "d",
    "ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ı": "i",
})


def fold(text):
    """Texte comparable : minuscules, sans accent, mots séparés d'un espace."""
    text = (text or "").translate(EXTRA_FOLDS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def split_keywords(raw):
    """Les mots déclarés sur une catégorie : un par ligne, ou séparés par des virgules."""
    keywords = []
    for chunk in re.split(r"[\n,;]+", raw or ""):
        folded = fold(chunk)
        if folded and folded not in keywords:
            keywords.append(folded)
    return keywords


# ---------------------------------------------------------------------------
# Mots par défaut, par famille de frais
# ---------------------------------------------------------------------------

#: Mots proposés à la création des catégories. Ils ne servent qu'à remplir
#: une fiche vide : ensuite, c'est la fiche qui fait foi, et chacun y
#: ajoute ses enseignes. Les marques y figurent parce qu'elles sont souvent
#: la seule chose lisible d'un en-tête.
#:
#: « total » n'y est pas, bien que ce soit une marque de carburant : c'est
#: aussi le libellé du montant sur tous les tickets.
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
        # enseignes
        "airbnb", "booking.com", "ibis", "novotel", "mercure", "campanile", "kyriad",
        "première classe", "b&b hotel", "holiday inn", "best western", "marriott",
        "hilton", "radisson", "motel one", "logis hotels", "appart city",
    ],
    'meal': [
        # fr
        "restaurant", "brasserie", "couverts", "couvert", "menu", "plat du jour",
        # Mentions des titres-restaurant : un ticket qui les accepte est un repas.
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
        # enseignes
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
        # enseignes
        "totalenergies", "esso", "shell", "avia", "agip", "eni", "q8", "aral",
        "orlen", "lotos", "circle k", "tamoil", "repsol", "cepsa", "galp", "omv",
        "ionity", "fastned", "electra", "freshmile",
    ],
    'toll_parking': [
        # fr
        "péage", "autoroute", "autoroutes", "stationnement", "parking", "horodateur",
        # Mentions propres aux reçus de péage, qui n'impriment souvent pas
        # le mot « péage » — et dont l'OCR colle volontiers le sigle
        # (« ASFLieu-dit ») au mot suivant.
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
        # enseignes
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
        # enseignes
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
        # enseignes
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
        # enseignes
        "hertz", "avis budget", "europcar", "sixt", "enterprise rent", "ada",
        "getaround", "goldcar", "ucar", "leasys",
    ],
    'telecom': [
        "forfait mobile", "téléphonie", "roaming",
        "sfr", "orange sa", "bouygues telecom", "free mobile", "vodafone", "tim",
        "t-mobile", "telekom", "movistar", "play", "plus gsm",
    ],
}

#: Mots ajoutés après la première proposition, par version :
#: ``{'19.0.x.y.z': {famille: [mots]}}``. Le script de migration de la
#: version les ajoute aux fiches déjà remplies, sans rien y retirer.
ADDED_KEYWORDS = {}

#: Comment reconnaître, à son nom ou à sa référence, la catégorie qui
#: correspond à une famille. Premier indice trouvé, première famille servie.
FAMILY_HINTS = [
    ('lodging', ("heberg", "hotel", "bnb", "lodging", "accommodation")),
    ('taxi', ("taxi", "vtc", "mobilit", "mob")),
    ('train_air', ("train", "avion", "transport", "flight", "air")),
    ('fuel', ("carbur", "energie", "fuel", "essence", "elec")),
    ('toll_parking', ("peage", "parking", "park", "toll")),
    ('car_rental', ("location", "rental", "loc")),
    ('meal', ("repas", "meal", "restaur")),
    ('telecom', ("communication", "comm", "telecom", "telephon")),
]
#: Catégories qu'aucune famille ne réclame : les forfaits, et les repas
#: d'affaires, que rien sur le ticket ne distingue d'un repas ordinaire.
FAMILY_EXCLUDED = ("igd", "bareme", "forfait", "invit", "affaire", "kilomet", "mileage")


def family_of(*labels):
    """Famille de frais d'une catégorie, d'après son nom et sa référence."""
    words = fold(" ".join(label for label in labels if label)).split()
    if any(word.startswith(excluded) for word in words for excluded in FAMILY_EXCLUDED):
        return None
    for family, hints in FAMILY_HINTS:
        for hint in hints:
            # Indice court : mot entier, sans quoi « air » reconnaîtrait
            # « affaires » et « mob » n'importe quoi.
            if any(word == hint if len(hint) <= 4 else word.startswith(hint)
                   for word in words):
                return family
    return None


# ---------------------------------------------------------------------------
# Rapprochement
# ---------------------------------------------------------------------------

#: Seuil de ressemblance d'un mot mal lu avec un mot déclaré.
FUZZY_RATIO = 0.84
#: En deçà, un mot n'est comparé qu'à l'identique : « eni », « ada », « g7 »
#: ressemblent à trop de choses.
FUZZY_MIN_LENGTH = 5
#: Nombre de lignes considérées comme l'en-tête — là où l'enseigne s'imprime.
HEADER_LINES = 8
#: Poids d'un mot trouvé dans l'en-tête, puis ailleurs.
HEADER_WEIGHT = 2.0
BODY_WEIGHT = 1.0
#: Score minimal, et avance minimale sur la deuxième catégorie, pour trancher.
MIN_SCORE = 2.0
MIN_LEAD = 1.5


def similar(first, second):
    """Ressemblance de deux chaînes repliées, de 0 à 1."""
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def resembles(first, second, threshold):
    """Les deux chaînes se ressemblent-elles au moins à ce point ?

    Les bornes rapides de ``SequenceMatcher`` écartent l'immense majorité
    des couples avant le calcul complet : un ticket compte des centaines
    de mots, et chaque catégorie des dizaines de mots déclarés.
    """
    if not first or not second:
        return False
    matcher = SequenceMatcher(None, first, second)
    return (matcher.real_quick_ratio() >= threshold
            and matcher.quick_ratio() >= threshold
            and matcher.ratio() >= threshold)


def _find(keyword, line_words):
    """Le mot déclaré figure-t-il dans la ligne ? Tolère une faute de lecture."""
    parts = keyword.split()
    size = len(parts)
    for start in range(len(line_words) - size + 1):
        window = line_words[start:start + size]
        candidate = " ".join(window)
        if candidate == keyword:
            return True
        if len(keyword) >= FUZZY_MIN_LENGTH and abs(len(candidate) - len(keyword)) <= 2 \
                and resembles(candidate, keyword, FUZZY_RATIO):
            return True
    # Mot collé à un autre par l'OCR : « TOTALENERGIESSTATION », « IBISLYON ».
    if len(keyword) >= 6 and " " not in keyword:
        return any(keyword in word for word in line_words if len(word) > len(keyword))
    return False


def score_categories(lines, categories):
    """Score de chaque catégorie sur les lignes du ticket.

    ``categories`` associe un identifiant à ses mots repliés. Un mot ne
    compte qu'une fois par catégorie, au meilleur de ses emplacements : un
    « PARKING » répété dix fois en bas de ticket ne vaut pas mieux qu'une
    fois.
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
                    # Une expression de plusieurs mots — « classe tarif »,
                    # « taxe de séjour » — est assez parlante pour valoir,
                    # où qu'elle soit, un mot d'en-tête : sur un reçu de
                    # péage, elle n'arrive qu'à la neuvième ligne.
                    header = position < HEADER_LINES or " " in keyword
                    best = max(best, HEADER_WEIGHT if header else BODY_WEIGHT)
            if best:
                # Une expression de plusieurs mots est plus parlante qu'un
                # mot isolé.
                score += best * (1.25 if " " in keyword else 1.0)
        if score:
            scores[category] = score
    return scores


def pick_category(scores):
    """La catégorie qui l'emporte nettement, ou ``None``.

    Mieux vaut ne rien proposer qu'une catégorie douteuse : une case vide
    se remarque, une mauvaise catégorie passe en comptabilité.
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
# Enseignes connues
# ---------------------------------------------------------------------------

#: Ressemblance exigée entre une ligne d'en-tête et une enseigne connue.
MERCHANT_RATIO = 0.8
#: Ressemblance exigée, en plus, entre les premiers mots : le nom du commerce.
FIRST_WORD_RATIO = 0.75
#: Longueur minimale d'une enseigne rapprochable : en deçà, trop d'homonymes.
MERCHANT_MIN_LENGTH = 3


def merchant_key(name):
    """Clé de rapprochement d'une enseigne."""
    return fold(name)


def match_merchant(lines, known_keys, max_lines=HEADER_LINES + 4):
    """L'enseigne connue que l'en-tête du ticket désigne, ou ``None``.

    Compare chaque ligne du haut du ticket — et chaque groupe de mots de
    cette ligne — aux enseignes déjà rencontrées. C'est ce qui rattrape
    « Rchan » pour « Auchan », dès qu'un Auchan a été classé une fois.
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
                    # « Nord Villefranche d'Orbec » ressemble à « Dormizz
                    # Villefranche d'Orbec » par la ville seule : c'est le nom
                    # du commerce, en tête, qui doit se ressembler.
                    continue
                else:
                    ratio = similar(candidate, key)
                # Les premières lignes l'emportent à égalité.
                ranked = ratio - position * 0.001
                if ratio >= MERCHANT_RATIO and ranked > best[0]:
                    best = (ranked, key)
    return best[1]


# ---------------------------------------------------------------------------
# Codes d'activité : APE/NAF, NACE, MCC
# ---------------------------------------------------------------------------

#: Classe NAF rév. 2 (quatre premiers chiffres, ceux de la NACE européenne,
#: de l'ATECO italien ou du WZ allemand) → famille de frais. Les classes
#: de la NACE rév. 2.1, qui remplace peu à peu la précédente, y figurent
#: aussi quand elles diffèrent (56.11, 56.12, 56.40).
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

#: Codes MCC des reçus de carte → famille de frais.
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
#: Plages MCC réservées aux grandes enseignes d'un secteur.
MCC_RANGES = [
    (3000, 3351, 'train_air'),     # compagnies aériennes
    (3351, 3501, 'car_rental'),    # loueurs de voitures
    (3501, 4000, 'lodging'),       # chaînes hôtelières
]


def activity_family(code):
    """Famille de frais d'un code d'activité lu sur le ticket.

    ``code`` vaut ``"NAF:5610A"`` ou ``"MCC:5812"``, comme le rend le
    parseur ; ``None`` si le code ne désigne aucune famille connue.
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
# Marques européennes (Name Suggestion Index d'OpenStreetMap)
# ---------------------------------------------------------------------------

BRANDS_FILE = os.path.join(os.path.dirname(__file__), 'brands_europe.json')

#: Quand une marque relève de plusieurs familles — Esso vend du carburant et
#: des sandwichs, Indigo gère des parkings et des bornes —, la première de
#: cette liste l'emporte : la boutique d'une station reste une station.
BRAND_FAMILY_PRIORITY = ('lodging', 'toll_parking', 'car_rental', 'taxi', 'fuel', 'meal')

#: Marques de moins de quatre lettres admises malgré tout : les autres
#: (« ed », « me », « bp », qui est aussi la boîte postale) désignent
#: n'importe quoi.
BRAND_SHORT_ALLOWED = {'q8', 'omv', 'kfc', 'ada', 'eni', 'erg', 'jet', 'ina', 'mol', 'dia'}

#: Marques qui sont aussi des mots courants d'un ticket, d'une adresse ou
#: d'un prénom, et que l'on ne cherche donc pas.
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
    # Noms longs, cherchés aussi en milieu de ligne, qui sont des mots.
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
#: Longueur (sans espaces) à partir de laquelle une marque est assez
#: distinctive pour être cherchée partout dans une ligne d'en-tête :
#: « … CCIAL V2 Restaurant McDonald's ».
BRAND_ANYWHERE_LENGTH = 7

_BRAND_INDEX = None


def _latin_share(name):
    """Part des lettres d'un nom qui s'écrivent en alphabet latin."""
    letters = [char for char in name if char.isalpha()]
    if not letters:
        return 0.0
    latin = [char for char in letters if fold(char)]
    return len(latin) / float(len(letters))


def _nicer(name, other):
    """Des deux écritures d'une marque, celle qui s'affiche le mieux."""
    def rank(value):
        # « McDonald's » plutôt que « MCDONALD'S » ou « mcdonald's ».
        return (value != value.upper() and value != value.lower(), -len(value))
    return name if rank(name) > rank(other) else other


def brand_index():
    """``{marque repliée: (famille, nom affiché)}``, chargé une fois pour toutes."""
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
            # Écritures grecques, cyrilliques ou asiatiques : l'OCR ne les
            # lit pas, et leur repli ne laisserait que des débris.
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


def brand_family(lines, max_lines=HEADER_LINES, max_words=4):
    """``(famille, marque affichée)`` d'après l'en-tête, ou ``(None, None)``.

    Une marque courte doit ouvrir une ligne de l'en-tête ; une marque
    longue peut y figurer n'importe où. Toujours telle quelle : une base de
    milliers de noms, dont beaucoup ressemblent à des mots, ne supporte pas
    l'approximation.
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
