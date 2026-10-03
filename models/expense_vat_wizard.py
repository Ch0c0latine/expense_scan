# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Wizard that corrects the recoverable VAT of posted expenses.

Opened from the expense form (one expense) or from the list (a selection). A
selection only takes "Not recoverable" and "Recoverable", each expense with
its own tax; "Other amount" is for one expense at a time. Each expense gets
its own entry: its expense account, analytic distribution and tax are its
own, and a standard reversal then cancels one correction without touching
the others.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ExpenseScanVatWizard(models.TransientModel):
    _name = 'expense.scan.vat.wizard'
    _description = "VAT correction of expenses"

    expense_ids = fields.Many2many(
        comodel_name='hr.expense',
        relation='expense_scan_vat_wizard_expense_rel',
        column1='wizard_id',
        column2='expense_id',
        string="Expenses",
        readonly=True,
    )
    expense_count = fields.Integer(compute='_compute_expense_count')
    company_id = fields.Many2one(
        comodel_name='res.company',
        compute='_compute_company_id',
        string="Company",
    )
    currency_id = fields.Many2one(related='company_id.currency_id')
    mode = fields.Selection(
        selection=[
            ('none', "Not recoverable"),
            ('recoverable', "Recoverable"),
            ('amount', "Other amount"),
        ],
        string="Correction",
        default='none',
        required=True,
    )
    # A selection cannot take "Other amount": the form shows this field instead of ``mode``.
    batch_mode = fields.Selection(
        selection=[
            ('none', "Not recoverable"),
            ('recoverable', "Recoverable"),
        ],
        string="Correction of the selection",
        default='none',
        required=True,
    )
    tax_id = fields.Many2one(
        comodel_name='account.tax',
        string="Tax",
        compute='_compute_tax_id',
        store=True,
        readonly=False,
        domain="[('type_tax_use', '=', 'purchase'), ('amount_type', '=', 'percent'),"
               " ('company_id', '=', company_id)]",
    )
    amount = fields.Monetary(
        string="New recoverable VAT",
        currency_field='currency_id',
    )
    current_vat = fields.Monetary(
        string="Recoverable VAT now",
        currency_field='currency_id',
        compute='_compute_current_vat',
    )
    new_vat = fields.Monetary(
        string="Recoverable VAT after",
        currency_field='currency_id',
        compute='_compute_new_vat',
    )
    date = fields.Date(
        string="Date of the entry",
        required=True,
        default=fields.Date.context_today,
    )
    journal_id = fields.Many2one(
        comodel_name='account.journal',
        string="Journal",
        compute='_compute_journal_id',
        store=True,
        readonly=False,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    lock_notice = fields.Char(compute='_compute_lock_notice')
    cycle_warning = fields.Text(compute='_compute_cycle_warning')
    needs_confirmation = fields.Boolean(compute='_compute_cycle_warning')
    confirmed = fields.Boolean(string="I checked with my accountant")

    @api.depends('expense_ids')
    def _compute_expense_count(self):
        for wizard in self:
            wizard.expense_count = len(wizard.expense_ids)

    @api.depends('expense_ids')
    def _compute_company_id(self):
        for wizard in self:
            wizard.company_id = wizard.expense_ids[:1].company_id or self.env.company

    @api.depends('expense_ids')
    def _compute_tax_id(self):
        for wizard in self:
            # Several expenses each have their own tax: none is proposed.
            wizard.tax_id = (wizard.expense_ids._expense_scan_vat_default_tax()
                             if wizard.expense_count == 1 else False)

    @api.depends('company_id')
    def _compute_journal_id(self):
        for wizard in self:
            wizard.journal_id = False
            if wizard.expense_ids:
                try:
                    wizard.journal_id = wizard.expense_ids[:1]._expense_scan_vat_journal()
                except UserError:
                    # The message comes when the correction is made.
                    pass

    @api.depends('expense_ids')
    def _compute_current_vat(self):
        for wizard in self:
            wizard.current_vat = sum(wizard.expense_ids.mapped('expense_scan_recoverable_vat'))

    @api.depends('expense_ids', 'mode', 'tax_id', 'amount')
    def _compute_new_vat(self):
        for wizard in self:
            wizard.new_vat = 0.0
            if wizard.expense_count != 1:
                continue
            try:
                wizard.new_vat = wizard.expense_ids._expense_scan_vat_target(
                    wizard.mode, wizard.tax_id, wizard.amount)[0]
            except UserError:
                # The message comes when the correction is made.
                pass

    @api.depends('date', 'journal_id', 'company_id')
    def _compute_lock_notice(self):
        """Odoo moves the date of an entry made in a locked period: say so beforehand."""
        for wizard in self:
            wizard.lock_notice = False
            if wizard.date and wizard.journal_id:
                move = self.env['account.move'].new({
                    'move_type': 'entry', 'journal_id': wizard.journal_id.id,
                    'company_id': wizard.company_id.id, 'date': wizard.date})
                wizard.lock_notice = move._get_lock_date_message(wizard.date, True)

    @api.depends('expense_ids')
    def _compute_cycle_warning(self):
        for wizard in self:
            closed = wizard.expense_ids.filtered(lambda expense: expense._expense_scan_vat_cycle_closed())
            wizard.needs_confirmation = bool(closed)
            if not closed:
                wizard.cycle_warning = False
            elif wizard.expense_count == 1:
                wizard.cycle_warning = _(
                    "This expense has been invoiced and reimbursed: its VAT may already have been "
                    "declared. Check with your accountant before correcting it.")
            else:
                wizard.cycle_warning = _(
                    "These expenses have been invoiced and reimbursed: their VAT may already have been "
                    "declared. Check with your accountant before correcting them.\n%(names)s",
                    names=", ".join(closed.mapped('display_name')))

    def _effective_mode(self):
        self.ensure_one()
        return self.batch_mode if self.expense_count > 1 else self.mode

    def action_confirm(self):
        self.ensure_one()
        expenses = self.expense_ids
        expenses._expense_scan_check_vat_manager()
        if self.needs_confirmation and not self.confirmed:
            raise UserError(_("Tick the box to confirm that you checked with your accountant."))
        single = self.expense_count == 1
        moves = self.env['account.move']
        for expense in expenses:
            moves |= expense._expense_scan_correct_vat(
                self._effective_mode(), tax=self.tax_id if single else None,
                amount=self.amount, date=self.date, journal=self.journal_id)
        if not moves:
            raise UserError(_("Nothing to correct: the recoverable VAT is already the one asked."))
        return self.env['hr.expense']._expense_scan_vat_moves_action(moves)
