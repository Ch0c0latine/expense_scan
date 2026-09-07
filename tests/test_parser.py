# -*- coding: utf-8 -*-
"""Tests du parseur de tickets.

Le parseur ne touche ni à la base ni à OpenCV : on lui fabrique directement
des mots situés, ce qui permet de couvrir les cas tordus (sous-total, TVA
multiple, date de garantie) sans avoir de vraies photos sous la main.
"""
from datetime import date, timedelta

from odoo.tests import common, tagged

from ..ocr import parser
from ..ocr.types import OcrWord


def words_from_text(text, score=0.95, line_height=20.0, char_width=9.0):
    """Fabrique des mots situés à partir d'un ticket écrit en texte.

    Chaque ligne du texte devient une ligne du ticket ; les colonnes sont
    reproduites à partir de la position des caractères, ce qui suffit à
    exercer la reconstruction des lignes et la lecture « dernier montant
    de la ligne ».
    """
    words = []
    for row, line in enumerate(text.strip("\n").split("\n")):
        top = row * line_height
        column = 0
        for chunk in line.split(" "):
            if not chunk:
                column += 1
                continue
            words.append(OcrWord(
                text=chunk,
                score=score,
                left=column * char_width,
                top=top,
                right=(column + len(chunk)) * char_width,
                bottom=top + line_height * 0.7,
            ))
            column += len(chunk) + 1
    return words


@tagged('post_install', '-at_install')
class TestReceiptParser(common.TransactionCase):

    def parse(self, text, **kwargs):
        return parser.parse(words_from_text(text), **kwargs)

    # -- Montants ---------------------------------------------------------

    def test_amount_formats(self):
        self.assertEqual(parser.find_amounts("TOTAL 12,50")[0][0], 12.50)
        self.assertEqual(parser.find_amounts("TOTAL 12.50")[0][0], 12.50)
        self.assertEqual(parser.find_amounts("TOTAL 1 234,56")[0][0], 1234.56)
        self.assertEqual(parser.find_amounts("TOTAL 1.234,56")[0][0], 1234.56)

    def test_rate_is_not_an_amount(self):
        """« 20,00 % » est un taux : il ne doit pas être lu comme un montant."""
        self.assertEqual(parser.find_amounts("TVA 20,00 %"), [])

    # -- Total ------------------------------------------------------------

    def test_total_prefers_net_a_payer(self):
        result = self.parse("""
CARREFOUR MARKET
SOUS-TOTAL 41,20
TOTAL HT 34,33
TVA 20,00 % 6,87
NET A PAYER 41,20
RENDU MONNAIE 8,80
""")
        self.assertEqual(result.value('total'), 41.20)
        self.assertGreater(result.confidence('total'), parser.LOW_CONFIDENCE)

    def test_total_ignores_sub_total_and_ht(self):
        result = self.parse("""
BOULANGERIE DUPONT
SOUS-TOTAL 9,90
TOTAL HT 8,25
TOTAL 9,90
""")
        self.assertEqual(result.value('total'), 9.90)

    def test_total_on_next_line(self):
        """Libellé et montant sur deux lignes, colonne étroite."""
        result = self.parse("""
LE PETIT CAFE
TOTAL A PAYER
7,80
""")
        self.assertEqual(result.value('total'), 7.80)

    def test_total_takes_last_amount_of_the_line(self):
        """La colonne de gauche porte souvent une quantité ou un prix unitaire."""
        result = self.parse("""
PHARMACIE DU CENTRE
TOTAL 3 X 5,20 15,60
""")
        self.assertEqual(result.value('total'), 15.60)

    def test_total_fallback_is_flagged(self):
        """Sans mot-clé, on propose un montant mais avec une faible confiance."""
        result = self.parse("""
KIOSQUE
ARTICLE A 2,00
ARTICLE B 3,50
5,50
""")
        self.assertEqual(result.value('total'), 5.50)
        self.assertLess(result.confidence('total'), parser.LOW_CONFIDENCE)
        self.assertIn("Total", parser.fields_to_check(result))

    # -- Date -------------------------------------------------------------

    def test_date_french_format(self):
        recent = date.today() - timedelta(days=3)
        result = self.parse("SUPERETTE\nLE %s A 14:32\nTOTAL 5,00" % recent.strftime("%d/%m/%Y"))
        self.assertEqual(result.value('date'), recent)

    def test_date_two_digit_year(self):
        recent = date.today() - timedelta(days=10)
        result = self.parse("SUPERETTE\n%s 09:10\nTOTAL 5,00" % recent.strftime("%d/%m/%y"))
        self.assertEqual(result.value('date'), recent)

    def test_date_written_month(self):
        recent = date.today() - timedelta(days=5)
        months = {1: "JANV", 2: "FEVR", 3: "MARS", 4: "AVRIL", 5: "MAI", 6: "JUIN",
                  7: "JUIL", 8: "AOUT", 9: "SEPT", 10: "OCT", 11: "NOV", 12: "DEC"}
        text = "TABAC PRESSE\n%d %s %d\nTOTAL 5,00" % (
            recent.day, months[recent.month], recent.year)
        result = self.parse(text)
        self.assertEqual(result.value('date'), recent)

    def test_future_date_is_rejected(self):
        """Une date de validité ne doit pas devenir la date de la dépense."""
        future = date.today() + timedelta(days=400)
        result = self.parse("MAGASIN\nCARTE VALIDE %s\nTOTAL 5,00"
                            % future.strftime("%d/%m/%Y"))
        self.assertIsNone(result.value('date'))

    def test_too_old_date_is_rejected(self):
        old = date.today() - timedelta(days=1500)
        result = self.parse("MAGASIN\n%s\nTOTAL 5,00" % old.strftime("%d/%m/%Y"))
        self.assertIsNone(result.value('date'))

    # -- Enseigne ---------------------------------------------------------

    def test_merchant_is_the_header_line(self):
        result = self.parse("""
CARREFOUR MARKET
12 RUE DE LA PAIX
75002 PARIS
TEL 01 23 45 67 89
TOTAL 12,00
""")
        self.assertEqual(result.value('merchant'), "Carrefour Market")

    def test_merchant_skips_ticket_headers(self):
        result = self.parse("""
TICKET DE CAISSE
BOULANGERIE DUPONT
TOTAL 3,20
""")
        self.assertEqual(result.value('merchant'), "Boulangerie Dupont")

    # -- TVA et devise ----------------------------------------------------

    def test_single_vat_rate(self):
        result = self.parse("""
RESTAURANT
TVA 10,00 % 4,55 0,45
TOTAL 5,00
""")
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 0.45)

    def test_multiple_vat_rates_are_not_applied(self):
        """Deux taux sur un ticket : aucun ne peut être reporté seul."""
        result = self.parse("""
SUPERMARCHE
TVA 5,50 % 10,00 0,55
TVA 20,00 % 10,00 2,00
TOTAL 22,55
""")
        self.assertIsNone(result.value('tax_rate'))
        self.assertEqual(result.value('tax_amount'), 2.55)

    def test_currency_detection(self):
        result = self.parse("BAR DU PORT\nTOTAL 8,40 EUR")
        self.assertEqual(result.value('currency'), "EUR")

    # -- Synthèse ---------------------------------------------------------

    def test_clean_receipt_needs_no_review(self):
        recent = date.today() - timedelta(days=1)
        result = self.parse("""
CARREFOUR MARKET
LE %s A 18:04
ARTICLE A 2,00
ARTICLE B 3,50
NET A PAYER 5,50
""" % recent.strftime("%d/%m/%Y"))
        self.assertEqual(parser.fields_to_check(result), [])

    def test_empty_scan_flags_everything(self):
        result = parser.parse([])
        self.assertEqual(
            sorted(parser.fields_to_check(result)),
            sorted(["Marchand", "Date", "Total"]),
        )
