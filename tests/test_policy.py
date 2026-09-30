# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Expense rules: general good practice and customer requirements."""
from datetime import date

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestExpensePolicy(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The warnings are checked in English, whatever the database language.
        cls.env['res.lang']._activate_lang('en_US')
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        # The delivered examples must not mix with the test rules.
        cls.env['expense.scan.policy'].search([]).action_archive()
        Partner = cls.env['res.partner']
        client = Partner.create({'name': "Client final Zz", 'is_company': True})
        site = Partner.create({'name': "Site Zz", 'parent_id': client.id})
        cls.project = cls.env['project.project'].create(
            {'name': "Mission Zz", 'partner_id': site.id})
        cls.other_project = cls.env['project.project'].create({'name': "Mission libre Zz"})
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Règles"})
        cls.employee.work_contact_id.lang = 'en_US'
        Product = cls.env['product.product']
        cls.meal = Product.create({'name': "Repas règles", 'can_be_expensed': True})
        cls.hotel = Product.create({'name': "Hôtel règles", 'can_be_expensed': True})
        cls.train = Product.create({'name': "Train règles", 'can_be_expensed': True})
        meal = [(6, 0, cls.meal.ids)]
        cls.env['expense.scan.policy'].create({
            'name': "Règles Zz",
            'partner_ids': [(6, 0, client.ids)],
            'rule_ids': [
                (0, 0, {'name': "Classe", 'product_ids': [(6, 0, cls.train.ids)],
                        'rule_type': 'forbidden_words', 'words': "1ère classe\nbusiness"}),
                (0, 0, {'name': "Petit-déjeuner 20", 'product_ids': meal,
                        'meal_period': 'breakfast', 'amount': 20}),
                (0, 0, {'name': "Déjeuner 20", 'product_ids': meal,
                        'meal_period': 'lunch', 'amount': 20}),
                (0, 0, {'name': "Dîner 40", 'product_ids': meal,
                        'meal_period': 'dinner', 'amount': 40}),
                (0, 0, {'name': "Journée 60", 'product_ids': meal, 'rule_type': 'daily_max',
                        'meal_period': 'day', 'amount': 60}),
                (0, 0, {'name': "Hôtel 100", 'product_ids': [(6, 0, cls.hotel.ids)],
                        'amount': 100, 'per_night': True, 'extra_amount': 20,
                        'extra_cities': "Paris, Lyon, Strasbourg"}),
            ],
        })

    def expense(self, name, total, product=None, project=None, **values):
        return self.env['hr.expense'].create(dict({
            'name': name, 'employee_id': self.employee.id,
            'product_id': (product or self.meal).id,
            'project_id': (project or self.project).id,
            'reinvoice_mode': 'project',
            'date': date(2026, 9, 1), 'total_amount_currency': total,
        }, **values))

    def test_lunch_over_its_ceiling_on_an_expensive_day(self):
        lunch = self.expense("Ticket", 25.0, scan_time="12:30")
        self.expense("Repas soir", 40.0)
        self.assertTrue(lunch.expense_scan_policy_breach)
        self.assertIn("Déjeuner 20", lunch.expense_scan_policy_alert)
        self.assertIn("Journée 60", lunch.expense_scan_policy_alert)

    def test_a_day_within_its_limit_covers_each_meal(self):
        """Lunch at 25 and dinner at 30: 55 for the day, accepted."""
        lunch = self.expense("Ticket", 25.0, scan_time="12:30")
        dinner = self.expense("Repas soir", 30.0)
        self.assertFalse(lunch.expense_scan_policy_alert)
        self.assertFalse(dinner.expense_scan_policy_alert)

    def test_breakfast_counts_in_the_day(self):
        breakfast = self.expense("Petit-déjeuner", 15.0)
        lunch = self.expense("Repas midi", 25.0)
        dinner = self.expense("Repas soir", 25.0)
        for meal in breakfast | lunch | dinner:
            self.assertIn("Journée 60", meal.expense_scan_policy_alert)
        self.assertIn("Déjeuner 20", lunch.expense_scan_policy_alert)

    def test_dinner_within_its_ceiling(self):
        dinner = self.expense("Repas soir", 35.0)
        self.assertFalse(dinner.expense_scan_policy_breach)

    def test_lunch_and_dinner_of_the_same_day(self):
        lunch = self.expense("Repas midi", 18.0)
        dinner = self.expense("Repas soir", 45.0)
        self.assertIn("Dîner 40", dinner.expense_scan_policy_alert)
        self.assertIn("Journée 60", dinner.expense_scan_policy_alert)
        # The lunch, under its ceiling, also reports the day going over.
        self.assertIn("Journée 60", lunch.expense_scan_policy_alert)

    def test_meal_without_time_is_a_suspicion(self):
        meal = self.expense("Ticket", 30.0)
        self.assertFalse(meal.expense_scan_policy_alert)  # alone, it fits in the day
        self.expense("Repas soir", 35.0)
        self.assertIn("without a readable time", meal.expense_scan_policy_alert)

    def test_hotel_per_night_and_city_extra(self):
        lyon = self.expense("Nuits", 230.0, product=self.hotel, expense_scan_nights=2,
                            scan_raw_text="HOTEL DU CENTRE\n69002 LYON")
        self.assertFalse(lyon.expense_scan_policy_breach)
        elsewhere = self.expense("Nuits", 230.0, product=self.hotel, expense_scan_nights=2,
                                 scan_raw_text="HOTEL DU CENTRE\n13001 MARSEILLE")
        self.assertRegex(elsewhere.expense_scan_policy_alert, r"115[.,]00")

    def test_first_class_train(self):
        ticket = self.expense("Billet", 80.0, product=self.train,
                              scan_raw_text="TGV INOUI\n1ERE CLASSE\nVOITURE 1")
        self.assertIn("1ere classe", ticket.expense_scan_policy_alert)

    def test_a_class_named_in_the_conditions_is_not_the_one_bought(self):
        """The fare conditions of an economy ticket name the business class."""
        ticket = self.expense("Billet", 80.0, product=self.train, scan_raw_text=(
            "EXEMPLE AIR\nECONOMY\nTOTAL 80,00\nConditions generales de transport\n"
            "Business: bagage de 32 kg"))
        self.assertFalse(ticket.expense_scan_policy_alert)

    def test_the_signs_tell_a_breach_from_a_doubt(self):
        lunch = self.expense("Repas midi", 25.0)
        self.expense("Repas soir", 40.0)
        self.assertEqual(lunch.expense_scan_policy_status, 'breach')
        self.assertTrue(lunch.expense_scan_policy_alert.startswith("⚠ "))
        ticket = self.expense("Billet", 80.0, product=self.train,
                              scan_raw_text="TGV INOUI\n1ERE CLASSE")
        self.assertEqual(ticket.expense_scan_policy_status, 'check')
        self.assertTrue(ticket.expense_scan_policy_alert.startswith("ℹ︎ "))
        self.assertFalse(self.expense("Repas midi", 15.0, date=date(2026, 9, 9))
                         .expense_scan_policy_status)

    def test_an_amount_without_exchange_rate_is_not_compared(self):
        """40 units of a currency without a rate are not 40 EUR."""
        currency = self.env['res.currency'].create({
            'name': 'XZZ', 'symbol': 'Zz', 'active': True})
        self.expense("Repas soir", 40.0)
        lunch = self.expense("Repas midi", 25.0, currency_id=currency.id)
        self.assertNotIn("Déjeuner 20", lunch.expense_scan_policy_alert)
        self.assertIn("No exchange rate for XZZ", lunch.expense_scan_policy_alert)
        self.assertEqual(lunch.expense_scan_policy_status, 'check')
        self.assertTrue(lunch._expense_scan_missing_rate())

    def test_an_amount_in_an_inactive_currency_is_not_compared(self):
        """Receipt in CHF, currency not active: the amount is not in euros."""
        self.expense("Repas soir", 40.0)
        lunch = self.expense(
            "Repas midi", 25.0, expense_scan_todo_codes='currency',
            expense_scan_read_values='{"currency_id": %d}' % self.env.company.currency_id.id)
        self.assertNotIn("Déjeuner 20", lunch.expense_scan_policy_alert)
        self.assertIn("not active in Odoo", lunch.expense_scan_policy_alert)

    def test_the_findings_speak_the_employee_language(self):
        """Stored text: written for the employee, whoever triggers the check."""
        self.env['res.lang']._activate_lang('fr_FR')
        self.employee.work_contact_id.lang = 'fr_FR'
        self.expense("Repas soir", 40.0)
        lunch = self.expense("Repas midi", 25.0)
        self.assertEqual(lunch.env.lang, 'en_US')
        self.assertIn("autorisés", lunch.expense_scan_policy_alert)

    def test_other_clients_are_not_checked(self):
        free = self.expense("Ticket", 90.0, project=self.other_project, scan_time="12:30")
        self.assertFalse(free.expense_scan_policy_alert)

    def test_company_wide_rules_check_every_expense(self):
        """Rules for "all expenses": even without a project, and without a family."""
        self.env['expense.scan.policy'].create({
            'name': "Bonne conduite Zz",
            'apply_to_all': True,
            'rule_ids': [(0, 0, {'name': "Pas d'amende", 'rule_type': 'forbidden_words',
                                 'words': "amende"})],
        })
        fine = self.env['hr.expense'].create({
            'name': "Stationnement", 'employee_id': self.employee.id,
            'product_id': self.train.id, 'date': date(2026, 9, 2),
            'total_amount_currency': 35.0,
            'scan_raw_text': "AVIS DE CONTRAVENTION\nAMENDE FORFAITAIRE 35 EUR",
        })
        self.assertIn("Pas d'amende", fine.expense_scan_policy_alert)
        self.assertFalse(fine.project_id)

    def test_delivered_good_practice_is_archivable(self):
        policy = self.env.ref('expense_scan.policy_good_practice')
        self.assertTrue(policy.apply_to_all)
        self.assertTrue(policy.rule_ids)
        policy.action_unarchive()
        meal = self.env['hr.expense'].create({
            'name': "Repas midi", 'employee_id': self.employee.id,
            'product_id': self.meal.id, 'date': date(2026, 9, 3),
            'total_amount_currency': 30.0, 'scan_raw_text': "MINIBAR",
        })
        # The minibar only concerns the hotel.
        self.assertNotIn("minibar", meal.expense_scan_policy_alert or "")
        policy.action_archive()
        self.assertFalse(policy.active)
