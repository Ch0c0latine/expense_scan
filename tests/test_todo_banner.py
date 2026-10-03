# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The review banner goes away as the corrections are made, without a new scan."""
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestTodoBanner(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A database without a chart of accounts has no fiscal country, and a tax needs one.
        if not cls.env.company.account_fiscal_country_id:
            cls.env.company.account_fiscal_country_id = cls.env.ref('base.fr')
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Bandeau"})
        cls.product = cls.env['product.product'].create(
            {'name': "Repas bandeau", 'can_be_expensed': True})

    def expense(self, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.product.id, 'total_amount_currency': 10.0,
        }, **values))

    def test_reinvoice_flag_clears_once_decided(self):
        """'Re-invoice' flagged by the scan: goes away once the decision is made."""
        expense = self.expense(
            scan_state='partial', scan_todo="Re-invoice",
            expense_scan_todo_codes='reinvoice', reinvoice_mode='todo')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.reinvoice_mode = 'none'
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_tax_flag_clears_once_amount_entered(self):
        expense = self.expense(
            scan_state='partial', scan_todo="Tax (none on the receipt)",
            expense_scan_todo_codes='tax_amount')
        self.assertTrue(expense.expense_scan_todo_pending)
        # Below the ceiling of any usual rate on 10.00, whatever the country.
        expense.scan_tax_amount = 0.5
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_tax_category_flag_clears_once_a_tax_is_set(self):
        tax = self.env['account.tax'].search([
            ('company_id', '=', self.env.company.id), ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent')], limit=1) or self.env['account.tax'].create({
                'name': "Achat bandeau", 'amount': 20.0,
                'amount_type': 'percent', 'type_tax_use': 'purchase'})
        # Category without a default tax: the case flagged by the code
        # "tax_category".
        bare = self.env['product.product'].create({
            'name': "Sans taxe bandeau", 'can_be_expensed': True,
            'supplier_taxes_id': [(5, 0, 0)]})
        expense = self.expense(
            product_id=bare.id, tax_ids=[(5, 0, 0)],
            scan_state='partial', scan_todo="Tax (none on the category)",
            expense_scan_todo_codes='tax_category')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.tax_ids = [(6, 0, tax.ids)]
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_static_points_never_clear_on_their_own(self):
        """Date, category, currency: the flag does not go away on its own."""
        expense = self.expense(
            scan_state='partial', scan_todo="Category",
            expense_scan_todo_codes='static')
        self.assertTrue(expense.expense_scan_todo_pending)
        expense.product_id = self.env['product.product'].create(
            {'name': "Autre bandeau", 'can_be_expensed': True}).id
        self.assertTrue(expense.expense_scan_todo_pending)

    def test_several_points_all_need_resolving(self):
        """The banner stays as long as one point is still open."""
        expense = self.expense(
            scan_state='partial', scan_todo="Re-invoice, Category",
            expense_scan_todo_codes='reinvoice,static', reinvoice_mode='todo')
        expense.reinvoice_mode = 'none'
        self.assertTrue(expense.expense_scan_todo_pending)  # "static" remains

    def test_no_codes_means_nothing_pending(self):
        expense = self.expense(scan_state='done')
        self.assertFalse(expense.expense_scan_todo_pending)


@tagged('post_install', '-at_install')
class TestFieldHints(common.TransactionCase):
    """Each point to check shows under its field and goes away once corrected."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Indication"})
        cls.product = cls.env['product.product'].create(
            {'name': "Repas indication", 'can_be_expensed': True})

    def scanned(self, codes, hints, **values):
        import json
        from datetime import date
        expense = self.env['hr.expense'].create(dict({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.product.id, 'total_amount_currency': 10.0,
            'date': date(2026, 9, 10), 'scan_state': 'partial',
            'expense_scan_todo_codes': codes, 'expense_scan_hints': json.dumps(hints),
        }, **values))
        expense.expense_scan_read_values = json.dumps({
            name: expense._expense_scan_comparable(name)
            for name in expense.READ_VALUE_FIELDS})
        return expense

    def test_date_hint_clears_once_the_date_is_corrected(self):
        from datetime import date
        expense = self.scanned('date', {'date': "Date hard to read"})
        self.assertEqual(expense.expense_scan_hint_date, "Date hard to read")
        self.assertFalse(expense.expense_scan_hint_total)
        expense.date = date(2026, 9, 11)
        self.assertFalse(expense.expense_scan_hint_date)
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_total_and_currency_share_the_amount_line(self):
        expense = self.scanned('total,currency', {'total': "Amount?", 'currency': "Currency?"})
        self.assertEqual(expense.expense_scan_hint_total, "Amount? Currency?")
        expense.total_amount_currency = 12.5
        self.assertEqual(expense.expense_scan_hint_total, "Currency?")

    def test_category_hint_clears_once_another_is_chosen(self):
        expense = self.scanned('category', {'category': "Category?"})
        self.assertEqual(expense.expense_scan_hint_category, "Category?")
        expense.product_id = self.env['product.product'].create(
            {'name': "Autre indication", 'can_be_expensed': True})
        self.assertFalse(expense.expense_scan_hint_category)

    def test_done_clears_every_hint(self):
        expense = self.scanned('date,category', {'date': "D", 'category': "C"})
        expense.action_expense_scan_done()
        self.assertFalse(expense.expense_scan_hint_date)
        self.assertFalse(expense.expense_scan_hint_category)
        self.assertFalse(expense.expense_scan_todo_pending)

    def test_old_analyses_keep_their_banner(self):
        """Code "static": a point older than the per-field hints, with no field of its own."""
        expense = self.scanned('static', {})
        self.assertTrue(expense.expense_scan_todo_unplaced)
        placed = self.scanned('date', {'date': "D"})
        self.assertFalse(placed.expense_scan_todo_unplaced)
