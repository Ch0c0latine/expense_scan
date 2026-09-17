# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Extraction des champs d'un ticket de caisse européen.

Français d'abord, mais aussi anglais, allemand, italien, espagnol,
polonais, néerlandais et portugais : les libellés du total, de la TVA et
des mois de chaque langue sont reconnus, ainsi que les taux de TVA en
vigueur dans l'Union et les principales devises du continent.

Le parseur travaille sur des mots **situés** (et non sur un bloc de texte),
ce qui permet de reconstruire les lignes puis de raisonner ligne par ligne :
sur un ticket, le montant qui compte est presque toujours celui qui est
imprimé à droite d'un libellé donné.

Aucune dépendance à Odoo : ce fichier se teste avec un simple interpréteur
Python, ce qui rend les réglages de mots-clés faciles à faire évoluer.
"""
import re
import unicodedata
from datetime import date, time as dtime, timedelta

from .types import ExtractedField, OcrLine, ScanResult

# ---------------------------------------------------------------------------
# Montants
# ---------------------------------------------------------------------------

# Un montant a toujours deux décimales sur un ticket. On accepte le point et
# la virgule, ainsi que les séparateurs de milliers usuels (espace, espace
# insécable, point). Le lookahead final écarte « 20,00 % », qui est un taux.
#
# Le lookbehind ne rejette qu'un chiffre ou une virgule, surtout pas un
# point : d'innombrables tickets alignent leurs colonnes avec des points de
# conduite — « PRIX TTC......6,80 » — et interdire le point précédent
# revenait à n'y reconnaître aucun montant. Le cas « 1.234,56 » reste
# couvert : l'alternative des milliers est tentée en premier et consomme le
# nombre entier avant qu'on puisse en attaquer la fin.
AMOUNT_RE = re.compile(
    r"(?<![\d,])(\d{1,3}(?:[  .]\d{3})+|\d+)[.,](\d{2})(?![\d])(?!\s*%)"
)
# Un taux de TVA : « 20 % », « 5,50% », « TVA 10.0 »
RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
# L'heure peut suivre l'année sans espace : « 13/06/202616:30:16 ».
TIME_RE = re.compile(r"(?:\b|(?<=[12]\d{3}))([01]?\d|2[0-3])\s*[:hH]\s*([0-5]\d)\b")
VAT_NUMBER_RE = re.compile(r"\bFR\s?([0-9A-Z]{2})\s?(\d{3})\s?(\d{3})\s?(\d{3})\b")

# Plafond de vraisemblance : au-delà, c'est un numéro (SIRET, carte, code)
# lu comme un montant, pas le total d'un ticket de caisse.
MAX_PLAUSIBLE_AMOUNT = 100000.0


#: Lettres qu'aucune décomposition Unicode ne ramène à l'alphabet latin :
#: sans elles, « ZAPŁATY » deviendrait « ZAP ATY ».
EXTRA_LETTERS = str.maketrans({
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
})


def strip_accents(text):
    """Supprime les accents : l'OCR les rend de façon peu fiable."""
    normalized = unicodedata.normalize("NFD", text.translate(EXTRA_LETTERS))
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
    (re.compile(r"\bDO\s*ZAPLATY\b"), 0.95),                        # pl
    (re.compile(r"\bTOTAL\s*T\.?\s*T\.?\s*C\b"), 0.93),
    (re.compile(r"\bTOTALE\s*(?:COMPLESSIVO|DOCUMENTO)\b"), 0.93),  # it
    (re.compile(r"\bZU\s*ZAHLEN\b"), 0.93),                         # de
    (re.compile(r"\bTOTAL\s*A\s*PAGAR\b"), 0.93),                   # es, pt
    # « PRIX TTC » est le libellé des tickets de péage, de carburant et de
    # nombreux automates : aussi décisif qu'un « TOTAL TTC ».
    (re.compile(r"\bPRIX\s*T\.?\s*T\.?\s*C\b"), 0.92),
    (re.compile(r"\bMONTANT\s*(?:DU|A\s*PAYER)\b"), 0.90),
    (re.compile(r"\b(?:AMOUNT|BALANCE)\s*DUE\b|\bGRAND\s*TOTAL\b"), 0.90),  # en
    (re.compile(r"\bTE\s*BETALEN\b"), 0.90),                        # nl
    (re.compile(r"\bRESTE\s*A\s*PAYER\b"), 0.88),
    (re.compile(r"\bIMPORTO\s*PAGATO\b"), 0.88),                    # it
    (re.compile(r"\bA\s*PAYER\b"), 0.86),
    (re.compile(r"\bSUMA\b|\bSUMME\b"), 0.85),                      # pl, de
    (re.compile(r"\bGESAMT(?:BETRAG)?\b"), 0.82),                   # de
    (re.compile(r"\bTOTAL\b|\bTOTALE\b|\bTOTAAL\b"), 0.80),
    (re.compile(r"\bRAZEM\b"), 0.75),                               # pl
    (re.compile(r"\bMONTANT\b|\bIMPORTO\b|\bIMPORTE\b|\bBETRAG\b|\bBEDRAG\b"), 0.70),
    (re.compile(r"\bPAIEMENT\b|\bREGLEMENT\b|\bPAGAMENTO\b|\bPLATNOSC\b"
                r"|\bKARTENZAHLUNG\b|\bPAYMENT\b"), 0.65),
    (re.compile(r"\bCARTE\s*BANCAIRE\b|\bCB\b|\bSANS\s*CONTACT\b|\bKARTA\b"), 0.60),
    (re.compile(r"\bESPECES\b|\bCHEQUE\b|\bCONTANT[EI]\b|\bEFECTIVO\b|\bGOTOWKA\b"
                r"|\bCASH\b"), 0.55),
]
# Une ligne contenant l'un de ces termes n'est jamais le total à retenir.
#
# Les mentions de TVA étrangères sont écartées comme « TVA », sauf quand le
# total les dit incluses : « TOTALE IVA INCLUSA », « SUMME INKL. MWST ».
TOTAL_EXCLUDE_RE = re.compile(
    r"\bSOUS\s*[- ]?\s*TOTAL\b|\bSUB\s*[- ]?\s*TOTAL\b|\bSUBTOTALE?\b|"
    r"\bZWISCHENSUMME\b|\bTOTAL\s*H\.?\s*T\b|\bPRIX\s*H\.?\s*T\b|"
    r"\bMONTANT\s*H\.?\s*T\b|\bTVA\b|\bT\.V\.A\b|"
    r"\bIVA\b(?!\s*INCL)|(?<!INKL\s)(?<!INKL\.\s)\b(?:MWST|UST)\b|"
    r"\bVAT\b(?!\s*INCL)|\bBTW\b(?!\s*INCL)|\bPTU\b|\bOPOD|\bNETTO\b|"
    r"\bIMPONIBILE\b|\bBASE\s*IMPONIBLE\b|\bDI\s*CUI\b|"
    r"\bRENDU\b|\bMONNAIE\b|\bRECU\b|\bREMISE\b|\bECONOMIE\b|\bAVANTAGE\b|"
    r"\bCAGNOTTE\b|\bFIDELITE\b|\bPOINTS?\b|\bSOLDE\b|\bDONT\b|\bACOMPTE\b|"
    r"\bRESTO\b|\bRESZTA\b|\bRUCKGELD\b|\bWECHSELGELD\b|\bGEGEBEN\b|"
    r"\bCAMBIO\b|\bWISSELGELD\b|\bCHANGE\b|\bSCONTO\b|\bRABATT?\b|\bDESCUENTO\b"
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
#: Mois des autres langues du continent, par leurs trois premières lettres
#: (sans accent) : anglais, allemand, italien, espagnol, polonais,
#: néerlandais, portugais. Aucun ne contredit un mois français.
MONTHS_OTHER = {
    "FEB": 2, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,   # en, de
    "OKT": 10, "DEZ": 12, "MRT": 3, "MEI": 5,                      # de, nl
    "GEN": 1, "MAG": 5, "GIU": 6, "LUG": 7, "AGO": 8, "SET": 9,    # it, es, pt
    "OTT": 10, "DIC": 12, "ENE": 1, "ABR": 4,
    "STY": 1, "LUT": 2, "KWI": 4, "MAJ": 5, "CZE": 6, "LIP": 7,   # pl
    "SIE": 8, "WRZ": 9, "PAZ": 10, "LIS": 11, "GRU": 12,
}

DATE_PATTERNS = [
    # 04/09/2026 - 04-09-2026 - 04.09.2026
    # « 13/06/202616:30:16 » : certains terminaux collent l'heure à l'année.
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?:\b|(?=\d{1,2}:\d{2}))"), "dmy", 0.90),
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
DATE_KEYWORD_RE = re.compile(
    r"\bDATE\b|\bLE\b\s|\bCAISSE\b|\bTICKET\b|\bDATA\b|\bDATUM\b|\bFECHA\b")


def _month_number(name):
    """Numéro du mois à partir de son nom, abrégé ou non."""
    cleaned = strip_accents(name).upper()
    return (MONTHS_FR.get(cleaned[:4]) or MONTHS_FR.get(cleaned[:3])
            or MONTHS_OTHER.get(cleaned[:3]))


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
    r"\bWWW\b|\bHTTP\b|\bEMAIL\b|\bMAIL\b|\bHORAIRES?\b|\bCLIENT\b|"
    # Champs d'un ticket de transport ou de péage : ils occupent le haut du
    # ticket et se feraient volontiers passer pour une enseigne.
    r"\bSORTIE\b|\bENTREE\b|\bDEPART\b|\bARRIVEE\b|\bCLASSE\b|\bTARIF\b|"
    r"\bPAIEMENT\b|\bREGLEMENT\b|\bCARTE\b|\bMONTANT\b|\bTOTAL\b|"
    # En-tête d'un ticket de carte bancaire : l'enseigne y figure bien plus
    # bas, après le bloc de la banque et du terminal.
    r"\bCONTACT\b|\bBANQUE\b|\bBANCAIRE\b|\bMASTERCARD\b|\bVISA\b|"
    r"\bDEBIT\b|\bCREDIT\b|\bAUTO\b|\bCONSERVER\b|\bTERMINAL\b|"
    # Mentions d'en-tête étrangères : ticket fiscal, identifiant fiscal,
    # formules d'accueil.
    r"\bPARAGON\b|\bFISKALNY\b|\bNIP\b|\bREGON\b|\bSCONTRINO\b|\bFISCALE\b|"
    r"\bDOCUMENTO\b|\bCOMMERCIALE\b|\bPARTITA\b|\bP\.?\s*IVA\b|\bRECHNUNG\b|"
    r"\bKASSENBON\b|\bBELEG\b|\bQUITTUNG\b|\bSTEUER|\bFACTURA\b|\bRECIBO\b|"
    r"\bCIF\b|\bNIF\b|\bRECEIPT\b|\bINVOICE\b|\bTHANK\b|\bWELCOME\b|"
    r"\bGRAZIE\b|\bDANKE\b|\bDZIEKUJEMY\b|\bGRACIAS\b|\bKASA\b|\bKASSE\b|"
    r"\bCASSA\b|\bCAJA\b|\bVAT\b|\bIVA\b|\bMWST\b|\bUST\b|"
    # En-têtes de colonnes et pieds de liste, qui ne sont jamais une enseigne.
    r"\bARTICLES?\b|\bQTE\b|\bQUANTITE\b|\bDESIGNATION\b|\bPRODUITS?\b|"
    r"\bLIBELLE\b|\bNOMBRE\b|\bLIGNES?\b|\bVENTES?\b"
)
ADDRESS_RE = re.compile(
    r"\b(RUE|AVENUE|AV|BOULEVARD|BD|PLACE|PL|CHEMIN|ROUTE|RTE|IMPASSE|ALLEE|"
    r"QUAI|ZAC|ZI|CEDEX|BP|"
    # Voies étrangères : ul. (pl), via, viale, piazza, corso (it), Straße,
    # Platz (de), calle, plaza (es), rua (pt), street, road (en).
    r"UL|ULICA|ALEJA|VIA|VIALE|PIAZZA|CORSO|STRASSE|STR|PLATZ|WEG|"
    r"CALLE|AVENIDA|PLAZA|RUA|STREET|ROAD|STRAAT)\b"
)


def _has_date(text, today=None):
    """La ligne normalisée porte-t-elle une date plausible ?"""
    today = today or date.today()
    for pattern, kind, _weight in DATE_PATTERNS:
        for match in pattern.finditer(text):
            if _build_date(kind, match.groups(), today):
                return True
    return False


MERCHANT_PREFIX_RE = re.compile(
    r"^\s*(?:[ÉEée]tablissement|[Cc]ommer[çc]ant|[Mm]archand|[Ee]nseigne|"
    r"[Ss]oci[ée]t[ée]|[Mm]erchant|[Ss]tore|[Hh]ändler|[Ee]sercente|"
    r"[Cc]omercio|[Ss]klep)\b\s*:?\s*",
    re.IGNORECASE)


def extract_merchant(lines, max_lines=10):
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
        if _has_date(text):
            continue  # « ← 18 novembre » : une date, pas une enseigne
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
    # « Établissement DORMIZZ », « Société : POPEYES » : le libellé qui
    # précède l'enseigne sur un reçu de carte n'en fait pas partie.
    name = MERCHANT_PREFIX_RE.sub("", name).strip(" -*:.") or name
    name = re.sub(r"^[^\w]+", "", name) or name  # « ← », « * » d'un logo
    if name.isupper() and len(name) > 3:
        name = name.title()
    return ExtractedField(value=name, confidence=min(confidence, 0.95), source=raw)


# ---------------------------------------------------------------------------
# TVA, devise, divers
# ---------------------------------------------------------------------------

#: Libellé d'une ligne de TVA, dans les langues du continent : TVA, VAT,
#: IVA (it, es, pt), MwSt et USt (de), PTU (pl), BTW (nl), DPH (cs), MOMS.
TVA_LINE_RE = re.compile(
    r"\bT\.?\s*V\.?\s*A\b|\bV\.?A\.?T\b|\bI\.?V\.?A\b|\bMWST\b|\bUST\b|\bPTU\b"
    r"|\bB\.?T\.?W\b|\bDPH\b|\bMOMS\b|\bPODATEK\b")
#: « TVA:D » — code de taux renvoyant au tableau, sans montant de taxe.
TAX_CODE_RE = re.compile(r"\bT\.?\s*V\.?\s*A\s*:\s*[A-Z]\b")
#: Montant nul, que la recherche des montants ignore à dessein.
ZERO_AMOUNT_RE = re.compile(r"(?<![\d,.])0[.,]00(?!\d)")
#: Ligne qui donne la base taxable, non la taxe, malgré son libellé.
TAX_BASE_RE = re.compile(
    r"\bOPOD|\bNETTO\b|\bIMPONIBILE\b|\bBASE\s*IMPONIBLE\b|\bSPRZED|"
    r"\b(?:TOTAL|BASE|MONTANT)\s*H\.?\s*T\b")
#: Ligne qui totalise la TVA plutôt que d'en donner un taux.
TAX_SUM_RE = re.compile(
    r"\b(?:SUMA|TOTAL|TOTALE|SUMME|TOTAAL|RAZEM|GESAMT)\s*(?:DE\s*LA\s*|DI\s*)?"
    r"(?:T\.?\s*V\.?\s*A|PTU|IVA|VAT|MWST|UST|BTW|PODATEK)\b"
    r"|\bTOTAL\s*TAX\b|\bPODATEK\s*PTU\b"
    # « Montant TVA 10,00 € » : le total d'un tableau mis en colonnes.
    r"|\bMONTANT\s*(?:DE\s*LA\s*|DE\s*)?T\.?\s*V\.?\s*A\b")
# Taux de TVA en vigueur dans l'Union européenne, en Suisse et au
# Royaume-Uni ; un taux lu hors de cette liste est presque toujours une
# erreur de lecture.
KNOWN_RATES = (
    20.0, 10.0, 5.5, 2.1, 8.5, 0.0,                   # fr
    27.0, 25.5, 25.0, 24.0, 23.0, 22.0, 21.0, 19.0,   # ue, taux normaux
    18.0, 17.0, 16.0, 15.0, 14.0, 13.5, 13.0, 12.0,
    9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0,                # ue, taux réduits
    8.1, 3.8, 2.6,                                    # ch
)
#: Devises, reconnues à leur code ou à leur symbole. L'ordre ne sert qu'à
#: départager deux devises citées autant de fois l'une que l'autre.
CURRENCIES = [
    (re.compile(r"€|\bEUR\b|\bEUROS?\b"), "EUR"),
    (re.compile(r"\bPLN\b|\bZL\b"), "PLN"),
    (re.compile(r"\bCHF\b|\bFRANCS?\s*SUISSES?\b"), "CHF"),
    (re.compile(r"\bGBP\b|£"), "GBP"),
    (re.compile(r"\bCZK\b|\bKC\b"), "CZK"),
    (re.compile(r"\bHUF\b|\bFT\b"), "HUF"),
    (re.compile(r"\bRON\b|\bLEI\b"), "RON"),
    (re.compile(r"\bSEK\b"), "SEK"),
    (re.compile(r"\bDKK\b"), "DKK"),
    (re.compile(r"\bNOK\b"), "NOK"),
    (re.compile(r"\bUSD\b|\$"), "USD"),
]


def _closest_known_rate(value):
    closest = min(KNOWN_RATES, key=lambda rate: abs(rate - value))
    return closest if abs(closest - value) <= 0.35 else None


# Un tableau de TVA se reconnaît à son en-tête, puis à des lignes qui
# commencent par un taux. Ses montants portent souvent quatre décimales
# (« 56,8182 »), ce que l'expression des montants courants refuse à juste
# titre — elle en exige exactement deux pour ne pas confondre un prix avec
# un numéro. On en utilise donc une autre, cantonnée au tableau.
TAX_TABLE_HEADER_RE = re.compile(
    r"\bH\.?\s*T\b.{0,24}\bT\.?\s*V\.?\s*A\b"
    r"|\b(?:NETTO|NET|IMPONIBILE|BASE)\b.{0,24}\b(?:MWST|UST|VAT|IVA|BTW)\b")
# Une ligne de tableau commence par son taux, que certains tickets font
# précéder du mot TVA : « 10%(C) ... » comme « TVA 10 % ... ».
TAX_TABLE_ROW_RE = re.compile(
    r"^\s*(?:[A-D]\s+)?(?:(?:T\.?\s*V\.?\s*A|MWST|UST|VAT|IVA|BTW)\.?\s*)?"
    r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
#: Ligne de tableau dont le taux n'a pas de « % » : « 10,00 14,36 1,44 15,80 ».
TAX_TABLE_BARE_ROW_RE = re.compile(r"^\s*(?:[A-D]\s+)?(\d{1,2}[.,]\d{1,2})\s+\d")
#: Colonnes d'un tableau de TVA, dans n'importe quel ordre :
#: « TVA Taux MONT.TTC MONT.TVA TOTAL HT ».
TAX_COLUMNS_RE = re.compile(r"\bHT\b|\bTTC\b|\bTAUX\b|\bNETTO\b|\bBRUTTO\b|\bIMPONIBILE\b")
TAX_TABLE_AMOUNT_RE = re.compile(r"(?<![\d,])(\d+)[.,](\d{2,4})(?![\d])")
#: Nombre de lignes examinées après l'en-tête avant d'abandonner.
TAX_TABLE_DEPTH = 8


def _table_amounts(text):
    """Montants d'une ligne de tableau, décimales longues acceptées."""
    values = []
    for match in TAX_TABLE_AMOUNT_RE.finditer(text):
        try:
            values.append(float("%s.%s" % (match.group(1), match.group(2))))
        except ValueError:
            continue
    return values


def extract_tax_table(lines):
    """Lit le tableau de TVA du ticket, s'il en porte un.

    Format très répandu sur les tickets de caisse :

    ::

                     HT       TVA      TTC
        10%(A)   0,0000    0,0000     0,00
        20%(B)   0,0000    0,0000     0,00
        10%(C)  56,8182    5,6818    62,50

    Renvoie les triplets (taux, montant, ligne) dont la TVA est non nulle.
    La ligne de totaux, qui ne commence pas par un taux, est ignorée — sans
    quoi la TVA serait comptée deux fois.
    """
    for index, line in enumerate(lines):
        header = normalize(line.text)
        if find_amounts(line.text):
            continue  # une ligne d'en-tête ne porte pas de montant
        if not (TAX_TABLE_HEADER_RE.search(header)
                or (TVA_LINE_RE.search(header) and TAX_COLUMNS_RE.search(header))):
            continue
        # « Taux HT TVA TTC » : le taux ouvre la ligne, parfois sans « % ».
        has_rate_column = bool(re.search(r"\bTAUX\b|\bRATE\b|\bALIQUOTA\b", header))
        entries = []
        for row in lines[index + 1:index + 1 + TAX_TABLE_DEPTH]:
            text = normalize(row.text)
            match = TAX_TABLE_ROW_RE.match(text)
            bare = None
            if not match and has_rate_column:
                bare = TAX_TABLE_BARE_ROW_RE.match(text)
            if not (match or bare):
                continue
            rate = _closest_known_rate(float((match or bare).group(1).replace(",", ".")))
            amounts = _table_amounts(row.text)
            if bare:
                amounts = amounts[1:]  # le taux lui-même, lu comme un montant
            if rate is None or len(amounts) < 2:
                continue
            # Colonnes HT, TVA, TTC : la taxe est la deuxième.
            amount = round(amounts[1], 2)
            if amount > 0:
                entries.append((rate, amount, row.text))
        if entries:
            return entries
    return []


def extract_taxes(lines):
    """Taux, montant de TVA et taux le plus élevé lu sur le ticket.

    Le troisième champ ne se reporte jamais sur la dépense : il ne sert
    qu'à borner la vérification. Sur un ticket à plusieurs taux, aucun ne
    vaut pour la dépense entière — mais le plus élevé donne le plafond
    au-delà duquel la TVA lue serait forcément fausse.
    """
    table = extract_tax_table(lines)
    if table:
        source = " | ".join(row for _rate, _amount, row in table)
        rates = sorted({rate for rate, _amount, _row in table})
        total = round(sum(amount for _rate, amount, _row in table), 2)
        single = rates[0] if len(rates) == 1 else None
        return (
            ExtractedField(value=single,
                           confidence=0.9 if single is not None else 0.0,
                           source=source),
            ExtractedField(value=total, confidence=0.9, source=source),
            ExtractedField(value=rates[-1], confidence=0.9, source=source),
        )
    rate_field, amount_field, max_field = _extract_taxes_by_line(lines)
    if rate_field.value is None and max_field.value is None:
        # Aucun taux sur une ligne étiquetée TVA : on le cherche là où il se
        # vérifie, sur une ligne « base, taux, montant » cohérente —
        # « Normale 50,00 € 20,00% 10,00 € ».
        rates = _consistent_rates(lines)
        if len(rates) == 1:
            rate, source = rates.popitem()
            rate_field = ExtractedField(value=rate, confidence=0.8, source=source)
            max_field = ExtractedField(value=rate, confidence=0.8, source=source)
    return rate_field, amount_field, max_field


def _consistent_rates(lines):
    """``{taux: ligne}`` des lignes où une base fois le taux donne un montant."""
    found = {}
    for line in lines:
        text = normalize(line.text)
        rate_match = RATE_RE.search(text)
        if not rate_match:
            continue
        rate = _closest_known_rate(float(rate_match.group(1).replace(",", ".")))
        if not rate:
            continue
        amounts = [value for value, _position in find_amounts(line.text)]
        if any(abs(base * rate / 100.0 - tax) <= 0.02
               for base in amounts for tax in amounts if tax < base):
            found.setdefault(rate, line.text)
    return found


def _extract_taxes_by_line(lines):
    """Repli : tickets qui impriment leur TVA sur une ligne étiquetée."""
    entries = []
    stated_total = None
    for line in lines:
        text = normalize(line.text)
        if not TVA_LINE_RE.search(text):
            continue
        if TAX_SUM_RE.search(text):
            # « SUMA PTU 18,70 », « TOTAL TVA 6,87 » : la somme des lignes
            # de taux, déjà imprimée. L'additionner aux lignes qu'elle
            # résume compterait la TVA deux fois ; elle fait foi à leur place.
            amounts = [value for value, _position in find_amounts(line.text)]
            if amounts:
                stated_total = (amounts[-1], line.text, line.score)
            continue
        if TAX_BASE_RE.search(text) and not RATE_RE.search(text):
            # « SPRZED. OPOD. PTU A 260,00 », « Détail de la TVA Total HT
            # 50,00 » : la base taxable, pas la taxe.
            continue
        rate_match = RATE_RE.search(text)
        if not rate_match and TAX_CODE_RE.search(text):
            # « Prix:12.49 TVA:D » : la lettre renvoie au tableau des taux,
            # et le montant de la ligne est un prix, pas une taxe.
            continue
        rate = None
        if rate_match:
            rate = _closest_known_rate(float(rate_match.group(1).replace(",", ".")))
        amounts = [value for value, _position in find_amounts(line.text)]
        if not amounts and ZERO_AMOUNT_RE.search(line.text):
            # « TVA 5.50%: 0.00 0.00 0.00 » : un taux prévu mais inutilisé
            # ne fait pas de ce ticket un ticket à plusieurs taux.
            continue
        # Le rang du montant de taxe dépend du nombre de colonnes :
        #
        #   « TVA 20,00%....1,13 »              -> le seul montant
        #   « TVA 10,00 % 4,55 0,45 »           -> base puis taxe
        #   « TVA 10 % 26,39 2,64 29,03 »       -> HT, taxe, TTC
        #
        # Prendre systématiquement le dernier revenait, sur trois colonnes,
        # à retenir le TTC — et donc à additionner les totaux du ticket au
        # lieu de ses taxes.
        amount = None
        if len(amounts) >= 3:
            amount = amounts[1]
        elif amounts:
            amount = amounts[-1]
        if rate is None and amount is None:
            continue
        entries.append((rate, amount, line.text, line.score))

    if not entries and stated_total:
        entries.append((None, stated_total[0], stated_total[1], stated_total[2]))
        stated_total = None
    if not entries:
        empty = ExtractedField(value=None, confidence=0.0)
        return empty, empty, empty

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
    if stated_total:
        amount_field = ExtractedField(value=stated_total[0],
                                      confidence=min(0.85 * max(stated_total[2], 0.4), 0.9),
                                      source=stated_total[1])
    elif amounts:
        amount_field = ExtractedField(value=round(sum(amounts), 2),
                                      confidence=min(0.8 * max(score, 0.4), 0.9),
                                      source=source)

    # Plafond de contrôle : le taux le plus élevé effectivement lu.
    max_field = ExtractedField(value=None, confidence=0.0)
    if rates:
        max_field = ExtractedField(value=max(rates), confidence=0.9, source=source)
    return rate_field, amount_field, max_field


# ---------------------------------------------------------------------------
# Sens de lecture
# ---------------------------------------------------------------------------

#: Libellés qui, sur un ticket, précèdent toujours leur montant.
READING_LABEL_RE = re.compile(
    r"\b(TOTAL|PRIX|MONTANT|TVA|PAIEMENT|REGLEMENT|NET A PAYER|ESPECES|RENDU"
    r"|SOUS-TOTAL|A PAYER|DONT TVA)\b")
#: Mentions qui closent un ticket : elles doivent finir en bas.
READING_FOOTER_RE = re.compile(
    r"\b(TOTAL|NET A PAYER|A PAYER|PAIEMENT|REGLEMENT|CARTE BANCAIRE|MERCI"
    r"|AU REVOIR|RENDU|ESPECES)\b")
#: Mentions qui ouvrent un ticket : elles doivent rester en haut.
READING_HEADER_RE = re.compile(
    r"\b(SARL|SAS|SASU|EURL|SA|SIRET|SIREN|RCS|TEL|TELEPHONE|RUE|AVENUE"
    r"|BOULEVARD|ROUTE|PLACE|CEDEX|BP)\b")
#: Nombre de lignes concordantes exigé avant de conclure.
READING_MIN_VOTES = 2


def _block_votes(lines, pattern, expected_bottom):
    """Vote de position : ces mentions tombent-elles du bon côté ?

    Un ticket a une géométrie stable — coordonnées de l'enseigne en haut,
    règlement en bas. Retourné, tout se retrouve du mauvais côté. Le vote
    ne compte que les lignes franchement écartées du milieu : celles qui
    l'entourent ne prouvent rien.
    """
    positions = [line.center_y for line in lines]
    if len(positions) < 4:
        return 0
    middle = (min(positions) + max(positions)) / 2.0
    span = max(positions) - min(positions)
    if span <= 0:
        return 0

    votes = 0
    for line in lines:
        if not pattern.search(strip_accents(line.text or "").upper()):
            continue
        offset = (line.center_y - middle) / span
        if abs(offset) < 0.15:
            continue
        votes += 1 if (offset > 0) == expected_bottom else -1
    return votes


def reading_direction(lines):
    """Dit si le ticket se lit à l'endroit (+1), à l'envers (-1) ou sans avis.

    Une photo à 180° se lit parfaitement mot à mot — le classifieur d'angle
    du moteur redresse chaque ligne — mais les emplacements restent en
    miroir : les montants passent alors *devant* leur libellé, et les lignes
    se suivent de la dernière à la première. C'est ce renversement qu'on
    mesure, sur le seul texte, sans relire l'image.

    Comparer les orientations en relisant l'image ne marche pas : suivant
    la version du moteur, désactiver le classifieur reste sans effet, les
    deux sens obtiennent alors le même score, et l'égalité ne tranche rien.
    """
    votes = 0
    for line in lines:
        text = strip_accents(line.text or "").upper()
        label = READING_LABEL_RE.search(text)
        if not label:
            continue
        # Positions relevées sur la même chaîne que le libellé : `normalize`
        # compacte les séparateurs et décalerait les indices.
        positions = [position for _value, position in find_amounts(text)]
        if not positions:
            continue
        if max(positions) > label.end():
            votes += 1
        elif min(positions) < label.start():
            votes -= 1

    # Second faisceau, sur la géométrie du ticket plutôt que sur ses
    # lignes : l'enseigne et son adresse en haut, le règlement en bas.
    votes += _block_votes(lines, READING_FOOTER_RE, expected_bottom=True)
    votes += _block_votes(lines, READING_HEADER_RE, expected_bottom=False)

    if votes <= -READING_MIN_VOTES:
        return -1
    if votes >= READING_MIN_VOTES:
        return 1
    return 0


def extract_currency(lines, default="EUR"):
    """Devise du ticket, déduite du symbole ou du code imprimé."""
    joined = normalize(" ".join(line.text for line in lines))
    raw = " ".join(line.text for line in lines)
    # La devise la plus citée l'emporte : un ticket polonais qui convertit
    # son total en euros cite le zloty à chaque ligne, l'euro une fois.
    best = None
    for pattern, code in CURRENCIES:
        count = max(len(pattern.findall(joined)), len(pattern.findall(raw)))
        if count and (best is None or count > best[0]):
            best = (count, code)
    if best:
        return ExtractedField(value=best[1], confidence=0.85)
    return ExtractedField(value=default, confidence=0.3)


def extract_time(lines, date_source=""):
    """Heure d'achat imprimée sur le ticket.

    Sert à ordonner plusieurs justificatifs d'une même journée, ce que la
    seule date ne permet pas. On privilégie l'heure imprimée sur la ligne
    qui porte déjà la date : c'est presque toujours l'horodatage de la
    caisse, et non une heure de vol ou d'ouverture du magasin.
    """
    best = None
    for line in lines:
        text = normalize(line.text)
        for match in TIME_RE.finditer(text):
            hour, minute = int(match.group(1)), int(match.group(2))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            weight = 0.9 if date_source and line.text == date_source else 0.7
            confidence = weight * max(line.score, 0.4)
            if best is None or confidence > best[0]:
                best = (confidence, dtime(hour, minute), line.text)

    if best is None:
        return ExtractedField(value=None, confidence=0.0)
    return ExtractedField(value=best[1], confidence=min(best[0], 0.95), source=best[2])


#: « APE 5610A », « Code NAF : 55.10Z », « ATECO 56.10.11 ». L'OCR lit
#: volontiers le Z final comme un 2.
ACTIVITY_RE = re.compile(
    r"\b(?:CODE\s*)?(?:APE|NAF|ATECO|NACE)\s*[:.\-]?\s*(\d{2})\s*[.,]?\s*(\d{2})\s*([A-Z2])?\b")
#: « MCC 5812 », « MCC : 7011 » sur les reçus de carte.
MCC_RE = re.compile(r"\bMCC\s*[:.\-]?\s*(\d{4})\b")
#: « SIRET : 123 456 789 00012 », « N° SIRET 12345678900012 ».
SIRET_RE = re.compile(r"\bSIRE[TN]\b\D{0,12}((?:\d[\s.]?){8}\d(?:[\s.]?\d){0,5})")
#: « RCS PARIS B 123 456 789 » : le SIREN suit la ville du greffe.
RCS_RE = re.compile(r"\bRCS\b[^0-9]{0,30}((?:\d[\s.]?){8}\d)\b")


def extract_activity(lines):
    """Code d'activité imprimé : ``"NAF:5610A"`` ou ``"MCC:5812"``.

    C'est l'indice le plus sûr de la nature d'un commerce, quand il figure :
    beaucoup de tickets français impriment le code APE à côté du SIRET.
    """
    for line in lines:
        text = normalize(line.text)
        match = ACTIVITY_RE.search(text)
        if match:
            letter = (match.group(3) or "").replace("2", "Z")
            return ExtractedField(value="NAF:%s%s%s" % (match.group(1), match.group(2), letter),
                                  confidence=0.9, source=line.text)
    for line in lines:
        match = MCC_RE.search(normalize(line.text))
        if match:
            return ExtractedField(value="MCC:%s" % match.group(1),
                                  confidence=0.9, source=line.text)
    return ExtractedField(value=None, confidence=0.0)


def _luhn(digits):
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def extract_company_number(lines):
    """SIRET (14 chiffres) ou, à défaut, SIREN (9 chiffres) du commerçant.

    La clé de Luhn écarte les numéros mal lus : un chiffre de travers ne
    doit pas désigner un autre établissement.
    """
    siren = None
    for line in lines:
        text = normalize(line.text)
        for pattern in (SIRET_RE, RCS_RE):
            match = pattern.search(text)
            if not match:
                continue
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) == 14 and _luhn(digits):
                return ExtractedField(value=digits, confidence=0.9, source=line.text)
            if len(digits) >= 9 and _luhn(digits[:9]) and not siren:
                siren = ExtractedField(value=digits[:9], confidence=0.8, source=line.text)
    return siren or ExtractedField(value=None, confidence=0.0)


FRENCH_TVA_RE = re.compile(r"\bT\.?\s*V\.?\s*A\b")


def extract_tax_label(lines):
    """Nom sous lequel le ticket imprime sa taxe : « TVA », « IVA », « PTU »…

    Une taxe étrangère ne se déduit pas comme la TVA française : le module
    s'en sert pour ne pas la reporter en TVA déductible. On ne regarde que
    les lignes qui portent un taux ou un montant, pour ignorer un numéro de
    TVA intracommunautaire imprimé en en-tête.
    """
    for line in lines:
        text = normalize(line.text)
        match = TVA_LINE_RE.search(text)
        if not match or not (RATE_RE.search(text) or find_amounts(line.text)):
            continue
        if FRENCH_TVA_RE.search(text):
            return ExtractedField(value="TVA", confidence=0.9, source=line.text)
        label = re.sub(r"[^A-Z]", "", match.group(0))
        return ExtractedField(value=label, confidence=0.9, source=line.text)
    return ExtractedField(value=None, confidence=0.0)


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
    "merchant": "Enseigne",
    "date": "Date",
    "time": "Heure",
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
    tax_rate, tax_amount, tax_rate_max = extract_taxes(lines)
    scan_date = extract_date(lines, today=today, max_age_days=max_age_days)
    fields = {
        "merchant": extract_merchant(lines),
        "date": scan_date,
        "time": extract_time(lines, date_source=scan_date.source),
        "total": extract_total(lines),
        "currency": extract_currency(lines, default=default_currency),
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "tax_rate_max": tax_rate_max,
        "tax_label": extract_tax_label(lines),
        "activity": extract_activity(lines),
        "company_number": extract_company_number(lines),
        "vat_number": extract_vat_number(lines),
    }
    return ScanResult(lines=lines, fields=fields)


def fields_to_check(result, names=("date", "total")):
    """Libellés des champs absents ou peu fiables, à faire relire.

    Le marchand n'y figure pas : l'enseigne se lit mal une fois sur deux —
    logo stylisé, en-tête tronqué — et le signaler à chaque ticket usait
    l'avertissement au point qu'on ne le lisait plus. Ce qui doit être juste
    est ce qui part en comptabilité : la date et le montant.
    """
    todo = []
    for name in names:
        field = result.fields.get(name)
        if field is None or field.value is None or field.confidence < LOW_CONFIDENCE:
            todo.append(FIELD_LABELS.get(name, name))
    return todo
