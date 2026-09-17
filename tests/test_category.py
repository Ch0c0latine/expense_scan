# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Catégorie reconnue, enseignes apprises, tickets venus d'ailleurs.

Les tests tournent sur une base qui a déjà ses catégories et son
historique : les mots et les enseignes employés ici sont inventés, pour
ne rien croiser de réel.
"""
from odoo.tests import common, tagged

from ..ocr import lexicon, parser
from .test_parser import words_from_text


def reading(text):
    return parser.parse(words_from_text(text))


@tagged('post_install', '-at_install')
class TestLexicon(common.TransactionCase):

    def test_fold_handles_polish_and_german_letters(self):
        self.assertEqual(lexicon.fold("DO ZAPŁATY — Straße"), "do zaplaty strasse")

    def test_families_follow_category_names(self):
        self.assertEqual(lexicon.family_of("HEBERGEMENT", "Hebergement Hotel/Bnb"), 'lodging')
        self.assertEqual(lexicon.family_of("ENERGIE", "Carburant/Elec"), 'fuel')
        self.assertEqual(lexicon.family_of("PARK", "Péages et Parking"), 'toll_parking')
        self.assertEqual(lexicon.family_of("TRANSPORT", "Train/Avion Transport"), 'train_air')
        self.assertEqual(lexicon.family_of("MOB_URB", "Taxi/VTC/Metro/Tram"), 'taxi')
        self.assertEqual(lexicon.family_of("LOC", "Location de vehicule"), 'car_rental')
        self.assertEqual(lexicon.family_of("REPAS", "Meals"), 'meal')

    def test_flat_rates_and_business_meals_have_no_family(self):
        self.assertIsNone(lexicon.family_of("IDG Logement", "IGD Logement - Bareme Urssaf"))
        self.assertIsNone(lexicon.family_of("INVITATION", "Repas d'affaires / invitations"))
        self.assertIsNone(lexicon.family_of("KM", "Kilométrage"))

    def test_polish_fuel_receipt_scores_fuel(self):
        categories = {
            'fuel': lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['fuel'])),
            'meal': lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['meal'])),
        }
        lines = ["ORLEN S.A.", "PARAGON FISKALNY", "PB95 40,00 l x 6,50 260,00 A",
                 "SUMA PLN 260,00"]
        self.assertEqual(lexicon.pick_category(lexicon.score_categories(lines, categories)), 'fuel')

    def test_one_body_word_is_not_enough(self):
        """Un « dessert » en bas de ticket ne fait pas un restaurant."""
        categories = {'meal': ["dessert"], 'fuel': ["gazole"]}
        lines = ["SUPERMARCHE"] + ["ARTICLE"] * 10 + ["DESSERT 2,00"]
        self.assertIsNone(lexicon.pick_category(lexicon.score_categories(lines, categories)))

    def test_misread_keyword_is_tolerated(self):
        categories = {'lodging': ["pernottamento"], 'meal': ["ristorante"]}
        self.assertEqual(lexicon.pick_category(
            lexicon.score_categories(["PERNOTTAMENT0 2 NOTTI"], categories)), 'lodging')

    def test_close_scores_decide_nothing(self):
        categories = {'lodging': ["hotel"], 'meal': ["restaurant"]}
        lines = ["HOTEL DU PARC", "RESTAURANT"]
        self.assertIsNone(lexicon.pick_category(lexicon.score_categories(lines, categories)))


@tagged('post_install', '-at_install')
class TestEuropeanReceipts(common.TransactionCase):

    def test_polish_receipt(self):
        result = reading("""
ORLEN S.A.
ul. Chemików 7
NIP 774-00-01-454
PARAGON FISKALNY
PB95 40,00 l x 6,50 260,00 A
SPRZED. OPOD. PTU A 260,00
PTU A 23,00 % 48,62
SUMA PTU 48,62
SUMA PLN 260,00
KARTA 260,00
""")
        self.assertEqual(result.value('total'), 260.00)
        self.assertEqual(result.value('tax_rate'), 23.0)
        self.assertEqual(result.value('tax_amount'), 48.62)
        self.assertEqual(result.value('tax_label'), "PTU")
        self.assertEqual(result.value('currency'), "PLN")
        self.assertTrue(result.value('merchant').startswith("Orlen"))

    def test_italian_receipt(self):
        result = reading("""
AUTOGRILL SPA
Via Roma 12
DOCUMENTO COMMERCIALE
PANINO 6,50
ACQUA 1,50
TOTALE COMPLESSIVO 8,00
DI CUI IVA 0,73
PAGAMENTO ELETTRONICO 8,00
""")
        self.assertEqual(result.value('total'), 8.00)
        self.assertEqual(result.value('tax_amount'), 0.73)
        self.assertEqual(result.value('tax_label'), "IVA")
        self.assertEqual(result.value('merchant'), "Autogrill Spa")

    def test_german_receipt(self):
        result = reading("""
BÄCKEREI MÜLLER
Hauptstraße 5
2 x Brezel 2,40
SUMME EUR 2,40
MwSt 7% 0,16
Netto 2,24
Bar 5,00
Rückgeld 2,60
""")
        self.assertEqual(result.value('total'), 2.40)
        self.assertEqual(result.value('tax_rate'), 7.0)
        self.assertEqual(result.value('tax_amount'), 0.16)
        self.assertEqual(result.value('tax_label'), "MWST")

    def test_french_tax_total_is_not_counted_twice(self):
        result = reading("""
BRASSERIE
TVA 10 % 1,00
TOTAL TVA 1,00
TOTAL 11,00
""")
        self.assertEqual(result.value('tax_amount'), 1.00)
        self.assertEqual(result.value('tax_label'), "TVA")

    def test_foreign_month_names(self):
        self.assertEqual(parser._month_number("SETTEMBRE"), 9)
        self.assertEqual(parser._month_number("WRZEŚNIA"), 9)
        self.assertEqual(parser._month_number("Dezember"), 12)


@tagged('post_install', '-at_install')
class TestCategoryRecognition(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Product = cls.env['product.product']
        cls.company = cls.env.company
        cls.default = Product.create({'name': "Divers test", 'can_be_expensed': True})
        cls.company.expense_scan_product_id = cls.default
        cls.company.expense_scan_apply_tax = True
        cls.lodging = Product.create({
            'name': "Nuits test", 'can_be_expensed': True,
            'expense_scan_keywords': "zorblax\nquimbo",
        })
        cls.food = Product.create({
            'name': "Mangeaille test", 'can_be_expensed': True,
            'expense_scan_keywords': "fromzak",
        })
        cls.flat = Product.create({
            'name': "Forfait test", 'can_be_expensed': True, 'standard_price': 48.3,
            'expense_scan_no_vat': True, 'expense_scan_keywords': "zorblax\nquimbo",
        })
        cls.train = Product.create({
            'name': "Rail test", 'can_be_expensed': True,
            'expense_scan_no_vat': True, 'expense_scan_keywords': "glumptrain",
        })
        cls.employee = cls.env['hr.employee'].create({'name': "Claude Catégorie"})

    def expense(self, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.default.id,
        }, **values))

    def test_keywords_choose_the_category(self):
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("ZORBLAX INN\nQUIMBO 2\nTOTAL 90,00"), self.company)
        self.assertEqual(values['product_id'], self.lodging.id)
        self.assertEqual(values['expense_scan_guessed_product_id'], self.lodging.id)

    def test_flat_rate_categories_are_never_guessed(self):
        """Le forfait déclare les mêmes mots, mais un ticket n'y mène pas."""
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("ZORBLAX INN\nQUIMBO 2\nTOTAL 90,00"), self.company)
        self.assertNotEqual(values.get('product_id'), self.flat.id)

    def test_category_without_vat_is_guessed_and_its_vat_dropped(self):
        """Le train n'a pas de TVA récupérable, mais il a bien un ticket."""
        expense = self.expense()
        receipt = reading("GLUMPTRAIN\nTVA 10 % 2,00\nTOTAL 22,00 EUR")
        values = expense._expense_scan_field_values(receipt, self.company)
        self.assertEqual(values['product_id'], self.train.id)
        self.assertEqual(values['scan_tax_amount'], 0.0)

    def test_nothing_sure_keeps_the_default(self):
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("ENSEIGNE INCONNUE\nTOTAL 9,00"), self.company)
        self.assertNotIn('product_id', values)
        self.assertFalse(values['expense_scan_guessed_product_id'])

    def test_a_chosen_category_is_left_alone(self):
        expense = self.expense(product_id=self.food.id)
        values = expense._expense_scan_category_values(
            reading("ZORBLAX INN\nQUIMBO 2\nTOTAL 90,00"), self.company)
        self.assertNotIn('product_id', values)

    def test_history_recognises_a_misread_logo(self):
        """Un « Rchan » corrigé une fois en « Auchan » l'est pour toujours."""
        past = self.expense(product_id=self.food.id, total_amount_currency=14.0)
        past.write({
            'expense_scan_merchant': "Auchanzz",
            'expense_scan_merchant_read': "rchanzz",
            'approval_state': 'submitted',
        })
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("RCHANZZ\n12 RUE DU MARCHE\nTOTAL 14,00"), self.company)
        self.assertEqual(values['product_id'], self.food.id)
        self.assertEqual(values['expense_scan_merchant'], "Auchanzz")
        self.assertEqual(values['expense_scan_merchant_read'], "rchanzz")

    def test_drafts_teach_nothing(self):
        past = self.expense(product_id=self.food.id)
        past.write({'expense_scan_merchant': "Brouillonzz",
                    'expense_scan_merchant_read': "brouillonzz"})
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("BROUILLONZZ\nTOTAL 14,00"), self.company)
        self.assertNotIn('product_id', values)

    def test_mission_in_the_description_survives(self):
        expense = self.expense(name="FAI chez Bidule")
        self.assertFalse(expense._expense_scan_name_is_automatic())
        expense.name = expense._get_untitled_expense_name("16/09/2026")
        self.assertTrue(expense._expense_scan_name_is_automatic())

    def test_description_names_the_recognised_category(self):
        """« Péage du … » tant que personne n'a écrit de description."""
        from datetime import date
        expense = self.expense()
        name = expense._expense_scan_auto_name(self.food, date(2026, 9, 12))
        self.assertTrue(name.startswith(self.food.name))
        expense.name = name
        self.assertTrue(expense._expense_scan_name_is_automatic())
        expense.name = expense._expense_scan_auto_name(False, date(2026, 9, 12))
        self.assertTrue(expense._expense_scan_name_is_automatic())
        expense.name = "%s chez Bidule" % self.food.name
        self.assertFalse(expense._expense_scan_name_is_automatic())
        expense.name = "Zorglub du 12/09/2026"
        self.assertFalse(expense._expense_scan_name_is_automatic())

    def test_foreign_tax_is_not_deducted(self):
        expense = self.expense()
        italian = reading("AUTOGRILL\nTOTALE COMPLESSIVO 8,00\nDI CUI IVA 0,73")
        self.assertEqual(expense._expense_scan_foreign_tax(italian, self.company), "IVA")
        values = expense._expense_scan_field_values(italian, self.company)
        self.assertEqual(values['scan_tax_amount'], 0.0)

    def test_french_receipt_is_not_foreign(self):
        expense = self.expense()
        french = reading("BRASSERIE\nTVA 10 % 1,00\nTOTAL 11,00 EUR")
        rates = self.env['account.tax'].search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'), ('amount', '=', 10.0)])
        if rates or not self.env['account.tax'].search_count([
                ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase')]):
            self.assertFalse(expense._expense_scan_foreign_tax(french, self.company))

    def test_seeding_never_overwrites(self):
        hotel = self.env['product.product'].create({
            'name': "Hotel maison", 'can_be_expensed': True,
            'expense_scan_keywords': "mon mot",
        })
        self.env['product.template']._expense_scan_seed_keywords()
        self.assertEqual(hotel.expense_scan_keywords, "mon mot")

    def test_archived_categories_take_no_family(self):
        archived = self.env['product.template'].create({
            'name': "Accommodation archivée", 'can_be_expensed': True,
            'sequence': -1000, 'active': False,
        })
        families = self.env['product.template']._expense_scan_family_templates()
        self.assertNotIn(archived, families)

    def test_added_keywords_complete_a_filled_category(self):
        families = self.env['product.template']._expense_scan_family_templates()
        parking = next((t for t, f in families.items() if f == 'toll_parking'), None)
        if not parking or not parking.expense_scan_keywords:
            return
        parking.expense_scan_keywords = "péage\nmon mot"
        self.env['product.template']._expense_scan_add_keywords(
            {'toll_parking': ["classe tarif", "PEAGE"]})
        self.assertEqual(parking.expense_scan_keywords, "péage\nmon mot\nclasse tarif")


@tagged('post_install', '-at_install')
class TestCardSlips(common.TransactionCase):

    def test_time_glued_to_the_year(self):
        result = reading("""
Établissement DORMIZZ VILLEFRANCHE D'ORBEC
Carte de Mastercard****0000
Date 13/06/202616:30:16
Montant 350,28 €
""")
        self.assertEqual(result.value('date').strftime('%d/%m/%Y'), "13/06/2026")
        self.assertEqual(result.value('time').strftime('%H:%M'), "16:30")
        self.assertEqual(result.value('total'), 350.28)
        self.assertEqual(result.value('merchant'), "Dormizz Villefranche D'Orbec")

    def test_company_label_is_not_the_merchant(self):
        result = reading("SOCiete: POPEYES FAMOUS\nTOTAL 12,00")
        self.assertEqual(result.value('merchant'), "Popeyes Famous")

    def test_toll_receipt_without_the_word_toll(self):
        keywords = lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['toll_parking']))
        # Le texte tel que lu sur un vrai reçu : « Classe-tarif » en
        # neuvième ligne, hors de l'en-tête.
        lines = ["ASFLieu-dit Les Pins BP 10017", "99901 VILLEBOURG Cedex 9", "Te1:3605",
                 "RECU", "N°R1700000000000000000", "Date.. .20/06/26",
                 "Sortie...VILLEBOURG SUD ES", "Entree... .BOURGNEUF",
                 "Classe-tarif..1 km..065", "PRIX HT.......5,67 euros",
                 "TVA 20,00%....1,13 euros", "PRIX TTC......6,80 euros",
                 "Paiement....6,80 E ..CB", "N° carte: .XX00"]
        scores = lexicon.score_categories(lines, {'toll': keywords})
        self.assertEqual(lexicon.pick_category(scores), 'toll')
