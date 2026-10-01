# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Extraction of the fields of a European till receipt.

Languages recognised: French, English, German, Italian, Spanish, Polish,
Dutch and Portuguese. The labels of the total, of the VAT and the months of
each language are recognised, as well as the VAT rates of the European Union
and the main European currencies.

The parser works on positioned words, which lets it rebuild the lines and
then reason line by line: the useful amount is most often printed to the
right of a label.

No dependency on Odoo: the file can be tested with a Python interpreter
alone.
"""
import re
import unicodedata
from datetime import date, time as dtime, timedelta

from . import lexicon
from .types import ExtractedField, OcrLine, ScanResult

# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------

# An amount always has two decimals on a receipt. The dot and the comma are
# accepted, as well as the usual thousands separators (space, no-break space,
# dot). The final lookahead rules out "20,00 %", which is a rate.
# The lookbehind only rejects a digit or a comma, not a dot: many receipts
# align their columns with leader dots ("PRIX TTC......6,80"), and forbidding
# a preceding dot would miss these amounts. The "1.234,56" case is still
# covered: the thousands alternative is tried first and takes the whole
# number.
# An amount is neither followed nor preceded by another separator next to a
# digit: without this rule, "20.08.2026" and "27.05.25" gave the amounts
# 20.08, 27.05 or 5.25 (dotted dates of German, Croatian, Italian and British
# receipts).
AMOUNT_RE = re.compile(
    r"(?<![\d,])(?<!\d[.,])(\d{1,3}(?:[  .]\d{3})+|\d+)[.,](\d{2})(?![\d])(?![.,]\d)(?!\s*%)"
)
# A VAT rate: "20 %", "5,50%", "TVA 10.0"
# Never from the middle of a number: "10.0000%" is 10 %, not "00%".
RATE_RE = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,4})?)\s*%")
# The time may follow the year without a space: "13/06/202616:30:16".
TIME_RE = re.compile(r"(?:\b|(?<=[12]\d{3}))([01]?\d|2[0-3])\s*[:hH]\s*([0-5]\d)\b")
VAT_NUMBER_RE = re.compile(r"\bFR\s?([0-9A-Z]{2})\s?(\d{3})\s?(\d{3})\s?(\d{3})\b")

# Plausibility ceiling: beyond it, it is a number (SIRET, card, code) read as
# an amount, not the total of a till receipt.
MAX_PLAUSIBLE_AMOUNT = 100000.0


#: Letters that no Unicode decomposition brings back to the Latin alphabet:
#: without them, "ZAPŁATY" would become "ZAP ATY".
EXTRA_LETTERS = str.maketrans({
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
})


def strip_accents(text):
    """Remove the accents, which the OCR reads unreliably."""
    normalized = unicodedata.normalize("NFD", text.translate(EXTRA_LETTERS))
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def normalize(text):
    """Normalised form of a line, for keyword searches."""
    text = strip_accents(text or "").upper()
    # The OCR mixes up O and 0 in labels: only the separators are changed, so
    # as not to alter the amounts.
    text = re.sub(r"[^A-Z0-9%.,:/\- ]+", " ", text)
    # "HT TUA TTC", "TUA à 5.50%": the OCR mixes up V and U in small till
    # fonts. "TUA" is not a receipt word: it is corrected to "TVA".
    text = re.sub(r"\bT\.?\s?U\.?\s?A\b", "TVA", text)
    # "10% Taxe 1,81": the tax that follows a rate is the VAT, unlike the
    # tourist tax, which follows none.
    return re.sub(r"(%\s*)TAXE\b", r"\1TVA", text)


def parse_amount(integer_part, decimal_part):
    """Convert the two groups of a captured amount to a float."""
    cleaned = re.sub(r"[  .]", "", integer_part)
    try:
        return float("%s.%s" % (cleaned, decimal_part))
    except ValueError:
        return None


def find_amounts(text):
    """All the amounts of a line, in order of appearance."""
    amounts = []
    for match in AMOUNT_RE.finditer(text):
        value = parse_amount(match.group(1), match.group(2))
        if value is not None and 0 < value <= MAX_PLAUSIBLE_AMOUNT:
            amounts.append((value, match.start()))
    return amounts


# ---------------------------------------------------------------------------
# Rebuilding the lines
# ---------------------------------------------------------------------------

def build_lines(words, tolerance_ratio=0.6):
    """Group the words into lines by vertical overlap.

    Two words belong to the same line if their vertical centres are less than
    ``tolerance_ratio`` times the word height apart. This relative criterion
    tolerates font size changes, for instance a shop name printed twice as
    large as the body text.
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

# Keywords of the total, from the most to the least specific. The priority
# decides between receipts that print several "totals" (subtotal, total
# without tax, total).
TOTAL_KEYWORDS = [
    (re.compile(r"\bNET\s*A\s*PAYER\b"), 0.95),
    (re.compile(r"\bDO\s*ZAPLATY\b"), 0.95),                        # pl
    (re.compile(r"\bTOT(?:AL)?\.?\s*T\.?\s*T\.?\s*C\b"), 0.93),
    # "Montant final (TVA incluse) EUR 15,38": invoice of a charger, where the
    # total follows lines of energy in kWh.
    (re.compile(r"\bMONTANT\s*FINAL\b|\bTVA\s*INCLUSE\b"), 0.91),
    (re.compile(r"\bTOTALE\s*(?:COMPLESSIVO|DOCUMENTO)\b"), 0.93),  # it
    (re.compile(r"\bZU\s*ZAHLEN\b"), 0.93),                         # de
    # Billa, Spar, Hofer: "ENDSUMME 16,07 €", "Zahlbetrag", "Rechnungsbetrag".
    (re.compile(r"\bENDSUMME\b|\bENDBETRAG\b|\bZAHLBETRAG\b|\bRECHNUNGS(?:BETRAG|SUMME)\b"
                r"|\bGESAMT(?:SUMME|PREIS)\b"), 0.92),               # de, at, ch
    (re.compile(r"\bNALEZNOSC\b|\bAT\s*BETALE\b|\bSLUTSUMMA\b|\bSLUTSUM\b"), 0.90),  # pl, da, sv
    (re.compile(r"\bTOTAL\s*A\s*PAGAR\b"), 0.93),                   # es, pt
    # "PRIX TTC" is the label of toll, fuel and many vending machine
    # receipts. It amounts to a "TOTAL TTC".
    (re.compile(r"\bPRIX\s*T\.?\s*T\.?\s*C\b"), 0.92),
    (re.compile(r"\bMONTANT\s*(?:DU|A\s*PAYER)\b"), 0.90),
    (re.compile(r"\b(?:AMOUNT|BALANCE)\s*DUE\b|\bGRAND\s*TOTAL\b|\bBALANCE\s*TO\s*PAY\b"), 0.90),  # en
    # "Sum 3 varer 22,00": the amount paid, rounded; "Sum" alone also ends the
    # VAT table.
    (re.compile(r"\bSUM\s*\d+\s*VARER\b"), 0.88),                    # no
    (re.compile(r"\bTE\s*BETALEN\b"), 0.90),                        # nl
    (re.compile(r"\bA\s*BETALE\b|\bATT\s*BETALA\b"), 0.90),          # no, sv
    (re.compile(r"\bZA\s*PLATIT\w{0,2}\b"), 0.90),                  # hr ("platitj")
    (re.compile(r"\bRESTE\s*A\s*PAYER\b"), 0.88),
    (re.compile(r"\bIMPORTO\s*PAGATO\b"), 0.88),                    # it
    (re.compile(r"\bA\s*PAYER\b"), 0.86),
    # Receipts that show the amount paid: "Sie haben 18.00 CHF bezahlt",
    # "Amount paid".
    (re.compile(r"\bBEZAHLT\b|\bAMOUNT\s*PAID\b|\bBETALT\b|\bBETALAT\b"), 0.86),
    (re.compile(r"\bSUMA\b|\bSUMME\b|\bSOMME\b"), 0.85),             # pl, de, fr (ch)
    (re.compile(r"\bTOTALT\b|\bSUMMA\b|\bI\s*ALT\b"), 0.85),          # no, sv, da
    # "Ukupno: 27,70 EUR"; not the column of an item, "Ukupno 1 kom".
    (re.compile(r"\bUKUPNO\b(?!\s*\d+\s*KOM\b)"), 0.85),              # hr
    # "Sum 3 varer 47,00"; not "šum.voće", an item abbreviation.
    (re.compile(r"\bSUM\b(?!\.)"), 0.82),                           # no, da
    (re.compile(r"\bGESAMT(?:BETRAG)?\b"), 0.82),                   # de
    (re.compile(r"\bTOTAL\b|\bTOTALE\b|\bTOTAAL\b"), 0.80),
    # The first letter lost by the OCR: "otal CHF 32.50".
    (re.compile(r"^\s*OTAL\b"), 0.76),
    # "€* TOT 6,42" (Alcampo): the abbreviation opens the line.
    (re.compile(r"^\s*(?:X\s+)?TOT\b"), 0.78),                     # es; "€x" for "€*"
    (re.compile(r"\bRAZEM\b"), 0.75),                               # pl
    (re.compile(r"\bMONTANT\b|\bIMPORTO\b|\bIMPORTE\b|\bBETRAG\b|\bBEDRAG\b"), 0.70),
    # Toll receipt without a "total": the price of the passage is the amount.
    (re.compile(r"\bPEDAGGIO\b|\bPEAGE\b|\bPEAJE\b|\bMAUT\b"), 0.80),
    (re.compile(r"\bPAIEMENT\b|\bREGLEMENT\b|\bPAGAMENTO\b|\bPLATNOSC\b"
                r"|\bKARTENZAHLUNG\b|\bPAYMENT\b|\bKORT\b"), 0.65),
    (re.compile(r"\bCARTE\s*BANCAIRE\b|\bCB\b|\bSANS\s*CONTACT\b|\bKARTA\b"), 0.60),
    (re.compile(r"\bESPECES\b|\bCHEQUE\b|\bCONTANT[EI]\b|\bEFECTIVO\b|\bGOTOWKA\b"
                r"|\bCASH\b|\bKONTANT\b|\bBAR\s+(?:CHF|EUR|\d)"), 0.55),
]
# A line containing one of these terms is not kept as the total. Foreign VAT
# mentions are ruled out like "TVA", unless the total says they are included:
# "TOTALE IVA INCLUSA", "SUMME INKL. MWST".
TOTAL_EXCLUDE_RE = re.compile(
    r"\bS?OUS\s*[- ]?\s*TOTAL\b|\bSUB\s*[- ]?\s*TOTAL\b|\bSUBTOTALE?\b|\bSUBTOTAAL\b|"
    r"\bZWISCHENSUMME\b|\bTOTAL\s*H\.?\s*T\b|\bPRIX\s*H\.?\s*T\b|"
    r"\bMONTANT\s*H\.?\s*T\b|\bTVA\b(?!\s*(?:INCLUSE|INCLUS|COMPRISE|INCL))|\bT\.V\.A\b|"
    r"\bIVA\b(?!\s*INCL)|(?<!INKL\s)(?<!INKL\.\s)\b(?:MWST|UST)\b|"
    r"\bVAT\b(?!\s*INCL)|\bBTW\b(?!\s*INCL)|\bPTU\b|\bOPOD|\bNETTO\b|\bSALES\s*TAX\b|"
    # MVA (no), moms (sv, da): the tax, except "inkl. moms".
    r"(?<!INKL\s)(?<!INKL\.\s)\b(?:MVA|MOMS)\b|"
    r"\bIMPONIBILE\b|\bBASE\s*IMPONIBLE\b|\bDI\s*CUI\b|"
    r"\bRENDU\b|\bMONNAIE\b|\bRECU\b|\bREMISE\b|\bECONOMIE\b|\bECONO\w{0,3}ISEZ\b|\bAVANTAGE\b|"
    # "Points de retrait" (Colissimo) is not a loyalty points balance.
    r"\bCAGNOTTE\b|\bFIDELITE\b|\bPOINTS?\b(?!\s+(?:DE\s+)?RETRAIT)|\bSOLDE\b|\bDONT\b|"
    r"\bACOMPTE\b|"
    r"\bRESTO\b|\bRESZTA\b|\bRUCKGELD\b|\bWECHSELGELD\b|\bGEGEBEN\b|"
    r"\bCAMBIO\b|\bWISSELGELD\b|\bCHANGE\b|\bSCONTO\b|\bRABATT?\b|\bDESCUENTO\b|"
    r"\bVEKSEL\b|\bVAXEL\b|\bTILBAKE\b|"
    # Header of the item table: "Qté Désignation PU TotalT",
    # "Pris Mängd Summa(SEK)", "Description Quantity Price Total".
    r"\bMANGD\b|\bANTAL\b|\bARTIKELNUMMER\b|\bDESIGNATION\b|\bQTY\b|"
    r"\bDESCRIPTION\b|\bQUANT\b|\bMENGE\b|\bARTIKELBEZEICHNUNG\b|"
    # "Total Bottle Deposit $0.20", "Total Savings": deposit and discount.
    r"\bDEPOSIT\b|\bSAVINGS?\b|"
    # "Net Total: €7,73": in English, this label is the amount without tax of
    # a rate, unlike the French "TOTAL NET", often the amount to pay.
    # "Summe Nettoumsatz", "Steuersumme": the net and the tax in German.
    # The tip ("Summe Trinkgeld 9,20 0,46") is not the total either.
    r"\bNET\s*TOTAL\b|\bNETTOUMSATZ\b|\bSTEUERSUMME\b|\bTRINKGELD\b|\bPOURBOIRE\b|"
    # "Total produits 8,25": the items without delivery, hence a subtotal.
    r"\bTOTAL\s*(?:DES\s*)?(?:PRODUITS?|ARTICLES?|MARCHANDISES?)\b"
)


def _triplet_gross(values):
    """Return the gross amount of a valid net + VAT = gross triplet, or ``None``.

    Exactly three amounts: beyond that, the line mixes other columns (two
    currencies on a Czech invoice, for instance) and a chance sum may be
    found in it.
    """
    if len(values) != 3:
        return None
    for gross in sorted(values, reverse=True):
        others = list(values)
        others.remove(gross)
        if any(abs(a + b - gross) <= 0.02 and a > 0 and b > 0
               for i, a in enumerate(others) for b in others[i + 1:]):
            return gross
    return None


def _is_rate_line(line):
    """Tell whether the line is a VAT line with a rate ("2.27 T.V.A. 10% AE 25.00")."""
    text = normalize(line.text)
    return bool(TVA_LINE_RE.search(text) and RATE_RE.search(text))


def _other_currency(text, currency):
    """True if the line cites a currency, and none is the receipt's."""
    if not currency:
        return False
    cited = {code for pattern, code in CURRENCIES if pattern.search(text)}
    return bool(cited) and currency not in cited


#: "-0,60", "-£11.80": a reduction, not an amount to pay.
NEGATIVE_AMOUNT_RE = re.compile(r"(?<![\w.,])-[£$€]?\d+[.,]\d{2}\b")


#: "403, 00": a separator followed by a space and two digits.
SPLIT_DECIMALS_RE = re.compile(r"(\d[.,])\s(\d{2})(?!\d)")


def find_positive_amounts(text):
    """The amounts of a line, without the reductions ("OPUST ... -2,50")."""
    return [(value, position) for value, position in find_amounts(text)
            if not re.search(r"(?<![\w.,])-[£$€]?$", text[:position])]


#: Where a total line goes on with the tax: "SUMA PLN SUMA PTU 141,83 16,65",
#: "TOTALE COMPLESSIVO DI CUI IVA 48.00 4.36". What precedes is the total.
TOTAL_THEN_TAX_RE = re.compile(
    r"\b(?:SUMA\s+PTU|PODATEK\s+PTU|DI\s+CUI\s+IVA|DAVON\s+(?:MWST|UST)|DONT\s+TVA)\b")
SUBTOTAL_RE = re.compile(
    r"\bSOUS\s*[- ]?\s*TOTAL\b|\bSUB\s*[- ]?\s*TOTAL\b|\bSUBTOTALE?\b|\bSUBTOTAAL\b|"
    r"\bZWISCHENSUMME\b")


def extract_total(lines, currency=None):
    """Return the total amount paid and its confidence.

    ``currency``: currency of the receipt. A total line in another currency
    ("Total en EUR 22.50" on a Swiss receipt) comes after the others.
    """
    best = None
    for index, line in enumerate(lines):
        text = normalize(line.text)
        split = TOTAL_THEN_TAX_RE.search(text)
        if split:
            # The total label comes first, the tax label after it: the total
            # is the first amount, larger than the tax that follows.
            amounts = find_positive_amounts(line.text)
            if len(amounts) >= 2 and amounts[0][0] > amounts[-1][0]:
                for pattern, weight in TOTAL_KEYWORDS:
                    if pattern.search(text[:split.start()]):
                        candidate = (weight, amounts[0][0], weight * max(line.score, 0.4), line.text)
                        if best is None or candidate[0] > best[0] or (
                            candidate[0] == best[0] and candidate[1:3] > best[1:3]
                        ):
                            best = candidate
                        break
            continue
        if TOTAL_EXCLUDE_RE.search(text):
            continue
        if index and re.match(r"\s*SUMA\b", text) \
                and re.match(r"\s*KWOTA\s+[A-G]\b", normalize(lines[index - 1].text)):
            continue  # "Kwota C 5,00% 0,87" then "Suma 0,87": the sum of the tax rows
        shift = 0.2 if _other_currency(text, currency) else 0.0
        for pattern, weight in TOTAL_KEYWORDS:
            weight -= shift
            if not pattern.search(text):
                continue
            amounts = find_positive_amounts(line.text)
            if not amounts:
                # "Totalt 403, 00 SEK": the OCR left a space after the separator.
                amounts = find_positive_amounts(SPLIT_DECIMALS_RE.sub(r"\1\2", line.text))
            confidence_penalty = 1.0
            if not amounts and index + 1 < len(lines):
                # The label and the amount are often on two lines when the
                # column is narrow. Not when the next line is a tax or
                # another excluded amount: the total was just misread.
                following = normalize(lines[index + 1].text)
                if not (TOTAL_EXCLUDE_RE.search(following) or TVA_LINE_RE.search(following)):
                    amounts = find_positive_amounts(lines[index + 1].text)
                confidence_penalty = 0.85
            if not amounts:
                continue
            if re.search(r"\bTOTAL\s*NET\b", text) and index and _is_rate_line(lines[index - 1]):
                # "2.27 T.V.A. 10% 25.00 / Total net : 22.73": the amount
                # without tax of the previous rate, repeated under each rate.
                break
            # The useful amount is the last of the line: a quantity or a unit
            # price is often printed to its left. Not on the Polish "DO
            # ZAPLATY OPAKOWANIA ZWROTNE SUMA 50,57 PLN 1,00": the deposit
            # comes after the amount to pay.
            value = amounts[0][0] if "ZAPLATY" in pattern.pattern or "PLATIT" in pattern.pattern \
                else amounts[-1][0]
            # "68,60 € 11,43 € 57,17 € Total": gross, VAT and net columns read
            # out of order. A valid triplet points to its gross amount.
            gross = _triplet_gross([amount for amount, _position in amounts])
            if gross is not None:
                value = gross
            if (len(amounts) == 2 and amounts[0][0] > amounts[1][0] and index + 1 < len(lines)
                    and re.search(r"\bDI CUI IVA\b", normalize(lines[index + 1].text))):
                # "TOTALE COMPLESSIVO 35,90 4,26" above "di cui IVA": the
                # tax shares the line, after the total.
                value = amounts[0][0]
            confidence = weight * confidence_penalty * max(line.score, 0.4)
            candidate = (weight, value, confidence, line.text)
            # On equal weight, the largest amount wins; on equal amount, the
            # highest confidence (the amount follows the label on its line).
            if best is None or candidate[0] > best[0] or (
                candidate[0] == best[0] and (value, candidate[2]) > (best[1], best[2])
            ):
                best = candidate
            break

    if best is not None:
        return ExtractedField(value=best[1], confidence=min(best[2], 0.99), source=best[3])

    # A receipt cut before its total ends on a subtotal: the sum of all the
    # items, hence better than any one of them. Not when amounts follow
    # ("Savings", the card payment): the total is among them.
    def discount(line):
        text = normalize(line.text)
        return bool(NEGATIVE_AMOUNT_RE.search(line.text)
                    or re.search(r"\b(?:SCONT|SAVING|PROMO|RABATT|REMISE|DISCOUNT|DESCUENTO)", text))

    for index in range(len(lines) - 1, -1, -1):
        text = normalize(lines[index].text)
        if not SUBTOTAL_RE.search(text) or TVA_LINE_RE.search(text) or discount(lines[index]):
            continue
        amounts = find_amounts(lines[index].text)
        if amounts and not any(find_amounts(line.text) and not discount(line)
                               for line in lines[index + 1:]):
            return ExtractedField(value=amounts[-1][0], confidence=0.4, source=lines[index].text)
        break

    # Last resort: the largest amount at the bottom of the receipt. This
    # reading is unreliable, but better than an empty field the user has to
    # fill in.
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
#: Months of the other languages (first three letters, without accents):
#: English, German, Italian, Spanish, Polish, Dutch, Portuguese. None
#: contradicts a French month.
MONTHS_OTHER = {
    "FEB": 2, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,   # en, de
    "OKT": 10, "DEZ": 12, "MRT": 3, "MEI": 5,                      # de, nl
    "GEN": 1, "MAG": 5, "GIU": 6, "LUG": 7, "AGO": 8, "SET": 9,    # it, es, pt
    "OTT": 10, "DIC": 12, "ENE": 1, "ABR": 4,
    "STY": 1, "LUT": 2, "KWI": 4, "MAJ": 5, "CZE": 6, "LIP": 7,   # pl
    "SIE": 8, "WRZ": 9, "PAZ": 10, "LIS": 11, "GRU": 12,
}

DATE_PATTERNS = [
    # 04/09/2026, 04-09-2026, 04.09.2026. Some terminals glue the time to the
    # year: "13/06/202616:30:16".
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?:\b|(?=\d{1,2}:\d{2}))"), "dmy", 0.90),
    # 2026-09-04, or glued to the time: "2026-05-1413:32" (Swedish receipts).
    (re.compile(r"\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})(?:\b|(?=\d{1,2}:\d{2}))"), "ymd", 0.90),
    # 04/09/26, or glued to the time: "24.02.2518:18". The same separator on
    # both sides: "08.30-21.00" is an opening time.
    (re.compile(r"\b(\d{1,2})([/.\-])(\d{1,2})\2(\d{2})(?:\b|(?=\d{1,2}:\d{2}))"), "dmy2", 0.75),
    # 4 SEPT 2026, 23 SEPTEMBRE 2026, as well as "14/May/2025" (month in
    # letters between two slashes).
    (re.compile(r"\b(\d{1,2})(?:\s+|\s*[/.\-]\s*)([A-Z]{3,10})\.?(?:\s+|\s*[/.\-]\s*)(\d{4})\b"),
     "dmonthy", 0.85),
    # "Mai12'25 10:35AM": month glued to the day (restaurant terminals).
    (re.compile(r"\b([A-Z]{3,5})(\d{1,2})\s+(\d{2})\b(?![:\d])"), "mdy2", 0.80),
    # "28 Jul'26 15:41": two-digit year after an apostrophe, which the
    # normalisation turns into a space. It is not a time ("26:...").
    (re.compile(r"\b(\d{1,2})\s*([A-Z]{3,10})\s+(\d{2})\b(?![:\d])"), "dmonthy2", 0.80),
    # Without a year: "04/09", "23 SEPTEMBRE". The lookaheads rule out full
    # dates, already caught by the previous patterns.
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})(?![/.\-]?\d)"), "dm", 0.50),
    (re.compile(r"\b(\d{1,2})\s+([A-Z]{3,10})\b(?!\.?\s+\d{4})"), "dmonth", 0.50),
]
#: Patterns whose year is inferred, not read.
INFERRED_YEAR_KINDS = frozenset({"dm", "dmonth"})
#: A receipt without a year is almost always recent. Beyond this threshold,
#: the inference is refused, so as not to date the expense a year back.
NO_YEAR_MAX_AGE_DAYS = 120
DATE_KEYWORD_RE = re.compile(
    r"\bDATE\b|\bLE\b\s|\bCAISSE\b|\bTICKET\b|\bDATA\b|\bDATUM\b|\bFECHA\b")


def _month_number(name):
    """Month number from its name, abbreviated or not."""
    cleaned = strip_accents(name).upper()
    return (MONTHS_FR.get(cleaned[:4]) or MONTHS_FR.get(cleaned[:3])
            or MONTHS_OTHER.get(cleaned[:3]))


def _infer_year(day, month, today):
    """Return the latest occurrence of the day and month, up to "today"."""
    for year in (today.year, today.year - 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= today:
            return candidate
    return None


def _day_month(first, second, order):
    """Day and month of a numeric date, in the country's order.

    ``order`` is ``"dmy"`` (Europe) or ``"mdy"`` (United States). A date that
    is only valid in the other order ("06/26/2026", "26/06/2026") is read that
    way whatever the country.
    """
    day, month = (second, first) if order == "mdy" else (first, second)
    if month > 12 >= day:
        day, month = month, day
    return day, month


def _build_date(kind, groups, today, order="dmy"):
    try:
        if kind == "dmy":
            day, month = _day_month(int(groups[0]), int(groups[1]), order)
            year = int(groups[2])
        elif kind == "ymd":
            year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
        elif kind == "dmy2":
            day, month = _day_month(int(groups[0]), int(groups[2]), order)
            year = 2000 + int(groups[3])
        elif kind == "dmonthy":
            day = int(groups[0])
            month = _month_number(groups[1])
            year = int(groups[2])
            if not month:
                return None
        elif kind == "mdy2":
            month = _month_number(groups[0])
            day = int(groups[1])
            year = 2000 + int(groups[2])
            if not month:
                return None
        elif kind == "dmonthy2":
            day = int(groups[0])
            month = _month_number(groups[1])
            year = 2000 + int(groups[2])
            if not month:
                return None
        elif kind == "dm":
            # Without a year, "08/25" may be a card expiry date (month/year):
            # the order is only swapped in a country that writes the month
            # first.
            first, second = int(groups[0]), int(groups[1])
            day, month = (second, first) if order == "mdy" else (first, second)
            return _infer_year(day, month, today)
        elif kind == "dmonth":
            month = _month_number(groups[1])
            return _infer_year(int(groups[0]), month, today) if month else None
        else:
            return None
        return date(year, month, day)
    except (ValueError, TypeError):
        return None


def extract_date(lines, today=None, max_age_days=730, order="dmy"):
    """Return the purchase date, chosen among the plausible dates of the receipt.

    ``order``: order of day and month in numeric dates, ``"dmy"`` or
    ``"mdy"`` (see ``_day_month``).
    """
    today = today or date.today()
    oldest = today - timedelta(days=max_age_days)
    newest = today + timedelta(days=1)  # time zone tolerance

    candidates = []
    for position, line in enumerate(lines):
        text = normalize(line.text)
        for pattern, kind, weight in DATE_PATTERNS:
            for match in pattern.finditer(text):
                found = _build_date(kind, match.groups(), today, order)
                if not found or not (oldest <= found <= newest):
                    continue
                if kind in INFERRED_YEAR_KINDS and (today - found).days > NO_YEAR_MAX_AGE_DAYS:
                    # Inferred year too old: probably a travel or due date,
                    # not the purchase date.
                    continue
                confidence = weight * max(line.score, 0.4)
                # A date next to a time or to the word "date" is almost
                # always the purchase date, not the validity date of a
                # loyalty card or the end of a warranty.
                if TIME_RE.search(text) or DATE_KEYWORD_RE.search(text):
                    confidence = min(confidence * 1.15, 0.99)
                # The top of the receipt more often holds the dated header.
                if position < max(len(lines) * 0.35, 6):
                    confidence = min(confidence * 1.05, 0.99)
                candidates.append((confidence, found, line.text))

    if not candidates:
        return ExtractedField(value=None, confidence=0.0)
    confidence, found, source = max(candidates, key=lambda item: item[0])
    return ExtractedField(value=found, confidence=confidence, source=source)


# ---------------------------------------------------------------------------
# Merchant
# ---------------------------------------------------------------------------

MERCHANT_STOP_RE = re.compile(
    r"\bTICKET\b|\bFACTURE\b|\bRECU\b|\bDUPLICATA\b|\bCAISSE\b|\bVENDEUR\b|"
    r"\bMERCI\b|\bBIENVENUE\b|\bBONJOUR\b|\bTEL\b|\bTELEPHONE\b|\bSIRET\b|"
    r"\bSIREN\b|\bTVA\b|\bRCS\b|\bAPE\b|\bNAF\b|\bMAGASIN\b|\bADRESSE\b|"
    r"\bWWW\b|\bHTTP\b|\bEMAIL\b|\bMAIL\b|\bHORAIRES?\b|\bCLIENT\b|\bPRENOM\b|"
    # Fields of a transport or toll receipt: they fill the top of the receipt
    # and could be taken for a merchant.
    r"\bSORTIE\b|\bENTREE\b|\bDEPART\b|\bARRIVEE\b|\bCLASSE\b|\bTARIF\b|"
    r"\bPAIEMENT\b|\bREGLEMENT\b|\bCARTE\b|\bMONTANT\b|\bTOTAL\b|"
    # Header of a card slip: the merchant comes further down, after the
    # block of the bank and the terminal.
    r"\bCONTACT\b|\bBANQUE\b|\bBANCAIRE\b|\bMASTERCARD\b|\bVISA\b|"
    r"\bDEBIT\b|\bCREDIT\b|\bAUTO\b|\bCONSERVER\b|\bTERMINAL\b|"
    # Foreign header mentions: fiscal receipt, tax identifier, greetings.
    r"\bPARAGON\b|\bFISKALNY\b|\bNIP\b|\bREGON\b|\bSCONTRINO\b|\bFISCALE\b|"
    r"\bDOCUMENTO\b|\bCOMMERCIALE\b|\bPARTITA\b|\bP\.?\s*IVA\b|\bRECHNUNG\b|"
    r"\bKASSENBON\b|\w*BELEG\b|\bQUITTUNG\b|\bSTEUER|\bFACTURA\b|\bFATURA\b|\bRECIBO\b|"
    # "FACTURMPSIMPLIFICADA": the document type, misread and glued.
    r"SIMPLIFICADA\b|\bCUOTA\b|\bCAMBIO\b|\bENTREGA\b|\bPREFACTURA\b|\bATENDIO\b|\bFIRMA\b|"
    # Spanish table number and column headers.
    r"\bMESA\b|\bARTICULOS?\b|\bDESCRIPCION\b|\bUNID\b|"
    # German column headers ("Stk Artikel Preis Rabatt Summe").
    r"\bARTIKEL\b|\bPREIS\b|\bMENGE\b|\bANZAHL\b|\bSTK\b|"
    # Scandinavian: receipt ("Kvittokopia", "Salgskvittering"), the tagline of
    # Willys ("Vår affärsidé: ...").
    r"\w*KVITT\w*|\bAFFARSIDE\b|\bORDRENUMMER\b|"
    r"\bCIF\b|\bNIF\b|\bRECEIPT\b|\bINVOICE\b|\bTHANK\b|\bWELCOME\b|"
    r"\bGRAZIE\b|\bDANKE\b|\bDZIEKUJEMY\b|\bGRACIAS\b|\bKASA\b|\bKASSE\b|"
    r"\bCASSA\b|\bCAJA\b|\bVAT\b|\bIVA\b|\bMWST\b|\bUST\b|"
    # Column headers and list footers, which are not a merchant.
    r"\bARTICLES?\b|\bQTE\b|\bQUANTITE\b|\bDESIGNATION\b|\bPRODUITS?\b|"
    r"\bLIBELLE\b|\bNOMBRE\b|\bLIGNES?\b|\bVENTES?\b|"
    # Headings of an online receipt, before the name of the establishment.
    r"\bCOORDONNEES\b|\bVOICI\b|\bPAIEMENTS\b|"
    # "Servi par : Cassandra": first name of the waiter, not the merchant.
    r"\bSERVI\b|\bSERVEUR\b|\bSERVEUSE\b|\bCAISSIER\b|\bCAISSIERE\b|"
    # "Commentaire:" of an order (Shopcaisse), followed by the customer's
    # request.
    r"\bCOMMENTAIRES?\b|\bREMARQUES?\b|\bOBSERVATIONS?\b|"
    # Service mode printed large at the top of restaurant receipts.
    r"\bEMPORTER\b|\bSUR\s*PLACE\b|\bTAKE\s*(?:OUT|AWAY)\b|"
    # Screenshot of a web page: close button, journey as a title.
    r"\bFERMER\b|\bCLOSE\b|^\s*DE\s+\S+.*\sA\s+\S+"
)
ADDRESS_RE = re.compile(
    r"\b(RUE|AVENUE|AV|BOULEVARD|BD|PLACE|PL|CHEMIN|ROUTE|RTE|IMPASSE|ALLEE|"
    r"QUAI|ZAC|ZI|CEDEX|BP|"
    # Foreign street types: ul. (pl), via, viale, piazza, corso (it), Straße,
    # Platz (de), calle, plaza (es), rua (pt), street, road (en).
    r"UL|ULICA|ALEJA|VIA|VIALE|PIAZZA|CORSO|STRASSE|STR|PLATZ|WEG|"
    r"CALLE|AVENIDA|PLAZA|RUA|STREET|ROAD|STRAAT)\b"
    # Street names written in one word: "Hermannstr 158", "Hauptstrasse".
    r"|\b\w{3,}(?:STR|STRASSE|GASSE|PLATZ|ALLEE|STRAAT|GATAN|GADE|VEIEN|VEGEN|GATEN|VEJ)\b"
)
#: Street word inside a name, after an article and without any number:
#: "Brasserie du Quai", "Café de la Place", "Pizzeria della Piazza".
NAME_WITH_STREET_WORD_RE = re.compile(
    r"^[^\d]*\w[^\d]*\s(?:DU|DE LA|DE L|DES|AU|AUX|DE|DEL|DELLA|AM|ZUR|ZUM|THE)\s+"
    r"(?:QUAI|PLACE|AVENUE|BOULEVARD|RUE|ROUTE|CHEMIN|PLATZ|PIAZZA|CORSO|PLAZA|ROAD)\b[^\d]*$")


def _is_address(text):
    """Tell whether the normalised line is an address."""
    return bool(ADDRESS_RE.search(text)) and not NAME_WITH_STREET_WORD_RE.match(text)


def _has_date(text, today=None):
    """Tell whether the normalised line holds a plausible date."""
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


#: "Commerçant : SNCF CONNECT, 93212 La Plaine...": a label that names the
#: merchant without ambiguity, wherever it is. A payment receipt carries it in
#: its details, under a title and a logo.
LABELED_MERCHANT_RE = re.compile(
    r"^\s*(?:COMMER[CÇ]ANT|MARCHAND|MERCHANT|H[ÄA]NDLER)\s*:\s*([^,;|]{3,60})",
    re.IGNORECASE)


#: "Nom de l'établissement Hôtel Exemple 3 étoiles": booking receipt, without
#: a colon. The hotel's star rating is not part of the name.
ESTABLISHMENT_RE = re.compile(
    r"^\s*Nom\s+de\s+l['’]\s?[ée]tablissement\s*:?\s*(.{3,60}?)"
    r"(?:\s+\d\s*[ée]toiles?)?\s*$", re.IGNORECASE)
#: Company name: a name followed by a legal form is the merchant, even if the
#: address printed below is longer.
LEGAL_FORM_RE = re.compile(
    r"\bS\.?\s?P\.?\s?A\b|\bS\.?\s?R\.?\s?L\b|\bGMBH\b|\bLTD\b|\bLLC\b"
    r"|\bSARL\b|\bSASU?\b|\bEURL\b|\bSNC\b|\bB\.?V\b", re.IGNORECASE)
LEGAL_FORM_BONUS = 1.15
#: Coop's slogan, printed on its receipts: "Pour moi et pour toi. coop".
SLOGAN_RE = re.compile(
    r"^\W*(?:pour moi et pour toi|f[uü]r mich und f[uü]r dich|per me e per te)\W*", re.IGNORECASE)
#: The tagline of Willys, which names the chain in the place of the logo.
WILLYS_TAGLINE_RE = re.compile(r"\bAFFARSIDE\b.*\bBILLIG\w*\s+MATKASS\w*\s+WI\w{1,4}Y?")
#: The Polish form after the name, to leave out: "Rossmann SDP Sp. z o.o. Sk".
POLISH_FORM_TAIL_RE = re.compile(
    r"\s+sp\.?\s*z\s*[o0]\.?\s*[o0]\.?(?:\s+(?:sp\.?\s*)?[kj]\.?|\s+s\.?k\.?a?\.?)?\s*$",
    re.IGNORECASE)
#: A line made of a currency code only is a heading, not a merchant.
CURRENCY_ONLY_RE = re.compile(r"^(?:EUR|CHF|PLN|GBP|USD|SEK|NOK|DKK|CZK|HUF|RON|HRK)$")
#: Company name before the Polish form "sp. z o.o." (the OCR reads the o's as
#: zeros): at most two words, none starting with a digit.
POLISH_COMPANY_RE = re.compile(
    r"((?<![\w])[^\W\d_][\w&'’\-]{2,}(?:\s+[^\W\d_][\w&'’\-]+)?)\s+sp\.?\s*z\s*[o0]\.?\s*[o0]\b",
    re.IGNORECASE)
#: Name followed, on the same line, by a number and a street type.
NAME_BEFORE_STREET_RE = re.compile(
    r"^(.+?)\s+\d{1,5}\s*,?\s*(?:BIS|TER)?\s*,?\s*"
    r"(?:RUE|AVENUE|AV|BD|BOULEVARD|PLACE|PL|CHEMIN|ROUTE|RTE|ALL[EÉ]E|QUAI|IMPASSE|COURS)\b",
    re.IGNORECASE)
#: Name followed by a street written in one word: "Kaufland - Gutschmidtstraße
#: 19", "Hans im Glück Kloten Bahnhofstrasse 5".
GLUED_STREET_RE = re.compile(
    r"^(.+?)([\s,\-]+)\w{3,}(?:str|strasse|straße|gasse|platz|allee|straat|gatan|gade|veien|vej)\.?\b",
    re.IGNORECASE)
#: Document type at the start or end of the line, next to the merchant name.
DOCUMENT_KIND_RE = re.compile(
    r"^\s*(?:FACTURE|TICKET|RE[CÇ]U|INVOICE|RECEIPT)\s+"
    r"|\s+(?:FACTURE|TICKET|RE[CÇ]U|INVOICE|RECEIPT)\s*$", re.IGNORECASE)
#: "Voltix Innovations B.V. – Voorbeeldstraat 1": legal notice at the foot of
#: an invoice, which names the seller when the header only has a logo.
LEGAL_ENTITY_RE = re.compile(
    r"^\W*([^\W\d_][\w&'’\-]*(?:\s+[^\W\d_][\w&'’\-]*){0,3})\s+"
    r"(S\.?\s?P\.?\s?A\.?|S\.?\s?R\.?\s?L\.?|GMBH|LTD\.?|LLC|SARL|SASU?|EURL|SNC"
    r"|B\.\s?V\.?|BV)(?!\w)", re.IGNORECASE)
#: Category words: "Vols", "Repas" say what is bought, not who sells it.
CATEGORY_WORDS = {
    lexicon.fold(word) for words in lexicon.DEFAULT_KEYWORDS.values() for word in words}


def _words_of(text):
    return re.findall(r"[A-Z0-9]+", strip_accents(text or "").upper())


def _is_buyer(text, buyers):
    """Tell whether the line names the buyer (the employee, their company).

    An invoice prints the customer's name at the top, where a till receipt
    carries the merchant: "CAMILLE EXEMPLE" on a charging invoice.
    """
    words = set(_words_of(text))
    for buyer in buyers:
        expected = set(_words_of(buyer))
        if sum(len(word) for word in expected) >= 5 and expected <= words \
                and len(words) <= len(expected) + 2:
            return True
    tokens = text.split()
    span = _buyer_span(tokens, buyers)
    # Beyond one extra word, the line is a merchant with the buyer's name
    # attached: it is kept, and ``_without_buyer`` removes the name.
    return bool(span) and len(tokens) - (span[1] - span[0]) <= 1


def _buyer_span(tokens, buyers):
    """Return the position ``(start, end)`` of the words that spell a buyer.

    Spaces are ignored: the company "GreenExemple" is printed "GREEN EXEMPLE"
    on a supplier's invoice.
    """
    keys = ["".join(_words_of(token)) for token in tokens]
    for buyer in buyers:
        compact = "".join(_words_of(buyer))
        if len(compact) < 5:
            continue
        for start in range(len(keys)):
            joined = ""
            for end in range(start, len(keys)):
                joined += keys[end]
                if joined == compact:
                    return start, end + 1
                if len(joined) >= len(compact):
                    break
    return None


def _without_buyer(name, buyers):
    """Remove the buyer from a line that mixes seller and customer.

    "INSTITUT EXEMPLE GREEN EXEMPLE" joins two header columns (the seller and
    the customer) read in one piece: the customer is removed.
    """
    tokens = name.split()
    span = _buyer_span(tokens, buyers)
    if span:
        rest = tokens[:span[0]] + tokens[span[1]:]
        if sum(char.isalpha() for char in "".join(rest)) >= 3:
            return " ".join(rest)
    return name


def _legal_entity(lines, header, buyers):
    """Return the company name of a legal notice, if the header cites it."""
    for index, line in enumerate(lines):
        match = LEGAL_ENTITY_RE.match(line.text)
        if not match or _is_buyer(line.text, buyers):
            continue
        first = _words_of(match.group(1))[0]
        if len(first) < 3:
            continue
        cited = re.compile(r"\b%s\b" % re.escape(first))
        if any(cited.search(normalize(other.text))
               for position, other in enumerate(header) if position != index):
            name = "%s %s" % (re.sub(r"\s+", " ", match.group(1)), match.group(2))
            return name, line.text
    return None


def extract_merchant(lines, max_lines=10, buyers=()):
    """Return the merchant name, looked for in the receipt header.

    ``buyers``: names of the buyer (employee, company), not to be taken as
    the merchant.
    """
    for line in lines:
        match = LABELED_MERCHANT_RE.match(line.text)
        if match:
            name = re.sub(r"\s{2,}", " ", match.group(1)).strip(" -*:.")
            if sum(1 for char in name if char.isalpha()) >= 3:
                return ExtractedField(value=name, confidence=0.85, source=line.text)
    for line in lines:
        match = ESTABLISHMENT_RE.match(line.text)
        if match:
            return ExtractedField(value=match.group(1).strip(" -*:."), confidence=0.85,
                                  source=line.text)
    best = None
    for position, line in enumerate(lines[:max_lines]):
        raw = line.text.strip()
        # The slogan of a Swiss chain, printed before its name or on its own
        # line: the logo is then the merchant (Coop).
        slogan = SLOGAN_RE.match(raw)
        if slogan:
            raw = raw[slogan.end():].strip().title()
            if sum(char.isalpha() for char in raw) < 3:
                raw = "Coop"
        elif WILLYS_TAGLINE_RE.search(normalize(raw)):
            raw, slogan = "Willys", True  # "Vår affärsidé: Sveriges billigaste matkasse WiLLY:S"
        # "ASFLieu-dit 47901 AGEN Cedex": name glued to the locality, followed
        # by the address. Only what precedes the locality is a candidate.
        before_place = re.split(r"(?i)\s*lieu[- ]?dit", raw)[0].strip()
        if before_place != raw and sum(char.isalpha() for char in before_place) >= 3:
            raw = before_place
        # "ANTIMOUSTIC FACTURE": the name and the document type are on the
        # same line. Without the word "facture", the name stays a candidate.
        without_kind = DOCUMENT_KIND_RE.sub("", raw).strip()
        if without_kind != raw and sum(char.isalpha() for char in without_kind) >= 3:
            raw = without_kind
        # "Les 3 Brasseurs 9003, rue Chanzy": the OCR merged the name and the
        # address into one line. What precedes the street number is the name.
        street = NAME_BEFORE_STREET_RE.match(raw)
        if not street:
            # Not "Gablenberger Hauptstraße": a single word before the street
            # word, no separator, is part of the street's name.
            glued = GLUED_STREET_RE.match(raw)
            if glued and (len(glued.group(1).split()) >= 2 or re.search(r",|\s-\s", glued.group(2))):
                street = glued
        if street and sum(char.isalpha() for char in street.group(1)) >= 3:
            raw = street.group(1).strip(" ,-")
        text = normalize(raw)
        letters = sum(1 for char in text if char.isalpha())
        digits = sum(1 for char in text if char.isdigit())
        if letters < 3 or len(raw) < 3:
            continue
        if MERCHANT_STOP_RE.search(text) or _is_address(text) or CURRENCY_ONLY_RE.match(text.strip()):
            continue
        if _has_date(text):
            continue  # "← 18 novembre": a date, not a merchant
        # Category word: judged on the whole line. "ASF" detached from its
        # locality is still the merchant, even though it is also a toll word.
        if _is_buyer(raw, buyers) or lexicon.fold(line.text) in CATEGORY_WORDS:
            continue
        if find_amounts(raw):
            continue  # "Vol aller x 1 passager 34,05 €": a purchase line
        if raw.endswith(":"):
            continue  # a label ("Caisse :"), whose value follows
        if "@" in raw:
            continue  # e-mail address, most often the customer's
        if digits > 2 or digits > letters / 2:
            # Postal code, phone, till number. Exception: a merchant whose
            # name holds a digit ("Distribo Tower 3 CS00000"), when the name
            # opens the line and letters remain a clear majority.
            starts_with_name = text[:1].isalpha() and text.split()[0].isalpha()
            if not (starts_with_name and letters >= 10 and digits <= 6 and digits <= letters / 2):
                continue
        # The score rises with how high the line is on the receipt and with
        # how many letters it holds.
        weight = (0.85 - 0.08 * position) * min(1.0, 0.4 + letters / 18.0)
        if LEGAL_FORM_RE.search(raw):
            weight *= LEGAL_FORM_BONUS
        if slogan:
            weight *= 2.0  # the logo under the slogan: nothing else competes
        confidence = weight * max(line.score, 0.4)
        if best is None or confidence > best[0]:
            best = (confidence, raw)

    entity = _legal_entity(lines, lines[:max_lines], buyers)
    if entity and (best is None or not re.search(
            r"\b%s\b" % re.escape(_words_of(entity[0])[0]), normalize(best[1]))):
        # The header only offers a heading ("Sessions de chargement"): the
        # seller is the one of the legal notice, which the header cites.
        name, source = entity
        if name.isupper():
            name = title_case(name)
        return ExtractedField(value=name, confidence=0.75, source=source)
    if best is None:
        # "Podgórne nr rej: BDO 000002265 Lidl sp. z o.o. sp.k.": the line is
        # mostly a registry number, but the company stands before its form.
        for line in lines[:max_lines]:
            match = POLISH_COMPANY_RE.search(line.text)
            if match and not _is_buyer(match.group(1), buyers):
                return ExtractedField(value=match.group(1).strip(), confidence=0.6, source=line.text)
        return ExtractedField(value=None, confidence=0.0)
    confidence, raw = best
    name = re.sub(r"\s{2,}", " ", raw).strip(" -*:.")
    # "ASFLieu-ditExemple 12": remove the locality glued to the name.
    name = re.sub(r"(?i)\s*lieu[- ]?dit.*$", "", name) or name
    # "Établissement DORMIZZ", "Société : POPEYES": the label before the
    # merchant on a card slip is not part of it.
    name = MERCHANT_PREFIX_RE.sub("", name).strip(" -*:.") or name
    name = re.sub(r"^[^\w]+", "", name) or name  # "←", "*" of a logo
    name = re.sub(r"(?i)\s+sklep(?:\s+nr\.?)?\s*\d*\s*$", "", name) or name  # "Sklep nr 1025"
    name = POLISH_FORM_TAIL_RE.sub("", name) or name
    name = _without_buyer(name, buyers)
    if raw.endswith(".") and LEGAL_FORM_RE.search(name):
        name += "."  # "S.p.A.": the last dot is part of the legal form
    if name.isupper() and len(name) > 3:
        name = title_case(name)
    return ExtractedField(value=name, confidence=min(confidence, 0.95), source=raw)


def title_case(name):
    """Capitalise each word, English contractions aside: "JOE'S DINER" gives
    "Joe's Diner", "L'ATELIER" gives "L'Atelier"."""
    return re.sub(r"(?<=\w)['\u2019](S|T|D|Ll|Re|Ve|M)\b",
                  lambda match: match.group(0).lower(), name.title())


# ---------------------------------------------------------------------------
# VAT, currency, miscellaneous
# ---------------------------------------------------------------------------

#: Labels of a VAT line in the languages of the continent: TVA, VAT, IVA (it,
#: es, pt), MwSt and USt (de), PTU (pl), BTW (nl), DPH (cs), MOMS.
TVA_LINE_RE = re.compile(
    r"\bT\.?\s*V\.?\s*A\b|\bV\.?A\.?T\b|\bI\.?V\.?A\b|\bMWST\b|\bUST\b|\bPTU\b"
    r"|\bB\.?T\.?W\b|\bDPH\b|\bMOMS\b|\bMVA\b|\bPODATEK\b|\bSTEUERSUMME\b|\bTAXES?\s*TOTALES?\b"
    r"|\bSALES\s*TAX\b"
    # Carrefour Polska: "Kwota A 23,00% 3,22", the tax of rate A.
    r"|\bKWOTA\s+[A-G]\b")
#: "TVA:D", "(c° tva: 2)": rate code referring to the table, without a tax
#: amount. A single digit only counts if it does not start an amount
#: ("TVA: 5,50").
TAX_CODE_RE = re.compile(r"\bT\.?\s*V\.?\s*A\s*:\s*[A-Z0-9]\b(?![,.]\d)")
#: Zero amount, which the amount search ignores.
ZERO_AMOUNT_RE = re.compile(r"(?<![\d,.])0[.,]00(?!\d)")
#: Line that gives the taxable base (net) and not the tax, despite its label.
TAX_BASE_RE = re.compile(
    r"\bOPOD|\bNETTO\b|\bIMPONIBILE\b|\bBASE\s*IMPONIBLE\b|\bSPRZED|"
    r"\b(?:TOTAL|BASE|MONTANT)\s*H\.?\s*T\b")
#: Line that names the VAT to say an amount includes or excludes it:
#: "Montant final (hors TVA)", "(TVA incluse)", "excl. VAT".
TAX_NOT_A_TAX_RE = re.compile(
    r"\bHORS\s*(?:T\.?\s*V\.?\s*A|TAXES?)\b"
    r"|\bT\.?\s*V\.?\s*A\s*(?:INCLUSE|INCLUS|COMPRISE|INCL)\b"
    r"|\b(?:EXCL|EXCLUDING|EXCLUSIVE|INCL|INCLUDING|INCLUSIVE)\.?\s*(?:OF\s*)?(?:VAT|TAX)\b"
    # "Totalt (ink. moms) 17.00": the total with tax, as on a Swedish receipt.
    r"|\bINKL?\.?\s*(?:MOMS|MVA|MWST)\s+\d+[.,]\d{2}\s*$"
    # "Cena bez DPH", "sin IVA", "senza IVA", "ohne MwSt": without the tax.
    r"|\b(?:BEZ|SIN|SENZA|OHNE|ZONDER|UTAN|UDEN|SEM)\s*(?:DPH|IVA|MWST|BTW|MOMS|MVA|VAT|PTU)\b")
#: Line that sums the VAT, without giving a rate.
TAX_SUM_RE = re.compile(
    r"\b(?:SUMA|TOTAL|TOTALE|SUMME|TOTAAL|RAZEM|GESAMT)\s*(?:DE\s*LA\s*|DI\s*)?"
    r"(?:T\.?\s*V\.?\s*A|PTU|IVA|VAT|MWST|UST|BTW|PODATEK)\b"
    r"|\bTOTAL\s*TAX(?:ES)?\b|\bPODATEK\s*PTU\b|\bSTEUERSUMME\b"
    # "Taxe totale 2,57 €": the sum of an online shop invoice.
    r"|\bTAXES?\s*TOTALES?\b|\bTOTAL\s*DES\s*TAXES\b|\bT\.?\s*V\.?\s*A\s*TOTALE\b"
    # "Montant TVA 10,00 €": the total of a table laid out in columns.
    r"|\bMONTANT\s*(?:DE\s*LA\s*|DE\s*)?T\.?\s*V\.?\s*A\b")
# VAT rates in force in the European Union, Switzerland and the United
# Kingdom. A rate read outside this list is almost always a reading error.
KNOWN_RATES = (
    20.0, 10.0, 5.5, 2.1, 8.5, 0.0,                   # fr
    27.0, 25.5, 25.0, 24.0, 23.0, 22.0, 21.0, 19.0,   # eu, standard rates
    18.0, 17.0, 16.0, 15.0, 14.0, 13.5, 13.0, 12.0,
    9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0,                # eu, reduced rates
    8.1, 3.8, 2.6,                                    # ch
)
#: Currencies recognised by their code or symbol. The order only decides
#: between two currencies cited as many times each.
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
    # "kr": Norwegian, Swedish or Danish krone, told apart by ``_krone``.
    (re.compile(r"\bKR\b|\bKRONER\b|\bKRONOR\b"), "KR"),
]
#: Kroner: the tax word (MVA in Norway, moms elsewhere), the total words and
#: the shape of the company number tell the three countries apart.
KRONE_HINTS = [
    (re.compile(r"\bMVA\b|\bA\s*BETALE\b|\bVARER\b|\bORG\.?\s*NR\.?\s*:?\s*\d{3}\s?\d{3}\s?\d{3}\b"),
     "NOK"),
    (re.compile(r"\bATT\s*BETALA\b|\bSUMMA\b|\bKVITTO\b|\bVAXEL\b|\b\d{6}-\d{4}\b"), "SEK"),
    (re.compile(r"\bI\s*ALT\b|\bBELOB\b|\bKVITTERING\b|\bCVR\b"), "DKK"),
]
#: Codes of the US states, which precede the ZIP code of an address.
US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
             "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC")
#: Receipt without a printed currency: the country shows in its legal
#: mentions (company number, tax name, address). Without any of these clues,
#: the company currency applies.
COUNTRY_CURRENCY_HINTS = [
    (re.compile(r"\bNIP\b|\bPTU\b"), "PLN"),
    # No British VAT number: booking platforms (car rental, hotels) print it
    # on invoices in euros.
    (re.compile(r"\bCHE[-\s]?\d{3}\.\d{3}\.\d{3}\b"), "CHF"),
    (re.compile(r"\b(?:%s)\s+\d{5}(?:-\d{4})?\b" % "|".join(US_STATES.split())), "USD"),
]


def _closest_known_rate(value):
    closest = min(KNOWN_RATES, key=lambda rate: abs(rate - value))
    return closest if abs(closest - value) <= 0.35 else None


# A VAT table shows by its header, then by lines that start with a rate. Its
# amounts often have four decimals ("56,8182"), which the expression for
# ordinary amounts refuses: it requires exactly two, so as not to take a
# number for a price. Another expression, limited to the table, is therefore
# used.
TAX_TABLE_HEADER_RE = re.compile(
    r"\bH\.?\s*T\b.{0,24}\bT\.?\s*V\.?\s*A\b"
    r"|\b(?:NETTO|NETO|NET|IMPONIBILE|BASE)\b.{0,24}\b(?:MWST|UST|VAT|IVA|BTW)\b"
    r"|\b(?:MWST|UST|VAT|IVA|BTW)\b.{0,24}\bNETO\b"
    # "Code Taux HT Montant TTC": some fast food tills call the tax column
    # "Montant" and never write "TVA". The word "Taux" next to "HT" is enough
    # to point to the table. Without it, the line "A 10,00 18,00 1,80 19,80"
    # is read, but no rate is attached to it and the expense falls back on
    # the category's default tax, which may differ from the rate printed on
    # the receipt.
    r"|\bTAUX\b.{0,24}\bH\.?\s*T\b|\bH\.?\s*T\b.{0,24}\bTAUX\b"
    # Spanish tables: "TIPO BASE CUOTA", "Imp. % Base Cuota", "IVA% IVA + PN =
    # PVP" (Lidl: net price, retail price), "Tasa Sin IVA Total IVA IVA Inc.".
    r"|\bTIPO\b.{0,24}\bBASE\b|\bBASE\b.{0,24}\bCUOTA\b"
    r"|\bI\.?V\.?A\s*%.{0,30}\bP\.?\s*V\.?\s*P\b|\bSIN\s*IVA\b.{0,30}\bIVA\s*INC"
    # Norwegian and Danish: "MVA-grunnlag MVA-% MVA Sum", "Mva% Grunnlag Mva Totalt".
    r"|\bMVA\b.{0,20}\bGRUNNLAG\b|\bGRUNNLAG\b.{0,20}\bMVA\b|\bMOMS\b.{0,20}\bGRUNDLAG\b")
#: The three column labels net, VAT and gross, one after the other with no
#: amount between them. This signal is stricter than ``TAX_TABLE_HEADER_RE``,
#: which only cites two labels. It does not match a toll receipt that writes
#: its VAT on a single line ("PRIX HT....5,67 TVA 20,00% ....1,13"), nor a
#: two-rate line that, badly laid out by the OCR, mixes the three words with
#: the amounts ("53,64 HT 5,36 TVA 59,00 TTC") without being a header.
TAX_TABLE_ALL_LABELS_RE = re.compile(
    r"\bH\.?\s*T\b[^\d]{0,20}\bT\.?\s*V\.?\s*A\b[^\d]{0,20}\bT\.?\s*T\.?\s*C\b")
#: Column labels of a VAT table. Three different ones in a row, the tax
#: among them, with no figure between them, make a header even when the OCR
#: glued an amount of the next line to it: "Total Promotion TVA Taux
#: MONT.TTC MONT.TVA TOTAL HT 3,02" (Lidl).
TAX_COLUMN_LABEL_RE = re.compile(
    r"\b(HT|TTC|TVA|TAUX|NET|NETTO|BRUT|BRUTTO|HTVA|TVAC|MWST|UST|VAT|IVA|BTW|IMPONIBILE"
    r"|MOMS|MVA)\b")
TAX_LABELS = {"TVA", "MWST", "UST", "VAT", "IVA", "BTW", "MOMS", "MVA"}


#: Net and gross columns side by side: a table header even when the tax
#: word is misread ("NUST BRUTTO NETTO" for "MWST BRUTTO NETTO").
NET_GROSS_PAIRS = ({"NETTO", "BRUTTO"}, {"NET", "BRUT"}, {"HT", "TTC"}, {"HTVA", "TVAC"})
#: Table line whose "%" the OCR read as an 8: "A 198 0.68 4.28 3.60" for
#: "A 19% ...", even its letter at times ("8 78 1,98 30,29 28,31" for
#: "B 7% ..."). Only kept when the amounts hold at that rate.
TAX_TABLE_MISREAD_ROW_RE = re.compile(r"^\s*[A-D8]\s+(\d{1,2})[8B]\s+\d")


def _column_labels_run(header):
    """Tell whether the line holds column labels in a row, without a figure
    between them: three with the tax, or the net and gross pair."""
    for part in re.split(r"\d", header):
        labels = set(TAX_COLUMN_LABEL_RE.findall(part))
        if len(labels) >= 3 and labels & TAX_LABELS:
            return True
        if any(pair <= labels for pair in NET_GROSS_PAIRS):
            return True
    return False


# A table line starts with its rate, which some receipts precede with the
# word TVA: "10%(C) ..." as well as "TVA 10 % ...".
TAX_TABLE_ROW_RE = re.compile(
    r"^\s*(?:[A-H]\s+|\d{1,2}\s+)?"
    r"(?:(?:T\.?\s*V\.?\s*A|MWST|UST|VAT|IVA|BTW)\.?\s*)?"
    r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
#: Table line that opens on its base and goes on with the rate: "317.48 15%
#: 47.62 365.10" (Norwegian "MVA-grunnlag MVA-% MVA Sum").
TAX_TABLE_MIDDLE_ROW_RE = re.compile(r"^\s*\d+[.,]\d{2}\s+(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
#: Table line whose rate has no "%": "10,00 14,36 1,44 15,80", or, when a
#: "Code" column precedes it, "2 10,00 4 36,18 3,62 39,80".
TAX_TABLE_BARE_ROW_RE = re.compile(
    r"^\s*(?:(?:[A-H]|\d)\s+)?(?:(?:I\.?V\.?A|T\.?V\.?A|MWST|VAT)\.?\s+)?(\d{1,2}[.,]\d{1,2})\s+\d")
#: Columns of a VAT table, in any order: "TVA Taux MONT.TTC MONT.TVA TOTAL
#: HT", "TVA% TVA Net Brut" (rate, tax, net, gross), "TVA % Taxe HTVA TVAC"
#: (without VAT, VAT included: Belgian labels, used by some French till
#: software).
TAX_COLUMNS_RE = re.compile(
    r"\bHT\b|\bTTC\b|\bTAUX\b|\bNETTO\b|\bBRUTTO\b|\bIMPONIBILE\b|\bNET\b|\bBRUT\b"
    r"|\bHTVA\b|\bTVAC\b|\bGRUNNLAG\b|\bGRUNDLAG\b")
# A number followed by "%" is a rate ("10.00%"), not an amount.
TAX_TABLE_AMOUNT_RE = re.compile(r"(?<![\d,])(?<!\d[.,])(\d+)[.,](\d{2,4})(?![\d])(?![.,]\d)(?!\s*%)")
TAX_TABLE_CENTS_RE = re.compile(r"(?<![\w.,])[.,](\d{2})(?![\d.,])(?!\s*%)")
#: Number of lines examined after the table header, before giving up.
TAX_TABLE_DEPTH = 8


def _table_amounts(text):
    """Return the amounts of a table line (four decimals accepted)."""
    found = []
    for match in TAX_TABLE_AMOUNT_RE.finditer(text):
        try:
            found.append((match.start(), float("%s.%s" % (match.group(1), match.group(2)))))
        except ValueError:
            continue
    # ",33" (Alcampo): an amount under one euro printed without its zero.
    found += [(match.start(), float("0.%s" % match.group(1)))
              for match in TAX_TABLE_CENTS_RE.finditer(text)]
    return [value for _position, value in sorted(found)]


def _consistent_tax(amounts, rate):
    """Return the VAT of a (net, VAT, gross) triplet consistent with this rate, or ``None``.

    ``net + VAT = gross``, and ``VAT`` is ``net`` times the rate within 0.03.
    These checks find the right column in a line where the OCR mixed in other
    ones.
    """
    for i, base in enumerate(amounts):
        for j, tax in enumerate(amounts):
            if j == i or tax <= 0 or abs(base * rate / 100.0 - tax) > 0.03:
                continue
            for k, total in enumerate(amounts):
                if k not in (i, j) and abs(base + tax - total) <= 0.02:
                    return round(tax, 2)
    return None


def _consistent_row(amounts, rate):
    """Return the VAT of a line of two or three amounts that hold at this rate.

    Three amounts: net, VAT and gross in any order. Two: net and VAT, or VAT
    and gross ("39,82 3,98" under "TVA 10 % HT TVA").
    """
    if len(amounts) >= 3:
        return _consistent_tax(amounts, rate)
    if len(amounts) != 2:
        return None
    for tax, other in (amounts, reversed(amounts)):
        if 0 < tax < other and (abs(other * rate / 100.0 - tax) <= 0.03
                                or abs(other * rate / (100.0 + rate) - tax) <= 0.03):
            return round(tax, 2)
    # Net and gross, the VAT column lost ("1,57 1,87" at 19 %).
    first, second = amounts
    if 0 < first < second and abs(first * (100.0 + rate) / 100.0 - second) <= 0.03:
        return round(second - first, 2)
    return None


def extract_tax_table(lines):
    """Read the VAT table of the receipt, if it has one.

    Very common format on till receipts:

    ::

                         HT       TVA      TTC
        10%(A)   0,0000    0,0000     0,00
        20%(B)   0,0000    0,0000     0,00
        10%(C)  56,8182    5,6818    62,50

    Returns the (rate, amount, line) triplets whose VAT is not zero. The line
    of totals, which does not start with a rate, is ignored, otherwise the
    VAT would be counted twice.
    """
    for index, line in enumerate(lines):
        header = normalize(line.text)
        # The three labels HT, TVA and TTC together, with no amount between
        # them, are a strong enough signal to recognise the header even if
        # the line also carries an amount elsewhere. On a vending machine
        # receipt, the OCR sometimes mixes a nearby total into the header
        # ("TOTAL EN EUROS : 15,80 HT TVA TTC") that is not the tax; the next
        # line is still the real line of values. A two-rate line badly laid
        # out by the OCR can also hold the three words ("53,64 HT 5,36 TVA
        # 59,00 TTC"), but with amounts between them: it is not a header.
        # Any other, weaker signal requires a line without an amount, so as
        # not to take a line of totals for the header before it.
        explicit = bool(TAX_TABLE_ALL_LABELS_RE.search(header)) or _column_labels_run(header)
        if not explicit:
            if find_amounts(line.text):
                continue  # a header line carries no amount
            if not (TAX_TABLE_HEADER_RE.search(header)
                    or (TVA_LINE_RE.search(header) and TAX_COLUMNS_RE.search(header))):
                continue
        # "Taux HT TVA TTC": the rate opens the line, sometimes without "%".
        # A tax word followed by "%" too: "Moms% Moms Netto Brutto" (se, dk).
        has_rate_column = bool(re.search(
            r"\bTAUX\b|\bRATE\b|\bALIQUOTA\b|\bSATS\b|\bTIPO\b|\bTASA\b|\bIMP\.?\s*%|%\s*I\.?V\.?A\b"
            r"|\b(?:T\.?\s*V\.?\s*A|MOMS|MVA|MWST|VAT|IVA|BTW|PTU|UST)\s*%", header))
        # "MwSt 19% Netto MwSt Brutto" then "33,28 6,32 39,60": a single rate,
        # printed in the header, for a line of amounts without one.
        header_rates = RATE_RE.findall(header)
        header_rate = (_closest_known_rate(float(header_rates[0].replace(",", ".")))
                       if len(header_rates) == 1 else None)
        entries = []
        for row in lines[index + 1:index + 1 + TAX_TABLE_DEPTH]:
            text = normalize(row.text)
            match = TAX_TABLE_ROW_RE.match(text)
            middle = None if match else TAX_TABLE_MIDDLE_ROW_RE.match(text)
            if middle:
                rate = _closest_known_rate(float(middle.group(1).replace(",", ".")))
                amounts = _table_amounts(row.text)
                if rate and len(amounts) >= 2:
                    amount = _consistent_row(amounts, rate)
                    if amount:
                        entries.append((rate, amount, row.text))
                continue
            if not match and has_rate_column and not re.search(r"[A-Z]{2}", text):
                inside = _rate_inside_row(_table_amounts(row.text))
                if inside:
                    entries.append((inside[0], inside[1], row.text))
                    continue
            bare = misread = None
            if not match and has_rate_column:
                bare = TAX_TABLE_BARE_ROW_RE.match(text)
            if not (match or bare):
                misread = TAX_TABLE_MISREAD_ROW_RE.match(text)
            if not (match or bare or misread):
                if (header_rate and not entries
                        and not re.search(r"[A-Z%]", re.sub(r"\b(?:EUR|CHF)\b", "", text))):
                    # A line of amounts only ("Product 68.24 13.65 81.89" is
                    # an item of an invoice). Only amounts that hold at that
                    # rate are taken, and only once: a line of totals below
                    # repeats them.
                    amount = _consistent_row(_table_amounts(row.text), header_rate)
                    if amount:
                        return [(header_rate, amount, "%s | %s" % (line.text, row.text))]
                continue
            rate = _closest_known_rate(
                float((match or bare or misread).group(1).replace(",", ".")))
            amounts = _table_amounts(row.text)
            if bare:
                amounts = amounts[1:]  # the rate itself, read as an amount
            if rate is None or len(amounts) < 2:
                continue
            if misread:
                # A rate guessed from a misread "%": only amounts that hold at
                # that rate confirm it.
                amount = _consistent_tax(amounts, rate) if len(amounts) >= 3 else None
                if amount:
                    entries.append((rate, amount, row.text))
                continue
            # Net, VAT, gross columns: the tax is the second. But the order
            # varies ("TVA Net Brut") and, on a slanted photo, the columns of
            # two lines mix: the triplet that holds, net + VAT = gross at the
            # row's rate, wins over the position.
            amount = round(amounts[1], 2)
            if len(amounts) >= 3:
                amount = _consistent_tax(amounts, rate) or amount
            elif len(amounts) == 2:
                amount = _consistent_row(amounts, rate) or amount
            if amount > 0:
                entries.append((rate, amount, row.text))
        if entries:
            return entries
    return []


def extract_taxes(lines):
    """Return the rate, the VAT amount and the highest rate read on the receipt.

    The third field first bounds the check: on a receipt with several rates,
    none holds for the whole expense, but the highest gives the ceiling
    beyond which the VAT read would be wrong. For want of anything better,
    the model also applies that rate to the expense in this case (see
    ``_expense_scan_field_values``).
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
        # No rate on a line labelled VAT: it is looked for where it can be
        # checked, on a consistent "base, rate, amount" line
        # ("Normale 50,00 € 20,00% 10,00 €").
        rates = _consistent_rates(lines)
        if len(rates) == 1:
            rate, source = rates.popitem()
            rate_field = ExtractedField(value=rate, confidence=0.8, source=source)
            max_field = ExtractedField(value=rate, confidence=0.8, source=source)
    return rate_field, amount_field, max_field


def _consistent_rates(lines):
    """``{rate: line}`` of the lines where a base times the rate gives an amount."""
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


#: A line opening on the title of the item column: a table header.
ITEM_HEADER_START_RE = re.compile(
    r"^\s*(?:DESCRIZIONE|DESCRIPCION|DESIGNATION|BESCHREIBUNG|OMSCHRIJVING|DESCRICAO|ARTIKEL|OPIS)\b")
#: Line of the total with tax: it is not a tax line, even if "TVA" is in it.
TOTAL_TTC_RE = re.compile(r"\bTOT(?:AL)?\.?\s*T\.?\s*T\.?\s*C\b")


def _tax_of_pair(amounts, rate):
    """Return the tax of a line with two amounts: the one the other explains.

    "TVA 10 % 4,55 0,45" (base, tax) as well as "0,77 VAT 10% 8,50" (tax,
    gross): the tax is the base times the rate, or the gross times rate /
    (100 + rate). Without a match, the last amount is kept.
    """
    first, second = amounts
    for tax, other in ((second, first), (first, second)):
        if tax < other and (abs(other * rate / 100.0 - tax) <= 0.02
                            or abs(other * rate / (100.0 + rate) - tax) <= 0.02):
            return tax
    # "B 19% 1,57 0,3) 1,87": base and gross, the tax column mangled.
    if abs(first * (100.0 + rate) / 100.0 - second) <= 0.02:
        return round(second - first, 2)
    return second


#: A table row opening on the tax name and its rate without "%": "C IVA
#: 4,00". Elsewhere in a line ("FUNGHI IVA 13.00 A"), the number is a price.
TAX_NAME_RATE_RE = re.compile(
    r"^\s*(?:[A-H]\s+)?(?:I\.?V\.?A|T\.?V\.?A|MWST|VAT|BTW)\s+(\d{1,2}[.,]\d{1,2})\b(?!\s*%)")


def _rate_inside_row(amounts):
    """``(rate, tax)`` of a row whose rate is one of its columns, or ``None``.

    "63,82 10,00 6,38" under "BASE %IVA IMP.IVA": the rate sits between the
    base and the tax. It is taken only when the other amounts hold at it.
    """
    for position, value in enumerate(amounts):
        if value > 0 and _closest_known_rate(value) == value:
            tax = _consistent_row(amounts[:position] + amounts[position + 1:], value)
            if tax:
                return value, tax
    return None


def _extract_taxes_by_line(lines):
    """Fallback: receipts that print their VAT on a labelled line."""
    entries = []
    stated_total = None
    # "Kwota C 5,00% 0,87" gives the tax of letter C: a "PTU C 18,28" without
    # rate, on the same receipt, is its taxable base.
    kwota_letters = {match.group(1) for match in
                     (re.match(r"\s*KWOTA\s+([A-G])\b", normalize(line.text)) for line in lines)
                     if match}
    for line in lines:
        text = normalize(line.text)
        if not TVA_LINE_RE.search(text):
            continue
        base_of = re.match(r"\s*PTU\s+([A-G])\b", text)
        if base_of and base_of.group(1) in kwota_letters and not RATE_RE.search(text):
            continue
        if TAX_TABLE_ALL_LABELS_RE.search(text):
            # "... TOTAL EN EUROS : 15,80 HT TVA TTC ...": the three column
            # labels HT, TVA and TTC together mark a table header that the
            # OCR mixed with a nearby total. The amount of this line is not
            # a tax; if there is one, it is on another line. Better to read
            # nothing than to read the total. A toll receipt that prints
            # everything on one line ("PRIX HT....5,67 TVA 20,00%....1,13")
            # only cites HT and TVA, without TTC in the same place: it is
            # still read normally.
            continue
        if TAX_NOT_A_TAX_RE.search(text):
            continue  # "Montant final (TVA incluse) 15,38": a total, not the tax
        if TOTAL_TTC_RE.search(text) and not TAX_SUM_RE.search(text):
            continue  # "TOTAL TTC: TVA 25,50 EUR TTC": columns mixed with the total
        if TAX_SUM_RE.search(text):
            # "SUMA PTU 18,70", "TOTAL TVA 6,87": sum of the rate lines,
            # already printed. Adding it to the lines it sums up would count
            # the VAT twice; it prevails in their place.
            amounts = [value for value, _position in find_amounts(line.text)]
            if re.search(r"\bSUMA\s+PLN\b", text) and len(amounts) == 1:
                continue  # "SUMA PLN SUMA PTU 297,16": the total only, the tax was lost
            if amounts:
                stated_total = (amounts[-1], line.text, line.score)
            continue
        if TAX_BASE_RE.search(text) and not RATE_RE.search(text):
            # "SPRZED. OPOD. PTU A 260,00", "Détail de la TVA Total HT
            # 50,00": taxable base, not tax.
            continue
        rate_match = RATE_RE.search(text)
        if not rate_match and TAX_CODE_RE.search(text):
            # "Prix:12.49 TVA:D": the letter refers to the rate table, and the
            # amount of the line is a price, not a tax.
            continue
        rate = None
        if rate_match:
            rate = _closest_known_rate(float(rate_match.group(1).replace(",", ".")))
        amounts = [value for value, _position in find_amounts(line.text)]
        if ITEM_HEADER_START_RE.search(text):
            # "DESCRIZIONE TAGLIATELLE FUNGHI IVA 13.00", "DESCRIZIONE Brioch
            # IVA 10% Prezzo 1,50": the column title "IVA" and the price of the
            # first item, merged by the OCR. Its rate stands, its amount does not.
            amounts = []
        if not amounts and ZERO_AMOUNT_RE.search(line.text):
            # "TVA 5.50%: 0.00 0.00 0.00": a rate provided for but unused
            # does not make this a receipt with several rates.
            continue
        # The position of the tax amount depends on the number of columns:
        #
        #   "TVA 20,00%....1,13"              -> the only amount
        #   "TVA 10,00 % 4,55 0,45"           -> base then tax
        #   "TVA 10 % 26,39 2,64 29,03"       -> net, tax, gross
        #
        # Always taking the last amount would keep the gross on three
        # columns, and add up the receipt's totals instead of its taxes.
        if rate is None and len(amounts) >= 3:
            # "E TVA 10.00 13.59 1.36 14.95", "ITSR 17.93 22.00 3.94": the
            # rate, without "%", is read as an amount. It is kept if it is a
            # known rate and the other amounts hold at it.
            inside = _rate_inside_row(amounts)
            if inside:
                rate, tax = inside
                entries.append((rate, tax, line.text, line.score))
                continue
        if rate == 0:
            # "0 %" (exempt, "ESC.IVA ART.15"): whatever the amount printed,
            # the tax is nil.
            entries.append((0.0, 0.0, line.text, line.score))
            continue
        named = TAX_NAME_RATE_RE.search(text) if rate is None else None
        if named:
            # "C IVA 4,00 96 ,04": the rate, without "%", right after the tax
            # name, is not an amount; the amounts the OCR mangled are lost.
            value = float(named.group(1).replace(",", "."))
            if _closest_known_rate(value) == value and value in amounts:
                rate = value
                amounts.remove(value)
        amount = None
        if len(amounts) >= 3:
            amount = amounts[1]
            if rate:
                amount = _consistent_tax(amounts, rate) or amount
        elif len(amounts) == 2 and rate:
            # A rate read from the row's number ("TVA 10.00 9.91 099 10.90",
            # "0.99" misread) only stands with amounts that hold at it.
            amount = _consistent_row(amounts, rate) if named else _tax_of_pair(amounts, rate)
        elif amounts:
            amount = amounts[-1]
        if rate is None and amount is None:
            continue
        entries.append((rate, amount, line.text, line.score))

    if not entries and stated_total:
        # Printed VAT sum, used when there are no rate lines.
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
        # Several rates on the same receipt: a single one cannot be chosen for
        # the expense. The information is passed on without being applied.
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

    # Control ceiling: the highest rate actually read.
    max_field = ExtractedField(value=None, confidence=0.0)
    if rates:
        max_field = ExtractedField(value=max(rates), confidence=0.9, source=source)
    return rate_field, amount_field, max_field


# ---------------------------------------------------------------------------
# Reading direction
# ---------------------------------------------------------------------------

#: Labels that always precede their amount on a receipt.
#: In every language met: French words alone left a foreign receipt without
#: an opinion, or turned it on a stray "TOTAL" or "SA". Measured on the
#: corpus (a photo as taken and turned over): 68 % of consistent decisions
#: instead of 51 %, none that turns a receipt both ways.
READING_LABEL_RE = re.compile(
    r"\b(TOTAL|PRIX|MONTANT|TVA|PAIEMENT|REGLEMENT|NET A PAYER|ESPECES|RENDU"
    r"|SOUS-TOTAL|A PAYER|DONT TVA"
    r"|TOT|SUBTOTAL|SUMME|SUMA|SUMMA|SUM|TOTALE|TOTAAL|TOTALT|GESAMT|BETRAG|BEDRAG|IMPORTE|IMPORTO"
    r"|TARJETA|EFECTIVO|CAMBIO|ENTREGA|CONTANTI|RESTO|BARGELD|RUCKGELD|GEGEBEN|KARTE"
    r"|IVA|MWST|BTW|MOMS|MVA|PTU|VAT|TAX|A PAGAR|ZU ZAHLEN|TE BETALEN|ATT BETALA|DO ZAPLATY|RAZEM)\b")
#: Mentions that close a receipt: they are at the bottom.
READING_FOOTER_RE = re.compile(
    r"\b(TOTAL|NET A PAYER|A PAYER|PAIEMENT|REGLEMENT|CARTE BANCAIRE|MERCI"
    r"|AU REVOIR|RENDU|ESPECES"
    r"|GRACIAS|GRAZIE|DANKE|OBRIGADO|THANK YOU|BEDANKT|TACK|TAKK|DZIEKUJEMY"
    r"|EFECTIVO|CAMBIO|TARJETA|CONTANTI|RESTO|BARGELD|RUCKGELD|CHANGE|CASH)\b")
#: Mentions that open a receipt: they are at the top. Not "SA" alone: "V sa
#: Credit" on a card slip at the bottom.
READING_HEADER_RE = re.compile(
    r"\b(SARL|SAS|SASU|EURL|SIRET|SIREN|RCS|TEL|TELEPHONE|RUE|AVENUE"
    r"|BOULEVARD|ROUTE|PLACE|CEDEX|BP"
    r"|S\.A\.|S\.L\.|S\.R\.L\.|GMBH|SPA|CIF|NIF|P\.? ?IVA|PARTITA|NIP|TELF|TELEFONO|TELEFON"
    r"|CALLE|AVDA|STRASSE|STR|VIA|PIAZZA|ULICA|UL)\b")
#: Number of agreeing lines required before concluding.
READING_MIN_VOTES = 2


def _block_votes(lines, pattern, expected_bottom):
    """Position vote: tell whether the mentions fall on the right side (top or bottom).

    A receipt has a stable geometry: merchant details at the top, payment at
    the bottom. Upside down, everything ends up on the wrong side. The vote
    only counts the lines clearly away from the middle: those around it prove
    nothing.
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
    """Tell whether the receipt reads right side up (+1), upside down (-1) or no opinion (0).

    A photo at 180° reads correctly word by word (the engine's angle
    classifier straightens each line), but the positions stay reversed: the
    amounts come before their label and the lines follow each other from the
    last to the first. This reversal is measured on the text alone, without
    reading the image again.

    Comparing orientations by reading the image again does not work: with
    some engine versions, turning the classifier off has no effect, both
    directions then get the same score and the tie decides nothing.
    """
    votes = 0
    for line in lines:
        text = strip_accents(line.text or "").upper()
        label = READING_LABEL_RE.search(text)
        if not label:
            continue
        # Positions taken on the same string as the label: `normalize`
        # compacts the separators and would shift the indexes.
        positions = [position for _value, position in find_amounts(text)]
        if not positions:
            continue
        if max(positions) > label.end():
            votes += 1
        elif min(positions) < label.start():
            votes -= 1

    # Second clue, on the geometry of the receipt rather than on its lines:
    # the merchant and its address at the top, the payment at the bottom.
    votes += _block_votes(lines, READING_FOOTER_RE, expected_bottom=True)
    votes += _block_votes(lines, READING_HEADER_RE, expected_bottom=False)

    if votes <= -READING_MIN_VOTES:
        return -1
    if votes >= READING_MIN_VOTES:
        return 1
    return 0


def extract_currency(lines, default="EUR"):
    """Return the currency of the receipt, deduced from the printed symbol or code."""
    joined = normalize(" ".join(line.text for line in lines))
    raw = " ".join(line.text for line in lines)
    # The most cited currency wins: a Polish receipt that converts its total
    # into euros cites the zloty on every line and the euro only once.
    # On a tie, the currency of the first total line wins ("Total CHF 32.50"
    # comes before the conversion "Total en EUR"), then the one cited first.
    total_lines = [normalize(line.text) for line in lines
                   if any(weight >= 0.8 and pattern.search(normalize(line.text))
                          for pattern, weight in TOTAL_KEYWORDS)]
    best = None
    for pattern, code in CURRENCIES:
        count = max(len(pattern.findall(joined)), len(pattern.findall(raw)))
        if not count:
            continue
        found = pattern.search(joined)
        on_total = next((i for i, text in enumerate(total_lines) if pattern.search(text)), None)
        rank = (count, on_total is not None, -(on_total if on_total is not None else 0),
                -(found.start() if found else len(joined)))
        if best is None or rank > best[2]:
            best = (count, code, rank)
    if best and best[1] == "KR":
        code = _krone(joined, default)
        if code:
            return ExtractedField(value=code, confidence=0.75)
        best = None
    if best:
        return ExtractedField(value=best[1], confidence=0.85)
    code = _country_currency(joined)
    if code:
        return ExtractedField(value=code, confidence=0.6)
    return ExtractedField(value=default, confidence=0.3)


def _country_currency(text):
    """Currency of a receipt without a printed currency, from its legal mentions."""
    krone = _krone(text, None)
    if krone:
        return krone
    for pattern, code in COUNTRY_CURRENCY_HINTS:
        if pattern.search(text):
            return code
    return None


def _krone(text, default):
    """Krone of the receipt from its words; the default currency if it is one."""
    votes = {code: len(pattern.findall(text)) for pattern, code in KRONE_HINTS}
    code, count = max(votes.items(), key=lambda item: item[1])
    if count:
        return code
    return default if default in ("NOK", "SEK", "DKK") else None


def extract_time(lines, date_source=""):
    """Return the purchase time printed on the receipt.

    It orders several receipts of the same day, which the date alone cannot
    do. The time printed on the line that already carries the date is
    preferred: it is almost always the till's time stamp, not a flight time
    or the shop's opening hours.
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


#: "APE 5610A", "Code NAF : 55.10Z", "ATECO 56.10.11". The OCR often reads the
#: final Z as a 2.
ACTIVITY_RE = re.compile(
    r"\b(?:CODE\s*)?(?:APE|NAF|ATECO|NACE)\s*[:.\-]?\s*(\d{2})\s*[.,]?\s*(\d{2})\s*([A-Z2])?\b")
#: "MCC 5812", "MCC : 7011" on card slips.
MCC_RE = re.compile(r"\bMCC\s*[:.\-]?\s*(\d{4})\b")
#: "SIRET : 123 456 789 00012", "N° SIRET 12345678900012".
SIRET_RE = re.compile(r"\bSIRE[TN]\b\D{0,12}((?:\d[\s.]?){8}\d(?:[\s.]?\d){0,5})")
#: "RCS PARIS B 123 456 789": the SIREN follows the city of the registry.
RCS_RE = re.compile(r"\bRCS\b[^0-9]{0,30}((?:\d[\s.]?){8}\d)\b")


def extract_activity(lines):
    """Return the printed activity code: ``"NAF:5610A"`` or ``"MCC:5812"``.

    It is the most reliable clue to the nature of a business, when present:
    many French receipts print the APE code next to the SIRET.
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


#: Clues of the country a receipt comes from, with their weight: tax
#: numbers with their country prefix and national identifiers (2), phone
#: prefixes (1). A tax name is shared by several countries ("IVA" in Spain,
#: Italy and Portugal, "TVA" in France and Belgium): these clues tell them
#: apart.
COUNTRY_CLUES = [
    (re.compile(r"\bATU\s?\d{8}\b"), 'AT', 2),
    (re.compile(r"\bBE\s?[01]\d{3}[.\s]?\d{3}[.\s]?\d{3}\b"), 'BE', 2),
    (re.compile(r"\bDE\s?\d{9}\b|\bST\.?\s?-?\s?NR\b|\bSTEUERNUMMER\b"), 'DE', 2),
    (re.compile(r"\bES\s?[A-Z]\d{7}[0-9A-Z]\b"
                r"|\b(?:C\.?\s?I\.?\s?F|N\.?\s?I\.?\s?F)\.?\s*:?\s*[A-HJ-NP-SUVW]-?\d{7}[0-9A-J]\b"),
     'ES', 2),
    (re.compile(r"\bFR\s?[0-9A-Z]{2}\s?\d{3}\s?\d{3}\s?\d{3}\b|\bSIRE[TN]\b"), 'FR', 2),
    (re.compile(r"\bIT\s?\d{11}\b|\bP\.?\s?IVA\b|\bPARTITA\s+IVA\b"), 'IT', 2),
    (re.compile(r"\bLU\s?\d{8}\b"), 'LU', 2),
    (re.compile(r"\bNL\s?\d{9}\s?B\s?\d{2}\b"), 'NL', 2),
    (re.compile(r"\bPT\s?\d{9}\b|\bCONTRIBUINTE\b|\bN\.?\s?I\.?\s?F\.?\s*:?\s*[1-9]\d{8}\b"), 'PT', 2),
    (re.compile(r"\bPL\s?\d{10}\b|\bNIP\b"), 'PL', 2),
    (re.compile(r"\bCHE[-\s]?\d{3}\.?\d{3}\.?\d{3}\b"), 'CH', 2),
]
#: A whole number after the prefix: "+45 PUNTS" or "+43" alone is no phone.
PHONE_PREFIX_RE = re.compile(
    r"\+\s?(351|352|30|31|32|33|34|39|41|43|44|45|46|47|48|49)[\s.()/-]*\d(?:[\s.()/-]?\d){6,}")
PHONE_COUNTRIES = {
    '351': 'PT', '352': 'LU', '30': 'GR', '31': 'NL', '32': 'BE', '33': 'FR', '34': 'ES',
    '39': 'IT', '41': 'CH', '43': 'AT', '44': 'GB', '45': 'DK', '46': 'SE', '47': 'NO',
    '48': 'PL', '49': 'DE',
}


def extract_country_clues(lines):
    """``[(country, weight, text)]``: what tells where the receipt was issued."""
    clues = []
    for line in lines:
        text = strip_accents(line.text).upper()
        for pattern, country, weight in COUNTRY_CLUES:
            clues += [(country, weight, match.group(0)) for match in pattern.finditer(text)]
        clues += [(PHONE_COUNTRIES[match.group(1)], 1, match.group(0))
                  for match in PHONE_PREFIX_RE.finditer(text)]
    return clues


def receipt_country(clues, own_numbers=()):
    """Country the clues point to, or ``None`` when they are missing or disagree.

    ``own_numbers``: tax numbers of the buying company, which an invoice
    prints for the customer and which say nothing of the seller.
    """
    own = {re.sub(r"\W", "", number).upper() for number in own_numbers if number}
    scores = {}
    for country, weight, text in clues:
        if re.sub(r"\W", "", text) in own:
            continue
        scores[country] = scores.get(country, 0) + weight
    if not scores:
        return None
    ranked = sorted(scores.values(), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return None
    return max(scores, key=scores.get)


def extract_company_number(lines):
    """Return the merchant's SIRET (14 digits) or, failing that, its SIREN (9 digits).

    The Luhn check rules out misread numbers: a wrong digit must not point to
    another establishment.
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
    """Return the label under which the receipt prints its tax ("TVA", "IVA", "PTU"...).

    A foreign tax is not deducted like domestic VAT: the module uses this so
    as not to carry it as deductible VAT. Only the lines carrying a rate or
    an amount are examined, which ignores an intra-community VAT number
    printed in the header.
    """
    for line in lines:
        text = normalize(line.text)
        match = TVA_LINE_RE.search(text)
        if not match or not (RATE_RE.search(text) or find_amounts(line.text)):
            continue
        if FRENCH_TVA_RE.search(text):
            return ExtractedField(value="TVA", confidence=0.9, source=line.text)
        label = re.sub(r"[^A-Z]", "", match.group(0))
        if label.startswith("TAXE"):
            label = "TVA"  # "Taxe totale": the VAT of a French invoice
        elif label.startswith("SALES"):
            label = "Sales tax"  # US sales tax, never deductible
        return ExtractedField(value=label, confidence=0.9, source=line.text)
    return ExtractedField(value=None, confidence=0.0)


#: Number of nights: "2 Nuitées x 95,00", "3 nights", "2 Übernachtungen".
NIGHTS_RE = re.compile(
    r"\b(\d{1,2})\s*(?:X\s*)?(?:NUITEES?|NUITS?|NIGHTS?|NACHTE|UBERNACHTUNG(?:EN)?|NOTTI|NOTTE"
    r"|NOCHES?|NOITES?|NOCY|NOCLEGI?|NACHTEN|NETTER|NATTER|NAETTER)\b")
#: Arrival and departure lines of a hotel bill.
ARRIVAL_RE = re.compile(r"\bARRIV|\bCHECK\s*-?\s*IN\b|\bANREISE\b|\bARRIVO\b|\bLLEGADA\b"
                        r"|\bCHEGADA\b|\bPRZYJAZD\b|\bANKOMST\b")
DEPARTURE_RE = re.compile(r"\bDEPART|\bCHECK\s*-?\s*OUT\b|\bABREISE\b|\bPARTENZA\b"
                          r"|\bSALIDA\b|\bPARTIDA\b|\bWYJAZD\b|\bAVREISE\b|\bAFREJSE\b")
#: Longest stay believed from a receipt.
MAX_NIGHTS = 60


def _line_date(text, today, order):
    for pattern, kind, _weight in DATE_PATTERNS:
        for match in pattern.finditer(text):
            found = _build_date(kind, match.groups(), today, order)
            if found:
                return found
    return None


def extract_nights(lines, today=None, order="dmy"):
    """Number of nights of a hotel bill, if the receipt gives it.

    Printed as a quantity ("2 Nuitées"), or deduced from the arrival and
    departure dates.
    """
    today = today or date.today()
    arrival = departure = None
    for line in lines:
        text = normalize(line.text)
        match = NIGHTS_RE.search(text)
        if match and 0 < int(match.group(1)) <= MAX_NIGHTS:
            return ExtractedField(value=int(match.group(1)), confidence=0.8, source=line.text)
        if arrival is None and ARRIVAL_RE.search(text):
            arrival = _line_date(text, today, order)
        elif departure is None and DEPARTURE_RE.search(text):
            departure = _line_date(text, today, order)
    if arrival and departure and 0 < (departure - arrival).days <= MAX_NIGHTS:
        return ExtractedField(value=(departure - arrival).days, confidence=0.75)
    return ExtractedField(value=None, confidence=0.0)


def extract_vat_number(lines):
    """Return the intra-community VAT number, useful to find the supplier."""
    for line in lines:
        text = normalize(line.text)
        match = VAT_NUMBER_RE.search(text)
        if match:
            return ExtractedField(value="FR" + "".join(match.groups()),
                                  confidence=0.8, source=line.text)
    return ExtractedField(value=None, confidence=0.0)


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------

#: Postal code followed by a city: "31150 Fenouillet", "10144 Torino".
POSTAL_CITY_RE = re.compile(r"(?<![\d.,])(\d{5})\s+([A-Z][A-Z\-]{2,})")
#: Toll station: "Sortie ..Muret", "Entree.. Toulouse-S-E",
#: "USCITA: MARCALLO MESERO".
TOLL_STATION_RE = re.compile(
    r"\b(?:SORTIE|ENTREE|USCITA|ENTRATA|AUSFAHRT|EINFAHRT|SALIDA|ENTRADA)\b[\s.:]*"
    r"([A-Z][A-Z\- ]{2,40})")
#: Printed journey: "De Toulouse à Lille", "Lille à Bordeaux (Billi)".
ROUTE_RE = re.compile(r"^\s*(?:DE\s+)?([A-Z][A-Z\-]{3,})\s+A\s+([A-Z][A-Z\-]{3,})\b")
#: Words that a journey or an address passes off as a city.
NOT_A_CITY = {
    "EMPORTER", "CONSOMMER", "PLACE", "PARTIR", "BIENTOT", "VOUS", "PAYER",
    "CEDEX", "FRANCE", "SAINT", "SAINTE", "ROUTE", "AVENUE",
}
#: The shop's address is at the top; the head office's, at the foot of the
#: page, does not say where the purchase was made.
PLACE_HEADER_LINES = 10


def _city(word):
    word = word.strip("-")
    return word if len(word) >= 4 and word not in NOT_A_CITY else None


def trip_places(lines):
    """Return the places of a receipt: ``(keys, cities)``.

    The keys (postal code, toll station) are compared as they are; the
    cities are also looked for in the whole text of the other receipt: the
    ticket "Lille à Bordeaux" joins the hotel "33000 BORDEAUX". The argument
    is a list of text lines, so that it also works on the stored text read.
    """
    places, cities = set(), set()
    for index, raw in enumerate(lines):
        text = normalize(raw)
        if index < PLACE_HEADER_LINES:
            for match in POSTAL_CITY_RE.finditer(text):
                places.add("cp:" + match.group(1))
                city = _city(match.group(2))
                if city:
                    cities.add(city)
        for match in TOLL_STATION_RE.finditer(text):
            # The first two words are enough: the OCR often truncates the
            # rest ("TARBES/EST", "Toulouse-S-E").
            words = re.findall(r"[A-Z]{3,}", match.group(1))[:2]
            if words:
                places.add("gare:" + " ".join(words))
                cities.update(filter(None, (_city(word) for word in words)))
        route = ROUTE_RE.match(text)
        if route:
            cities.update(filter(None, (_city(word) for word in route.groups())))
    return places, cities


#: Street before a city name: "Rue de Lyon" is in Paris, not in Lyon.
STREET_BEFORE_RE = re.compile(
    r"\b(?:RUE|AVENUE|AV|BD|BOULEVARD|PLACE|PL|QUAI|ROUTE|RTE|CHEMIN|ALLEE|IMPASSE|COURS"
    r"|GARE|STRASSE|STR|VIA|CALLE|RUA|STREET|ROAD)\.?\s+(?:DE\s+|DU\s+|DES\s+|D\s*)?$")


def cites_city(city, lines):
    """Tell whether the city is cited, as a whole word, in these lines.

    A street or station named after a city does not count.
    """
    pattern = re.compile(r"(?<![A-Z])%s(?![A-Z])" % re.escape(city))
    for line in lines:
        text = normalize(line)
        for match in pattern.finditer(text):
            if not STREET_BEFORE_RE.search(text[:match.start()]):
                return True
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

#: Labels shown to the user for the fields to check again.
FIELD_LABELS = {
    "merchant": "Merchant",
    "date": "Date",
    "time": "Time",
    "total": "Total",
    "currency": "Currency",
    "tax_rate": "Tax rate",
    "tax_amount": "Tax amount",
    "vat_number": "VAT number",
}
#: Below this threshold, the field is flagged "to check".
LOW_CONFIDENCE = 0.65


#: Countries that write the month before the day.
MONTH_FIRST_COUNTRIES = frozenset({"US"})


def parse(words, today=None, max_age_days=730, default_currency="EUR", buyers=(),
          country=None):
    """Parse a list of positioned words and return a :class:`ScanResult`.

    ``country``: code of the company's country. With the currency read, it
    decides the order of day and month (a receipt in US dollars writes
    "06/26/2026").
    """
    lines = build_lines(words)
    tax_rate, tax_amount, tax_rate_max = extract_taxes(lines)
    currency = extract_currency(lines, default=default_currency)
    month_first = (currency.value == "USD" and currency.confidence >= 0.5
                   or country in MONTH_FIRST_COUNTRIES and currency.value == default_currency)
    scan_date = extract_date(lines, today=today, max_age_days=max_age_days,
                             order="mdy" if month_first else "dmy")
    fields = {
        "merchant": extract_merchant(lines, buyers=buyers),
        "date": scan_date,
        "time": extract_time(lines, date_source=scan_date.source),
        "total": extract_total(lines, currency=currency.value),
        "currency": currency,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "tax_rate_max": tax_rate_max,
        "tax_label": extract_tax_label(lines),
        "activity": extract_activity(lines),
        "company_number": extract_company_number(lines),
        "vat_number": extract_vat_number(lines),
        # Not a field of the expense: no confidence, so as not to weigh in the
        # choice of the richest piece of a receipt.
        "country_clues": ExtractedField(value=extract_country_clues(lines) or None,
                                        confidence=0.0),
        "nights": extract_nights(lines, today=today, order="mdy" if month_first else "dmy"),
    }
    _rate_from_items(fields, lines)
    _swap_total_and_tax(fields)
    _total_from_tax(fields)
    _tax_from_total_line(fields, lines)
    _tax_not_total(fields)
    return ScanResult(lines=lines, fields=fields)


#: Item line with its own rate: "FOCACCIA FORMAGGIO 10,00% 13,00".
ITEM_RATE_RE = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{2})?)\s*%")


def _rate_from_items(fields, lines):
    """The rate printed on the items, when the tax line gives none.

    Italian receipts print the rate of each item and the tax without one
    ("di cui IVA 3,55"). One rate on every item is the rate of the receipt;
    several give the highest, as a ceiling.
    """
    if fields["tax_rate"].value is not None or fields["tax_rate_max"].value is not None:
        return
    if fields["tax_amount"].value is None:
        # Without a tax line, a "25%" on an item is a discount ("App-Joker
        # 25%") or a fat content ("Topfen 20%"), as likely as a rate.
        return
    rates, source = set(), []
    for line in lines:
        text = normalize(line.text)
        if TVA_LINE_RE.search(text) or re.search(r"\bTOT", text) or not find_amounts(line.text):
            continue
        if re.search(r"\b(?:SCONTO|REMISE|RABATT|DESCUENTO|DISCOUNT|PROMO|OFF)\b", text):
            continue  # "SCONTO 20,00% -0,60": a discount, not a rate
        for match in ITEM_RATE_RE.finditer(text):
            rate = _closest_known_rate(float(match.group(1).replace(",", ".")))
            if rate:
                rates.add(rate)
                source.append(line.text)
    if not rates:
        return
    joined = " | ".join(source[:3])
    if len(rates) == 1:
        fields["tax_rate"] = ExtractedField(value=next(iter(rates)), confidence=0.7, source=joined)
    fields["tax_rate_max"] = ExtractedField(value=max(rates), confidence=0.7, source=joined)


def _total_from_tax(fields):
    """Another amount of the total's line, when only that one holds with the tax.

    "TOTALE COMPLESSIVO COCA BOTT 10,00% 19,30 3,90": the OCR glued the
    price of an item to the total. With the tax (1,75) and its single rate
    (10 %) known, the total is the amount whose tax at that rate it is.
    """
    total, tax, rate = fields["total"], fields["tax_amount"].value, fields["tax_rate"].value
    if not (total.value and tax and rate) or not total.source:
        return

    def holds(amount):
        return abs(amount * rate / (100.0 + rate) - tax) <= 0.02

    if holds(total.value):
        return
    candidates = [value for value, _position in find_amounts(total.source)
                  if value != total.value and holds(value)]
    if len(candidates) == 1:
        fields["total"] = ExtractedField(value=candidates[0], confidence=min(total.confidence, 0.7),
                                         source=total.source)


def _swap_total_and_tax(fields):
    """Put back a total and a tax read the other way round.

    On a crumpled or slanted receipt, the OCR can join the amount of each
    line to the label of the other ("inkl. 19% MwSt 12,00 EUR", "Betrag
    1,92"). A tax larger than the total is impossible; when the total is the
    tax of that larger amount at the rate read, the two are exchanged. With
    several rates ("di cui IVA TOTALE COMPLESSIVO 37,09 / ... 37,09 2,83"),
    only the highest is known: the total must then be a possible tax of the
    larger amount, at most at that rate, and the larger amount must be on the
    line the total was read from: a column mix-up, not two unrelated lines.
    """
    total, tax = fields["total"], fields["tax_amount"]
    rate, rate_max = fields["tax_rate"].value, fields["tax_rate_max"].value
    if not (total.value and tax.value and (rate or rate_max)) or tax.value <= total.value:
        return

    def fits(rate):
        return abs(tax.value * rate / (100.0 + rate) - total.value) <= 0.02

    same_line = any(abs(value - tax.value) < 0.005 for value, _position in find_amounts(total.source or ""))
    if not (fits(rate or rate_max)
            or (not rate and same_line
                and total.value <= tax.value * rate_max / (100.0 + rate_max) + 0.02)):
        return
    fields["total"] = ExtractedField(value=tax.value, confidence=min(total.confidence, 0.6),
                                     source=tax.source)
    fields["tax_amount"] = ExtractedField(value=total.value, confidence=min(tax.confidence, 0.6),
                                          source=total.source)


def _tax_from_total_line(fields, lines):
    """The tax printed beside the total, under the label "di cui IVA" of the next line.

    "TOTALE COMPLESSIVO 35,90 4,26" then "di cui IVA 20,00 ...": the two
    columns share their lines, and the next line holds the payment amount.
    The second amount of the total line is the tax when it is smaller.
    """
    total, tax = fields["total"], fields["tax_amount"]
    if not total.value or not total.source:
        return
    rate, rate_max = fields["tax_rate"].value, fields["tax_rate_max"].value

    def possible(amount):
        """Whether this amount can be the tax of the total, at the rates read."""
        if rate:
            return abs(total.value * rate / (100.0 + rate) - amount) <= 0.02
        if rate_max:
            return amount <= total.value * rate_max / (100.0 + rate_max) + 0.02
        return amount < total.value * 0.3

    if tax.value is not None and possible(tax.value):
        return
    for index, line in enumerate(lines[:-1]):
        if line.text != total.source:
            continue
        if not re.search(r"\bDI CUI IVA\b", normalize(lines[index + 1].text)):
            return
        amounts = [value for value, _position in find_positive_amounts(line.text)]
        if len(amounts) >= 2 and amounts[0] == total.value and possible(amounts[-1]):
            fields["tax_amount"] = ExtractedField(
                value=amounts[-1], confidence=min(total.confidence, 0.7), source=line.text)
        return


def _tax_not_total(fields):
    """A tax equal to the total is the total read in the tax's place.

    "DI CUI IVA 0,35 5,67" under a total of 5,67: the other amount of the
    line is the tax.
    """
    total, tax = fields["total"], fields["tax_amount"]
    if not (total.value and tax.value) or abs(total.value - tax.value) > 0.005:
        return
    others = {value for value, _position in find_amounts(tax.source or "")
              if 0 < value < total.value - 0.005}
    if len(others) == 1:
        fields["tax_amount"] = ExtractedField(
            value=others.pop(), confidence=min(tax.confidence, 0.6), source=tax.source)


def fields_to_check(result, names=("date", "total")):
    """Return the labels of the missing or unreliable fields, to be checked.

    The merchant is not included: it is misread one time in two (stylised
    logo, truncated header), and flagging it on every receipt would make the
    warning less useful. What must be right is what goes to the accounts:
    the date and the amount.
    """
    todo = []
    for name in names:
        field = result.fields.get(name)
        if field is None or field.value is None or field.confidence < LOW_CONFIDENCE:
            todo.append(FIELD_LABELS.get(name, name))
    return todo
