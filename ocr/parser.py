# -*- coding: utf-8 -*-
"""Extraction des champs d'un ticket de caisse français.

Le parseur travaille sur des mots **situés** (et non sur un bloc de texte),
ce qui permet de reconstruire les lignes puis de raisonner ligne par ligne :
sur un ticket, le montant qui compte est presque toujours celui qui est
imprimé à droite d'un libellé donné.

Aucune dépendance à Odoo : ce fichier se teste avec un simple interpréteur
Python, ce qui rend les réglages de mots-clés faciles à faire évoluer.
"""
import re
import unicodedata
from datetime import date, timedelta

from .types import ExtractedField, OcrLine, ScanResult

# ---------------------------------------------------------------------------
# Montants
# ---------------------------------------------------------------------------

# Un montant a toujours deux décimales sur un ticket. On accepte le point et
# la virgule, et les séparateurs de milliers usuels (espace, espace insécable,
# point). Le lookahead écarte « 20,00 % » qui est un taux, pas un montant.
AMOUNT_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[  .]\d{3})+|\d+)[.,](\d{2})(?![\d])(?!\s*%)"
)
# Un taux de TVA : « 20 % », « 5,50% », « TVA 10.0 »
RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])\s*[:hH]\s*([0-5]\d)\b")
VAT_NUMBER_RE = re.compile(r"\bFR\s?([0-9A-Z]{2})\s?(\d{3})\s?(\d{3})\s?(\d{3})\b")

# Plafond de vraisemblance : au-delà, c'est un numéro (SIRET, carte, code)
# lu comme un montant, pas le total d'un ticket de caisse.
MAX_PLAUSIBLE_AMOUNT = 100000.0


def strip_accents(text):
    """Supprime les accents : l'OCR les rend de façon peu fiable."""
    normalized = unicodedata.normalize("NFD", text)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def normalize(text):
    """Forme normalisée d'une ligne pour la recherche de mots-clés."""
    text = strip_accents(text or "").upper()
    # L'OCR confond volontiers O et 0 dans les libellés ; on ne touche qu'aux
    # séparateurs pour ne pas abîmer les montants.
    return re.sub(r"[^A-Z0-9%.,:/\- ]+", " ", text)


def parse_amount(integer_part, decimal_part):
    """Convertit les deux groupes d'un montant capturé en flottant."""
    cleaned = re.sub(r"[  .]", "", integer_part)
    try:
        return float("%s.%s" % (cleaned, decimal_part))
    except ValueError:
        return None


def find_amounts(text):
    """Tous les montants d'une ligne, dans l'ordre d'apparition."""
    amounts = []
    for match in AMOUNT_RE.finditer(text):
        value = parse_amount(match.group(1), match.group(2))
        if value is not None and 0 < value <= MAX_PLAUSIBLE_AMOUNT:
            amounts.append((value, match.start()))
    return amounts


# ---------------------------------------------------------------------------
# Reconstruction des lignes
# ---------------------------------------------------------------------------

def build_lines(words, tolerance_ratio=0.6):
    """Regroupe les mots en lignes par recouvrement vertical.

    Deux mots appartiennent à la même ligne si leurs centres verticaux sont
    distants de moins de ``tolerance_ratio`` fois la hauteur du mot. Ce
    critère relatif encaisse les tickets où le nom du magasin est imprimé
    deux fois plus grand que le corps du texte.
    """
    remaining = sorted((w for w in words if w.text.strip()), key=lambda w: (w.top, w.left))
    lines = []
    for word in remaining:
        placed = False
        for line in lines:
            tolerance = tolerance_ratio * max(word.height, line.words[0].height)
            if abs(line.center_y - word.center_y) <= tolerance:
                line.words.append(word)
                placed = True
                break
        if not placed:
            lines.append(OcrLine(words=[word]))

    for line in lines:
        line.words.sort(key=lambda w: w.left)
    lines.sort(key=lambda line: line.top)
    return lines


# ---------------------------------------------------------------------------
# Total
# ---------------------------------------------------------------------------

# Mots-clés du total, du plus au moins spécifique. La priorité tranche les
# tickets qui impriment plusieurs « totaux » (sous-total, total HT, total).
TOTAL_KEYWORDS = [
    (re.compile(r"\bNET\s*A\s*PAYER\b"), 0.95),
    (re.compile(r"\bTOTAL\s*T\.?\s*T\.?\s*C\b"), 0.93),
    (re.compile(r"\bMONTANT\s*(?:DU|A\s*PAYER)\b"), 0.90),
    (re.compile(r"\bRESTE\s*A\s*PAYER\b"), 0.88),
    (re.compile(r"\bA\s*PAYER\b"), 0.86),
    (re.compile(r"\bTOTAL\b"), 0.80),
    (re.compile(r"\bMONTANT\b"), 0.70),
    (re.compile(r"\bCARTE\s*BANCAIRE\b|\bCB\b|\bSANS\s*CONTACT\b"), 0.60),
    (re.compile(r"\bESPECES\b|\bCHEQUE\b"), 0.55),
]
# Une ligne contenant l'un de ces termes n'est jamais le total à retenir.
TOTAL_EXCLUDE_RE = re.compile(
    r"\bSOUS\s*[- ]?\s*TOTAL\b|\bTOTAL\s*H\.?\s*T\b|\bTVA\b|\bT\.V\.A\b|"
    r"\bRENDU\b|\bMONNAIE\b|\bRECU\b|\bREMISE\b|\bECONOMIE\b|\bAVANTAGE\b|"
    r"\bCAGNOTTE\b|\bFIDELITE\b|\bPOINTS?\b|\bSOLDE\b|\bDONT\b|\bACOMPTE\b"
)


def extract_total(lines):
    """Le montant total payé, avec sa fiabilité."""
    best = None
    for index, line in enumerate(lines):
        text = normalize(line.text)
        if TOTAL_EXCLUDE_RE.search(text):
            continue
        for pattern, weight in TOTAL_KEYWORDS:
            if not pattern.search(text):
                continue
            amounts = find_amounts(line.text)
            confidence_penalty = 1.0
            if not amounts and index + 1 < len(lines):
                # Beaucoup de tickets impriment le libellé et le montant sur
                # deux lignes distinctes lorsque la colonne est étroite.
                amounts = find_amounts(lines[index + 1].text)
                confidence_penalty = 0.85
            if not amounts:
                continue
            # Le montant utile est le dernier de la ligne : à gauche on
            # trouve souvent une quantité ou un prix unitaire.
            value = amounts[-1][0]
            confidence = weight * confidence_penalty * max(line.score, 0.4)
            candidate = (weight, value, confidence, line.text)
            if best is None or candidate[0] > best[0] or (
                candidate[0] == best[0] and value > best[1]
            ):
                best = candidate
            break

    if best is not None:
        return ExtractedField(value=best[1], confidence=min(best[2], 0.99), source=best[3])

    # Dernier recours : le plus gros montant du bas du ticket. Peu fiable,
    # mais préférable à un champ vide que l'utilisateur devra saisir.
    tail = lines[int(len(lines) * 0.55):] if lines else []
    fallback = [(value, line) for line in tail for value, _ in find_amounts(line.text)
                if not TOTAL_EXCLUDE_RE.search(normalize(line.text))]
    if fallback:
        value, line = max(fallback, key=lambda item: item[0])
        return ExtractedField(value=value, confidence=0.35, source=line.text)
    return ExtractedField(value=None, confidence=0.0)


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

MONTHS_FR = {
    "JANV": 1, "JAN": 1, "FEVR": 2, "FEV": 2, "MARS": 3, "MAR": 3,
    "AVRIL": 4, "AVR": 4, "MAI": 5, "JUIN": 6, "JUIL": 7, "JUILLET": 7,
    "AOUT": 8, "AOU": 8, "SEPT": 9, "SEP": 9, "OCT": 10, "OCTO": 10,
    "NOV": 11, "NOVE": 11, "DEC": 12, "DECE": 12,
}

DATE_PATTERNS = [
    # 04/09/2026 - 04-09-2026 - 04.09.2026
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b"), "dmy", 0.90),
    # 2026-09-04
    (re.compile(r"\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b"), "ymd", 0.90),
    # 04/09/26
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2})\b"), "dmy2", 0.75),
    # 4 SEPT 2026 - 23 SEPTEMBRE 2026
    (re.compile(r"\b(\d{1,2})\s+([A-Z]{3,10})\.?\s+(\d{4})\b"), "dmonthy", 0.85),
    # Sans millésime : « 04/09 », « 23 SEPTEMBRE ». Les lookaheads écartent
    # les dates complètes, déjà captées par les motifs précédents.
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})(?![/.\-]?\d)"), "dm", 0.50),
    (re.compile(r"\b(\d{1,2})\s+([A-Z]{3,10})\b(?!\.?\s+\d{4})"), "dmonth", 0.50),
]
#: Motifs dont l'année est devinée, et non lue.
INFERRED_YEAR_KINDS = frozenset({"dm", "dmonth"})
#: Un ticket qui n'imprime pas son millésime est forcément récent. Au-delà,
#: l'inférence est refusée plutôt que de dater la dépense d'un an en arrière.
NO_YEAR_MAX_AGE_DAYS = 120
DATE_KEYWORD_RE = re.compile(r"\bDATE\b|\bLE\b\s|\bCAISSE\b|\bTICKET\b")


def _month_number(name):
    """Numéro du mois à partir de son nom, abrégé ou non."""
    cleaned = strip_accents(name).upper()
    return MONTHS_FR.get(cleaned[:4]) or MONTHS_FR.get(cleaned[:3])


def _infer_year(day, month, today):
    """Occurrence la plus récente de ce jour/mois déjà passée."""
    for year in (today.year, today.year - 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            return candidate
    return None


def _build_date(kind, groups, today):
    try:
        if kind == "dmy":
            day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == "ymd":
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == "dmy2":
            day, month = int(groups[0]), int(groups[1])
            year = 2000 + int(groups[2])
        elif kind == "dmonthy":
            day = int(groups[0])
            month = _month_number(groups[1])
            year = int(groups[2])
            if not month:
                return None
        elif kind == "dm":
            return _infer_year(int(groups[0]), int(groups[1]), today)
        elif kind == "dmonth":
            month = _month_number(groups[1])
            return _infer_year(int(groups[0]), month, today) if month else None
        else:
            return None
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def extract_date(lines, today=None, max_age_days=730):
    """La date d'achat, choisie parmi les dates plausibles du ticket."""
    today = today or date.today()
    oldest = today - timedelta(days=max_age_days)
    newest = today + timedelta(days=1)  # tolérance fuseau horaire

    candidates = []
    for position, line in enumerate(lines):
        text = normalize(line.text)
        for pattern, kind, weight in DATE_PATTERNS:
            for match in pattern.finditer(text):
                found = _build_date(kind, match.groups(), today)
                if not found or not (oldest <= found <= newest):
                    continue
                if kind in INFERRED_YEAR_KINDS and (today - found).days > NO_YEAR_MAX_AGE_DAYS:
                    # Année devinée trop lointaine : c'est probablement une
                    # date de voyage ou d'échéance, pas la date d'achat.
                    continue
                confidence = weight * max(line.score, 0.4)
                # Une date accompagnée d'une heure ou du mot « date » est
                # presque toujours celle de l'achat, pas une date de validité
                # de carte de fidélité ou une échéance de garantie.
                if TIME_RE.search(text) or DATE_KEYWORD_RE.search(text):
                    confidence = min(confidence * 1.15, 0.99)
                # Le haut du ticket est plus souvent l'en-tête daté.
                if position < max(len(lines) * 0.35, 6):
                    confidence = min(confidence * 1.05, 0.99)
                candidates.append((confidence, found, line.text))

    if not candidates:
        return ExtractedField(value=None, confidence=0.0)
    confidence, found, source = max(candidates, key=lambda item: item[0])
    return ExtractedField(value=found, confidence=confidence, source=source)


# ---------------------------------------------------------------------------
# Enseigne
# ---------------------------------------------------------------------------

MERCHANT_STOP_RE = re.compile(
    r"\bTICKET\b|\bFACTURE\b|\bRECU\b|\bDUPLICATA\b|\bCAISSE\b|\bVENDEUR\b|"
    r"\bMERCI\b|\bBIENVENUE\b|\bBONJOUR\b|\bTEL\b|\bTELEPHONE\b|\bSIRET\b|"
    r"\bSIREN\b|\bTVA\b|\bRCS\b|\bAPE\b|\bNAF\b|\bMAGASIN\b|\bADRESSE\b|"
    r"\bWWW\b|\bHTTP\b|\bEMAIL\b|\bMAIL\b|\bHORAIRES?\b|\bCLIENT\b"
)
ADDRESS_RE = re.compile(
    r"\b(RUE|AVENUE|AV|BOULEVARD|BD|PLACE|PL|CHEMIN|ROUTE|RTE|IMPASSE|ALLEE|"
    r"QUAI|ZAC|ZI|CEDEX|BP)\b"
)


def extract_merchant(lines, max_lines=6):
    """Le nom de l'enseigne, cherché dans l'en-tête du ticket."""
    best = None
    for position, line in enumerate(lines[:max_lines]):
        raw = line.text.strip()
        text = normalize(raw)
        letters = sum(1 for char in text if char.isalpha())
        digits = sum(1 for char in text if char.isdigit())
        if letters < 3 or len(raw) < 3:
            continue
        if MERCHANT_STOP_RE.search(text) or ADDRESS_RE.search(text):
            continue
        if digits > 2 or digits > letters / 2:
            continue  # code postal, téléphone, numéro de caisse
        # Plus la ligne est haute et riche en lettres, plus c'est l'enseigne.
        weight = (0.85 - 0.08 * position) * min(1.0, 0.4 + letters / 18.0)
        confidence = weight * max(line.score, 0.4)
        if best is None or confidence > best[0]:
            best = (confidence, raw)

    if best is None:
        return ExtractedField(value=None, confidence=0.0)
    confidence, raw = best
    name = re.sub(r"\s{2,}", " ", raw).strip(" -*:.")
    if name.isupper() and len(name) > 3:
        name = name.title()
    return ExtractedField(value=name, confidence=min(confidence, 0.95), source=raw)


# ---------------------------------------------------------------------------
# TVA, devise, divers
# ---------------------------------------------------------------------------

TVA_LINE_RE = re.compile(r"\bT\.?\s*V\.?\s*A\b")
# Taux de TVA en vigueur en France ; un taux lu hors de cette liste est
# presque toujours une erreur de lecture.
KNOWN_RATES = (20.0, 10.0, 5.5, 2.1, 8.5, 0.0)
CURRENCIES = [
    (re.compile(r"€|\bEUR\b|\bEUROS?\b"), "EUR"),
    (re.compile(r"\bCHF\b|\bFRANCS?\s*SUISSES?\b"), "CHF"),
    (re.compile(r"\bGBP\b|£"), "GBP"),
    (re.compile(r"\bUSD\b|\$"), "USD"),
]


def _closest_known_rate(value):
    closest = min(KNOWN_RATES, key=lambda rate: abs(rate - value))
    return closest if abs(closest - value) <= 0.35 else None


def extract_taxes(lines):
    """Taux et montant de TVA, quand le ticket les détaille."""
    entries = []
    for line in lines:
        text = normalize(line.text)
        if not TVA_LINE_RE.search(text):
            continue
        rate_match = RATE_RE.search(text)
        rate = None
        if rate_match:
            rate = _closest_known_rate(float(rate_match.group(1).replace(",", ".")))
        amounts = find_amounts(line.text)
        # Format courant : « TVA 20,00% <base> <montant> ». Le montant de
        # taxe est le dernier de la ligne.
        amount = amounts[-1][0] if amounts else None
        if rate is None and amount is None:
            continue
        entries.append((rate, amount, line.text, line.score))

    if not entries:
        return ExtractedField(value=None, confidence=0.0), ExtractedField(value=None, confidence=0.0)

    rates = [entry[0] for entry in entries if entry[0] is not None]
    amounts = [entry[1] for entry in entries if entry[1] is not None]
    score = max((entry[3] for entry in entries), default=0.5)
    source = " | ".join(entry[2] for entry in entries)

    rate_field = ExtractedField(value=None, confidence=0.0)
    if len(set(rates)) == 1:
        rate_field = ExtractedField(value=rates[0], confidence=min(0.9 * max(score, 0.4), 0.95),
                                    source=source)
    elif rates:
        # Plusieurs taux sur le même ticket : on ne peut pas en choisir un
        # seul pour la dépense, on remonte l'information sans l'appliquer.
        rate_field = ExtractedField(value=None, confidence=0.0, source=source)

    amount_field = ExtractedField(value=None, confidence=0.0)
    if amounts:
        amount_field = ExtractedField(value=round(sum(amounts), 2),
                                      confidence=min(0.8 * max(score, 0.4), 0.9),
                                      source=source)
    return rate_field, amount_field


def extract_currency(lines, default="EUR"):
    """Devise du ticket, déduite du symbole ou du code imprimé."""
    joined = normalize(" ".join(line.text for line in lines))
    raw = " ".join(line.text for line in lines)
    for pattern, code in CURRENCIES:
        if pattern.search(joined) or pattern.search(raw):
            return ExtractedField(value=code, confidence=0.85)
    return ExtractedField(value=default, confidence=0.3)


def extract_vat_number(lines):
    """Numéro de TVA intracommunautaire, utile pour retrouver le fournisseur."""
    for line in lines:
        text = normalize(line.text)
        match = VAT_NUMBER_RE.search(text)
        if match:
            return ExtractedField(value="FR" + "".join(match.groups()),
                                  confidence=0.8, source=line.text)
    return ExtractedField(value=None, confidence=0.0)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

#: Libellés affichés à l'utilisateur pour les champs à revérifier.
FIELD_LABELS = {
    "merchant": "Marchand",
    "date": "Date",
    "total": "Total",
    "currency": "Devise",
    "tax_rate": "Taux de TVA",
    "tax_amount": "Montant de TVA",
    "vat_number": "N° de TVA",
}
#: En dessous de ce seuil, le champ est signalé comme « à vérifier ».
LOW_CONFIDENCE = 0.65


def parse(words, today=None, max_age_days=730, default_currency="EUR"):
    """Analyse une liste de mots situés et renvoie un :class:`ScanResult`."""
    lines = build_lines(words)
    tax_rate, tax_amount = extract_taxes(lines)
    fields = {
        "merchant": extract_merchant(lines),
        "date": extract_date(lines, today=today, max_age_days=max_age_days),
        "total": extract_total(lines),
        "currency": extract_currency(lines, default=default_currency),
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "vat_number": extract_vat_number(lines),
    }
    return ScanResult(lines=lines, fields=fields)


def fields_to_check(result, names=("merchant", "date", "total")):
    """Libellés des champs absents ou peu fiables, à faire relire."""
    todo = []
    for name in names:
        field = result.fields.get(name)
        if field is None or field.value is None or field.confidence < LOW_CONFIDENCE:
            todo.append(FIELD_LABELS.get(name, name))
    return todo
