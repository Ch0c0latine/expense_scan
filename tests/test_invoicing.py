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
            {'name': "Hotel facturable", 'can_be_expensed': True, 'standard_price': 0.0,
             'supplier_taxes_id': [Command.clear()]})
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
        self.assertEqual(self.line.qty_delivered, 0.0, "waiting for the manager: nothing moves")
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

    def test_while_expenses_wait_the_line_bills_nothing_more(self):
        self.line.product_uom_qty = 59.0
        self.expense(30.0)
        self.assertEqual(self.line.product_uom_qty, 0.0)

    def test_an_expense_taken_back_leaves_the_line_even_while_others_wait(self):
        first, second = self.expense(100.0), self.expense(50.0)
        self.approve(first | second)
        self.assertEqual(self.line.qty_delivered, 150.0)
        self.expense(10.0)  # waits for the manager
        second.sudo().action_expense_scan_unapprove()
        self.assertEqual(self.line.qty_delivered, 100.0)

    def test_an_activity_flags_the_order_while_expenses_wait(self):
        activity_type = self.env.ref('expense_scan.mail_activity_type_waiting')
        waiting = self.expense(30.0)
        flagged = self.order.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        self.assertEqual(len(flagged), 1)
        self.approve(waiting)
        self.assertFalse(self.order.activity_ids.filtered(lambda a: a.activity_type_id == activity_type))

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
        self.assertEqual((first | second).expense_scan_invoice_id, invoice,
                         "the draft invoice already reserves the expenses")
        self.assertEqual((first | second).mapped('state'), ['approved', 'approved'])
        invoice.action_post()
        self.assertEqual((first | second).expense_scan_invoice_id, invoice)
        self.assertEqual(self.line.qty_invoiced, 150.0)
        for expense in first | second:
            self.assertEqual(expense.state, 'posted', "the invoice posts the expenses it covers")

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

    def test_the_amount_re_invoiced_includes_tax(self):
        tax = self.env['account.tax'].search([
            ('type_tax_use', '=', 'purchase'), ('amount_type', '=', 'percent'), ('amount', '=', 20.0),
            ('company_id', '=', self.company.id)], limit=1)
        if not tax:
            self.skipTest("no 20 % purchase tax")
        expense = self.expense(120.0, tax_ids=[Command.set(tax.ids)])
        self.approve(expense)
        self.assertEqual(self.line.qty_delivered, 120.0)

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
        self.assertEqual(expense.reinvoice_mode, 'todo')
        self.assertEqual(expense.project_id, self.project, "the loop can come back to yes")

    def test_list_toggle_loops_through_the_three_answers(self):
        """To decide, yes, no, to decide... A click without a choice moves on."""
        expense = self.expense(25.0, mode='none', submit=False)
        seen = []
        for _click in range(4):
            expense.action_expense_scan_set_reinvoice()
            seen.append(expense.reinvoice_mode)
        self.assertEqual(seen, ['todo', 'project', 'none', 'todo'])

    def test_an_approved_expense_stays_decided_in_the_loop(self):
        expense = self.expense(25.0)
        self.approve(expense)
        seen = []
        for _click in range(3):
            expense.action_expense_scan_set_reinvoice()
            seen.append(expense.reinvoice_mode)
        self.assertEqual(seen, ['none', 'project', 'none'])
        with self.assertRaises(UserError):
            expense.action_expense_scan_set_reinvoice('todo')

    def test_unapprove_takes_an_approval_back(self):
        first, second = self.expense(100.0), self.expense(40.0)
        self.approve(first | second)
        self.assertEqual(self.line.qty_delivered, 140.0)
        second.sudo().action_expense_scan_unapprove()
        self.assertEqual(second.state, 'submitted')
        self.assertEqual(second.approval_state, 'submitted')
        self.assertEqual(self.line.qty_delivered, 100.0,
                         "the unapproved expense leaves the line; the line does not grow back until all are settled")
        self.approve(second)
        self.assertEqual(self.line.qty_delivered, 140.0)

    def test_only_an_approval_can_be_taken_back(self):
        waiting = self.expense(10.0)
        with self.assertRaises(UserError):
            waiting.sudo().action_expense_scan_unapprove()

    def test_an_expense_on_a_draft_invoice_cannot_change(self):
        first = self.expense(100.0)
        self.approve(first)
        invoice = self.order._create_invoices()
        self.assertEqual(first.expense_scan_invoice_id, invoice)
        with self.assertRaises(UserError):
            first.action_expense_scan_set_reinvoice('none')
        with self.assertRaises(UserError):
            first.write({'reinvoice_mode': 'none'})
        with self.assertRaises(UserError):
            first.sudo().action_expense_scan_unapprove()
        with self.assertRaises(UserError):
            first.sudo().write({'total_amount_currency': 5.0})
        with self.assertRaises(UserError):
            first.sudo().action_reset()
        # The way out: delete the draft, change the expense, invoice again.
        invoice.unlink()
        self.assertFalse(first.expense_scan_invoice_id)
        first.action_expense_scan_set_reinvoice('none')
        self.assertEqual(first.reinvoice_mode, 'none')
        self.assertEqual(self.line.qty_delivered, 0.0)

    def test_a_reset_expense_is_free_again(self):
        """Posted by the invoice, then put back to draft: the column works again."""
        first = self.expense(100.0)
        self.approve(first)
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.assertTrue(first.expense_scan_invoice_id)
        first.sudo().account_move_id.button_draft()
        first.sudo().account_move_id.button_cancel()
        first.sudo().action_reset()
        self.assertFalse(first.expense_scan_invoice_id)
        self.assertEqual(first.state, 'draft')
        first.action_expense_scan_set_reinvoice('none')
        self.assertEqual(first.reinvoice_mode, 'none')

    def test_the_create_invoice_button_warns_about_waiting_expenses(self):
        action = self.env.ref('sale.action_view_sale_advance_payment_inv')
        Expense = self.env['hr.expense']
        self.assertFalse(Expense.expense_scan_order_warning(self.order.id, action.id))
        waiting = self.expense(30.0)
        warning = Expense.expense_scan_order_warning(self.order.id, action.id)
        self.assertTrue(warning)
        self.assertIn('1', warning)
        self.assertFalse(Expense.expense_scan_order_warning(self.order.id, action.id + 1),
                         "only the button that creates invoices")
        self.approve(waiting)
        self.assertFalse(Expense.expense_scan_order_warning(self.order.id, action.id))

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
