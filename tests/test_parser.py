# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tests of the receipt parser.

The parser uses neither the database nor OpenCV: the tests give it
positioned words directly, which covers special cases (subtotal, several VAT
rates, warranty date) without a real photo.
"""
from datetime import date, timedelta

from odoo.tests import common, tagged

from ..ocr import parser
from ..ocr.types import OcrWord


def words_from_text(text, score=0.95, line_height=20.0, char_width=9.0):
    """Build positioned words from a receipt written as text.

    Each line of the text becomes a line of the receipt; the columns follow
    the position of the characters, which is enough to test the rebuilding
    of the lines and the reading of the "last amount of the line".
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

    # -- Amounts ----------------------------------------------------------

    def test_amount_formats(self):
        self.assertEqual(parser.find_amounts("TOTAL 12,50")[0][0], 12.50)
        self.assertEqual(parser.find_amounts("TOTAL 12.50")[0][0], 12.50)
        self.assertEqual(parser.find_amounts("TOTAL 1 234,56")[0][0], 1234.56)
        self.assertEqual(parser.find_amounts("TOTAL 1.234,56")[0][0], 1234.56)

    def test_rate_is_not_an_amount(self):
        """'20,00 %' is a rate: it must not be read as an amount."""
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
        """Label and amount on two lines, narrow column."""
        result = self.parse("""
LE PETIT CAFE
TOTAL A PAYER
7,80
""")
        self.assertEqual(result.value('total'), 7.80)

    def test_total_takes_last_amount_of_the_line(self):
        """The left column often holds a quantity or a unit price."""
        result = self.parse("""
PHARMACIE DU CENTRE
TOTAL 3 X 5,20 15,60
""")
        self.assertEqual(result.value('total'), 15.60)

    def test_total_fallback_is_flagged(self):
        """Without a keyword, an amount is suggested with a low confidence."""
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
        """'SEPTEMBRE' has 9 letters: the pattern must not stop at 8."""
        recent = date.today() - timedelta(days=4)
        months = {1: "JANVIER", 2: "FEVRIER", 3: "MARS", 4: "AVRIL", 5: "MAI",
                  6: "JUIN", 7: "JUILLET", 8: "AOUT", 9: "SEPTEMBRE",
                  10: "OCTOBRE", 11: "NOVEMBRE", 12: "DECEMBRE"}
        result = self.parse("BOUTIQUE\n%d %s %d\nTOTAL 9,00"
                            % (recent.day, months[recent.month], recent.year))
        self.assertEqual(result.value('date'), recent)

    def test_date_without_year_is_inferred(self):
        """Many receipts only print the day and the month."""
        recent = date.today() - timedelta(days=3)
        result = self.parse("SUPERETTE\n%s 10:05\nTOTAL 5,00" % recent.strftime("%d/%m"))
        self.assertEqual(result.value('date'), recent)
        # Guessed year: the field must stay flagged for review.
        self.assertLess(result.confidence('date'), parser.LOW_CONFIDENCE)
        self.assertIn("Date", parser.fields_to_check(result))

    def test_old_date_without_year_is_refused(self):
        """A date without a year, too old, is not inferred.

        Without a year, the only possible inference would point to the
        previous year: the date stays empty and the field is flagged.
        """
        far = date.today() - timedelta(days=200)
        result = self.parse("RESERVATION\nDepart le %d/%d\nTOTAL 35,00"
                            % (far.day, far.month))
        self.assertIsNone(result.value('date'))

    def test_decimal_amount_is_not_a_date(self):
        """'5.67' is not a date (day 5, month 67)."""
        result = self.parse("GARAGE\nPRIX HT 5.67\nTOTAL 6,80")
        self.assertIsNone(result.value('date'))

    def test_future_date_is_rejected(self):
        """A validity date is not taken as the date of the expense."""
        future = date.today() + timedelta(days=400)
        result = self.parse("MAGASIN\nCARTE VALIDE %s\nTOTAL 5,00"
                            % future.strftime("%d/%m/%Y"))
        self.assertIsNone(result.value('date'))

    def test_too_old_date_is_rejected(self):
        old = date.today() - timedelta(days=1500)
        result = self.parse("MAGASIN\n%s\nTOTAL 5,00" % old.strftime("%d/%m/%Y"))
        self.assertIsNone(result.value('date'))

    # -- Merchant ---------------------------------------------------------

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

    # -- VAT and currency ------------------------------------------------

    def test_single_vat_rate(self):
        result = self.parse("""
RESTAURANT
TVA 10,00 % 4,55 0,45
TOTAL 5,00
""")
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 0.45)

    def test_multiple_vat_rates_are_not_applied(self):
        """Two rates on a receipt: neither can be carried alone."""
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

    # -- Summary ----------------------------------------------------------

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
        """Nothing read: the date and the total are flagged, not the merchant.

        The merchant is often misread: flagging it on every receipt would
        make the warning useless.
        """
        result = parser.parse([])
        self.assertEqual(
            sorted(parser.fields_to_check(result)),
            sorted(["Date", "Total"]),
        )

    # -- Receipts with leader dots ----------------------------------------

    def test_amount_after_leader_dots(self):
        """'PRIX TTC......6,80': the leader dot precedes the amount."""
        self.assertEqual(parser.find_amounts("PRIX TTC......6,80 euros")[0][0], 6.80)

    def test_toll_receipt(self):
        """Real toll receipt: labels PRIX HT / PRIX TTC."""
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

    # -- VAT table ------------------------------------------------------

    def test_vat_table(self):
        """Net / VAT / gross table, a common till format.

        The amounts have four decimals; the line of totals, which does not
        start with a rate, must not be counted twice.
        """
        result = self.parse("""
SAS ZORGLUB GRILL
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
        """Two active rates: the sum prevails, no rate applies."""
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
        """'TVA 10 % 26,39 2,64 29,03': the rate follows the word TVA.

        Three columns: taking the last amount would keep the gross, and so
        add up the receipt's totals instead of its taxes.
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
        # No rate holds for the whole expense; the highest only serves as
        # the ceiling of the consistency check.
        self.assertIsNone(result.value('tax_rate'))
        self.assertEqual(result.value('tax_rate_max'), 20.0)

    def test_vat_table_header_says_montant_not_tva(self):
        """'Code Taux HT Montant TTC': the tax column does not say TVA.

        A fast food till, with no "%" on the rate and no word TVA in the
        header. Without the word "Taux" next to "HT", the rate is attached to
        nothing and the expense falls back on the category's default tax,
        whose rate may differ from the receipt's (20% kept instead of 10%).
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
        """'TVA % Taxe HTVA TVAC': without VAT and VAT included, Belgian labels."""
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
        """Table header glued by the OCR to an unrelated total.

        On a vending machine receipt, the header line "HT TVA TTC" ends up
        glued to a nearby total ("TOTAL EN EUROS : 15,80 HT TVA TTC") and so
        carries, like a line of values, an amount that is not the tax.
        Without special handling, this amount (15.80, the total) would be
        taken for the VAT instead of the 1.44 printed.
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
        """A receipt with two rates, whose lines also spell out HT/TVA/TTC.

        Unlike the header line above, the labels are separated by amounts
        ("53,64 HT 5,36 TVA 59,00 TTC"): these are real lines of values, not
        a header to ignore. Both rates must still be counted.
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
        """Same lines, without the HT/TVA/TTC header: the fallback must hold.

        The table is no longer recognised: the line by line reading must pick
        the second column (the tax) and not the gross at the end of the line.
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
        """Card slip: nothing to deduct, so nothing to suggest."""
        result = self.parse("""
CREDIT AGRICOLE
CB CONTACT A0000000031010
MONTANT 45,00 EUR
DEBIT
""")
        self.assertIsNone(result.value('tax_rate'))
        self.assertIsNone(result.value('tax_rate_max'))
        self.assertIsNone(result.value('tax_amount'))

    # -- Reading direction ------------------------------------------------

    def test_reading_direction_upright(self):
        result = self.parse("""
ASF Lieu-dit Les Pins BP 10017
PRIX HT.......5,67 euros
TVA 20,00%....1,13 euros
PRIX TTC......6,80 euros
""")
        self.assertEqual(parser.reading_direction(result.lines), 1)

    def test_reading_direction_upside_down(self):
        """Photo at 180°: the words are right but their positions reversed.

        The engine straightens each line as it reads, so the text looks
        right; but the amounts precede their label and the lines are ordered
        from the last to the first.
        """
        result = self.parse("""
6,80 euros PRIX TTC
1,13 euros TVA 20,00%
5,67 euros PRIX HT
ASF Lieu-dit Les Pins BP 10017
""")
        self.assertEqual(parser.reading_direction(result.lines), -1)

    def test_reading_direction_of_a_spanish_receipt(self):
        """Spanish words decide too; "V sa Credit" of the card slip at the
        bottom is no company header, "NUM. TOTAL ART." near the top no footer.
        """
        upright = self.parse("""
ALCAMPO SANT ADRIA
CIF: A28090108
HARINA DE TRIGO 1,00
NUM. TOTAL ART. VENDIDOS 1
TOT 1,00
TARJETA 1,00
CAMBIO 0,00
V sa Credit
GRACIAS POR SU VISITA
""")
        self.assertEqual(parser.reading_direction(upright.lines), 1)
        upside_down = self.parse("""
VISITA SU POR GRACIAS
Credit sa V
0,00 CAMBIO
1,00 TARJETA
1,00 TOT
1 VENDIDOS ART. TOTAL NUM.
1,00 TRIGO DE HARINA
A28090108 CIF:
ADRIA SANT ALCAMPO
""")
        self.assertEqual(parser.reading_direction(upside_down.lines), -1)

    def test_reading_direction_stays_neutral_without_evidence(self):
        """A receipt without an amount label cannot decide."""
        result = self.parse("CREDIT AGRICOLE\nCB CONTACT\n45,00 EUR")
        self.assertEqual(parser.reading_direction(result.lines), 0)

    # -- Foreign receipts ---------------------------------------------------

    def test_a_dotted_date_is_not_an_amount(self):
        """'20.08.2026' and '27.05.25': neither 20.08, nor 27.05, nor 5.25."""
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
        # A day above 12: the order leaves no doubt, even in euros.
        result = self.parse("CAFE\n06/26/2026\nTOTAL 4,50 EUR", today=date(2026, 9, 1))
        self.assertEqual(result.value('date'), date(2026, 6, 26))

    def test_a_card_expiry_is_not_a_date(self):
        """'08/25' without a year: month and year of a card, not 25 August."""
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
        """'Lu-Je : 08.30-20.00' is not 30 August 2020."""
        result = self.parse("CARREFOUR\nLu-Je : 08.30-20.00\nTOTAL 12,40",
                            today=date(2026, 3, 1), max_age_days=3650)
        self.assertIsNone(result.value('date'))

    def test_currency_from_legal_mentions(self):
        """Without a printed currency, the legal mentions point to the country."""
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
        # A British booking platform invoices in euros.
        result = self.parse("BOOKING.COM\nVAT ID: GB855349007\nTOTAL 123,86", default_currency='EUR')
        self.assertEqual(result.value('currency'), 'EUR')

    def test_swiss_receipt_total(self):
        """'Article Quant Prix Action Total' is the item header; 'Total en
        EUR' a conversion: the total is 'SOMME CHF'."""
        result = self.parse("MIGROS\nArticle Quant Prix Action Total\nPain 1 1.95 1.95\n"
                            "SOMME CHF 30.60\nTotal en EUR 32.80\nVisa Debit CHF 30.60")
        self.assertEqual(result.value('currency'), 'CHF')
        self.assertEqual(result.value('total'), 30.60)

    def test_bottle_deposit_is_not_the_total(self):
        result = self.parse("TRADER JOE'S\nTotal Bottle Deposit $0.20\nTotal Savings: -$2.10\n"
                            "Balance to pay $53.37\nVISA $53.37")
        self.assertEqual(result.value('total'), 53.37)

    def test_a_total_misread_does_not_take_the_tax_line_below(self):
        """The amount under an unreadable total is not the total if it is a tax."""
        result = self.parse("BISTRO\nKawa 12,00\nSUMA PLN 00'09\nPTU B 8% 4,44\nGotowka 00'09")
        self.assertNotEqual(result.value('total'), 4.44)
        self.assertIn(parser.FIELD_LABELS['total'], parser.fields_to_check(result))

    def test_a_vat_header_glued_to_an_amount_is_still_a_header(self):
        """Lidl: the promotion total ends up on the header line of the table."""
        result = self.parse(
            "LIDL\nA payer 64,37\nCarte 64,37\n"
            "Total Promotion TVA Taux MONT.TTC MONT.TVA TOTAL HT 3,02\n"
            "A 5,5% 44,32 2,31 42,01\nB 20% 20,05 3,34 16,71")
        self.assertEqual(result.value('tax_amount'), 5.65)
        self.assertEqual(result.value('tax_rate_max'), 20.0)

    def test_a_label_is_not_the_merchant(self):
        """Shopcaisse: a logo instead of the name, then the order comment."""
        result = self.parse("TICKET N 14401 - VENTE\n23/09/2026 16:54:23\nCommentaire:\n"
                            "Demande du client\nTotal: 14,00 EUR\nCB 14,00 EUR")
        self.assertNotEqual(result.value('merchant'), "Commentaire:")
        self.assertNotIn("Commentaire", result.value('merchant') or "")

    def test_a_street_word_in_the_name(self):
        """"Brasserie du Quai" is a name; "3 quai des Bateliers" the address."""
        result = self.parse("Brasserie du Quai\n3 quai des Bateliers\n67000 Strasbourg\n"
                            "27/09/2026 20:14\nTOTAL TTC 43,80 €")
        self.assertEqual(result.value('merchant'), "Brasserie du Quai")
        result = self.parse("Place de la Gare\nCafe 2,40\nTOTAL 2,40")
        self.assertNotEqual(result.value('merchant'), "Place de la Gare")

    def test_vat_table_rate_in_the_header(self):
        result = self.parse("GASTHAUS\nSUMME EUR 39,60\nMwSt 19% Netto MwSt Brutto\n"
                            "33,28 6,32 39,60\nBezahlt mit Karte")
        self.assertEqual(result.value('tax_amount'), 6.32)
        self.assertEqual(result.value('tax_rate'), 19.0)
        result = self.parse("BRASSERIE\nTOTAL TTC 43,80 €\nTVA 10 % HT TVA\n39,82 3,98")
        self.assertEqual(result.value('tax_amount'), 3.98)

    def test_total_and_tax_read_the_other_way_round(self):
        """Crumpled parking ticket: each amount joined to the other label."""
        result = self.parse("PARKHAUS AM MARKT\nBetrag:\ninkl. 19% MwSt 12,00 EUR\n"
                            "Vielen Dank! 1,92")
        self.assertEqual(result.value('total'), 12.00)
        self.assertEqual(result.value('tax_amount'), 1.92)

    def test_spanish_vat_tables(self):
        """Headers "TIPO BASE CUOTA", "IVA% IVA + P N = PVP" (Lidl), "Imp. %
        Base Cuota" with amounts under one euro printed without their zero,
        "Tasa Sin IVA Total IVA IVA Inc."."""
        cases = [
            ("CARREFOUR EXPRESS\n3 ART. TOTAL A PAGAR : 3,83\nTIPO BASE CUOTA\n"
             "4,00% 1,35 0,05\n10,00% 1,43 0,14\n21,00% 0,71 0,15\nVENTA 3,83", 3.83, 0.34),
            ("LIDL\nTotal 61,95\nIVA% IVA + P N = PVP\nA 4% 0,74 18,48 19,22\n"
             "B 10% 3,88 38,85 42,73\nSuma 4,62 57,33 61,95", 61.95, 4.62),
            ("ALCAMPO\nFACTURA SIMPLIFICADA\nCEPILLO DIENTES 1,93 A\n€* TOT 3,68\n"
             "Imp. % Base Cuota\nA IVA 21,00 1,60 ,33\nB IVA 10,00 1,59 ,16", 3.68, 0.49),
            ("DECATHLON\nTOTAL 9,99 EUR\nTasa Sin IVA Total IVA IVA Inc.\n"
             "21,00 8,26 1,73 9,99 EUR", 9.99, 1.73),
        ]
        for text, total, tax in cases:
            result = self.parse(text)
            self.assertEqual(result.value('total'), total, text.split("\n")[0])
            self.assertEqual(result.value('tax_amount'), tax, text.split("\n")[0])

    def test_country_of_the_receipt(self):
        def country(text, own=()):
            return parser.receipt_country(self.parse(text).value('country_clues') or [], own)
        self.assertEqual(country("AUTOGRILL\nP.IVA 00000000000\nTOTALE 8,00"), 'IT')
        self.assertEqual(country("CARREFOUR\nCIF: A28090108\nTOTAL 3,83"), 'ES')
        self.assertEqual(country("BRASSERIE\nTVA BE0123.456.789\nTOTAL 12,10"), 'BE')
        self.assertEqual(country("HOTEL\nTel. +39 02 94757100\nTOTALE 80,00"), 'IT')
        # The buyer's own number is left out; points of a loyalty card are no phone.
        self.assertEqual(country("HOTEL\nCIF B12345678\nCliente: FR 23 334175221",
                                 own=("FR23334175221",)), 'ES')
        self.assertIsNone(country("BONPREU\n+45 PUNTS\nTOTAL 45.05"))

    def test_spanish_restaurant_and_shop_tables(self):
        """The rate between the base and the tax ("BASE %IVA IMP.IVA"), a rate
        code beyond D, "Neto", "€x TOT" for "€* TOT", "IVA 4,00" as a rate."""
        cases = [
            ("CASA PEPE\nBASE %IVA IMP.IVA\n63.82 10.00 6,38\nTOTAL 70,20", 70.20, 6.38),
            ("MINIPRECIO\nImp. Base Cuota TOTAL\nA 4% 5.98 0.24 6.22\n"
             "F 10% 38.79 3.88 42.67\nTOTAL 48.89", 48.89, 4.12),
            ("VALCARCE\nTOTAL € 3.35\nCod. IVA % Neto Importe\n2 10.00 € 3.05 € 0.30", 3.35, 0.30),
            ("ALCAMPO\nHARINA DE TRIGO 1,00 C\n€x TOT 1,00\nImp. % Base Cuota\n"
             "C IVA 4,00 96 ,04", 1.00, None),
        ]
        for text, total, tax in cases:
            result = self.parse(text)
            self.assertEqual(result.value('total'), total, text.split("\n")[0])
            self.assertEqual(result.value('tax_amount'), tax, text.split("\n")[0])
        self.assertIsNone(self.parse("PREFACTURA\nMesa 6\nTOTAL FACTURA 63,92").value('merchant'))

    def test_italian_rates_on_the_items(self):
        """"di cui IVA 3,55" without a rate: the rate printed on the items."""
        single = self.parse("TRATTORIA\nCOPERTO 10,00% 2,00\nFOCACCIA 10,00% 13,00\n"
                            "TOTALE COMPLESSIVO 15,00\ndi cui IVA 1,36")
        self.assertEqual(single.value('tax_rate'), 10.0)
        mixed = self.parse("DECO\nCARTA 22,00% 3,89\nRICOTTA 4,00% 4,30\nSCONTO 50,00% 0,60\n"
                           "TOTALE COMPLESSIVO 8,19\ndi cui IVA 0,87")
        self.assertIsNone(mixed.value('tax_rate'))
        self.assertEqual(mixed.value('tax_rate_max'), 22.0)

    def test_total_that_fits_the_tax(self):
        """The OCR glued an item price to the total line."""
        result = self.parse("LAGARDERE TRAVEL RETAIL\nBICCHIERE MELONE 10,00% 6,50\n"
                            "TOTALE COMPLESSIVO COCA BOTT 10,00% 19,30 3,90\n"
                            "di cui IVA Pagamento elettronico 19,30 1,75")
        self.assertEqual(result.value('total'), 19.30)
        self.assertEqual(result.value('tax_amount'), 1.75)

    def test_rate_among_three_amounts_and_exempt_lines(self):
        tesla = self.parse("TESLA\nTVA totale 4.19\nMontant total (EUR) 26.25\n"
                           "DESR 22.06 19.00 4.19 Taux de TVA locale standard")
        self.assertEqual(tesla.value('tax_amount'), 4.19)
        czech = self.parse("TESLA\nCena bez DPH 299.63\nCZSR 299.63 21.00 62.92 VAT standard\n"
                           "Celkova cena (CZK) 362.55")
        self.assertEqual(czech.value('tax_amount'), 62.92)
        exempt = self.parse("EUROPCAR\nPenalita 95,00\nESC.IVA ART.15 0% 95,00\nTOTALE 95,00")
        self.assertEqual(exempt.value('tax_amount'), 0.0)
        # "10.0000%" is ten per cent, not "00%".
        incl = self.parse("RESTAURANT\n2 T 18.45 Incl 10.0000% TVA = 1.68\nTotal 18.45")
        self.assertEqual(incl.value('tax_rate'), 10.0)
        self.assertEqual(incl.value('tax_amount'), 1.68)

    def test_columns_mixed_on_the_total_line_of_several_rates(self):
        """Total and tax joined to the other's label, with several rates."""
        result = self.parse(
            "DECO'SHOPPERS\n249110683 38X65 22,00% 0,20\nBARILLA 4,00% 1,29\n"
            "SUBTOTALE 37,09\nSconto -0.30\n"
            "di cui IVA TOTALE COMPLESSIVO 37,09\n"
            "Pagamento elettronico Importo pagato 37,09 2,83")
        self.assertEqual(result.value('total'), 37.09)
        self.assertEqual(result.value('tax_amount'), 2.83)
        # Two unrelated lines are not a mix-up: the total labelled as such stays.
        kept = self.parse("HOTEL\n205 Double room 12% 89,00\nTotal price incl. tax 104,02 C\n"
                          "Supplier is VAT payer, deposit 733,69")
        self.assertEqual(kept.value('total'), 104.02)

    def test_a_receipt_cut_before_its_total_ends_on_a_subtotal(self):
        cut = self.parse("SUPERMARKET\nMILK 1,89\nCHEESE 4,55\nHAM 4,79\nSUBTOTAAL 11,23")
        self.assertEqual(cut.value('total'), 11.23)
        # After the subtotal, savings and the payment: the total is among them.
        paid = self.parse("SUPERMARKET\nBREAD 2,75\nWRAPS 3,80\nSubtotal: £61,25\n"
                          "Savings: -£11,70\nCard £49,55")
        self.assertNotEqual(paid.value('total'), 61.25)
        # A discount under the subtotal does not hide it.
        discount = self.parse("FONTE\nACQUA 22,00% 2,98\nSUBTOTALE 75,23\nScont* Val\n-0,60")
        self.assertEqual(discount.value('total'), 75.23)

    def test_polish_total_lines_and_deposits(self):
        # Total and tax on one line, each behind its own label.
        both = self.parse("DEALZ\nPTU A 23,00 % 13,28\nSPRZEDAZ OPODATKOWANA C 70,80\n"
                          "PTU C 5,00 % 3,37\nSUMA PLN SUMA PTU 141,83 16,65")
        self.assertEqual(both.value('total'), 141.83)
        self.assertEqual(both.value('tax_amount'), 16.65)
        # The deposit comes after the amount to pay, or is a reduction.
        deposit = self.parse("LIDL\nPTU C 5% 2,36\nSUMA PTU 2,36\nSUMA PLN 49,57\n"
                             "KAUCJA ZA BUT. PLASTIKOWA 2 x0.50 1.00\n"
                             "DO ZAPLATY OPAKOWANIA ZWROTNE SUMA 50,57 PLN 1,00")
        self.assertEqual(deposit.value('total'), 50.57)
        returned = self.parse("LIDL\nPTU B 8% 13,96\nSUMA PLN SUMA PTU 297,16\n"
                              "DO ZAPLATY OPAKOWANIA ZWROTNE SUMA 294,66 PLN -2,50")
        self.assertEqual(returned.value('total'), 294.66)
        self.assertNotEqual(returned.value('tax_amount'), 297.16)

    def test_polish_tax_rows_and_merchants(self):
        carrefour = self.parse(
            "Nr rej. GIOS: E0002419WZBW\n03-734 Warszawa ul. Targowa 72 CARREFOUR Polska Sp. z o. o.\n"
            "NIP 937-00-08-168\nPARAGON FISKALNY\nSprzed. opod. PTU A 17,24\nKwota A 23,00% 3,22\n"
            "Sprzed. opod. PTU B 30,12\nKwota B 08,00% 2,23\n"
            "Kwota C 05,00% Sprzed. opod. PTU C 43,85 2,09\nSUMA PLN Podatek PTU 91,21 7,54")
        self.assertEqual(carrefour.value('merchant'), "CARREFOUR Polska")
        self.assertEqual(carrefour.value('tax_amount'), 7.54)
        self.assertEqual(carrefour.value('tax_rate_max'), 23.0)
        # The company stands before its form, on a line made of a registry number.
        lidl = self.parse("Podgórne nr rej: BDO 000002265 Lidl sp. z o.o. sp.k.\n"
                          "ul. ks. Kojzara 3, 43-450 Ustroń\nNIP 7811897358 nr:872927\nPARAGON FISKALNY")
        self.assertEqual(lidl.value('merchant'), "Lidl")
        self.assertEqual(self.parse("Rossmann SDP Sp. z 0.0. Sk\nul. Złota 59\nPARAGON FISKALNY")
                         .value('merchant'), "Rossmann SDP")
        # "PTU C 18,28" is the base of C; "Suma 0,87" the sum of the tax rows.
        lidl_new = self.parse("LIDL\nPTU C 18,28\nKwota C 5,00% 0,87\nSuma 0,87\nRazem 18,28\nRAZEM PLN 18,28")
        self.assertEqual(lidl_new.value('total'), 18.28)
        self.assertEqual(lidl_new.value('tax_amount'), 0.87)

    def test_croatian_and_lost_letter_totals(self):
        self.assertEqual(self.parse("KONZUM\nUkupno 20,02\nPDV 2,03\nZa platitj EUR 22,05")
                         .value('total'), 22.05)
        # The total on the line of the next article: the first amount.
        self.assertEqual(self.parse("SPAR\nRadenska gaz. 1,51\nZa platiti Pecivo pšen.sir 6,32 0,69 c\n"
                                    "Euro EUR 10,00").value('total'), 6.32)
        # "OUS-TOTAL" and "OTAL": the first letters lost by the OCR.
        lost = self.parse("LIQUEURS\nCHARTREUSE 70CL 47,95 €\nOUS-TOTAL 39,96 €\nOTAL TVA 7,99 €\n"
                          "OTAL [1] Article 47,95 €")
        self.assertEqual(lost.value('total'), 47.95)

    def test_norwegian_tax_tables(self):
        # Rate first, then base, tax, total.
        first = self.parse("REMA 1000\nSJOKOMELK 1L TINE 15% 26,90\nSum 1 varer 26,90\n"
                           "Mva% Grunnlag Mva Totalt\n15,00 23,39 3,51 26,90")
        self.assertEqual(first.value('tax_amount'), 3.51)
        self.assertEqual(first.value('tax_rate'), 15.0)
        # Base, rate, tax, total; a zero rate is skipped and the rates add up.
        second = self.parse("EXTRA\nVARER 371,10\nMVA-grunnlag MVA-% MVA Sum\n"
                            "6.00 0% 0.00 6.00\n317.48 15% 47.62 365.10\nSummer 323.48 47.62 371.10")
        self.assertEqual(second.value('tax_amount'), 47.62)
        self.assertEqual(second.value('tax_rate'), 15.0)

    def test_the_slogan_of_a_swiss_chain_is_not_part_of_its_name(self):
        self.assertEqual(self.parse("Pour moi et pour toi. coop\nNeuchâtel Maladière\n"
                                    "SOMME CHF 86.75").value('merchant'), "Coop")
        self.assertEqual(self.parse("Pour moi et pour toi.\nNeuchâtel Gare\nSOMME CHF 7.25")
                         .value('merchant'), "Coop")

    def test_a_street_written_in_one_word_is_not_the_merchant(self):
        self.assertIsNone(self.parse("Hauptstrasse 45\n2340 Mödling\nTOTAL 3,50").value('merchant'))
        self.assertEqual(self.parse("EUR\nKaufland - Gutschmidtstraße 19\nSUMME 3,50").value('merchant'),
                         "Kaufland")
        # A street named after a person is a street.
        self.assertEqual(self.parse("SPAR\nFriedrich-Schillerstrasse 74\nSUMME 3,50").value('merchant'),
                         "Spar")
        self.assertIsNone(self.parse("EUR\nTOTAL 3,50").value('merchant'))

    def test_columns_of_the_german_and_swedish_tax_tables(self):
        # "ENDSUMME" is the total of the Austrian chains.
        self.assertEqual(self.parse("BILLA\nMILCH 1,99 B\nENDSUMME 16,07 €").value('total'), 16.07)
        # A space left after the separator, and "(ink. moms)" is no tax line.
        swedish = self.parse("MCDONALD'S\nTa med Totalt (ink. moms) 17.00\nMOMS % BELOPP MOMS\n"
                             "inkl. moms 12.00% 17.00 1.82")
        self.assertEqual(swedish.value('tax_amount'), 1.82)
        self.assertEqual(self.parse("ICA\nTotalt 15 varor\nTotalt 403, 00 SEK").value('total'), 403.0)
        # Base and gross, the tax column mangled: the tax is their difference.
        mangled = self.parse("ALDI\nSUMME Posten:2 € 1,87\nMwSt NETTO MwSt UMSATZ\nB 19% 1,57 0,3) 1,87")
        self.assertEqual(mangled.value('tax_amount'), 0.30)

    def test_italian_tax_beside_the_total(self):
        # The tax cannot be the total: it is the other amount of the line.
        same = self.parse("CONAD\nPASTA 4% 0,99\nTOTALE COMPLESSIVO 5,67\nDI CUI IVA 0,35 5,67")
        self.assertEqual(same.value('tax_amount'), 0.35)
        # Both values on the total line, the next line holds the payment.
        beside = self.parse("CONAD\nSHAMPOO 22,00% 4,49\nMUFFIN 10,00% 1,99\nTOTALE COMPLESSIVO 35,90 4,26\n"
                            "di cui IVA 20,00\nPagamento contante Ticket 20,00 4,10")
        self.assertEqual(beside.value('tax_amount'), 4.26)
        # The rate of the column title is the items', its price is not a tax.
        title = self.parse("BAR\nDESCRIZIONE Brioch IVA 10% Prezzo(€) 1,50\nTOTALE COMPLESSIVO 1,50\n"
                           "di cui IVA Pagamento contante 0,14 1,50")
        self.assertEqual(title.value('tax_amount'), 0.14)

    def test_the_item_column_title_is_not_a_tax_line(self):
        result = self.parse("ATREIU S.R.L.\nDESCRIZIONE TAGLIATELLE FUNGHI IVA 13.00 A\n"
                            "PANE & COPERTO 2.00 A\nTOTALE COMPLESSIVO DI CUI IVA 48.00 4.36\n"
                            "A:IVA 10.00%")
        self.assertEqual(result.value('tax_amount'), 4.36)

    def test_a_spanish_document_type_is_not_the_merchant(self):
        result = self.parse("FACTURMPSIMPLIFICADA\nTURRON COCO 2,52 B\n€* TOT 6,42\n"
                            "W CAMBIO ,00\nImp. % Base Cuota\nIVA 10,00 5,83 ,59")
        self.assertIsNone(result.value('merchant'))
        self.assertEqual(result.value('total'), 6.42)

    def test_us_sales_tax_is_a_foreign_tax(self):
        result = self.parse("JOE'S DINER\nSUBTOTAL 17.49\nSALES TAX 8.875% 1.55\nTOTAL $19.04")
        self.assertEqual(result.value('tax_label'), "Sales tax")
        self.assertEqual(result.value('total'), 19.04)

    def test_a_contraction_keeps_its_small_letter(self):
        self.assertEqual(parser.title_case("JOE'S DINER"), "Joe's Diner")
        self.assertEqual(parser.title_case("L'ATELIER DU PAIN"), "L'Atelier Du Pain")

    def test_nights_of_a_hotel_bill(self):
        printed = self.parse("HOTEL\n2 Nuitees x 95,00 190,00\nTOTAL TTC 219,76 EUR",
                             today=date(2026, 9, 29))
        self.assertEqual(printed.value('nights'), 2)
        dated = self.parse("HOTEL\nCheck-in 22/09/2026\nCheck-out 25/09/2026\nTOTAL 300,00 EUR",
                           today=date(2026, 9, 29))
        self.assertEqual(dated.value('nights'), 3)
        self.assertIsNone(self.parse("CAFE\nTOTAL 3,00 EUR").value('nights'))

    def test_norwegian_sum_of_items_beats_the_vat_table(self):
        result = self.parse("KIWI\nOrg.nr 979 443 137 MVA\nSum 3 varer 22,00\n"
                            "Mva% Grunnlag Mva Sum\nSum 17,83 4,47 22,30")
        self.assertEqual(result.value('total'), 22.00)

    def test_a_percent_read_as_an_eight(self):
        """"NUST BRUTTO NETTO / A 198 ...": MWST and 19 % misread by the OCR."""
        result = self.parse("NETTO\nSUMME [19] 27.91\nNUST BRUTTO NETTO\n"
                            "A 198 0.68 4.28 3.60\nB 78 1.55 23.63 22.08")
        self.assertEqual(result.value('tax_amount'), 2.23)
        self.assertEqual(result.value('tax_rate_max'), 19.0)

    def test_swedish_vat_table(self):
        """"Moms% Moms Netto Brutto": the rate column has no "%" on its lines."""
        result = self.parse("BORJES\nTo.alt 87,75 SEK\nMoms% Moms Netto Brutto\n"
                            "12,00 9,41 78,34 87,75\nSPARA KVITTOT")
        self.assertEqual(result.value('tax_amount'), 9.41)
        self.assertEqual(result.value('tax_rate'), 12.0)
