# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Recognised category, learned merchants, receipts from abroad.

The tests run on a database that already holds categories and a history:
the words and merchants used here are made up, to avoid any clash with real
data.
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

    def test_learnt_words(self):
        from ..ocr import categorize
        chosen, kept = categorize.CHOSEN, categorize.KEPT
        # (category, text, merchant, how the category was set)
        receipts = ([('toll', "ASF peage autoroute A62 sortie classe 1 Agen", 'asf', chosen)] * 2
                    + [('toll', "APRR peage autoroute A6 sortie classe 1 Agen", 'aprr', chosen)] * 2
                    + [('meal', "Brasserie du Quai couverts plat du jour parking TOTAL",
                        'quai', chosen)] * 2
                    + [('meal', "Bistrot Zorg couverts plat du jour parking TOTAL",
                        'zorg', chosen)] * 2
                    + [('meal', "Pizzeria Roma coperto margherita", 'roma', chosen)] * 3
                    + [('meal', "Snack kebab frites", None, chosen)] * 3
                    + [('toll', "Parking Zentrum ticket horaire", 'zentrum', kept)] * 3
                    + [('toll', "Parking Gare ticket horaire", 'gare', kept)] * 3)
        learnt = categorize.learn_words(
            receipts, excluded={'agen'}, known={'toll': ['peage'], 'lunch': ['plat']})
        self.assertIn('autoroute', learnt['toll'])
        self.assertIn('couverts', learnt['meal'])
        self.assertNotIn('peage', learnt['toll'])      # already declared
        self.assertNotIn('plat', learnt['meal'])       # declared on another category
        self.assertNotIn('agen', learnt['toll'])       # a name, excluded
        self.assertNotIn('total', learnt['meal'])      # on every receipt
        self.assertNotIn('pizzeria', learnt['meal'])   # a single merchant
        self.assertNotIn('kebab', learnt['meal'])      # merchants unknown: one at most
        self.assertNotIn('parking', learnt['meal'])    # also on the receipts of tolls
        self.assertNotIn('horaire', learnt['toll'])    # only suggestions kept as is
        self.assertEqual(categorize.learn_words(receipts[:5]), {})  # too few receipts
        # "jour" led a hotel receipt to the meals; corrected once, it is
        # no longer a word of the meals.
        corrected = receipts + [('hotel', "Hotel Zorn nuit petit jour", 'zorn',
                                 categorize.CORRECTED)]
        learnt = categorize.learn_words(corrected, known={'toll': ['peage']})
        self.assertNotIn('jour', learnt['meal'])
        self.assertIn('couverts', learnt['meal'])

    def test_families_follow_category_names(self):
        self.assertEqual(lexicon.family_of("HEBERGEMENT", "Hebergement Hotel/Bnb"), 'lodging')
        self.assertEqual(lexicon.family_of("ENERGIE", "Carburant/Elec"), 'fuel')
        self.assertEqual(lexicon.family_of("PARK", "Péages et Parking"), 'toll_parking')
        self.assertEqual(lexicon.family_of("TRANSPORT", "Train/Avion Transport"), 'train_air')
        self.assertEqual(lexicon.family_of("MOB_URB", "Taxi/VTC/Metro/Tram"), 'taxi')
        self.assertEqual(lexicon.family_of("LOC", "Location de vehicule"), 'car_rental')
        self.assertEqual(lexicon.family_of("REPAS", "Meals"), 'meal')
        # Odoo's own category, and names in other languages.
        self.assertEqual(lexicon.family_of("FOOD", "Meals"), 'meal')
        self.assertEqual(lexicon.family_of("Kraftstoff"), 'fuel')
        self.assertEqual(lexicon.family_of("Parkgebühren"), 'toll_parking')
        self.assertEqual(lexicon.family_of("Übernachtung"), 'lodging')
        self.assertEqual(lexicon.family_of("Drivmedel"), 'fuel')
        self.assertEqual(lexicon.family_of("Måltider"), 'meal')

    def test_flat_rates_and_business_meals_have_no_family(self):
        self.assertIsNone(lexicon.family_of("IDG Logement", "IGD Logement - Bareme Urssaf"))
        self.assertIsNone(lexicon.family_of("INVITATION", "Repas d'affaires / invitations"))
        self.assertIsNone(lexicon.family_of("KM", "Kilométrage"))
        self.assertIsNone(lexicon.family_of("Verpflegungspauschale pro Tag"))
        self.assertIsNone(lexicon.family_of("Bewirtung"))

    def test_polish_fuel_receipt_scores_fuel(self):
        categories = {
            'fuel': lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['fuel'])),
            'meal': lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['meal'])),
        }
        lines = ["ORLEN S.A.", "PARAGON FISKALNY", "PB95 40,00 l x 6,50 260,00 A",
                 "SUMA PLN 260,00"]
        self.assertEqual(lexicon.pick_category(lexicon.score_categories(lines, categories)), 'fuel')

    def test_one_body_word_is_not_enough(self):
        """A single word ("dessert") at the bottom of a receipt does not make a restaurant."""
        categories = {'meal': ["dessert"], 'fuel': ["gazole"]}
        lines = ["SUPERMARCHE"] + ["ARTICLE"] * 10 + ["DESSERT 2,00"]
        self.assertIsNone(lexicon.pick_category(lexicon.score_categories(lines, categories)))

    def test_a_keyword_of_two_letters_matches_nothing(self):
        """"b&b" folds to "b b": the gum "WRIGLEY'S B-B" is no bed and breakfast."""
        scores = lexicon.score_categories(
            ["Sklep Zabka", "GUMA DO ZUCIA WRIGLEY'S B-B 1szt. x3,99"],
            {'hotel': lexicon.split_keywords("b&b\nhotel")})
        self.assertEqual(scores, {})

    def test_misread_keyword_is_tolerated(self):
        categories = {'lodging': ["pernottamento"], 'meal': ["ristorante"]}
        self.assertEqual(lexicon.pick_category(
            lexicon.score_categories(["PERNOTTAMENT0 2 NOTTI"], categories)), 'lodging')

    def test_price_per_litre_or_kwh_is_fuel(self):
        self.assertTrue(lexicon.fuel_unit(["4,83 L x 2,069 €/L", "Totale 10,00"]))
        self.assertTrue(lexicon.fuel_unit(["Energy Tariff: 0.28 EUR/kWh"]))
        self.assertTrue(lexicon.fuel_unit(["Energy: 23.3170 kWh"]))
        self.assertFalse(lexicon.fuel_unit(["Eau minérale 1,5L 0,80", "TOTAL 0,80"]))

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
        cls.employee = cls.env['hr.employee'].create({'name': "Clovis Catégorie"})

    def expense(self, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.default.id,
        }, **values))

    def fresh(self):
        """An expense just uploaded: Odoo gives it a provisional name."""
        expense = self.expense()
        expense.name = expense._get_untitled_expense_name("10/09/2026")
        return expense

    def test_keywords_choose_the_category(self):
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("ZORBLAX INN\nQUIMBO 2\nTOTAL 90,00"), self.company)
        self.assertEqual(values['product_id'], self.lodging.id)
        self.assertEqual(values['expense_scan_guessed_product_id'], self.lodging.id)

    def test_flat_rate_categories_are_never_guessed(self):
        """The flat rate declares the same words, but a receipt does not lead to it."""
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("ZORBLAX INN\nQUIMBO 2\nTOTAL 90,00"), self.company)
        self.assertNotEqual(values.get('product_id'), self.flat.id)

    def test_category_without_vat_is_guessed_and_its_vat_dropped(self):
        """The train has no recoverable VAT but has a receipt."""
        expense = self.expense()
        receipt = reading("GLUMPTRAIN\nTVA 10 % 2,00\nTOTAL 22,00 EUR")
        values = expense._expense_scan_field_values(receipt, self.company)
        self.assertEqual(values['product_id'], self.train.id)
        self.assertEqual(values['scan_tax_amount'], 0.0)

    def test_a_receipt_in_an_inactive_currency_is_recorded_in_it(self):
        """12.50 CHF are not 12.50 in the company currency: the currency is activated."""
        chf = self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', 'CHF')], limit=1)
        if not chf or self.company.currency_id == chf:
            self.skipTest("needs CHF as a foreign currency")
        chf.active = False
        values = self.fresh()._expense_scan_field_values(
            reading("CAFE ZUERI\n8001 Zurich\nTOTAL CHF 12.50\nMWST 8.1% 0.94"), self.company)
        self.assertEqual(values['currency_id'], chf.id)
        self.assertTrue(chf.active)
        self.assertTrue(values['_expense_scan_notes'])

    def test_odoo_default_category_is_not_a_choice(self):
        """No default category in the settings: Odoo's "Expenses" is no choice either."""
        self.company.expense_scan_product_id = False
        generic = self.env['product.product'].search([
            ('default_code', '=', 'EXP_GEN'), ('can_be_expensed', '=', True)], limit=1)
        if not generic:
            generic = self.env['product.product'].create({
                'name': "Expenses", 'default_code': 'EXP_GEN', 'can_be_expensed': True})
        expense = self.expense(product_id=generic.id)
        self.assertEqual(self.company._expense_scan_default_product(), generic)
        self.assertTrue(expense._expense_scan_category_is_free(self.company))

    def test_several_taxes_at_one_rate(self):
        """Goods and services at 7 %: the category's own tax, else the first one."""
        Tax = self.env['account.tax']
        goods, services = [Tax.create({
            'name': name, 'amount': 7.0, 'amount_type': 'percent', 'type_tax_use': 'purchase',
            'company_id': self.company.id, 'sequence': sequence})
            for name, sequence in (("7% goods", 1), ("7% services", 2))]
        expense = self.fresh()
        self.assertEqual(expense._expense_scan_tax(7.0, self.company), goods)
        self.food.supplier_taxes_id = services
        self.assertEqual(expense._expense_scan_tax(7.0, self.company, self.food), services)

    def test_the_domestic_tax_like_the_default_one(self):
        """Spanish chart: "10% IG" (investment goods) and "10% EX G" (import)
        come first; "10% G" is named like the default purchase tax "21% G"."""
        Tax = self.env['account.tax']
        Tax.search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'), ('amount', '=', 10.0),
        ]).active = False

        def tax(name, amount, sequence):
            return Tax.create({'name': name, 'amount': amount, 'amount_type': 'percent',
                               'type_tax_use': 'purchase', 'company_id': self.company.id,
                               'sequence': sequence})
        default = tax("21% Gx (test)", 21.0, 1)
        investment = tax("10% IGx (test)", 10.0, 1)
        imported = tax("10% EX Gx (test)", 10.0, 0)
        goods = tax("10% Gx (test)", 10.0, 2)
        imported.fiscal_position_ids = self.env['account.fiscal.position'].create(
            {'name': "Import (test)", 'company_id': self.company.id})
        self.company.account_purchase_tax_id = default
        expense = self.fresh()
        self.assertEqual(expense._expense_scan_tax(10.0, self.company), goods)
        # Without a default tax of the same family: the first domestic one.
        self.company.account_purchase_tax_id = False
        self.assertEqual(expense._expense_scan_tax(10.0, self.company), investment)

    def test_no_tax_of_another_kind(self):
        """Italian chart: at 4 %, only "4% INPS", a pension contribution."""
        Tax = self.env['account.tax']
        Group = self.env['account.tax.group']
        Tax.search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'), ('amount', '=', 4.0),
        ]).active = False
        vat = Group.create({'name': "22% VATx", 'company_id': self.company.id})
        pension = Group.create({'name': "Pension Fundsx", 'company_id': self.company.id})
        default = Tax.create({'name': "22% Gy (test)", 'amount': 22.0, 'amount_type': 'percent',
                              'type_tax_use': 'purchase', 'company_id': self.company.id,
                              'tax_group_id': vat.id})
        Tax.create({'name': "4% INPSy (test)", 'amount': 4.0, 'amount_type': 'percent',
                    'type_tax_use': 'purchase', 'company_id': self.company.id,
                    'tax_group_id': pension.id})
        self.company.account_purchase_tax_id = default
        self.assertFalse(self.fresh()._expense_scan_tax(4.0, self.company))

    def test_a_tax_rounded_line_by_line_is_accepted(self):
        """9.41 printed at 12 % on 87.75: the exact ceiling is 9.40."""
        expense = self.fresh()
        self.assertTrue(expense._expense_scan_tax_fits(9.41, 87.75, 12.0))
        self.assertFalse(expense._expense_scan_tax_fits(9.50, 87.75, 12.0))

    def test_several_rates_apply_the_highest(self):
        """Two rates on the same receipt: the highest is set on the expense.

        Neither rate holds for the whole expense, but a tax is needed: the
        highest is kept rather than the category's tax (5.5% here, which
        would let too little VAT through).

        Any other purchase tax at these two rates is archived, so that the
        tax picked is the one created here. A French receipt, for a French
        company: elsewhere its "TVA" would be a foreign tax.
        """
        Tax = self.env['account.tax']
        Tax.search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'), ('amount', 'in', (5.5, 10.0)),
        ]).active = False
        low = Tax.create({
            'name': "TVA test 5,5 %", 'amount': 5.5,
            'amount_type': 'percent', 'type_tax_use': 'purchase',
            'company_id': self.company.id})
        high = Tax.create({
            'name': "TVA test 10 % (max ticket)", 'amount': 10.0,
            'amount_type': 'percent', 'type_tax_use': 'purchase',
            'company_id': self.company.id})
        self.food.supplier_taxes_id = low
        # Set once the taxes exist: their default group follows the country.
        self.company.account_fiscal_country_id = self.env['res.country'].search(
            [('code', '=', 'FR')])
        expense = self.expense(product_id=self.food.id)
        receipt = reading(
            "FROMZAK\nTVA 5.50% HT 1,80 0,10 TVA 1,90 TTC\nTVA 10% 7,00 0,70 7,70\n"
            "TOTAL 9,60")  # no currency: domestic, whatever the company's currency
        values = expense._expense_scan_field_values(receipt, self.company)
        self.assertEqual(values['tax_ids'], [(6, 0, high.ids)])
        self.assertEqual(values['scan_tax_amount'], 0.80)

    def test_a_default_category_chosen_by_hand_survives_a_new_scan(self):
        """The default category, chosen by hand, is a choice like any other."""
        expense = self.expense(expense_scan_manual_fields='product_id')
        values = expense._expense_scan_category_values(
            reading("ZORBLAX INN\nQUIMBO 2\nTOTAL 90,00"), self.company)
        self.assertNotIn('product_id', values)

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
        """A "Rchan" corrected once into "Auchan" is recognised afterwards."""
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

    def test_words_learnt_from_filed_receipts(self):
        """A category named like no family learns the words of its receipts."""
        software = self.env['product.product'].create({
            'name': "Abonnements test", 'can_be_expensed': True})
        self.employee.name = "Zelinska"
        filed = [(software, "Glorpsoft"), (self.food, "Fromzak"),
                 (software, "Zundaq"), (self.food, "Quimbar"),
                 (software, "Vrellix"), (self.food, "Fromzak")]
        for index, (product, merchant) in enumerate(filed):
            text = ("Licencezz annualizz %s\nFacture Mme Zelinska" % merchant
                    if product == software else "Plat du jour\nMme Zelinska")
            self.expense(product_id=product.id, total_amount_currency=10.0 + index,
                         expense_scan_merchant=merchant,
                         scan_raw_text="%s\n%s\nTOTAL %d,00" % (merchant.upper(), text, 10 + index)
                         ).approval_state = 'submitted'
        for index in range(6):
            self.expense(product_id=self.lodging.id, total_amount_currency=80.0,
                         scan_raw_text="HOTEL %d\nNuit" % index).approval_state = 'submitted'
        # Suggested by the scan and kept as is: teaches nothing.
        for index in range(3):
            self.expense(product_id=software.id, expense_scan_guessed_product_id=software.id,
                         expense_scan_merchant="Plonkware %d" % index, total_amount_currency=20.0,
                         scan_raw_text="PLONKWARE\nMaintenancezz trimestrizz\nTOTAL 20,00",
                         ).approval_state = 'submitted'
        self.env['hr.expense']._cron_expense_scan_learn_words()
        learnt = (software.expense_scan_learned_keywords or "").split("\n")
        self.assertIn("licencezz", learnt)
        self.assertNotIn("glorpsoft", learnt)  # a single merchant
        self.assertNotIn("zelinska", learnt)  # the employee's name
        self.assertNotIn("total", learnt)
        self.assertNotIn("trimestrizz", learnt)
        values = self.expense()._expense_scan_category_values(
            reading("KWOBBLE\nLicencezz annualizz\nTOTAL 49,00"), self.company)
        self.assertEqual(values['product_id'], software.id)

    def test_a_corrected_suggestion_relearns_at_once(self):
        """Correcting a suggested category checks the learnt words again."""
        self.lodging.expense_scan_learned_keywords = "quimbozz"
        cron = self.env.ref('expense_scan.ir_cron_expense_scan_learn_words')
        triggers = self.env['ir.cron.trigger'].sudo()
        before = triggers.search_count([('cron_id', '=', cron.id)])
        self.expense(product_id=self.food.id).product_id = self.lodging
        self.assertEqual(triggers.search_count([('cron_id', '=', cron.id)]), before)
        expense = self.expense(product_id=self.lodging.id,
                               expense_scan_guessed_product_id=self.lodging.id)
        expense.product_id = self.food
        self.assertEqual(triggers.search_count([('cron_id', '=', cron.id)]), before + 1)

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
        """A "Toll on ..." name as long as nobody wrote a description."""
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
        expense.name = expense._expense_scan_date_name("Zorglub", "12/09/2026")
        self.assertFalse(expense._expense_scan_name_is_automatic())

    def fiscal_country(self, code):
        self.company.account_fiscal_country_id = self.env['res.country'].search(
            [('code', '=', code)])

    def test_foreign_tax_is_not_deducted(self):
        expense = self.expense()
        self.fiscal_country('FR')
        italian = reading("AUTOGRILL\nTOTALE COMPLESSIVO 8,00\nDI CUI IVA 0,73")
        self.assertEqual(expense._expense_scan_foreign_tax(italian, self.company), "IVA")
        values = expense._expense_scan_field_values(italian, self.company)
        self.assertEqual(values['scan_tax_amount'], 0.0)

    def test_the_domestic_tax_follows_the_company_country(self):
        """MWST: domestic for a German company, foreign for a French one."""
        expense = self.expense()
        german = reading("NETTO MARKEN-DISCOUNT\nMWST 19% 1,00\nSUMME 6,26")
        # A German chart of accounts knows the 19 % rate.
        self.env['account.tax'].create({
            'name': "19% Vorsteuer (test)", 'amount': 19.0, 'amount_type': 'percent',
            'type_tax_use': 'purchase', 'company_id': self.company.id})
        Country = self.env['res.country']
        self.company.account_fiscal_country_id = Country.search([('code', '=', 'DE')])
        self.assertFalse(expense._expense_scan_foreign_tax(german, self.company))
        self.company.account_fiscal_country_id = Country.search([('code', '=', 'FR')])
        self.assertEqual(expense._expense_scan_foreign_tax(german, self.company), "MWST")

    def test_french_receipt_is_not_foreign(self):
        expense = self.expense()
        self.fiscal_country('FR')
        french = reading("BRASSERIE\nTVA 10 % 1,00\nTOTAL 11,00 EUR")
        rates = self.env['account.tax'].search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'), ('amount', '=', 10.0)])
        if rates or not self.env['account.tax'].search_count([
                ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase')]):
            self.assertFalse(expense._expense_scan_foreign_tax(french, self.company))

    def test_the_receipt_country_decides_for_a_shared_tax_name(self):
        """"IVA" is Spanish, Italian and Portuguese; "TVA" French and Belgian."""
        expense = self.expense()
        self.fiscal_country('ES')
        # A rate the company's taxes do not know (10 % on a Polish chart)
        # would make the receipt foreign by itself.
        purchase_taxes = self.env['account.tax'].search([
            ('company_id', '=', self.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent')])
        if purchase_taxes and not purchase_taxes.filtered(lambda tax: tax.amount == 10.0):
            purchase_taxes[0].copy({'name': "10% test", 'amount': 10.0})
        italian = reading("AUTOGRILL SPA\nP.IVA 00000000000\nTOTALE COMPLESSIVO 8,00\n"
                          "DI CUI IVA 10% 0,73")
        self.assertEqual(expense._expense_scan_foreign_tax(italian, self.company), "IVA")
        spanish = reading("CARREFOUR\nCIF: A28090108\nTOTAL A PAGAR 3,83\nIVA 10% 0,35")
        self.assertFalse(expense._expense_scan_foreign_tax(spanish, self.company))
        self.fiscal_country('FR')
        belgian = reading("BRASSERIE EXEMPLE\nTVA BE0123.456.789\nTOTAL 12,10\nTVA 21% 2,10")
        self.assertEqual(expense._expense_scan_foreign_tax(belgian, self.company), "TVA")

    def test_the_buyer_tax_number_says_nothing_of_the_seller(self):
        """A Spanish hotel prints the French customer's VAT number."""
        expense = self.expense()
        self.fiscal_country('FR')
        self.company.vat = "FR23334175221"
        hotel = reading("HOTEL EXEMPLO\nCIF B12345678\nCliente: FR 23 334175221\n"
                        "TOTAL 110,00\nIVA 10% 10,00")
        self.assertEqual(expense._expense_scan_foreign_tax(hotel, self.company), "IVA")

    def test_same_day_reason_is_kept(self):
        from datetime import date
        self.expense(name="Déplacement commercial Exemple", date=date(2026, 9, 10))
        values = self.fresh()._expense_scan_field_values(
            reading("PEAGE EXEMPLE\n10/09/2026\nPRIX TTC 6,80"), self.company)
        self.assertEqual(values['name'], "Déplacement commercial Exemple")

    def test_return_toll_keeps_the_trip_reason(self):
        """Outward on the 8th, return on the 10th: the same stations, the other way."""
        from datetime import date
        self.expense(name="Mission Exemple", date=date(2026, 9, 8),
                     scan_raw_text="ASF\nSortie ..Villeun\nEntree.. Villedeux\nPRIX TTC 6,80")
        values = self.fresh()._expense_scan_field_values(reading(
            "ASF\nDate 10/09/26\nSortie ..Villedeux\nEntree.. Villeun\nPRIX TTC 6,80"),
            self.company)
        self.assertEqual(values['name'], "Mission Exemple")

    def test_an_unrelated_trip_keeps_its_own_name(self):
        from datetime import date
        self.expense(name="Autre mission", date=date(2026, 9, 9),
                     scan_raw_text="HOTEL EXEMPLE\n1 rue Exemple\n75001 Paris")
        values = self.fresh()._expense_scan_field_values(
            reading("RESTAURANT EXEMPLE\n69001 Lyon\n10/09/2026\nTOTAL 20,00"), self.company)
        self.assertNotEqual(values['name'], "Autre mission")

    def test_a_city_cited_elsewhere_links_the_trip(self):
        """The ticket "Villeun à Villedeux", then the hotel in Villedeux."""
        from datetime import date
        self.expense(name="Audit Exemple", date=date(2026, 9, 8),
                     scan_raw_text="Villeun à Villedeux\nDépart 08:10\nTOTAL 35,00")
        values = self.fresh()._expense_scan_field_values(reading(
            "HOTEL EXEMPLE\n1 rue Exemple\n12345 Villedeux\n10/09/2026\nTOTAL 80,00"),
            self.company)
        self.assertEqual(values['name'], "Audit Exemple")

    def test_a_neighbour_without_a_read_date_links_nothing(self):
        """Its date is only the day it was entered, not the day of the trip."""
        from datetime import date
        self.expense(name="Saisie du jour", date=date(2026, 9, 10), scan_state='partial')
        values = self.fresh()._expense_scan_field_values(
            reading("BOULANGERIE EXEMPLE\n10/09/2026\nTOTAL 4,20"), self.company)
        self.assertNotEqual(values['name'], "Saisie du jour")

    def test_a_street_named_after_a_city_links_nothing(self):
        """The hotel of "rue de Villedeux" is not in Villedeux."""
        from datetime import date
        self.expense(name="Salon Villedeux", date=date(2026, 9, 8),
                     scan_raw_text="CAFE EXEMPLE\n12345 Villedeux\nTOTAL 3,00")
        values = self.fresh()._expense_scan_field_values(reading(
            "HOTEL EXEMPLE\n20 rue de Villedeux 54321 Villeun\n10/09/2026\nTOTAL 80,00"),
            self.company)
        self.assertNotEqual(values['name'], "Salon Villedeux")

    def test_a_day_between_two_days_of_a_trip_belongs_to_it(self):
        from datetime import date
        self.expense(name="Chantier Exemple", date=date(2026, 9, 9))
        self.expense(name="Chantier Exemple", date=date(2026, 9, 11))
        values = self.fresh()._expense_scan_field_values(
            reading("BOULANGERIE EXEMPLE\n10/09/2026\nTOTAL 4,20"), self.company)
        self.assertEqual(values['name'], "Chantier Exemple")

    def test_company_address_links_nothing(self):
        """The invoice footer ("... 99999 Exempleville") is the address of the buying company."""
        from datetime import date
        self.company.write({'zip': "99999", 'city': "Exempleville"})
        self.expense(name="Formation Exemple", date=date(2026, 9, 9),
                     scan_raw_text="FACTURE\nGREEN EXEMPLE 99999 Exempleville")
        values = self.fresh()._expense_scan_field_values(
            reading("BORNE EXEMPLE\n99999 Exempleville\n10/09/2026\nTOTAL 12,00"), self.company)
        self.assertNotEqual(values['name'], "Formation Exemple")

    def test_seeding_never_overwrites(self):
        hotel = self.env['product.product'].create({
            'name': "Hotel maison", 'can_be_expensed': True,
            'expense_scan_keywords': "mon mot",
        })
        self.env['product.template']._expense_scan_seed_keywords()
        self.assertEqual(hotel.expense_scan_keywords, "mon mot")

    def test_a_category_created_later_gets_its_words(self):
        fuel = self.env['product.template'].create({
            'name': "Kraftstoff", 'can_be_expensed': True, 'sequence': -1000,
        })
        self.assertIn("tankstelle", (fuel.expense_scan_keywords or "").splitlines())

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

    def test_removed_keywords_leave_the_rest_alone(self):
        families = self.env['product.template']._expense_scan_family_templates()
        train = next((t for t, f in families.items() if f == 'train_air'), None)
        if not train:
            return
        train.expense_scan_keywords = "Gare\nAéroport, billet\nmon mot"
        self.env['product.template']._expense_scan_remove_keywords(
            {'train_air': ["aeroport"]})
        self.assertEqual(train.expense_scan_keywords, "Gare\nbillet\nmon mot")

    def test_a_brand_outweighs_a_stray_word(self):
        """Brand (KFC) at the top and a stray hotel word lower down: the category is a meal."""
        meal = self.env['product.product'].create({
            'name': "Restaurant test marque", 'can_be_expensed': True, 'sequence': -999,
            'expense_scan_keywords': "zzrepas"})
        hotel = self.env['product.product'].create({
            'name': "Hôtel test marque", 'can_be_expensed': True, 'sequence': -999,
            'expense_scan_keywords': "zzchambre"})
        families = self.env['product.template']._expense_scan_family_templates()
        if families.get(meal.product_tmpl_id) != 'meal' \
                or families.get(hotel.product_tmpl_id) != 'lodging':
            return
        values = self.expense()._expense_scan_category_values(
            reading("KFC\nKFC Exempleville\n1 Rue Exemple\n00000 Exempleville\n"
                    "N 43\n25/07/2025\nMerci\nArticle 15,90\n"
                    "ZZCHAMBRE\nTOTAL 15,90"), self.company)
        self.assertEqual(values.get('product_id'), meal.id)

    def test_history_is_kept_per_company(self):
        """The history of one company does not affect the classification of another."""
        past = self.expense(product_id=self.food.id, total_amount_currency=14.0)
        past.write({
            'expense_scan_merchant': "Autresocietezz",
            'expense_scan_merchant_read': "autresocietezz",
            'approval_state': 'submitted',
        })
        other = self.env['res.company'].create({'name': "Autre société test"})
        self.env.flush_all()
        self.env.cr.execute("UPDATE hr_expense SET company_id = %s WHERE id = %s",
                            [other.id, past.id])
        self.env.invalidate_all()
        expense = self.expense()
        values = expense._expense_scan_category_values(
            reading("AUTRESOCIETEZZ\nTOTAL 14,00"), self.company)
        self.assertNotIn('product_id', values)

    def test_the_pure_scoring_matches_the_model(self):
        """The benchmark and the module share the same category computation."""
        from ..ocr import categorize
        scores, reasons, brand = categorize.score(
            ["ZORBLAX INN", "QUIMBO 2"], {'A': ["zorblax", "quimbo"]}, {}, activity=None)
        self.assertGreater(scores['A'], 2.0)
        self.assertEqual(reasons['A'][0][1][0], categorize.WORDS)
        self.assertIsNone(brand)
        scores, reasons, _brand = categorize.score(
            ["Prix 1,85 EUR/L"], {}, {'fuel': 'F'}, activity=None)
        self.assertEqual(scores['F'], categorize.UNIT_WEIGHT)
        scores, _reasons, _brand = categorize.score(
            ["RIEN"], {}, {'lodging': 'H'}, activity="NAF:5510Z")
        self.assertEqual(scores.get('H'), categorize.CODE_WEIGHT)


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
        # Text read on a real receipt: "Classe-tarif" on the ninth line,
        # outside the header.
        lines = ["ASFLieu-dit Les Pins BP 10017", "99901 VILLEBOURG Cedex 9", "Te1:3605",
                 "RECU", "N°R1700000000000000000", "Date.. .20/06/26",
                 "Sortie...VILLEBOURG SUD ES", "Entree... .BOURGNEUF",
                 "Classe-tarif..1 km..065", "PRIX HT.......5,67 euros",
                 "TVA 20,00%....1,13 euros", "PRIX TTC......6,80 euros",
                 "Paiement....6,80 E ..CB", "N° carte: .XX00"]
        scores = lexicon.score_categories(lines, {'toll': keywords})
        self.assertEqual(lexicon.pick_category(scores), 'toll')


@tagged('post_install', '-at_install')
class TestCategoryIcons(common.TransactionCase):

    def test_icon_follows_the_kind_of_expense(self):
        Template = self.env['product.template']
        for name, key in (("Hébergement Hôtel", 'lodging'), ("Péages et Parking", 'toll_parking'),
                          ("Carburant/Elec", 'fuel'), ("IGD Logement - Barème", 'house'),
                          ("IGD Repas (x2)", 'meal'), ("Repas d'affaires", 'invitation'),
                          ("Zorglub inconnu", None)):
            self.assertEqual(Template._expense_scan_icon_key(Template.new({'name': name})), key, name)

    def test_seeding_fills_only_missing_images(self):
        Template = self.env['product.template']
        bare = Template.create({'name': "Location de voiture zz", 'can_be_expensed': True})
        own = Template.create({'name': "Hôtel zz", 'can_be_expensed': True,
                               'image_1920': bare_png()})
        Template._expense_scan_seed_icons()
        self.assertTrue(bare.image_1920)
        self.assertEqual(own.image_1920, bare_png())


def bare_png():
    """Minimal PNG (1 px) used as an image already in place."""
    import base64
    return base64.b64encode(
        b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
        b'\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82')
