# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Customer invoices that carry re-invoiced expenses.

A draft invoice made from a sales order reserves the expenses its expense line
covers: from then on they cannot be taken back or changed, since the invoice
bills them. Posting the invoice posts the expenses in turn, unless they were
posted by hand before (to repay the employee without waiting for the
invoice). Deleting, cancelling or resetting the invoice frees them.
"""
from odoo import api, fields, models
from odoo.tools import float_compare


class AccountMove(models.Model):
    _inherit = 'account.move'

    expense_scan_expense_ids = fields.One2many(
        comodel_name='hr.expense',
        inverse_name='expense_scan_invoice_id',
        string="Re-invoiced expenses",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves.filtered(lambda m: m.move_type == 'out_invoice')._expense_scan_reserve_expenses()
        return moves

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted.filtered(lambda m: m.move_type == 'out_invoice'):
            # The lines may have changed since the draft was made.
            covered = move._expense_scan_reserve_expenses()
            covered.filtered(lambda e: e.state == 'approved')._expense_scan_post_after_invoice(move)
        return posted

    def button_draft(self):
        self._expense_scan_release_expenses()
        return super().button_draft()

    def button_cancel(self):
        self._expense_scan_release_expenses()
        return super().button_cancel()

    def _expense_scan_release_expenses(self):
        """An invoice that is deleted, cancelled or back to draft lets its expenses go."""
        expenses = self.env['hr.expense'].sudo().search([('expense_scan_invoice_id', 'in', self.ids)])
        if expenses:
            expenses.with_context(expense_scan_free=True, expense_scan_no_sync=True).write(
                {'expense_scan_invoice_id': False})

    def _expense_scan_reserve_expenses(self):
        """Note on each invoice the expenses its expense line covers.

        Returns them. What was reserved and is no longer covered is freed.
        """
        Expense = self.env['hr.expense'].sudo()
        if 'sale_line_ids' not in self.env['account.move.line']._fields:
            return Expense
        everything = Expense
        for move in self:
            if not move.company_id.expense_scan_reinvoice:
                continue
            covered = Expense
            for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
                sale_line = line.sale_line_ids.filtered(
                    lambda l: l.product_id.can_be_expensed and l.product_id.type == 'service')[:1]
                if sale_line:
                    covered |= move._expense_scan_expenses_of_line(line, sale_line)
            free = {'expense_scan_free': True, 'expense_scan_no_sync': True}
            (move.expense_scan_expense_ids.sudo() - covered).with_context(**free).write(
                {'expense_scan_invoice_id': False})
            (covered - move.expense_scan_expense_ids.sudo()).with_context(**free).write(
                {'expense_scan_invoice_id': move.id})
            everything |= covered
        return everything

    def _expense_scan_expenses_of_line(self, line, sale_line):
        """Oldest expenses first, as long as the amount of the line allows."""
        self.ensure_one()
        Expense = self.env['hr.expense'].sudo()
        projects = Expense._expense_scan_projects_of(sale_line.order_id)
        if not projects:
            return Expense
        candidates = Expense._expense_scan_counted(projects, self.company_id, sale_line.order_id).filtered(
            lambda e: not e.expense_scan_invoice_id or e.expense_scan_invoice_id == self
        ).sorted(lambda e: (e.date or fields.Date.today(), e.id))
        left = line.price_subtotal
        if self.currency_id != self.company_id.currency_id:
            left = self.currency_id._convert(
                left, self.company_id.currency_id, self.company_id,
                self.invoice_date or fields.Date.context_today(self))
        covered = Expense
        for expense in candidates:
            if float_compare(expense.total_amount, left + 0.005, precision_digits=2) <= 0:
                covered |= expense
                left -= expense.total_amount
        return covered
