# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Remise en brouillon de plusieurs dépenses à la fois."""
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestBatchReset(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Sam Lots"})
        cls.product = cls.env['product.product'].create(
            {'name': "Divers lots", 'can_be_expensed': True})

    def expense(self, name, submitted=True):
        expense = self.env['hr.expense'].create({
            'name': name, 'employee_id': self.employee.id,
            'product_id': self.product.id, 'total_amount_currency': 10.0,
        })
        if submitted:
            expense.write({'approval_state': 'submitted'})
        return expense

    def test_submitted_expenses_go_back_to_draft(self):
        expenses = self.expense("Un") | self.expense("Deux") | self.expense("Brouillon", False)
        action = expenses.action_expense_scan_reset_batch()
        self.assertEqual(set(expenses.mapped('state')), {'draft'})
        self.assertEqual(action['params']['type'], 'success')
        self.assertIn("2", action['params']['message'])
