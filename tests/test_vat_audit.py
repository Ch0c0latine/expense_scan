# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Correcting the VAT of a posted expense: French chart, edge cases, access.

The first class loads the French chart of accounts in a company of its own
(``l10n_fr_account`` must be installed) and checks the accounts and the tax
grids of the return (CA3) that the three corrections reach. The second one
works on any chart with taxes of its own, like ``test_vat_correction``.
"""
import unittest

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import common, tagged


class VatAuditCase(common.TransactionCase):
    """Expenses, users and assertions shared by the classes below."""

    @classmethod
    def setup_users(cls):
        cls.category = cls.env['product.product'].create({
            'name': "Audit category", 'can_be_expensed': True, 'standard_price': 0.0,
            'supplier_taxes_id': [Command.clear()]})
        cls.employee = cls.env['hr.employee'].create({'name': "Paul Audit", 'company_id': cls.company.id})
        cls.manager = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': "VAT audit manager", 'login': 'expense_scan_vat_audit_%s' % cls.company.id,
            'company_id': cls.company.id, 'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set([cls.env.ref(group).id for group in (
                'base.group_user', 'account.group_account_manager', 'hr_expense.group_hr_expense_user')])]})

    def expense(self, tax, total=120.0, post=True, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': "Audit", 'employee_id': self.employee.id, 'product_id': self.category.id,
            'total_amount_currency': total, 'reinvoice_mode': 'none', 'company_id': self.company.id,
            'tax_ids': [Command.set(tax.ids)],
        }, **values))
        if post:
            self.post(expense)
        return expense

    def post(self, expense):
        expense.action_submit()
        expense._do_approve()
        if expense.payment_mode == 'company_account':
            expense.action_post()
        else:
            expense._post_without_wizard()
        return expense

    def pay(self, expense):
        """Reimburse the employee."""
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=expense.account_move_id.ids,
        ).create({})._create_payments()
        self.assertIn(expense.state, ('in_payment', 'paid'))

    def correct(self, expense, mode='none', **kwargs):
        """Correct as an accounting manager; the entry moves on the VAT accounts what the expense says."""
        before = expense.expense_scan_recoverable_vat
        move = expense.with_user(self.manager)._expense_scan_correct_vat(mode, **kwargs)
        self.assertEqual(move.state, 'posted')
        self.assertMoney(sum(move.line_ids.mapped('balance')), 0.0)
        moved = sum(self.vat_lines(move).filtered(
            lambda line: line.tax_repartition_line_id.use_in_tax_closing).mapped('balance'))
        self.assertMoney(before + moved, expense.expense_scan_recoverable_vat)
        return move

    def vat_lines(self, moves):
        return moves.line_ids.filtered('tax_line_id')

    def expense_account(self, expense):
        return expense.account_move_id.line_ids.filtered(lambda line: line.display_type == 'product').account_id

    def assertMoney(self, value, expected, msg=None):
        self.assertEqual(self.company.currency_id.compare_amounts(value, expected), 0,
                         msg or "%s instead of %s" % (value, expected))


@tagged('post_install', '-at_install')
class TestVatAuditFrench(VatAuditCase):
    """The three corrections on the taxes of the French chart, and the grids of the CA3.

    The purchase taxes of ``l10n_fr`` carry grid 20 (other goods and services)
    or 19 (fixed assets) on their invoice and refund repartitions alike; the
    return reads the balance of the lines: a credit lowers the deductible VAT.
    The bases carry no grid.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.env['ir.module.module']._get('l10n_fr_account').state != 'installed':
            raise unittest.SkipTest("the French chart of accounts is not installed")
        euro = cls.env.ref('base.EUR')
        euro.active = True
        cls.company = cls.env['res.company'].create({
            'name': "Conseil TVA (audit)", 'country_id': cls.env.ref('base.fr').id, 'currency_id': euro.id})
        cls.env.user.company_ids |= cls.company
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US', allowed_company_ids=cls.company.ids))
        cls.env['account.chart.template'].try_loading('fr', company=cls.company, install_demo=False)
        cls.setup_users()

    def tax(self, xmlid):
        return self.env.ref('account.%s_%s' % (self.company.id, xmlid))

    def grid(self, moves, name):
        """What the lines of the entries bring to a grid of the return (the balance of its lines)."""
        return sum(line.balance for line in moves.line_ids if name in line.tax_tag_ids.mapped('name'))

    def accounts(self, moves):
        result = {}
        for line in moves.line_ids:
            result[line.account_id.code] = round(result.get(line.account_id.code, 0.0) + line.balance, 2)
        return {code: balance for code, balance in result.items() if balance}

    def test_the_three_corrections_reach_line_20(self):
        for xmlid, total, vat in (('tva_acq_normale', 120.0, 20.0), ('tva_acq_intermediaire', 110.0, 10.0),
                                  ('tva_acq_reduite', 105.5, 5.5), ('tva_acq_super_reduite', 102.1, 2.1),
                                  ('tva_acq_normale_TTC', 120.0, 20.0)):
            tax = self.tax(xmlid)
            expense = self.expense(tax, total)
            entry = expense.account_move_id
            account = self.expense_account(expense).code
            self.assertMoney(self.grid(entry, '20'), vat)
            # Not recoverable: 445660 credited, the expense account debited, line 20 lowered.
            move = self.correct(expense)
            self.assertEqual(self.accounts(move), {'445660': -vat, account: vat}, xmlid)
            self.assertMoney(self.grid(move, '20'), -vat)
            self.assertEqual(self.vat_lines(move).tax_repartition_line_id.document_type, 'refund')
            self.assertFalse(move.line_ids.filtered(lambda line: not line.tax_line_id).tax_tag_ids,
                             "the bases carry no grid")
            # Recoverable: the other way round.
            move = self.correct(expense, 'recoverable')
            self.assertEqual(self.accounts(move), {'445660': vat, account: -vat}, xmlid)
            self.assertMoney(self.grid(move, '20'), vat)
            self.assertEqual(self.vat_lines(move).tax_repartition_line_id.document_type, 'invoice')
            # Another amount: only the difference, on the same line.
            move = self.correct(expense, 'amount', amount=1.0)
            self.assertMoney(self.grid(move, '20'), 1.0 - vat)
            self.assertMoney(self.grid(entry | expense.expense_scan_vat_move_ids, '20'), 1.0)
            self.assertMoney(expense.expense_scan_recoverable_vat, 1.0)

    def test_the_vat_on_fixed_assets_reaches_line_19(self):
        expense = self.expense(self.tax('tva_imm_normale'))
        move = self.correct(expense)
        self.assertMoney(self.accounts(move)['445620'], -20.0)
        self.assertMoney(self.grid(move, '19'), -20.0)
        self.assertMoney(self.grid(move, '20'), 0.0)

    def test_a_reversal_gives_line_20_back(self):
        expense = self.expense(self.tax('tva_acq_normale'))
        move = self.correct(expense)
        reversal = move._reverse_moves(cancel=True)
        self.assertMoney(self.grid(reversal, '20'), 20.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 20.0)

    def test_fuel_recovers_80_percent_of_its_vat(self):
        """20 % F: 80 % of the VAT on 445660, the rest on the expense account."""
        expense = self.expense(self.tax('tva_purchase_good_fuel'))
        self.assertMoney(expense.expense_scan_recoverable_vat, 16.0)
        self.assertMoney(self.grid(expense.account_move_id, '20'), 16.0)
        move = self.correct(expense)
        self.assertMoney(self.accounts(move)['445660'], -16.0)
        self.assertMoney(self.grid(move, '20'), -16.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
        move = self.correct(expense, 'amount', amount=5.0)
        self.assertMoney(self.accounts(move)['445660'], 5.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 5.0)
        self.assertMoney(self.grid(expense.account_move_id | expense.expense_scan_vat_move_ids, '20'), 5.0)
        self.correct(expense, 'recoverable')
        self.assertMoney(expense.expense_scan_recoverable_vat, 16.0)

    def test_a_tax_due_on_payment_is_corrected_once_paid(self):
        """20 % S: the VAT waits on 445640 until the reimbursement, then goes to 445660."""
        tax = self.tax('tva_acq_encaissement')
        expense = self.expense(tax)
        self.assertMoney(self.grid(expense.account_move_id, '20'), 0.0)
        with self.assertRaises(UserError):
            self.correct(expense)
        self.pay(expense)
        caba = self.env['account.move'].search([('tax_cash_basis_origin_move_id', '=', expense.account_move_id.id)])
        self.assertMoney(self.grid(caba, '20'), 20.0)
        move = self.correct(expense)
        # The correction is due at once: 445660 and line 20, not the waiting account.
        self.assertEqual(self.accounts(move), {'445660': -20.0, self.expense_account(expense).code: 20.0})
        self.assertMoney(self.grid(move, '20'), -20.0)
        self.assertMoney(self.grid(expense.account_move_id | caba | move, '20'), 0.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
        move = self.correct(expense, 'recoverable', tax=tax)
        self.assertMoney(self.grid(move, '20'), 20.0)

    def test_the_restaurant_rate_on_any_total(self):
        """10 % of a base ending in a half cent: the base found is the one the entry rounds the same way."""
        tax = self.tax('tva_acq_intermediaire')
        for total in (0.16, 0.49, 1.04, 1.92, 12.43, 99.98):
            expense = self.expense(tax, total)
            vat = expense.expense_scan_recoverable_vat
            move = self.correct(expense)
            self.assertMoney(self.grid(move, '20'), -vat, "on %s" % total)
            self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)


@tagged('post_install', '-at_install')
class TestVatAudit(VatAuditCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.company = cls.env.company
        if not cls.company.chart_template:
            raise unittest.SkipTest("no chart of accounts")
        cls.vat_account = cls.env['account.account'].create({
            'name': "Deductible VAT (audit)", 'code': 'AUDVAT445', 'account_type': 'asset_current'})
        cls.grid = cls.env['account.account.tag'].create({
            'name': "Deductible VAT grid (audit)", 'applicability': 'taxes',
            'country_id': cls.company.account_fiscal_country_id.id})
        cls.tax20 = cls.make_tax("Audit 20 %", 20.0)
        cls.tax10 = cls.make_tax("Audit 10 %", 10.0)
        cls.tax55 = cls.make_tax("Audit 5.5 %", 5.5)
        cls.tax21 = cls.make_tax("Audit 2.1 %", 2.1)
        cls.setup_users()

    @classmethod
    def make_tax(cls, name, rate, deductible=100.0, **values):
        lines = [
            Command.create({'repartition_type': 'base'}),
            Command.create({'repartition_type': 'tax', 'factor_percent': deductible,
                            'account_id': cls.vat_account.id, 'tag_ids': [Command.set(cls.grid.ids)]}),
        ]
        if deductible < 100.0:
            # The rest has no account: it falls on the expense account.
            lines.append(Command.create({'repartition_type': 'tax', 'factor_percent': 100.0 - deductible}))
        return cls.env['account.tax'].create(dict({
            'name': name, 'amount': rate, 'amount_type': 'percent', 'type_tax_use': 'purchase',
            'company_id': cls.company.id,
            'invoice_repartition_line_ids': lines, 'refund_repartition_line_ids': lines}, **values))

    def balance(self, moves):
        return sum(moves.line_ids.filtered(lambda line: line.account_id == self.vat_account).mapped('balance'))

    # -- Rounding: the base is checked as the entry computes it ----------------------------------

    def test_a_base_ending_in_a_half_cent(self):
        """10 % of 1.75 is 0.175: rounded to 0.18 by the entry, the correction must not miss it."""
        for tax, total in ((self.tax10, 1.92), (self.tax10, 0.16), (self.tax10, 0.49),
                           (self.tax55, 3.16), (self.tax21, 5.10)):
            expense = self.expense(tax, total)
            vat = expense.expense_scan_recoverable_vat
            move = self.correct(expense)
            self.assertMoney(self.balance(move), -vat, "%s on %s" % (tax.name, total))
            self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
            move = self.correct(expense, 'recoverable')
            self.assertMoney(self.balance(move), vat)

    def test_high_totals(self):
        for tax, total in ((self.tax20, 1234567.89), (self.tax10, 987654.33), (self.tax55, 45678.91),
                           (self.tax21, 100000.07)):
            expense = self.expense(tax, total)
            vat = expense.expense_scan_recoverable_vat
            self.correct(expense, 'amount', amount=round(vat / 3, 2))
            self.correct(expense)
            self.correct(expense, 'recoverable')
            self.assertMoney(expense.expense_scan_recoverable_vat, vat)

    # -- A receipt with its own VAT ----------------------------------------------------------------

    def receipt(self, total, vat):
        expense = self.expense(self.tax20, total, post=False)
        expense.scan_tax_amount = vat
        return self.post(expense)

    def test_recoverable_gives_back_the_vat_of_the_receipt(self):
        """1.00 of VAT printed on 20.00 (mixed rates): "Recoverable" is 1.00, not the 3.33 of the rate."""
        expense = self.receipt(20.0, 1.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.0)
        self.assertFalse(expense.with_user(self.manager)._expense_scan_correct_vat('recoverable'),
                         "nothing to deduct beyond the receipt")
        self.correct(expense)
        wizard = self.env['expense.scan.vat.wizard'].with_user(self.manager).create({
            'expense_ids': [Command.set(expense.ids)], 'mode': 'recoverable'})
        self.assertMoney(wizard.new_vat, 1.0)
        move = self.correct(expense, 'recoverable')
        self.assertMoney(self.balance(move), 1.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 1.0)

    def test_another_amount_may_still_reach_the_rate(self):
        expense = self.receipt(20.0, 1.0)
        self.correct(expense, 'amount', amount=3.33)
        self.assertMoney(expense.expense_scan_recoverable_vat, 3.33)

    # -- A share of the VAT that is a cost -----------------------------------------------------------

    def test_the_share_left_on_the_expense_account_is_not_recovered(self):
        """80 % deductible: 16.00 of the 20.00 of VAT is recovered."""
        tax = self.make_tax("Audit fuel", 20.0, deductible=80.0)
        expense = self.expense(tax)
        self.assertMoney(self.balance(expense.account_move_id), 16.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 16.0)
        wizard = self.env['expense.scan.vat.wizard'].with_user(self.manager).create({
            'expense_ids': [Command.set(expense.ids)]})
        self.assertMoney(wizard.current_vat, 16.0)
        move = self.correct(expense, 'amount', amount=10.0)
        self.assertMoney(self.balance(move), -6.0)
        self.assertMoney(expense.expense_scan_recoverable_vat, 10.0)
        move = self.correct(expense)
        self.assertMoney(self.balance(move), -10.0)
        move = self.correct(expense, 'recoverable')
        self.assertMoney(self.balance(move), 16.0)
        self.assertMoney(self.balance(expense.account_move_id | expense.expense_scan_vat_move_ids), 16.0)

    # -- Group of taxes ----------------------------------------------------------------------------------

    def test_a_group_whose_taxes_have_no_scope(self):
        child = self.make_tax("Audit 5.5 % (group)", 5.5, type_tax_use='none')
        group = self.env['account.tax'].create({
            'name': "Audit group", 'amount_type': 'group', 'type_tax_use': 'purchase',
            'company_id': self.company.id, 'children_tax_ids': [Command.set(child.ids)]})
        expense = self.expense(group, 105.5)
        self.assertMoney(self.balance(expense.account_move_id), 5.5)
        self.assertMoney(expense.expense_scan_recoverable_vat, 5.5)
        self.assertEqual(expense._expense_scan_vat_default_tax(), child)
        move = self.correct(expense)
        self.assertMoney(self.balance(move), -5.5)
        self.assertMoney(expense.expense_scan_recoverable_vat, 0.0)
        move = self.correct(expense, 'recoverable')
        self.assertMoney(self.balance(move), 5.5)
        self.assertMoney(expense.expense_scan_recoverable_vat, 5.5)
        move = self.correct(expense, 'amount', amount=2.0)
        self.assertMoney(self.balance(move), -3.5)

    # -- Foreign currency -----------------------------------------------------------------------------------

    def test_a_foreign_currency_round_trip_leaves_nothing(self):
        """The VAT recovered is in the currency of the company, the entry of the expense in its own."""
        currency = self.env.ref('base.USD')
        if currency == self.company.currency_id:
            currency = self.env.ref('base.EUR')
        currency.active = True
        today = fields.Date.context_today(self.employee)
        self.env['res.currency.rate'].search([('currency_id', '=', currency.id), ('name', '=', today)]).unlink()
        self.env['res.currency.rate'].create({
            'name': today, 'rate': 1.1713, 'currency_id': currency.id, 'company_id': self.company.id})
        for payment_mode in ('own_account', 'company_account'):
            for total in (123.45, 99.99, 7777.77):
                expense = self.expense(self.tax20, total, currency_id=currency.id, payment_mode=payment_mode)
                vat = expense.expense_scan_recoverable_vat
                self.assertMoney(vat, self.balance(expense.account_move_id))
                self.correct(expense)
                self.correct(expense, 'recoverable')
                self.assertMoney(expense.expense_scan_recoverable_vat, vat)
                self.assertMoney(self.balance(expense.expense_scan_vat_move_ids), 0.0)

    # -- Warning of the wizard -------------------------------------------------------------------------------

    def test_the_warning_says_what_happened_to_the_expense(self):
        """Reimbursed but not re-invoiced, or paid by the company: no invoice, no reimbursement to speak of."""
        reimbursed = self.expense(self.tax20)
        self.pay(reimbursed)
        company_paid = self.expense(self.tax20, payment_mode='company_account')
        for expense in reimbursed | company_paid:
            warning = self.env['expense.scan.vat.wizard'].with_user(self.manager).create({
                'expense_ids': [Command.set(expense.ids)]}).cycle_warning
            self.assertIn("is paid", warning)
            self.assertNotIn("invoiced", warning)
            self.assertNotIn("reimbursed", warning)

    # -- Access ---------------------------------------------------------------------------------------------

    def other_company_expense(self):
        other = self.env['res.company'].create({'name': "Other VAT company"})
        employee = self.env['hr.employee'].create({'name': "Elsewhere", 'company_id': other.id})
        return self.env['hr.expense'].create({
            'name': "Dinner with a secret client", 'employee_id': employee.id, 'company_id': other.id,
            'product_id': self.category.id, 'total_amount_currency': 10.0})

    def test_the_expense_of_another_company_stays_hidden(self):
        """An accounting manager of one company learns nothing of the expenses of another one."""
        expense = self.other_company_expense().with_user(self.manager)
        for call in (expense.action_expense_scan_correct_vat,
                     lambda: expense._expense_scan_correct_vat('none')):
            with self.assertRaises(AccessError):
                call()
        # The wizard does not see it: nothing is corrected.
        with self.assertRaises(UserError):
            self.env['expense.scan.vat.wizard'].with_user(self.manager).create({
                'expense_ids': [Command.set(expense.ids)]}).action_confirm()
        self.assertFalse(expense.sudo().expense_scan_vat_move_ids)

    def test_only_the_public_methods_are_reached_by_rpc(self):
        model = self.env['hr.expense']
        self.assertTrue(get_public_method(model, 'action_expense_scan_correct_vat'))
        self.assertTrue(get_public_method(model, 'action_expense_scan_open_vat_moves'))
        for name in ('_expense_scan_correct_vat', '_expense_scan_vat_move_values', '_expense_scan_vat_base'):
            with self.assertRaises(AccessError):
                get_public_method(model, name)

    def test_a_forged_context_does_not_change_the_entry(self):
        expense = self.expense(self.tax20)
        sale = self.env['account.journal'].search([
            *self.env['account.journal']._check_company_domain(self.company), ('type', '=', 'sale')], limit=1)
        forged = expense.with_context(
            default_move_type='out_invoice', default_journal_id=sale.id, default_partner_id=self.manager.partner_id.id,
            default_expense_scan_vat_expense_id=False, check_move_validity=False)
        move = forged.with_user(self.manager)._expense_scan_correct_vat('none')
        self.assertEqual(move.move_type, 'entry')
        self.assertEqual(move.journal_id.type, 'general')
        self.assertEqual(move.expense_scan_vat_expense_id, expense)
        self.assertMoney(sum(move.line_ids.mapped('balance')), 0.0)
        self.assertMoney(self.balance(move), -20.0)
