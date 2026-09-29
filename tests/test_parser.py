# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tests du parseur de tickets.

Le parseur n'utilise ni la base de données ni OpenCV : les tests lui
fournissent directement des mots situés, ce qui permet de couvrir des cas
particuliers (sous-total, TVA multiple, date de garantie) sans photo réelle.
"""
from datetime import date, timedelta

from odoo.tests import common, tagged

from ..ocr import parser
from ..ocr.types import OcrWord


def words_from_text(text, score=0.95, line_height=20.0, char_width=9.0):
    """Construit des mots situés à partir d'un ticket écrit en texte.

    Chaque ligne du texte devient une ligne du ticket ; les colonnes sont
    reproduites d'après la position des caractères, ce qui suffit à tester
    la reconstruction des lignes et la lecture du « dernier montant de la
    ligne ».
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
        """Sans mot-clé, un montant est proposé avec une faible confiance."""
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
        """Une date sans année, trop ancienne, n'est pas inférée.

        Sans année, la seule inférence possible renverrait à l'année
        précédente : la date reste vide et le champ est signalé.
        """
        far = date.today() - timedelta(days=200)
        result = self.parse("RESERVATION\nDepart le %d/%d\nTOTAL 35,00"
                            % (far.day, far.month))
        self.assertIsNone(result.value('date'))

    def test_decimal_amount_is_not_a_date(self):
        """« 5.67 » n'est pas une date (jour 5, mois 67)."""
        result = self.parse("GARAGE\nPRIX HT 5.67\nTOTAL 6,80")
        self.assertIsNone(result.value('date'))

    def test_future_date_is_rejected(self):
        """Une date de validité n'est pas retenue comme date de la dépense."""
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

    def test_empty_scan_flags_what_goes_to_accounting(self):
        """Rien de lu : la date et le total sont signalés, pas l'enseigne.

        L'enseigne est souvent mal lue : la signaler à chaque ticket
        rendrait l'avertissement inutile.
        """
        result = parser.parse([])
        self.assertEqual(
            sorted(parser.fields_to_check(result)),
            sorted(["Date", "Total"]),
        )

    # -- Tickets à points de conduite -------------------------------------

    def test_amount_after_leader_dots(self):
        """« PRIX TTC......6,80 » : le point de conduite précède le montant."""
        self.assertEqual(parser.find_amounts("PRIX TTC......6,80 euros")[0][0], 6.80)

    def test_toll_receipt(self):
        """Ticket de péage réel : libellés en PRIX HT / PRIX TTC."""
        result = self.parse("""
ASF Lieu-dit Les Pins BP 10017
Date .07/09/26 .BOURGNEUF
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
        """Tableau HT / TVA / TTC, format courant en caisse.

        Les montants ont quatre décimales ; la ligne de totaux, qui ne
        commence pas par un taux, ne doit pas être comptée deux fois.
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

    def test_vat_table_header_says_montant_not_tva(self):
        """« Code Taux HT Montant TTC » : la colonne de taxe ne dit pas TVA.

        Une caisse de restauration rapide, sans « % » sur le taux ni le mot
        TVA en en-tête. Sans le mot « Taux » à côté de « HT », le taux ne se
        rattache à rien et la dépense retombe sur la taxe par défaut de la
        catégorie, dont le taux peut différer de celui du ticket (20 %
        retenu au lieu de 10 %).
        """
        result = self.parse("""
BURGER ZORGLUB
Menu Big Combo 15,90
Total HT : 18,00
Total TVA : 1,80
Total TTC : 19,80
Code Taux HT Montant TTC
A 10,00 18,00 1,80 19,80
""")
        self.assertEqual(result.value('total'), 19.80)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_rate_max'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.80)

    def test_vat_table_with_htva_and_tvac_columns(self):
        """« TVA % Taxe HTVA TVAC » : hors TVA et TVA comprise, libellés belges."""
        result = self.parse("""
PIZZERIA ZORGLUB
23/09/2026 16:54:23
1 3 fromages 14,00 14,00 B
Total: 14,00 EUR
CB 14,00 EUR
TVA % Taxe HTVA TVAC
B 10,00 1,27 12,73 14,00
Total 1,27 12,73 14,00
""")
        self.assertEqual(result.value('total'), 14.00)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.27)

    def test_vat_table_header_merged_with_an_unrelated_total(self):
        """En-tête de tableau accolée par l'OCR à un total sans rapport.

        Sur un ticket d'automate, la ligne d'en-tête « HT TVA TTC » se
        retrouve collée à un total voisin (« TOTAL EN EUROS : 15,80 HT TVA
        TTC ») et porte alors, comme une ligne de valeurs, un montant qui
        n'est pas la taxe. Sans traitement, ce montant (15,80, le total)
        serait pris pour la TVA au lieu des 1,44 imprimés.
        """
        result = self.parse("""
ZORGLUB AUTOMATE
Trajet unitaire x10 1 x 15,80 = 15,80
Taux Paiement en CB TVA en Euros TOTAL EN EUROS : 15,80 HT TVA TTC
10,00 14,36 1,44 15,80
""")
        self.assertEqual(result.value('total'), 15.80)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.44)

    def test_two_rates_on_lines_that_also_spell_out_the_column_labels(self):
        """Un ticket à deux taux, dont les lignes citent aussi HT/TVA/TTC.

        Contrairement à la ligne d'en-tête ci-dessus, les libellés y sont
        séparés par des montants (« 53,64 HT 5,36 TVA 59,00 TTC ») : ce sont
        de vraies lignes de valeurs, pas un en-tête à ignorer. Les deux
        taux doivent rester comptés.
        """
        result = self.parse("""
RESTAURANT ZORGLUB
1 x Plat du jour 53,64
TVA 10 % 53,64 HT 5,36 TVA 59,00 A TTC
TVA 20 % 10,00 2,00 12,00 B
TOTAL 71,00 EUR
""")
        self.assertEqual(result.value('total'), 71.00)
        self.assertIsNone(result.value('tax_rate'))
        self.assertEqual(result.value('tax_amount'), 7.36)
        self.assertEqual(result.value('tax_rate_max'), 20.0)

    def test_vat_rows_without_table_header(self):
        """Mêmes lignes, sans l'en-tête HT/TVA/TTC : le repli doit tenir.

        Le tableau n'est plus reconnu : la lecture ligne à ligne doit
        choisir la deuxième colonne (la taxe) et non le TTC de fin de ligne.
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

    # -- Sens de lecture --------------------------------------------------

    def test_reading_direction_upright(self):
        result = self.parse("""
ASF Lieu-dit Les Pins BP 10017
PRIX HT.......5,67 euros
TVA 20,00%....1,13 euros
PRIX TTC......6,80 euros
""")
        self.assertEqual(parser.reading_direction(result.lines), 1)

    def test_reading_direction_upside_down(self):
        """Photo à 180° : les mots sont corrects mais leurs positions inversées.

        Le moteur redresse chaque ligne à la lecture, donc le texte paraît
        correct ; en revanche les montants précèdent leur libellé et les
        lignes sont ordonnées de la dernière à la première.
        """
        result = self.parse("""
6,80 euros PRIX TTC
1,13 euros TVA 20,00%
5,67 euros PRIX HT
ASF Lieu-dit Les Pins BP 10017
""")
        self.assertEqual(parser.reading_direction(result.lines), -1)

    def test_reading_direction_stays_neutral_without_evidence(self):
        """Un ticket sans libellé de montant ne permet pas de trancher."""
        result = self.parse("CREDIT AGRICOLE\nCB CONTACT\n45,00 EUR")
        self.assertEqual(parser.reading_direction(result.lines), 0)

    # -- Tickets étrangers --------------------------------------------------

    def test_a_dotted_date_is_not_an_amount(self):
        """« 20.08.2026 » et « 27.05.25 » : ni 20,08, ni 27,05, ni 5,25."""
        self.assertEqual(parser.find_amounts("Datum 20.08.2026 12:30"), [])
        self.assertEqual(parser.find_amounts("Data 27.05.25 18:02"), [])
        result = self.parse("KONZUM\n20.08.2026 12:30\nZa platiti 13,40 EUR",
                            today=date(2026, 9, 1))
        self.assertEqual(result.value('total'), 13.40)
        self.assertEqual(result.value('date'), date(2026, 8, 20))

    def test_american_dates_on_a_dollar_receipt(self):
        result = self.parse("CORNER DELI\n08/07/2026 10:20 AM\nTOTAL $28.12",
                            today=date(2026, 9, 1))
        self.assertEqual(result.value('date'), date(2026, 8, 7))
        # Un jour au-delà de 12 : l'ordre ne fait pas de doute, même en euros.
        result = self.parse("CAFE\n06/26/2026\nTOTAL 4,50 EUR", today=date(2026, 9, 1))
        self.assertEqual(result.value('date'), date(2026, 6, 26))

    def test_a_card_expiry_is_not_a_date(self):
        """« 08/25 » sans année : mois et année d'une carte, pas le 25 août."""
        result = self.parse("CAFE\nCARTE 08/25\nTOTAL 4,50 EUR", today=date(2026, 9, 1))
        self.assertIsNone(result.value('date'))

    def test_norwegian_kroner(self):
        result = self.parse("REMA 1000\nOrg.nr 979 443 137 MVA\n3 varer\nTotalt 47,00\nkr 47,00\n"
                            "Kontant 200,00\nVeksel 153,00", default_currency='EUR')
        self.assertEqual(result.value('currency'), 'NOK')
        self.assertEqual(result.value('total'), 47.00)

    def test_swedish_item_header_is_not_the_total(self):
        result = self.parse("COOP\nBeskrivning Pris Mängd Summa(SEK)\nMjölk 24,55 1 24,55\n"
                            "Moms % Moms Netto Brutto\nKort 759,81\nBetalat 759,81 kr\nKvitto")
        self.assertEqual(result.value('total'), 759.81)
        self.assertEqual(result.value('currency'), 'SEK')

    def test_croatian_item_column_is_not_the_total(self):
        result = self.parse("PEKARA\nUkupno 1 kom 2,50 2,50\nUkupno: 13,95 EUR")
        self.assertEqual(result.value('total'), 13.95)

    def test_a_date_glued_to_the_time(self):
        result = self.parse("MERCADONA\n0178 230186/05 24.02.2618:18\nTOTAL 12,40",
                            today=date(2026, 3, 1))
        self.assertEqual(result.value('date'), date(2026, 2, 24))

    def test_opening_hours_are_not_a_date(self):
        """« Lu-Je : 08.30-20.00 » n'est pas le 30 août 2020."""
        result = self.parse("CARREFOUR\nLu-Je : 08.30-20.00\nTOTAL 12,40",
                            today=date(2026, 3, 1), max_age_days=3650)
        self.assertIsNone(result.value('date'))

    def test_currency_from_legal_mentions(self):
        """Sans devise imprimée, les mentions légales désignent le pays."""
        cases = [
            ("KIWI\nOrg.nr 979 443 137 MVA\nTotalt 38,00", 'NOK'),
            ("ICA\nOrg nr 556677-8899\nMoms 12%\nKvitto\nTotalt 38,00", 'SEK'),
            ("HOTEL KRAKOW\nNIP 7010906616\nSUMA 158,00", 'PLN'),
            ("CORNER DELI\n12 MAIN ST, BROOKLYN NY 11201\nTOTAL 28.12", 'USD'),
            ("MIGROS\nCHE-105.829.940 MWST\nTOTAL 12.40", 'CHF'),
        ]
        for text, currency in cases:
            self.assertEqual(self.parse(text, default_currency='EUR').value('currency'),
                             currency, text)
        # Une plateforme de réservation britannique facture en euros.
        result = self.parse("BOOKING.COM\nVAT ID: GB855349007\nTOTAL 123,86", default_currency='EUR')
        self.assertEqual(result.value('currency'), 'EUR')

    def test_swiss_receipt_total(self):
        """« Article Quant Prix Action Total » est l'en-tête des articles ;
        « Total en EUR » une conversion : le total est « SOMME CHF »."""
        result = self.parse("MIGROS\nArticle Quant Prix Action Total\nPain 1 1.95 1.95\n"
                            "SOMME CHF 30.60\nTotal en EUR 32.80\nVisa Debit 30.60")
        self.assertEqual(result.value('currency'), 'CHF')
        self.assertEqual(result.value('total'), 30.60)

    def test_bottle_deposit_is_not_the_total(self):
        result = self.parse("TRADER JOE'S\nTotal Bottle Deposit $0.20\nTotal Savings: -$2.10\n"
                            "Balance to pay $53.37\nVISA $53.37")
        self.assertEqual(result.value('total'), 53.37)

    def test_norwegian_sum_of_items_beats_the_vat_table(self):
        result = self.parse("KIWI\nOrg.nr 979 443 137 MVA\nSum 3 varer 22,00\n"
                            "Mva% Grunnlag Mva Sum\nSum 17,83 4,47 22,30")
        self.assertEqual(result.value('total'), 22.00)
