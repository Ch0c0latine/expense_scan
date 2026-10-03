# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""VAT of a posted expense, and its correction.

The VAT an expense gives back is what its journal entry says: the balance of
its purchase tax lines. Once the expense is posted, the accountant can find
that the VAT is not recoverable after all (or is, or is another amount). The
expense and its entry stay as they are, since the employee has been
reimbursed and the customer invoiced; a miscellaneous entry moves the
difference. It goes through the tax engine, so that the tax return follows.

For a VAT that stops being recoverable by ``delta``, on an expense account::

    A  expense account  credit  base   (carries the tax: Odoo adds the VAT
                                        line, credit ``delta``, on the
                                        refund repartition of the tax)
    B  expense account  debit   base + delta

The expense account, and with it the analytic distribution, ends up debited
by ``delta`` and the VAT account credited by ``delta``. Making VAT
recoverable swaps debit and credit.
"""
from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import formatLang
from odoo.tools.misc import clean_context

from .hr_expense import TAX_ROUNDING_MARGIN

#: Group that corrects the VAT of an expense.
VAT_GROUP = 'account.group_account_manager'
#: States of an expense whose entry is posted.
POSTED_STATES = ('posted', 'in_payment', 'paid')
#: Bases tried around the first guess when looking for the base that gives the VAT wanted.
BASE_SEARCH = 1000


class AccountMove(models.Model):
    _inherit = 'account.move'

    expense_scan_vat_expense_id = fields.Many2one(
        comodel_name='hr.expense',
        string="Expense of the VAT correction",
        readonly=True,
        copy=False,
        index='btree_not_null',
        ondelete='set null',
        help="Expense whose recoverable VAT this entry corrects. A reversal of "
             "the entry keeps the link, so that the correction is cancelled.",
    )

    def _reverse_moves(self, default_values_list=None, cancel=False):
        self._expense_scan_check_vat_corrections()
        default_values_list = default_values_list or [{} for _move in self]
        for move, values in zip(self, default_values_list):
            if move.expense_scan_vat_expense_id:
                values.setdefault('expense_scan_vat_expense_id', move.expense_scan_vat_expense_id.id)
        return super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)

    def button_draft(self):
        self._expense_scan_check_vat_corrections()
        return super().button_draft()

    def button_cancel(self):
        self._expense_scan_check_vat_corrections()
        return super().button_cancel()

    def _expense_scan_check_vat_corrections(self):
        """The entry of an expense stays while a VAT correction rests on it.

        The correction moves what the entry booked: undoing the entry alone
        would take the VAT back twice.
        """
        for expense in self.sudo().expense_ids:
            corrections = expense._expense_scan_vat_open_corrections()
            if corrections:
                raise UserError(_(
                    "%(expense)s has a VAT correction (%(entry)s). Reverse the correction first.",
                    expense=expense.name or expense.display_name,
                    entry=corrections[0].name))


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_vat_move_ids = fields.One2many(
        comodel_name='account.move',
        inverse_name='expense_scan_vat_expense_id',
        string="VAT corrections",
        readonly=True,
    )
    expense_scan_vat_move_count = fields.Integer(
        string="Number of VAT corrections",
        compute='_compute_expense_scan_vat_move_count',
        compute_sudo=True,
    )
    expense_scan_original_vat = fields.Monetary(
        string="VAT of the entry",
        currency_field='company_currency_id',
        compute='_compute_expense_scan_vat',
        compute_sudo=True,
        help="VAT recovered by the journal entry of the expense, before any correction.",
    )
    expense_scan_recoverable_vat = fields.Monetary(
        string="Recoverable VAT",
        currency_field='company_currency_id',
        compute='_compute_expense_scan_vat',
        compute_sudo=True,
        help="VAT the accounting recovers on this expense: the purchase tax lines "
             "of its journal entry, plus its posted corrections.",
    )
    expense_scan_vat_changed = fields.Boolean(
        string="VAT corrected",
        compute='_compute_expense_scan_vat',
        compute_sudo=True,
    )

    # ------------------------------------------------------------------
    # What the accounting recovers
    # ------------------------------------------------------------------

    @api.depends('expense_scan_vat_move_ids')
    def _compute_expense_scan_vat_move_count(self):
        for expense in self:
            expense.expense_scan_vat_move_count = len(expense.expense_scan_vat_move_ids)

    @api.depends('account_move_id.state', 'account_move_id.line_ids.balance',
                 'account_move_id.line_ids.tax_line_id', 'expense_scan_vat_move_ids.state',
                 'expense_scan_vat_move_ids.line_ids.balance')
    def _compute_expense_scan_vat(self):
        for expense in self:
            original = sum(expense._expense_scan_vat_by_tax(corrections=False).values())
            recoverable = sum(expense._expense_scan_vat_by_tax().values())
            currency = expense.company_currency_id
            expense.expense_scan_original_vat = original
            expense.expense_scan_recoverable_vat = recoverable
            expense.expense_scan_vat_changed = not currency.is_zero(recoverable - original)

    def _expense_scan_vat_by_tax(self, corrections=True):
        """Balance of the purchase tax lines of the expense, per tax: a debit is VAT recovered.

        The lines of its journal entry (one entry can hold several expenses:
        only the lines of this one), then those of its posted corrections.
        """
        self.ensure_one()
        # The wizard hands over virtual copies of the expenses: the figures are the original's.
        expense = self.sudo()._origin
        lines = expense.env['account.move.line']
        entry = expense.account_move_id
        if entry.state == 'posted':
            lines |= entry.line_ids.filtered(
                lambda line: line.tax_line_id and (len(entry.expense_ids) < 2 or line.expense_id == expense))
        if corrections:
            lines |= expense.expense_scan_vat_move_ids.filtered(
                lambda move: move.state == 'posted').line_ids.filtered('tax_line_id')
        balances = defaultdict(float)
        for line in lines.filtered(lambda line: line.tax_line_id.type_tax_use == 'purchase'):
            balances[line.tax_line_id] += line.balance
        currency = expense.company_currency_id
        return {tax: balance for tax, balance in balances.items() if not currency.is_zero(balance)}

    def _expense_scan_vat_open_corrections(self):
        """Posted corrections that no posted reversal has cancelled."""
        self.ensure_one()
        return self.sudo().expense_scan_vat_move_ids.filtered(
            lambda move: move.state == 'posted' and not move.reversed_entry_id
            and not move.reversal_move_ids.filtered(lambda back: back.state == 'posted'))

    def action_expense_scan_open_vat_moves(self):
        self.ensure_one()
        return self._expense_scan_vat_moves_action(self.sudo().expense_scan_vat_move_ids)

    @api.model
    def _expense_scan_vat_moves_action(self, moves):
        """Open the entry, or the list of the entries."""
        action = {
            'type': 'ir.actions.act_window',
            'name': _("VAT corrections"),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', moves.ids)],
        }
        if len(moves) == 1:
            action.update(view_mode='form', views=[(False, 'form')], res_id=moves.id)
        return action

    # ------------------------------------------------------------------
    # Opening the correction
    # ------------------------------------------------------------------

    def _expense_scan_check_vat_manager(self):
        if not self.env.su and not self.env.user.has_group(VAT_GROUP):
            raise AccessError(_("Only accounting managers can correct the VAT of an expense."))

    def _expense_scan_check_correctable(self):
        """The VAT of an expense is corrected once its entry is posted."""
        for expense in self.sudo():
            name = expense.name or expense.display_name
            if expense.state not in POSTED_STATES:
                raise UserError(_("%s is not posted: its VAT cannot be corrected yet.", name))
            if expense.account_move_id.state != 'posted':
                raise UserError(_("The journal entry of %s is not posted.", name))
        if len(self.company_id) > 1:
            raise UserError(_("Correct the VAT of the expenses of one company at a time."))

    def action_expense_scan_correct_vat(self):
        """The VAT correction wizard, for one expense or for a selection."""
        self._expense_scan_check_vat_manager()
        self._expense_scan_check_correctable()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Correct VAT"),
            'res_model': 'expense.scan.vat.wizard',
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
            'context': {'default_expense_ids': [Command.set(self.ids)]},
        }

    def _expense_scan_vat_cycle_closed(self):
        """Reimbursed to the employee, and invoiced to the customer when it is re-invoiced.

        The VAT of such an expense may have been declared long ago.
        """
        self.ensure_one()
        expense = self.sudo()._origin
        if expense.payment_mode != 'company_account' and expense.state not in ('in_payment', 'paid'):
            return False
        if expense.reinvoice_mode != 'project':
            return True
        return expense.expense_scan_invoice_id.state == 'posted'

    # ------------------------------------------------------------------
    # What to correct to
    # ------------------------------------------------------------------

    def _expense_scan_vat_default_tax(self):
        """Purchase tax for the VAT: the expense's own, its category's, the company's.

        Only a single percentage tax makes a rate to apply.
        """
        self.ensure_one()
        expense = self.sudo()._origin
        company = expense.company_id

        def usable(taxes):
            taxes = taxes.filtered(
                lambda tax: tax.type_tax_use == 'purchase' and tax.amount_type == 'percent'
                and tax.company_id == company)
            return taxes if len(taxes) == 1 else taxes.browse()

        return (usable(expense.tax_ids) or usable(expense.product_id.supplier_taxes_id)
                or usable(company.account_purchase_tax_id))

    def _expense_scan_vat_check_tax(self, tax):
        self.ensure_one()
        if tax.amount_type != 'percent' or tax.type_tax_use != 'purchase':
            raise UserError(_(
                "%s is not a purchase tax with a percentage: its VAT cannot be corrected here.",
                tax.display_name))
        if tax.tax_exigibility != 'on_invoice':
            raise UserError(_(
                "%s is due on payment: its VAT cannot be corrected here.", tax.display_name))
        if tax.company_id != self.company_id:
            raise UserError(_("%s belongs to another company.", tax.display_name))

    def _expense_scan_vat_target(self, mode, tax=None, amount=0.0):
        """Recoverable VAT the correction aims at, and the tax it goes through.

        ``mode``: ``none`` (nothing is recoverable), ``recoverable`` (the VAT
        the tax gives on the total) or ``amount`` (the amount entered, on the
        single tax of the expense).
        """
        self.ensure_one()
        expense = self.sudo()._origin
        currency = expense.company_currency_id
        name = expense.name or expense.display_name
        if mode == 'none':
            return 0.0, expense.env['account.tax']
        if mode == 'amount':
            tax = expense.tax_ids
            if len(tax) != 1:
                raise UserError(_(
                    "%(expense)s carries %(count)s taxes. Another amount needs exactly one: "
                    "use \"Recoverable\" or \"Not recoverable\" instead.",
                    expense=name, count=len(tax)))
        else:
            tax = tax or expense._expense_scan_vat_default_tax()
            if not tax:
                raise UserError(_("No purchase tax found for %s: choose one.", name))
        expense._expense_scan_vat_check_tax(tax)
        total = expense.total_amount
        ceiling = currency.round(total - total / (1.0 + tax.amount / 100.0))
        if mode == 'recoverable':
            return ceiling, tax
        if amount < 0 or currency.compare_amounts(amount, ceiling + TAX_ROUNDING_MARGIN) > 0:
            raise ValidationError(_(
                "The VAT of %(expense)s is between 0 and %(ceiling)s, the VAT that the rate "
                "of %(rate)s %% gives on %(total)s.",
                expense=name, ceiling=expense._expense_scan_company_money(ceiling),
                rate=expense._expense_scan_rate_text(tax.amount),
                total=expense._expense_scan_company_money(total)))
        return currency.round(amount), tax

    def _expense_scan_vat_deltas(self, mode, tax=None, amount=0.0):
        """What to move, as ``[(tax, delta)]``: a positive delta makes VAT recoverable."""
        self.ensure_one()
        expense = self.sudo()
        currency = expense.company_currency_id
        current = expense._expense_scan_vat_by_tax()
        if mode == 'none':
            for tax in current:
                expense._expense_scan_vat_check_tax(tax)
            return [(tax, -balance) for tax, balance in current.items()]
        target, tax = expense._expense_scan_vat_target(mode, tax, amount)
        delta = currency.round(target - sum(current.values()))
        return [] if currency.is_zero(delta) else [(tax, delta)]

    # ------------------------------------------------------------------
    # The entry
    # ------------------------------------------------------------------

    def _expense_scan_vat_journal(self):
        """Miscellaneous journal of the company, not the ones Odoo keeps for itself."""
        self.ensure_one()
        company = self.company_id
        journals = self.env['account.journal'].sudo().search([
            ('company_id', '=', company.id), ('type', '=', 'general')])
        journal = (journals - company.tax_cash_basis_journal_id - company.currency_exchange_journal_id)[:1]
        if not journal:
            raise UserError(_("No miscellaneous journal found for %s: create one first.", company.name))
        return journal

    def _expense_scan_vat_base(self, tax, vat):
        """The base on which the tax gives exactly ``vat``.

        Starting from the share of the real base of the expense that carries
        this VAT (the whole base when all the VAT is corrected). The rate
        applied to it can miss by a cent once rounded: the nearest base that
        hits the amount is taken.
        """
        self.ensure_one()
        currency = self.company_currency_id
        step = currency.rounding
        total = self.total_amount
        base = total / (1.0 + tax.amount / 100.0)
        # The VAT of the whole expense, as it is booked (to the cent).
        whole = currency.round(total - base)
        first = currency.round(base * vat / whole) if whole > 0 else currency.round(vat * 100.0 / tax.amount)
        for gap in range(BASE_SEARCH):
            for base in (first + gap * step, first - gap * step):
                if base <= 0:
                    continue
                taxes = tax.compute_all(base, currency=currency, quantity=1.0, handle_price_include=False)['taxes']
                if currency.compare_amounts(sum(t['amount'] for t in taxes), vat) == 0:
                    return currency.round(base)
        raise UserError(_(
            "The tax %(tax)s cannot give a VAT of %(amount)s.",
            tax=tax.display_name, amount=self._expense_scan_company_money(vat)))

    def _expense_scan_vat_account(self):
        """Expense account and analytic distribution the entry of the expense booked its cost on."""
        self.ensure_one()
        expense = self.sudo()
        entry = expense.account_move_id
        lines = entry.line_ids.filtered(
            lambda line: line.display_type == 'product' and not line.tax_line_id
            and (len(entry.expense_ids) < 2 or line.expense_id == expense))
        if lines:
            return lines[0].account_id, lines[0].analytic_distribution or expense.analytic_distribution or False
        return expense._get_base_account(), expense.analytic_distribution or False

    def _expense_scan_vat_move_values(self, deltas, date, journal):
        self.ensure_one()
        expense = self.sudo()
        account, analytic = expense._expense_scan_vat_account()
        label = _("VAT correction: %s", expense._get_move_line_name())
        lines = []
        for tax, delta in deltas:
            vat = abs(delta)
            base = expense._expense_scan_vat_base(tax, vat)
            recover = delta > 0
            lines.append(Command.create({
                'name': label,
                'account_id': account.id,
                'debit': base if recover else 0.0,
                'credit': 0.0 if recover else base,
                'tax_ids': [Command.set(tax.ids)],
                'analytic_distribution': analytic,
            }))
            lines.append(Command.create({
                'name': label,
                'account_id': account.id,
                'debit': 0.0 if recover else base + vat,
                'credit': base + vat if recover else 0.0,
                'analytic_distribution': analytic,
            }))
        return {
            'move_type': 'entry',
            'journal_id': (journal or expense._expense_scan_vat_journal()).id,
            'date': date or fields.Date.context_today(self),
            'ref': _("VAT correction: %s", expense.name or expense.display_name),
            'company_id': expense.company_id.id,
            'expense_scan_vat_expense_id': expense.id,
            'line_ids': lines,
        }

    def _expense_scan_correct_vat(self, mode, tax=None, amount=0.0, date=None, journal=None):
        """Correct the recoverable VAT of the expense; the entry made, or none when nothing changes."""
        self.ensure_one()
        # The wizard's context carries ``default_expense_ids``: the entry must not take it for its own.
        self = self.with_context(clean_context(self.env.context))  # noqa: PLW0642
        self._expense_scan_check_vat_manager()
        self._expense_scan_check_correctable()
        expense = self.sudo()
        before = sum(expense._expense_scan_vat_by_tax().values())
        deltas = expense._expense_scan_vat_deltas(mode, tax, amount)
        if not deltas:
            return self.env['account.move']
        values = expense._expense_scan_vat_move_values(deltas, date, journal)
        move = self.env['account.move'].with_company(expense.company_id).create(values)
        # The tax engine adds the VAT lines: they must come to what was asked.
        generated = sum(move.line_ids.filtered('tax_line_id').mapped('balance'))
        if not expense.company_currency_id.is_zero(generated - sum(delta for _tax, delta in deltas)):
            raise UserError(_(
                "The VAT generated for %s is not the one asked: the correction was not made.",
                expense.name or expense.display_name))
        move.action_post()
        expense.invalidate_recordset(['expense_scan_vat_move_ids'])
        after = sum(expense._expense_scan_vat_by_tax().values())
        expense.message_post(body=_(
            "Recoverable VAT: %(old)s → %(new)s (%(entry)s)",
            old=expense._expense_scan_company_money(before),
            new=expense._expense_scan_company_money(after),
            entry=move._get_html_link()))
        return move

    def _expense_scan_company_money(self, amount):
        """An amount in the company currency, with its symbol."""
        return formatLang(self.env, amount, currency_obj=self.company_currency_id or None)
