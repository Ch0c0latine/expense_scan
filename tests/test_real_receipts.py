# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tickets typiques, tels que les lit l'OCR (enseignes réelles, coordonnées fictives).

Chacun a révélé un défaut : ils le gardent corrigé.
"""
from odoo.tests import common, tagged

from ..ocr import lexicon, parser
from .test_parser import words_from_text

MCDONALDS = """4562
SAT 122
NORD VILLEFRANCHE D'ORBEC CCIAL V2 Restaurant McDonald's
12 Boulevard des Exemples
99650 VILLEFRANCHE D'ORBEC
SIRET 123 456 782 00010-APE 5610C Te1.01.00.00.00.00
RCS Orbec -TVA INTRA FR11123456782
Restaurant 25000001
#CDE 62 Kiosk 45 09/06/2026 12:17:34
Qte Produit Unit Total
1 B0 Double Cheese 7.50 7.50
MX CBO CurryMan 13.30 13.30
oldseno S3  .0   0R Total commande 22.30 EUR
Merci de votre visite - A bientôt"""

LIDL = """← 18 novembre ooo
7 Rue de l'Exemple
FR-99650 VILLEFRANCHE D'ORBEC
Ticket de vente
Article P.U.EUR Qté EUR
Pipe Rigate 0,871 0,87 A T
Réduction Lidl Plus -0,87
Bière blonde Goudale 4,10 1 4,10 B
Lardons fumés 1,59 1 1,59 A T
Nombre de lignes: 7
A payer 18,21
16,79 HT
Total éligible TR (T) : 12,32
Carte 18,21
Total Promotion 0,87
TVA Taux MONT.TTC MONT.TVA TOTAL HT
A 5,5% 14,11 0,74 13,37
B 20% 4,10 0,68 3,42"""

POPEYES = """Ticket de Ventes No: 100201
ORBEC
Siret No:90123456700302
NAF：5610C
12 Jun'26 17:05
Qte Produit Total
1 3 BIC 12.49
Prix:12.49 TVA:D
Total €12.49
Carte Bancaire €12.49
Imposable TVA Montant
TVA 10.00%: 11.35 1.14 12.49 D
TVA 5.50%: 0.00 0.00 0.00 C
TOTAL 11.35 1.14 12.49"""

URBAN_TICKET = """ViLLEBuS LES TRONSPORTS DE L AGGLO
Automate:BRM-CS-SDB-01
09106/2026 21h19
Trajet unitaire x10 1 x 15,80 = 15,80
TOTAL EN EUROS : 15,80
Paiement en CB
TVA en Euros
Taux HT TVA TTC
10,00 14,36 1,44 15,80
Merci et bonne journée www.villebus.example"""


def reading(text):
    return parser.parse(words_from_text(text))


@tagged('post_install', '-at_install')
class TestRealReceipts(common.TransactionCase):

    def test_town_alone_does_not_make_the_same_merchant(self):
        """« Nord Villefranche d'Orbec » n'est pas « Dormizz Villefranche d'Orbec »."""
        lines = MCDONALDS.split("\n")
        self.assertIsNone(lexicon.match_merchant(lines, ["dormizz villefranche d orbec"]))
        self.assertEqual(lexicon.match_merchant(["DORMIZZ VILEFRANCHE D'ORBEC"],
                                                ["dormizz villefranche d orbec"]),
                         "dormizz villefranche d orbec")

    def test_long_brand_is_found_inside_a_line(self):
        family, brand = lexicon.brand_family(MCDONALDS.split("\n"))
        self.assertEqual(family, 'meal')
        self.assertEqual(lexicon.fold(brand), "mcdonald s")

    def test_mcdonalds_codes(self):
        result = reading(MCDONALDS)
        self.assertEqual(result.value('activity'), "NAF:5610C")
        self.assertEqual(result.value('company_number'), "12345678200010")
        self.assertEqual(result.value('total'), 22.30)

    def test_lidl(self):
        result = reading(LIDL)
        self.assertNotIn("novembre", (result.value('merchant') or "").lower())
        self.assertEqual(result.value('total'), 18.21)
        self.assertEqual(result.value('tax_amount'), 1.42)
        self.assertEqual(result.value('tax_rate_max'), 20.0)
        categories = {'meal': lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['meal']))}
        self.assertEqual(lexicon.pick_category(
            lexicon.score_categories(LIDL.split("\n"), categories)), 'meal')

    def test_popeyes(self):
        result = reading(POPEYES)
        self.assertEqual(result.value('total'), 12.49)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.14)

    def test_urban_ticket(self):
        result = reading(URBAN_TICKET)
        self.assertEqual(result.value('total'), 15.80)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.44)


@tagged('post_install', '-at_install')
class TestHistoryIsNotAVeto(common.TransactionCase):

    def test_printed_code_and_brand_beat_a_wrong_history(self):
        families = self.env['product.template']._expense_scan_family_templates()
        by_family = {family: template.product_variant_id
                     for template, family in families.items()}
        if not {'meal', 'lodging'} <= set(by_family):
            return
        employee = self.env['hr.employee'].create({'name': "Camille Historique"})
        default = self.env['product.product'].create(
            {'name': "Divers historique", 'can_be_expensed': True})
        self.env.company.expense_scan_product_id = default
        Expense = self.env['hr.expense']
        # Une enseigne lue à l'identique, classée à tort en hébergement ;
        # préfixée pour ne croiser aucun ticket réel de la base.
        receipt = MCDONALDS.replace("NORD VILLEFRANCHE", "ZZNORD VILLEFRANCHE")
        past = Expense.create({
            'name': "Passé", 'employee_id': employee.id,
            'product_id': by_family['lodging'].id, 'total_amount_currency': 20.0,
        })
        past.write({
            'expense_scan_merchant': "Nord Villefranche Zz",
            'expense_scan_merchant_read': lexicon.fold(
                "ZZNORD VILLEFRANCHE D'ORBEC CCIAL V2 Restaurant McDonald's"),
            'approval_state': 'submitted',
        })
        expense = Expense.create({
            'name': "Ticket", 'employee_id': employee.id, 'product_id': default.id,
        })
        values = expense._expense_scan_category_values(reading(receipt), self.env.company)
        self.assertEqual(values['product_id'], by_family['meal'].id)


@tagged('post_install', '-at_install')
class TestRescanAfterApproval(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Alex Relance"})
        cls.default = cls.env['product.product'].create(
            {'name': "Divers relance", 'can_be_expensed': True})
        cls.chosen = cls.env['product.product'].create(
            {'name': "Choisie relance", 'can_be_expensed': True})
        cls.env.company.expense_scan_product_id = cls.default

    def approved(self, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': "Relance", 'employee_id': self.employee.id,
            'product_id': self.chosen.id, 'total_amount_currency': 18.21,
        }, **values))
        expense.write({'approval_state': 'submitted'})
        return expense

    def test_an_expense_is_not_its_own_history(self):
        expense = self.approved()
        expense.write({'expense_scan_merchant': "← 18 novembre zz",
                       'expense_scan_merchant_read': "18 novembre zz"})
        values = expense._expense_scan_category_values(
            reading(LIDL.replace("18 novembre ooo", "18 novembre zz")), self.env.company)
        self.assertNotIn("novembre", values.get('expense_scan_merchant') or "")

    def test_stale_reason_is_cleared(self):
        expense = self.approved()
        expense.write({'expense_scan_category_reason': "d'après une vieille raison"})
        values = expense._expense_scan_category_values(reading(URBAN_TICKET), self.env.company)
        self.assertFalse(values['expense_scan_category_reason'])
        self.assertNotIn('product_id', values)

    def test_column_titles_are_not_a_merchant(self):
        result = reading(LIDL)
        merchant = (result.value('merchant') or "").lower()
        self.assertNotIn("article", merchant)
        self.assertNotIn("nombre", merchant)


# Devis PDF mis en colonnes : le total HT et la TVA partagent des lignes,
# et le taux n'est écrit que dans le détail, sans le mot « TVA ».
DEVIS = """DOMICILIATION EXEMPLE
Siège social et Gestion du courrier DEVIS
Date d'émission : 08/09/2026
Libellé Qté PU HT Montant HT TVA
AR01027 -Domiciliation commerciale. 1,00 30,00 € 30,00 € 20,00%
AR00826 -Numérisation du courrier. 1,00 20,00 € 20,00 € 20,00%
Détail de la TVA Total HT 50,00 €
Code Base HT Taux Montant TVA 10,00 €
Normale 50,00 € 20,00% 10,00 € Total TTC 60,00 €
Règlement Chèque ou Virement
Code NAF (APE) 4321A - SARL au capital social de 3000 €
N° TVA FR00000000000"""


@tagged('post_install', '-at_install')
class TestColumnInvoice(common.TransactionCase):

    def test_quote_with_columns(self):
        result = reading(DEVIS)
        self.assertEqual(result.value('total'), 60.00)
        self.assertEqual(result.value('tax_amount'), 10.00)
        self.assertEqual(result.value('tax_rate'), 20.0)
        self.assertEqual(result.value('tax_rate_max'), 20.0)
