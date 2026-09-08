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

    def test_long_month_name(self):
        """« SEPTEMBRE » fait 9 lettres : le motif ne doit pas s'arrêter à 8."""
        recent = date.today() - timedelta(days=4)
        months = {1: "JANVIER", 2: "FEVRIER", 3: "MARS", 4: "AVRIL", 5: "MAI",
                  6: "JUIN", 7: "JUILLET", 8: "AOUT", 9: "SEPTEMBRE",
                  10: "OCTOBRE", 11: "NOVEMBRE", 12: "DECEMBRE"}
        result = self.parse("BOUTIQUE\n%d %s %d\nTOTAL 9,00"
                            % (recent.day, months[recent.month], recent.year))
        self.assertEqual(result.value('date'), recent)

    def test_date_without_year_is_inferred(self):
        """Beaucoup de tickets n'impriment que le jour et le mois."""
        recent = date.today() - timedelta(days=3)
        result = self.parse("SUPERETTE\n%s 10:05\nTOTAL 5,00" % recent.strftime("%d/%m"))
        self.assertEqual(result.value('date'), recent)
        # Année devinée : le champ doit rester signalé à la relecture.
        self.assertLess(result.confidence('date'), parser.LOW_CONFIDENCE)
        self.assertIn("Date", parser.fields_to_check(result))

    def test_old_date_without_year_is_refused(self):
        """Un billet daté « 23 septembre » n'est pas un achat d'il y a un an.

        Sans millésime, la seule inférence possible renverrait à l'année
        précédente : mieux vaut ne rien dater et le signaler.
        """
        far = date.today() - timedelta(days=200)
        result = self.parse("RESERVATION\nDepart le %d/%d\nTOTAL 35,00"
                            % (far.day, far.month))
        self.assertIsNone(result.value('date'))

    def test_decimal_amount_is_not_a_date(self):
        """« 5.67 » ne doit pas devenir le 5 du mois 67."""
        result = self.parse("GARAGE\nPRIX HT 5.67\nTOTAL 6,80")
        self.assertIsNone(result.value('date'))

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
        self.assertEqual(result.value('tax_rate_max'), 20.0)

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

    # -- Tickets à points de conduite -------------------------------------

    def test_amount_after_leader_dots(self):
        """« PRIX TTC......6,80 » : le point de conduite précède le montant."""
        self.assertEqual(parser.find_amounts("PRIX TTC......6,80 euros")[0][0], 6.80)

    def test_toll_receipt(self):
        """Ticket de péage réel : libellés en PRIX HT / PRIX TTC."""
        result = self.parse("""
ASF Lieu-dit Gaussens BP 40037
Date .07/09/26 .PAMIERS
PRIX HT.......5,67 euros TVA 20,00%....1,13 euros
PRIX TTC......6,80 euros
Paiement....6,80 E ..CB
""")
        self.assertEqual(result.value('total'), 6.80)
        self.assertGreater(result.confidence('total'), parser.LOW_CONFIDENCE)
        self.assertEqual(result.value('tax_rate'), 20.0)
        self.assertEqual(result.value('tax_amount'), 1.13)

    # -- Tableau de TVA ---------------------------------------------------

    def test_vat_table(self):
        """Tableau HT / TVA / TTC, format très répandu en caisse.

        Les montants y portent quatre décimales, et la ligne de totaux, qui
        ne commence pas par un taux, ne doit pas être comptée deux fois.
        """
        result = self.parse("""
SAS DERSIM GRILL
TOTAL: 62,50
HT TVA TTC
10%(A) 0,0000 0,0000 0,00
20%(B) 0,0000 0,0000 0,00
10%(C) 56,8182 5,6818 62,50
56,82 5,68 62,50
""")
        self.assertEqual(result.value('total'), 62.50)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 5.68)

    def test_vat_table_with_several_rates(self):
        """Deux taux actifs : la somme fait foi, aucun taux ne s'impose."""
        result = self.parse("""
SUPERMARCHE
TOTAL 100,00
HT TVA TTC
5,5%(A) 20,0000 1,1000 21,10
20%(B) 60,0000 12,0000 72,00
""")
        self.assertIsNone(result.value('tax_rate'))
        self.assertEqual(result.value('tax_amount'), 13.10)
        self.assertEqual(result.value('tax_rate_max'), 20.0)

    def test_vat_table_rows_prefixed_by_tva(self):
        """« TVA 10 % 26,39 2,64 29,03 » : le taux suit le mot TVA.

        Trois colonnes : prendre le dernier montant reviendrait à retenir
        le TTC, donc à additionner les totaux du ticket au lieu des taxes.
        """
        result = self.parse("""
LES 3 BRASSEURS
1 x Repas complet 33,10
HT TVA TTC
TVA 10 % 26,39 2,64 29,03
TVA 20 % 3,39 0,68 4,07
TOTAL 33,10 EUR
""")
        self.assertEqual(result.value('total'), 33.10)
        self.assertEqual(result.value('tax_amount'), 3.32)
        # Aucun taux ne vaut pour la dépense entière ; le plus élevé sert
        # uniquement de plafond au contrôle de cohérence.
        self.assertIsNone(result.value('tax_rate'))
        self.assertEqual(result.value('tax_rate_max'), 20.0)

    def test_vat_rows_without_table_header(self):
        """Mêmes lignes, sans l'en-tête HT/TVA/TTC : le repli doit tenir.

        Le tableau ne se reconnaît alors plus, et c'est la lecture ligne à
        ligne qui doit choisir la deuxième colonne — la taxe — au lieu du
        TTC de fin de ligne.
        """
        result = self.parse("""
LES 3 BRASSEURS
1 x Formule du jour 29,03
TOTAL 33,10 EUR
TVA 10 % 26,39 2,64 29,03
TVA 20 % 3,39 0,68 4,07
""")
        self.assertEqual(result.value('total'), 33.10)
        self.assertEqual(result.value('tax_amount'), 3.32)
        self.assertIsNone(result.value('tax_rate'))
        self.assertEqual(result.value('tax_rate_max'), 20.0)

    def test_no_vat_leaves_every_tax_field_empty(self):
        """Ticket de carte bancaire : rien à déduire, donc rien à proposer."""
        result = self.parse("""
CREDIT AGRICOLE
CB CONTACT A0000000031010
MONTANT 45,00 EUR
DEBIT
""")
        self.assertIsNone(result.value('tax_rate'))
        self.assertIsNone(result.value('tax_rate_max'))
        self.assertIsNone(result.value('tax_amount'))
