# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Re-invoicing the expenses of a project on one line of its sales order.

These scenarios need Sales and a chart of accounts: without them they skip.
"""
import unittest

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestInvoicing(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'sale.order' not in cls.env or 'reinvoiced_sale_order_id' not in cls.env['project.project']._fields:
            raise unittest.SkipTest("Sales is not installed")
        cls.company = cls.env.company
        cls.company.expense_scan_reinvoice = True
        cls.employee = cls.env['hr.employee'].create({
            'name': "Hugo Facturable",
            # With a manager the expenses stay "submitted" until approved.
            'expense_manager_id': cls.env.ref('base.user_admin').id,
        })
        cls.category = cls.env['product.product'].create(
            {'name': "Hotel facturable", 'can_be_expensed': True, 'standard_price': 0.0})
        cls.service = cls.env['product.product'].create({
            'name': "Assistance facturable", 'type': 'service', 'list_price': 500.0,
            'invoice_policy': 'delivery'})
        cls.expense_product = cls.env['product.product'].create({
            'name': "Depenses facturables", 'type': 'service', 'list_price': 1.0,
            'can_be_expensed': True, 'expense_policy': 'cost', 'invoice_policy': 'order'})
        cls.partner = cls.env['res.partner'].create({'name': "Client Facturable"})
        cls.project = cls.env['project.project'].create({'name': "Mission facturable"})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'order_line': [
                Command.create({'product_id': cls.service.id, 'product_uom_qty': 20.0}),
                Command.create({'product_id': cls.expense_product.id, 'product_uom_qty': 1.0}),
            ],
        })
        cls.order.action_confirm()
        cls.project.reinvoiced_sale_order_id = cls.order
        cls.line = cls.order.order_line.filtered(lambda l: l.product_id == cls.expense_product)

    def expense(self, amount=100.0, mode='project', submit=True, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': "Frais", 'employee_id': self.employee.id, 'product_id': self.category.id,
            'total_amount_currency': amount, 'reinvoice_mode': mode,
            'project_id': self.project.id if mode != 'todo' else False,
        }, **values))
        if submit:
            expense.action_submit()
        return expense

    def approve(self, expenses):
        expenses.sudo()._do_approve()

    # ------------------------------------------------------------------

    def test_approved_expenses_add_up_on_one_line(self):
        first, second = self.expense(100.0), self.expense(50.0)
        self.assertEqual(self.line.product_uom_qty, 1.0, "waiting for the manager: nothing moves")
        self.approve(first | second)
        self.assertEqual(self.line.product_uom_qty, 150.0)
        self.assertEqual(self.line.qty_delivered, 150.0)
        self.assertEqual(self.line.price_unit, 1.0)
        self.assertEqual(len(self.order.order_line), 2, "no line per expense")

    def test_a_waiting_expense_holds_the_line_back(self):
        done, waiting = self.expense(100.0), self.expense(30.0)
        self.approve(done)
        self.assertEqual(self.line.qty_delivered, 0.0,
                         "the second expense is still waiting: the line does not move")
        self.approve(waiting)
        self.assertEqual(self.line.qty_delivered, 130.0)

    def test_not_re_invoiced_and_other_projects_are_left_out(self):
        other = self.env['project.project'].create({'name': "Autre mission"})
        kept = self.expense(40.0, mode='none')
        elsewhere = self.expense(60.0, project_id=other.id)
        self.approve(kept | elsewhere)
        self.assertEqual(self.line.qty_delivered, 0.0)

    def test_deciding_is_required_to_approve(self):
        undecided = self.expense(20.0, mode='todo')
        with self.assertRaises(UserError):
            undecided.sudo()._do_approve()
        undecided.reinvoice_mode = 'none'
        undecided.sudo()._do_approve()
        self.assertEqual(undecided.approval_state, 'approved')

    def test_refusing_an_approved_expense_lowers_the_line(self):
        first, second = self.expense(100.0), self.expense(50.0)
        self.approve(first | second)
        second.sudo().write({'approval_state': 'refused'})
        self.assertEqual(self.line.qty_delivered, 100.0)

    def test_invoice_notes_and_posts_the_expenses(self):
        first, second = self.expense(100.0), self.expense(50.0)
        self.approve(first | second)
        self.order.order_line.filtered(lambda l: l.product_id == self.service).qty_delivered = 10.0
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.assertEqual((first | second).expense_scan_invoice_id, invoice)
        self.assertEqual(self.line.qty_invoiced, 150.0)
        for expense in first | second:
            self.assertIn(expense.state, ('posted', 'in_payment', 'paid', 'approved'))

    def test_next_invoice_only_takes_what_is_new(self):
        first = self.expense(100.0)
        self.approve(first)
        invoice = self.order._create_invoices()
        invoice.action_post()
        second = self.expense(30.0)
        self.approve(second)
        self.assertEqual(self.line.qty_delivered, 130.0)
        self.assertEqual(self.line.qty_to_invoice, 30.0)
        again = self.order._create_invoices()
        again.action_post()
        self.assertEqual(second.expense_scan_invoice_id, again)
        self.assertEqual(first.expense_scan_invoice_id, invoice)

    def test_posted_by_hand_before_the_invoice_still_counts(self):
        first = self.expense(100.0)
        self.approve(first)
        first.sudo()._post_without_wizard()
        self.assertIn(first.state, ('posted', 'in_payment', 'paid'))
        self.assertEqual(self.line.qty_delivered, 100.0)
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.assertEqual(first.expense_scan_invoice_id, invoice)

    def test_invoice_back_to_draft_releases_the_expenses(self):
        first = self.expense(100.0)
        self.approve(first)
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.assertTrue(first.expense_scan_invoice_id)
        invoice.button_draft()
        self.assertFalse(first.expense_scan_invoice_id)

    def test_nothing_happens_when_the_company_does_not_re_invoice(self):
        self.company.expense_scan_reinvoice = False
        expense = self.expense(100.0)
        self.approve(expense)
        self.assertEqual(self.line.qty_delivered, 0.0)

    def test_existing_price_is_respected(self):
        self.line.price_unit = 2.0
        expense = self.expense(100.0)
        self.approve(expense)
        self.assertEqual(self.line.product_uom_qty, 50.0)

    def test_line_added_when_the_order_has_none(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({'product_id': self.service.id, 'product_uom_qty': 5.0})],
        })
        order.action_confirm()
        project = self.env['project.project'].create({
            'name': "Sans ligne de frais", 'reinvoiced_sale_order_id': order.id})
        expense = self.expense(70.0, project_id=project.id)
        self.approve(expense)
        added = order.order_line.filtered(lambda l: l.product_id.can_be_expensed)
        self.assertEqual(len(added), 1)
        self.assertEqual(added.product_uom_qty, 70.0)

    # ------------------------------------------------------------------

    def test_list_toggle(self):
        expense = self.expense(25.0, mode='none', submit=False)
        expense.action_expense_scan_set_reinvoice('project')
        self.assertEqual(expense.reinvoice_mode, 'project')
        self.assertEqual(expense.project_id, self.project)
        expense.action_expense_scan_set_reinvoice('none')
        self.assertEqual(expense.reinvoice_mode, 'none')
        expense.action_expense_scan_set_reinvoice('todo')
        self.assertFalse(expense.project_id)

    def test_list_toggle_needs_a_project(self):
        expense = self.expense(25.0, mode='todo', submit=False)
        with self.assertRaises(UserError):
            expense.action_expense_scan_set_reinvoice('project')

    def test_budget_figures(self):
        self.project.expense_scan_budget = 300.0
        approved, waiting = self.expense(100.0), self.expense(50.0)
        self.approve(approved)
        project = self.project
        project.invalidate_recordset()
        self.assertEqual(project.expense_scan_spent, 100.0)
        self.assertEqual(project.expense_scan_waiting, 50.0)
        self.assertEqual(project.expense_scan_left, 150.0)
        self.assertAlmostEqual(project.expense_scan_progress, 50.0)
        self.assertFalse(project.expense_scan_over_budget)
        self.expense(200.0)
        project.invalidate_recordset()
        self.assertTrue(project.expense_scan_over_budget)
        self.assertEqual(project.expense_scan_held_back, 2)
