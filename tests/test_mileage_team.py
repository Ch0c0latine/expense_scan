# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tarif kilométrique et saisie pour un membre de l'équipe."""
from odoo.tests import Form, common, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestExpenseScanMileage(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mileage = cls.env.ref('hr_expense.expense_product_mileage')
        cls.mileage.standard_price = 1.0
        cls.employee = cls.env['hr.employee'].create({
            'name': "Camille Test",
            'expense_mileage_rate': 0.636,
        })

    def expense(self):
        return self.env['hr.expense'].create({
            'name': "Déplacement",
            'employee_id': self.employee.id,
            'product_id': self.mileage.id,
            'quantity': 100,
        })

    def test_employee_rate_is_the_default(self):
        """Trois décimales, comme le barème fiscal : aucun arrondi au centime."""
        self.assertAlmostEqual(self.expense().price_unit, 0.636)

    def test_category_cost_without_employee_rate(self):
        self.employee.expense_mileage_rate = 0.0
        self.assertAlmostEqual(self.expense().price_unit, 1.0)

    def test_typed_price_survives_a_one_km_quantity(self):
        """Le cas signalé : un kilomètre ramenait le prix à celui de la catégorie."""
        expense = self.expense()
        expense.price_unit = 0.7
        expense.quantity = 1
        self.assertAlmostEqual(expense.price_unit, 0.7)
        self.assertAlmostEqual(expense.total_amount_currency, 0.7)

    def test_typed_price_survives_any_quantity(self):
        expense = self.expense()
        expense.price_unit = 0.595
        expense.quantity = 250
        self.assertAlmostEqual(expense.price_unit, 0.595)

    def test_switching_to_mileage_resets_the_price(self):
        """Un prix issu d'un total saisi ne devient pas un tarif au kilomètre."""
        general = self.env['product.product'].create({
            'name': "Divers", 'can_be_expensed': True, 'standard_price': 0.0,
        })
        with Form(self.env['hr.expense']) as form:
            form.employee_id = self.employee
            form.product_id = general
            form.total_amount_currency = 50.0
            form.product_id = self.mileage
            self.assertAlmostEqual(form.price_unit, 0.636)


@tagged('post_install', '-at_install')
class TestExpenseScanTeam(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_user = new_test_user(
            cls.env, login='manager_scan',
            groups='base.group_user,hr_expense.group_hr_expense_team_approver')
        cls.manager = cls.env['hr.employee'].create({
            'name': "Manager", 'user_id': cls.manager_user.id,
        })
        cls.member = cls.env['hr.employee'].create({
            'name': "Léon", 'expense_manager_id': cls.manager_user.id,
        })
        cls.stranger = cls.env['hr.employee'].create({'name': "Étranger"})

    def team(self):
        return self.env['hr.expense'].with_user(self.manager_user) \
            ._expense_scan_team_employees()

    def test_team_follows_the_access_rules(self):
        team = self.team()
        self.assertIn(self.member, team)
        self.assertNotIn(self.stranger, team)

    def test_manager_is_not_in_his_own_team(self):
        """Ses propres frais ont déjà leur menu."""
        self.assertNotIn(self.manager, self.team())

    def test_hierarchy_counts(self):
        report = self.env['hr.employee'].create({
            'name': "Subordonné", 'parent_id': self.manager.id,
        })
        self.assertIn(report, self.team())

    def test_plain_employee_has_no_team(self):
        plain = new_test_user(self.env, login='plain_scan', groups='base.group_user')
        self.assertFalse(self.env['hr.expense'].with_user(plain)._expense_scan_team_employees())

    def test_opening_a_member_sets_the_default_employee(self):
        public = self.env['hr.employee.public'].with_user(self.manager_user).browse(self.member.id)
        action = public.action_expense_scan_open_expenses()
        self.assertEqual(action['context']['default_employee_id'], self.member.id)
        self.assertEqual(action['domain'], [('employee_id', '=', self.member.id)])

    def test_stranger_cannot_be_opened(self):
        public = self.env['hr.employee.public'].with_user(self.manager_user).browse(self.stranger.id)
        with self.assertRaises(Exception):
            public.action_expense_scan_open_expenses()

    def test_entry_for_someone_else_is_noted(self):
        """L'historique dit qui a saisi pour qui."""
        product = self.env.ref('hr_expense.expense_product_mileage')
        expense = self.env['hr.expense'].with_user(self.manager_user).create({
            'name': "Pour Léon", 'employee_id': self.member.id, 'product_id': product.id,
        })
        bodies = ' '.join(expense.sudo().message_ids.mapped('body'))
        self.assertIn("Saisie par", bodies)
        self.assertIn("Léon", bodies)


@tagged('post_install', '-at_install')
class TestExpenseScanCategories(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mileage = cls.env.ref('hr_expense.expense_product_mileage')
        cls.mileage.standard_price = 1.0
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Test"})
        cls.tax = cls.env['account.tax'].create({
            'name': "TVA test 20 %", 'amount': 20.0, 'type_tax_use': 'purchase',
        })

    def test_mileage_carries_no_tax(self):
        """Une indemnité kilométrique est un forfait, sans TVA récupérable."""
        self.mileage.supplier_taxes_id = self.tax
        expense = self.env['hr.expense'].create({
            'name': "Déplacement", 'employee_id': self.employee.id,
            'product_id': self.mileage.id, 'quantity': 10,
        })
        self.assertFalse(expense.tax_ids)
        self.assertTrue(expense.expense_scan_no_vat)

    def test_switching_to_mileage_clears_the_vat(self):
        general = self.env['product.product'].create({
            'name': "Divers", 'can_be_expensed': True, 'standard_price': 0.0,
            'supplier_taxes_id': [(6, 0, self.tax.ids)],
        })
        with Form(self.env['hr.expense']) as form:
            form.employee_id = self.employee
            form.product_id = general
            form.total_amount_currency = 12.0
            form.scan_tax_amount = 2.0
            form.product_id = self.mileage
            self.assertFalse(form.tax_ids)
            self.assertEqual(form.scan_tax_amount, 0.0)

    def test_categories_follow_their_sequence(self):
        """La liste déroulante suit la séquence, pas la référence interne."""
        first = self.env['product.product'].create({
            'name': "Zèbre", 'default_code': 'ZZZ', 'can_be_expensed': True, 'sequence': 1,
        })
        last = self.env['product.product'].create({
            'name': "Âne", 'default_code': 'AAA', 'can_be_expensed': True, 'sequence': 999,
        })
        found = self.env['product.product'].with_context(
            expense_scan_category_order=True,
        ).name_search('', [('id', 'in', (first | last).ids)])
        self.assertEqual([product_id for product_id, _name in found], [first.id, last.id])

    def test_other_searches_keep_odoo_order(self):
        """Sans le drapeau, rien ne change : tri par référence."""
        first = self.env['product.product'].create({
            'name': "Zèbre", 'default_code': 'ZZZ', 'can_be_expensed': True, 'sequence': 1,
        })
        last = self.env['product.product'].create({
            'name': "Âne", 'default_code': 'AAA', 'can_be_expensed': True, 'sequence': 999,
        })
        found = self.env['product.product'].name_search('', [('id', 'in', (first | last).ids)])
        self.assertEqual([product_id for product_id, _name in found], [last.id, first.id])

    def test_flat_rate_category_carries_no_tax(self):
        """Un barème Urssaf n'a pas d'unité de distance : c'est la case qui compte."""
        per_diem = self.env['product.product'].create({
            'name': "IGD Repas", 'can_be_expensed': True, 'standard_price': 20.7,
            'supplier_taxes_id': [(6, 0, self.tax.ids)],
            'expense_scan_no_vat': True,
        })
        expense = self.env['hr.expense'].create({
            'name': "Repas", 'employee_id': self.employee.id, 'product_id': per_diem.id,
        })
        self.assertTrue(expense.expense_scan_no_vat)
        self.assertFalse(expense.tax_ids)

    def test_ordinary_category_keeps_its_vat(self):
        general = self.env['product.product'].create({
            'name': "Divers", 'can_be_expensed': True,
            'supplier_taxes_id': [(6, 0, self.tax.ids)],
        })
        expense = self.env['hr.expense'].create({
            'name': "Achat", 'employee_id': self.employee.id, 'product_id': general.id,
            'total_amount_currency': 12.0,
        })
        self.assertFalse(expense.expense_scan_no_vat)
        self.assertEqual(expense.tax_ids, self.tax)

    def test_checking_no_vat_clears_the_category_taxes(self):
        category = self.env['product.product'].create({
            'name': "Indemnité", 'can_be_expensed': True,
            'supplier_taxes_id': [(6, 0, self.tax.ids)],
        })
        with Form(category, view='hr_expense.product_product_expense_form_view') as form:
            form.expense_scan_no_vat = True
        self.assertFalse(category.supplier_taxes_id)


@tagged('post_install', '-at_install')
class TestExpenseScanNightsAndReturn(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hotel = cls.env['product.product'].create({
            'name': "Hébergement Hôtel/Bnb", 'can_be_expensed': True,
        })
        cls.employee = cls.env['hr.employee'].create({'name': "Jules"})

    def test_hotel_without_nights_is_refused(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env['hr.expense'].create({
                'name': "Nuit", 'employee_id': self.employee.id,
                'product_id': self.hotel.id, 'total_amount_currency': 90.0,
                'expense_scan_nights': 0,
            })

    def test_hotel_defaults_to_one_night(self):
        expense = self.env['hr.expense'].create({
            'name': "Nuit", 'employee_id': self.employee.id,
            'product_id': self.hotel.id, 'total_amount_currency': 90.0,
        })
        self.assertEqual(expense.expense_scan_nights, 1)

    def test_only_hotels_count_nights(self):
        Product = self.env['product.product']
        self.assertTrue(self.hotel.expense_scan_nights_required)
        for name, code in (("IGD Logement&pt.dej - Bareme Urssaf", "IDG Logement"),
                           ("Taxi/VTC", "MOB_URB"), ("Repas", "REPAS")):
            other = Product.create({'name': name, 'default_code': code, 'can_be_expensed': True})
            self.assertFalse(other.expense_scan_nights_required, name)

    def test_hotel_with_nights_is_accepted(self):
        expense = self.env['hr.expense'].create({
            'name': "Nuits", 'employee_id': self.employee.id,
            'product_id': self.hotel.id, 'total_amount_currency': 180.0,
            'expense_scan_nights': 2,
        })
        self.assertEqual(expense.expense_scan_nights, 2)

    def test_other_categories_need_no_nights(self):
        other = self.env['product.product'].create({'name': "Divers", 'can_be_expensed': True})
        expense = self.env['hr.expense'].create({
            'name': "Achat", 'employee_id': self.employee.id, 'product_id': other.id,
        })
        self.assertFalse(expense.expense_scan_nights_required)

    def test_done_returns_to_the_employee_list(self):
        """Saisir pour Jules, puis « Terminé » : on revient chez Jules."""
        other = self.env['product.product'].create({'name': "Divers", 'can_be_expensed': True})
        expense = self.env['hr.expense'].create({
            'name': "Pour Jules", 'employee_id': self.employee.id, 'product_id': other.id,
        })
        action = expense.action_expense_scan_done()
        self.assertEqual(action['domain'], [('employee_id', '=', self.employee.id)])
        self.assertEqual(action['context']['default_employee_id'], self.employee.id)
        self.assertEqual(action['target'], 'main')

    def test_done_on_own_expense_returns_to_my_expenses(self):
        mine = self.env.user.employee_id or self.env['hr.employee'].create({
            'name': "Moi", 'user_id': self.env.user.id,
        })
        other = self.env['product.product'].create({'name': "Divers", 'can_be_expensed': True})
        expense = self.env['hr.expense'].create({
            'name': "À moi", 'employee_id': mine.id, 'product_id': other.id,
        })
        action = expense.action_expense_scan_done()
        self.assertNotIn('default_employee_id', action.get('context') or {})
