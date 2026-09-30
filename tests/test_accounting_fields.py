# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Account and analytic distribution on the expense form.

Shown to expense managers only, and only when Odoo shows them: the full
accounting features for the account, analytic accounting for the
distribution. Both conditions apply.
"""
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestAccountingFields(common.TransactionCase):

    def user(self, login, *groups):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': 'expense_scan_%s' % login,
            'group_ids': [(6, 0, [self.env.ref(group).id
                                  for group in ('base.group_user',) + groups])]})

    def form(self, user):
        return self.env['hr.expense'].with_user(user).get_views(
            [(False, 'form')])['views']['form']['arch']

    def test_a_manager_without_analytic_accounting_sees_neither(self):
        arch = self.form(self.user('manager', 'hr_expense.group_hr_expense_manager'))
        self.assertNotIn('name="analytic_distribution"', arch)
        self.assertNotIn('name="account_id"', arch)

    def test_a_manager_sees_what_odoo_shows(self):
        arch = self.form(self.user('manager_analytic', 'hr_expense.group_hr_expense_manager',
                                   'analytic.group_analytic_accounting',
                                   'account.group_account_readonly'))
        self.assertIn('name="analytic_distribution"', arch)
        self.assertIn('name="account_id"', arch)

    def test_an_employee_does_not_see_them(self):
        user = self.user('employee', 'analytic.group_analytic_accounting',
                         'account.group_account_readonly')
        expense = self.env['hr.expense'].with_user(user).new({})
        self.assertFalse(expense.expense_scan_expense_manager)
        manager = self.user('manager_flag', 'hr_expense.group_hr_expense_manager')
        self.assertTrue(self.env['hr.expense'].with_user(manager).new({})
                        .expense_scan_expense_manager)
