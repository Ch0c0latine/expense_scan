# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Correcting the recoverable VAT of a posted expense.

An expense of 10.00 incl. tax at 20 % gives 1.67 of VAT to recover. After
posting, the accountant declares that it is not recoverable, that it is, or
that it is another amount: a miscellaneous entry moves the difference through
the tax engine, on the expense account and on the analytic distribution of
the expense. These scenarios need a chart of accounts: without one they skip.
"""
import unittest

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form, common, tagged

from .tax_setup import ensure_fiscal_country

PAID = ("This expense is paid: its VAT may already have been declared. "
        "Check with your accountant before correcting it.")
PAID_INVOICED = ("This expense is paid and invoiced: its VAT may already have been declared. "
                 "Check with your accountant before correcting it.")


@tagged('post_install', '-at_install')
class TestVatCorrection(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The texts asserted below are the English ones, whatever the language of the user.
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.company = cls.env.company
        if not cls.company.chart_template:
            raise unittest.SkipTest("no chart of accounts")
        ensure_fiscal_country(cls.env)
        cls.currency = cls.company.currency_id
        cls.vat_account = cls.env['account.account'].create({
            'name': "Deductible VAT (test)", 'code': 'TESTVAT445', 'account_type': 'asset_current'})
        # French charts tag the VAT of a purchase and of a refund with the same grid.
        cls.grid = cls.env['account.account.tag'].create({
            'name': "Deductible VAT grid (test)", 'applicability': 'taxes',
            'country_id': cls.company.account_fiscal_country_id.id})
        cls.tax = cls.make_tax("Test VAT 20 %", 20.0)
        cls.other_tax = cls.make_tax("Test VAT 10 %", 10.0)
        cls.category = cls.env['product.product'].create({
            'name': "Repas test", 'can_be_expensed': True, 'standard_price': 0.0,
            'supplier_taxes_id': [Command.clear()]})
        cls.employee = cls.env['hr.employee'].create({'name': "Léa Récupère"})
        # The expense officer group lets them open the expenses, as an accountant does.
        cls.manager = cls.user('vat_manager', 'account.group_account_manager',
                               'hr_expense.group_hr_expense_user')
        cls.accountant = cls.user('vat_accountant', 'account.group_account_invoice',
                                  'hr_expense.group_hr_expense_user')
        plan = cls.env['account.analytic.plan'].create({'name': "Plan VAT test"})
        cls.analytic = cls.env['account.analytic.account'].create({
            'name': "Mission VAT", 'plan_id': plan.id})

    @classmethod
    def make_tax(cls, name, rate):
        lines = [
            Command.create({'repartition_type': 'base'}),
            Command.create({'repartition_type': 'tax', 'account_id': cls.vat_account.id,
                            'tag_ids': [Command.set(cls.grid.ids)]}),
        ]
        return cls.env['account.tax'].create({
            'name': name, 'amount': rate, 'amount_type': 'percent', 'type_tax_use': 'purchase',
            'company_id': cls.company.id,
            'invoice_repartition_line_ids': lines, 'refund_repartition_line_ids': lines})

    @classmethod
    def user(cls, login, *groups):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': 'expense_scan_%s' % login,
            'group_ids': [Command.set([cls.env.ref(group).id
                                       for group in ('base.group_user',) + groups])]})

    def expense(self, total=10.0, tax=True, post=True, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': "Déjeuner", 'employee_id': self.employee.id, 'product_id': self.category.id,
            'total_amount_currency': total, 'reinvoice_mode': 'none',
            'tax_ids': [Command.set(self.tax.ids if tax else [])],
        }, **values))
        if post:
            expense.action_submit()
            expense._do_approve()
            expense._post_without_wizard()
        return expense

    def balance(self, account, moves=None):
        """Balance of an account, over some entries or over everything."""
        domain = [('account_id', '=', account.id), ('parent_state', '=', 'posted')]
        if moves is not None:
            domain.append(('move_id', 'in', moves.ids))
        return sum(self.env['account.move.line'].search(domain).mapped('balance'))

    def expense_account(self, expense):
        return expense.account_move_id.line_ids.filtered(lambda l: l.display_type == 'product').account_id

    def correct(self, expense, mode='none', **kwargs):
        move = expense.with_user(self.manager)._expense_scan_correct_vat(mode, **kwargs)
        self.assertEqual(move.state, 'posted')
        return move

    def pay(self, expense):
        """Reimburse the employee."""
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=expense.account_move_id.ids,
        ).create({})._create_payments()
        self.assertIn(expense.state, ('in_payment', 'paid'))

    def assertMoney(self, value, expected, msg=None):
        self.assertEqual(self.currency.compare_amounts(value, expected), 0,
                         msg or "%s instead of %s" % (value, expected))

    # -- The three corrections -----------------------------------------------

    def test_the_vat_of_the_entry_is_what_the_expense_recovers(self):
        expense = self.expense()
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.67)
        self.assertMoney(expense.expense_scan_original_vat, 1.67)
        self.assertFalse(expense.expense_scan_vat_changed)

    def test_not_recoverable(self):
        expense = self.expense()
        account = self.expense_account(expense)
        entry = expense.account_move_id
        move = self.correct(expense)
        self.assertEqual(move.move_type, 'entry')
        self.assertEqual(move.journal_id.type, 'general')
        self.assertEqual(move.expense_scan_vat_expense_id, expense)
        self.assertEqual(expense.expense_scan_vat_move_ids, move)
        # Only the difference moves: the VAT account is credited by what was recovered,
        # the expense account debited by it.
        self.assertMoney(self.balance(self.vat_account, move), -1.67)
        self.assertMoney(self.balance(account, move), 1.67)
        self.assertMoney(self.balance(self.vat_account, entry), 1.67)
        self.assertMoney(self.balance(account, entry), 8.33)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
        self.assertMoney(expense.expense_scan_original_vat, 1.67)
        self.assertTrue(expense.expense_scan_vat_changed)
        # The expense, its entry and what was paid to the employee do not change.
        self.assertMoney(expense.total_amount, 10.0)
        self.assertEqual(expense.account_move_id, entry)
        self.assertMoney(entry.amount_total, 10.0)
        self.assertEqual(expense.state, 'posted')

    def test_the_vat_line_goes_through_the_tax_engine(self):
        expense = self.expense()
        move = self.correct(expense)
        # The whole VAT goes: on the real base of the expense, 8.33 + 1.67 = 10.00.
        self.assertEqual(sorted(move.line_ids.filtered(lambda l: not l.tax_line_id).mapped('balance')),
                         [-8.33, 10.0])
        vat_line = move.line_ids.filtered('tax_line_id')
        self.assertEqual(len(vat_line), 1)
        self.assertEqual(vat_line.tax_line_id, self.tax)
        self.assertEqual(vat_line.account_id, self.vat_account)
        self.assertMoney(vat_line.credit, 1.67)
        # Refund repartition of the tax, with its tax grid: the return follows.
        self.assertEqual(vat_line.tax_repartition_line_id.document_type, 'refund')
        self.assertEqual(vat_line.tax_tag_ids, self.grid)

    def test_recoverable_again(self):
        expense = self.expense()
        self.correct(expense)
        move = self.correct(expense, 'recoverable')
        self.assertMoney(self.balance(self.vat_account, move), 1.67)
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.67)
        vat_line = move.line_ids.filtered('tax_line_id')
        self.assertEqual(vat_line.tax_repartition_line_id.document_type, 'invoice')
        self.assertMoney(vat_line.debit, 1.67)
        self.assertEqual(len(expense.expense_scan_vat_move_ids), 2)

    def test_recoverable_on_an_expense_without_tax(self):
        expense = self.expense(12.0, tax=False)
        account = self.expense_account(expense)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
        move = self.correct(expense, 'recoverable', tax=self.tax)
        self.assertMoney(self.balance(self.vat_account, move), 2.0)
        self.assertMoney(self.balance(account, move), -2.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 2.0)

    def test_recoverable_with_the_category_tax(self):
        """Without a tax on the expense, the category's one is proposed."""
        self.category.supplier_taxes_id = [Command.set(self.other_tax.ids)]
        expense = self.expense(11.0, tax=False)
        self.assertEqual(expense._expense_scan_vat_default_tax(), self.other_tax)
        move = self.correct(expense, 'recoverable')
        self.assertMoney(self.balance(self.vat_account, move), 1.0)

    def test_recoverable_with_the_company_tax(self):
        self.company.account_purchase_tax_id = self.other_tax
        expense = self.expense(11.0, tax=False)
        self.assertEqual(expense._expense_scan_vat_default_tax(), self.other_tax)

    def test_other_amount(self):
        expense = self.expense()
        account = self.expense_account(expense)
        move = self.correct(expense, 'amount', amount=1.0)
        self.assertMoney(self.balance(self.vat_account, move), -0.67)
        self.assertMoney(self.balance(account, move), 0.67)
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.0)
        # And up again.
        move = self.correct(expense, 'amount', amount=1.5)
        self.assertMoney(self.balance(self.vat_account, move), 0.5)
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.5)

    def test_other_amount_stays_under_the_rate_ceiling(self):
        expense = self.expense()
        with self.assertRaises(ValidationError):
            self.correct(expense, 'amount', amount=1.7)
        with self.assertRaises(ValidationError):
            self.correct(expense, 'amount', amount=-1.0)
        self.assertFalse(expense.expense_scan_vat_move_ids)

    def test_other_amount_needs_one_tax(self):
        expense = self.expense()
        expense.tax_ids = [Command.link(self.other_tax.id)]
        with self.assertRaises(UserError):
            self.correct(expense, 'amount', amount=1.0)
        expense.tax_ids = [Command.clear()]
        with self.assertRaises(UserError):
            self.correct(expense, 'amount', amount=1.0)

    def test_nothing_to_correct_makes_no_entry(self):
        expense = self.expense()
        self.assertFalse(expense.with_user(self.manager)._expense_scan_correct_vat('recoverable'))
        self.assertFalse(expense.expense_scan_vat_move_ids)
        self.correct(expense)
        self.assertFalse(expense.with_user(self.manager)._expense_scan_correct_vat('none'))
        self.assertEqual(len(expense.expense_scan_vat_move_ids), 1)

    def entry_vat(self, tax, base):
        """The VAT an entry line of ``base`` carries, rounded as the entry rounds it.

        Not ``compute_all``: it works on unrounded amounts (2.1 % of 25.00 gives
        0.52 there, 0.53 in the entry).
        """
        AccountTax = self.env['account.tax']
        line = AccountTax._prepare_base_line_for_taxes_computation(
            None, tax_ids=tax, price_unit=base, quantity=1.0, currency_id=self.currency,
            special_mode='total_excluded')
        AccountTax._add_tax_details_in_base_lines([line], self.company)
        AccountTax._round_base_lines_tax_details([line], self.company)
        return sum(data['tax_amount'] for data in line['tax_details']['taxes_data'])

    def test_the_vat_generated_is_exactly_the_one_asked(self):
        """Whatever the rate and the cents, the base is found so that the VAT line is the delta."""
        expense = self.expense()
        for rate in (2.1, 5.5, 8.5, 10.0, 19.0, 20.0, 21.0):
            tax = self.make_tax("Test rate %s" % rate, rate)
            for cents in range(1, 600, 13):
                vat = cents / 100.0
                base = expense._expense_scan_vat_base(tax, vat)
                self.assertMoney(self.entry_vat(tax, base), vat, "%s %% on %s, not %s" % (rate, base, vat))

    def test_odd_amounts_keep_the_entry_balanced(self):
        expense = self.expense(33.33)
        self.assertMoney(expense.expense_scan_recoverable_vat, 5.56)
        move = self.correct(expense, 'amount', amount=0.01)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.01)
        self.assertMoney(sum(move.line_ids.mapped('balance')), 0.0)

    # -- Analytic -----------------------------------------------------------------

    def analytic_total(self):
        lines = self.env['account.analytic.line'].search([
            (self.analytic.plan_id._column_name(), '=', self.analytic.id)])
        return sum(lines.mapped('amount'))

    def test_the_project_cost_follows_the_correction(self):
        expense = self.expense(analytic_distribution={str(self.analytic.id): 100.0})
        self.assertMoney(self.analytic_total(), -8.33)
        move = self.correct(expense)
        self.assertMoney(self.analytic_total(), -10.0, "the cost of the project rises by the VAT")
        self.assertTrue(all(
            line.analytic_distribution == {str(self.analytic.id): 100.0}
            for line in move.line_ids if not line.tax_line_id))
        self.correct(expense, 'recoverable')
        self.assertMoney(self.analytic_total(), -8.33, "and falls back with it")

    # -- Reversal -------------------------------------------------------------------

    def reverse(self, move):
        wizard = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=move.ids).create({
                'date': fields.Date.context_today(self), 'journal_id': move.journal_id.id})
        return self.env['account.move'].browse(wizard.reverse_moves()['res_id'])

    def test_a_standard_reversal_cancels_the_correction(self):
        expense = self.expense(analytic_distribution={str(self.analytic.id): 100.0})
        account = self.expense_account(expense)
        # The expense account may hold other entries: only the change counts.
        vat_before, account_before = self.balance(self.vat_account), self.balance(account)
        move = self.correct(expense)
        self.assertMoney(self.balance(account), account_before + 1.67)
        back = self.reverse(move)
        self.assertEqual(back.state, 'posted')
        self.assertEqual(back.reversed_entry_id, move)
        self.assertEqual(back.expense_scan_vat_expense_id, expense, "the link follows the reversal")
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.67)
        self.assertFalse(expense.expense_scan_vat_changed)
        self.assertMoney(self.balance(self.vat_account), vat_before)
        self.assertMoney(self.balance(account), account_before)
        self.assertMoney(self.analytic_total(), -8.33)
        self.assertFalse(expense._expense_scan_vat_open_corrections())

    def test_a_duplicate_of_a_correction_is_not_a_correction(self):
        expense = self.expense()
        move = self.correct(expense)
        self.assertFalse(move.copy().expense_scan_vat_expense_id)

    def test_the_entry_of_the_expense_stays_while_a_correction_rests_on_it(self):
        expense = self.expense()
        move = self.correct(expense)
        entry = expense.account_move_id
        with self.assertRaises(UserError):
            entry.button_draft()
        with self.assertRaises(UserError):
            entry.button_cancel()
        today = fields.Date.context_today(expense)
        with self.assertRaises(UserError):
            entry._reverse_moves([{'invoice_date': today}], cancel=True)
        # Once the correction is reversed, the entry is free again.
        self.reverse(move)
        entry._reverse_moves([{'invoice_date': today}], cancel=True)

    # -- Several expenses of one employee in a single entry ----------------------------

    def test_the_lines_of_the_other_expenses_of_the_entry_are_left_out(self):
        first = self.expense(10.0, post=False)
        second = self.expense(24.0, post=False)
        for expense in first | second:
            expense.action_submit()
            expense._do_approve()
        (first | second)._post_without_wizard()
        self.assertEqual(first.account_move_id, second.account_move_id)
        self.assertMoney(first.expense_scan_recoverable_vat, 1.67)
        self.assertMoney(second.expense_scan_recoverable_vat, 4.0)
        move = self.correct(second)
        self.assertMoney(self.balance(self.vat_account, move), -4.0)
        self.assertMoney(first.expense_scan_recoverable_vat, 1.67)
        self.assertMoney(second.expense_scan_recoverable_vat, 0.0)

    # -- Expense paid by the company -----------------------------------------------------

    def test_an_expense_paid_by_the_company(self):
        expense = self.env['hr.expense'].create({
            'name': "Taxi", 'employee_id': self.employee.id, 'product_id': self.category.id,
            'total_amount_currency': 10.0, 'reinvoice_mode': 'none', 'payment_mode': 'company_account',
            'tax_ids': [Command.set(self.tax.ids)]})
        expense.action_submit()
        expense._do_approve()
        expense.action_post()
        self.assertEqual(expense.state, 'paid')
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.67)
        move = self.correct(expense)
        self.assertMoney(self.balance(self.vat_account, move), -1.67)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)

    # -- Which expenses, who ---------------------------------------------------------------

    def test_an_expense_not_posted_is_refused(self):
        expense = self.expense(post=False)
        with self.assertRaises(UserError):
            expense.with_user(self.manager).action_expense_scan_correct_vat()
        expense.action_submit()
        expense._do_approve()
        with self.assertRaises(UserError):
            self.correct(expense)

    def test_only_accounting_managers_correct_the_vat(self):
        expense = self.expense()
        as_accountant = expense.with_user(self.accountant)
        with self.assertRaises(AccessError):
            as_accountant.action_expense_scan_correct_vat()
        with self.assertRaises(AccessError):
            as_accountant._expense_scan_correct_vat('none')
        with self.assertRaises(AccessError):
            self.env['expense.scan.vat.wizard'].with_user(self.accountant).create(
                {'expense_ids': [Command.set(expense.ids)]})
        self.assertFalse(expense.expense_scan_vat_move_ids)
        self.assertTrue(expense.with_user(self.manager).action_expense_scan_correct_vat())

    def test_the_button_is_for_accounting_managers(self):
        arch = self.env['hr.expense'].with_user(self.manager).get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertIn('action_expense_scan_correct_vat', arch)
        arch = self.env['hr.expense'].with_user(self.accountant).get_views(
            [(False, 'form')])['views']['form']['arch']
        self.assertNotIn('action_expense_scan_correct_vat', arch)

    # -- Date and lock date ---------------------------------------------------------------------

    def test_the_entry_takes_the_date_asked(self):
        expense = self.expense()
        today = fields.Date.context_today(self.env['hr.expense'])
        move = self.correct(expense, date=today)
        self.assertEqual(move.date, today)

    def test_a_locked_period_moves_the_date_and_the_wizard_says_so(self):
        expense = self.expense()
        today = fields.Date.context_today(self.env['hr.expense'])
        yesterday = fields.Date.subtract(today, days=1)
        self.company.tax_lock_date = yesterday
        wizard = self.env['expense.scan.vat.wizard'].create({
            'expense_ids': [Command.set(expense.ids)], 'date': yesterday})
        self.assertTrue(wizard.lock_notice)
        wizard.date = today
        self.assertFalse(wizard.lock_notice)
        wizard.date = yesterday
        move = wizard.action_confirm()
        move = self.env['account.move'].browse(move['res_id'])
        self.assertEqual(move.state, 'posted')
        self.assertGreater(move.date, yesterday, "Odoo dates the entry on the first open day")

    # -- Cycle finished: invoiced and reimbursed ----------------------------------------------------

    def wizard(self, expenses):
        return self.env['expense.scan.vat.wizard'].with_user(self.manager).create({
            'expense_ids': [Command.set(expenses.ids)]})

    def invoice(self, expense, state='posted'):
        partner = self.env['res.partner'].create({'name': "Client refacturé"})
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_date': fields.Date.context_today(expense),
            'invoice_line_ids': [Command.create({'name': "Frais", 'quantity': 1, 'price_unit': 10.0})]})
        if state == 'posted':
            invoice.action_post()
        expense.write({'reinvoice_mode': 'project'})
        expense.sudo().write({'expense_scan_invoice_id': invoice.id})
        return invoice

    def test_no_warning_before_the_reimbursement(self):
        expense = self.expense()
        self.assertEqual(expense.state, 'posted')
        wizard = self.wizard(expense)
        self.assertFalse(wizard.needs_confirmation)
        self.assertFalse(wizard.cycle_warning)
        self.assertTrue(wizard.action_confirm())

    def test_a_reimbursed_expense_not_re_invoiced_warns(self):
        expense = self.expense()
        self.pay(expense)
        wizard = self.wizard(expense)
        self.assertTrue(wizard.needs_confirmation)
        self.assertEqual(wizard.cycle_warning, PAID)

    def test_the_box_is_required_to_correct_after_the_cycle(self):
        expense = self.expense()
        self.pay(expense)
        wizard = self.wizard(expense)
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.assertFalse(expense.expense_scan_vat_move_ids)
        wizard.confirmed = True
        wizard.action_confirm()
        self.assertEqual(len(expense.expense_scan_vat_move_ids), 1)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)

    def test_the_cycle_is_finished_on_a_posted_customer_invoice(self):
        expense = self.expense()
        self.pay(expense)
        self.invoice(expense)
        self.assertEqual(self.wizard(expense).cycle_warning, PAID_INVOICED)

    def test_a_draft_invoice_or_none_leaves_the_cycle_open(self):
        expense = self.expense()
        self.pay(expense)
        expense.write({'reinvoice_mode': 'project'})
        self.assertFalse(self.wizard(expense).needs_confirmation, "to invoice still")
        expense = self.expense()
        self.pay(expense)
        self.invoice(expense, state='draft')
        self.assertFalse(self.wizard(expense).needs_confirmation, "the invoice is a draft")

    def test_invoiced_but_not_reimbursed_leaves_the_cycle_open(self):
        expense = self.expense()
        self.invoice(expense)
        self.assertFalse(self.wizard(expense).needs_confirmation)

    def test_an_expense_paid_by_the_company_is_already_reimbursed(self):
        expense = self.env['hr.expense'].create({
            'name': "Train", 'employee_id': self.employee.id, 'product_id': self.category.id,
            'total_amount_currency': 10.0, 'reinvoice_mode': 'none', 'payment_mode': 'company_account',
            'tax_ids': [Command.set(self.tax.ids)]})
        expense.action_submit()
        expense._do_approve()
        expense.action_post()
        self.assertEqual(self.wizard(expense).cycle_warning, PAID)

    # -- Wizard on screen, several expenses ----------------------------------------------------------------

    def test_the_wizard_form(self):
        expense = self.expense()
        form = Form(self.env['expense.scan.vat.wizard'].with_user(self.manager).with_context(
            default_expense_ids=[Command.set(expense.ids)]))
        self.assertEqual(form.tax_id, self.tax)
        self.assertMoney(form.current_vat, 1.67)
        self.assertTrue(form.journal_id)
        self.assertEqual(form.date, fields.Date.context_today(expense))
        form.mode = 'amount'
        form.amount = 1.0
        self.assertMoney(form.new_vat, 1.0)
        form.mode = 'none'
        self.assertMoney(form.new_vat, 0.0)
        wizard = form.save()
        action = wizard.action_confirm()
        self.assertEqual(action['res_model'], 'account.move')
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
        self.assertIn("Recoverable VAT", expense.message_ids[:1].body)

    def test_the_form_shows_the_corrections_when_the_vat_changed(self):
        expense = self.expense()
        self.assertEqual(expense.expense_scan_vat_move_count, 0)
        move = self.correct(expense)
        self.assertEqual(expense.expense_scan_vat_move_count, 1)
        action = expense.action_expense_scan_open_vat_moves()
        self.assertEqual(action['res_id'], move.id)

    def test_several_expenses_one_entry_each(self):
        first, second = self.expense(10.0), self.expense(20.0, analytic_distribution={
            str(self.analytic.id): 100.0})
        form = Form(self.env['expense.scan.vat.wizard'].with_user(self.manager).with_context(
            default_expense_ids=[Command.set((first | second).ids)]))
        self.assertFalse(form.tax_id, "each expense has its own tax")
        self.assertMoney(form.current_vat, 1.67 + 3.33)
        form.batch_mode = 'none'
        action = form.save().action_confirm()
        moves = self.env['account.move'].search(action['domain'])
        self.assertEqual(len(moves), 2)
        self.assertEqual(moves.expense_scan_vat_expense_id, first | second)
        self.assertMoney(first.expense_scan_recoverable_vat, 0.0)
        self.assertMoney(second.expense_scan_recoverable_vat, 0.0)
        self.assertMoney(self.analytic_total(), -20.0)
        self.assertEqual(len(first.expense_scan_vat_move_ids), 1)

    def test_several_expenses_back_to_recoverable(self):
        first, second = self.expense(10.0), self.expense(20.0)
        for expense in first | second:
            self.correct(expense)
        wizard = self.wizard(first | second)
        wizard.batch_mode = 'recoverable'
        wizard.action_confirm()
        self.assertMoney(first.expense_scan_recoverable_vat, 1.67)
        self.assertMoney(second.expense_scan_recoverable_vat, 3.33)

    def test_a_selection_skips_what_is_already_right(self):
        first, second = self.expense(10.0), self.expense(20.0)
        self.correct(first)
        wizard = self.wizard(first | second)
        action = wizard.action_confirm()
        self.assertEqual(self.env['account.move'].browse(action['res_id']).expense_scan_vat_expense_id, second)
        wizard = self.wizard(first | second)
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_other_amount_is_for_one_expense(self):
        first, second = self.expense(10.0), self.expense(20.0)
        selection = self.env['expense.scan.vat.wizard'].fields_get(['batch_mode'])['batch_mode']['selection']
        self.assertNotIn('amount', dict(selection))
        wizard = self.wizard(first | second)
        self.assertEqual(wizard._effective_mode(), 'none')
        wizard.mode = 'amount'
        self.assertEqual(wizard._effective_mode(), 'none', "the selection never takes it")
