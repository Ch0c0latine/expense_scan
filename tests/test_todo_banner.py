# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Le bandeau de relecture disparaît au fil de l'eau, sans nouveau scan."""
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestTodoBanner(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Bandeau"})
        cls.product = cls.env['product.product'].create(
            {'name': "Repas bandeau", 'can_be_expensed': True})

    def expense(self, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.product.id, 'total_amount_currency': 10.0,
        }, **values))

    def test_reinvoice_flag_clears_once_decided(self):
        """« À refacturer » signalé au scan disparaît dès qu'on tranche."""
        expense = self.expense(
            scan_state='partial', scan_todo="À refacturer",
            expense_scan_todo_codes='reinvoice', reinvoice_mode='todo')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.reinvoice_mode = 'none'
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_tax_flag_clears_once_amount_entered(self):
        expense = self.expense(
            scan_state='partial', scan_todo="TVA (aucune sur le justificatif)",
            expense_scan_todo_codes='tax_amount')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.scan_tax_amount = 1.5
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_tax_category_flag_clears_once_a_tax_is_set(self):
        tax = self.env['account.tax'].search([
            ('company_id', '=', self.env.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent')], limit=1) or self.env['account.tax'].create({
                'name': "Achat bandeau", 'amount': 20.0,
                'amount_type': 'percent', 'type_tax_use': 'purchase'})
        expense = self.expense(
            scan_state='partial', scan_todo="Taxe (aucune sur la catégorie)",
            expense_scan_todo_codes='tax_category')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.tax_ids = [(6, 0, tax.ids)]
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_static_points_never_clear_on_their_own(self):
        """Date, catégorie, devise : rien ne les corrige tout seuls."""
        expense = self.expense(
            scan_state='partial', scan_todo="Catégorie",
            expense_scan_todo_codes='static')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.product_id = self.env['product.product'].create(
            {'name': "Autre bandeau", 'can_be_expensed': True}).id
        self.assertTrue(expense.expense_scan_todo_pending)

    def test_several_points_all_need_resolving(self):
        """Le bandeau reste tant qu'un seul point est encore ouvert."""
        expense = self.expense(
            scan_state='partial', scan_todo="À refacturer, Catégorie",
            expense_scan_todo_codes='reinvoice,static', reinvoice_mode='todo')
        expense.reinvoice_mode = 'none'
        self.assertTrue(expense.expense_scan_todo_pending)  # « static » reste

    def test_no_codes_means_nothing_pending(self):
        expense = self.expense(scan_state='done')
        self.assertFalse(expense.expense_scan_todo_pending)
