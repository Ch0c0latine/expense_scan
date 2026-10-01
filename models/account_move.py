# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Customer invoices that carry re-invoiced expenses.

When an invoice is posted, the expenses it covers are noted on it and posted
in turn, unless they were posted by hand before (to repay the employee
without waiting for the invoice).
"""
import logging

from odoo import _, fields, models
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    expense_scan_expense_ids = fields.One2many(
        comodel_name='hr.expense',
        inverse_name='expense_scan_invoice_id',
        string="Re-invoiced expenses",
        readonly=True,
    )

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted.filtered(lambda m: m.move_type == 'out_invoice')._expense_scan_settle_expenses()
        return posted

    def button_draft(self):
        self._expense_scan_release_expenses()
        return super().button_draft()

    def button_cancel(self):
        self._expense_scan_release_expenses()
        return super().button_cancel()

    def _expense_scan_release_expenses(self):
        """An invoice that is no longer posted no longer carries its expenses."""
        expenses = self.env['hr.expense'].sudo().search([('expense_scan_invoice_id', 'in', self.ids)])
        if expenses:
            expenses.with_context(expense_scan_no_sync=True).write({'expense_scan_invoice_id': False})

    def _expense_scan_settle_expenses(self):
        """Note the expenses covered by each invoice, then post them."""
        if 'sale_line_ids' not in self.env['account.move.line']._fields:
            return
        Expense = self.env['hr.expense'].sudo()
        for move in self:
            if not move.company_id.expense_scan_reinvoice:
                continue
            covered = Expense
            for line in move.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
                sale_line = line.sale_line_ids.filtered(
                    lambda l: l.product_id.can_be_expensed and l.product_id.type == 'service')[:1]
                if not sale_line:
                    continue
                covered |= move._expense_scan_expenses_of_line(line, sale_line)
            if covered:
                covered.with_context(expense_scan_no_sync=True).write({'expense_scan_invoice_id': move.id})
                covered.filtered(lambda e: e.state == 'approved')._expense_scan_post_after_invoice(move)

    def _expense_scan_expenses_of_line(self, line, sale_line):
        """Oldest expenses first, as long as the amount of the line allows."""
        self.ensure_one()
        Expense = self.env['hr.expense'].sudo()
        projects = Expense._expense_scan_projects_of(sale_line.order_id)
        if not projects:
            return Expense
        candidates = Expense._expense_scan_counted(projects, self.company_id).filtered(
            lambda e: not e.expense_scan_invoice_id).sorted(lambda e: (e.date or fields.Date.today(), e.id))
        left = line.price_subtotal
        if self.currency_id != self.company_id.currency_id:
            left = self.currency_id._convert(
                left, self.company_id.currency_id, self.company_id,
                self.invoice_date or fields.Date.context_today(self))
        covered = Expense
        for expense in candidates:
            if float_compare(expense.untaxed_amount, left + 0.005, precision_digits=2) <= 0:
                covered |= expense
                left -= expense.untaxed_amount
        return covered

