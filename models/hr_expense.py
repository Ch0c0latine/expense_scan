# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import base64
import json
import logging
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, time as dtime, timedelta

import psycopg2
from pytz import timezone, utc

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo import tools
from odoo.modules import module as odoo_module
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import email_normalize, format_date, formatLang

from ..ocr import engines, parser, preprocess
from .hr_expense_category import CONFIRMED_STATES

_logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_PREFIX = 'image/'
PDF_MIMETYPE = 'application/pdf'
#: Extra pages converted and read on a PDF whose first page gives no total:
#: a multi-page invoice carries it at the foot of the last one. Capped, like
#: the other input limits, to bound the processing time.
PDF_MAX_PAGES_READ = 5
#: Resolution of a PDF rendered for the phone preview: readable on a narrow
#: screen, light to transfer.
PDF_PREVIEW_DPI = 110
#: Gap, in days, between two expenses of the same trip.
TRIP_DAYS = 3
#: Tax printed above the exact ceiling of its rate, still accepted: tills
#: round the tax line by line.
TAX_ROUNDING_MARGIN = 0.02
#: Names of the domestic VAT on receipts, per country of the company, as
#: the parser reads them. A receipt printing another name comes from abroad:
#: "MWST" for a French company, "TVA" for a German one. "VAT", the English
#: word, belongs to every country.
DOMESTIC_TAX_LABELS = {
    'FR': {'TVA'}, 'MC': {'TVA'}, 'LU': {'TVA'}, 'BE': {'TVA', 'BTW'},
    'DE': {'MWST', 'UST', 'STEUERSUMME'}, 'AT': {'MWST', 'UST', 'STEUERSUMME'},
    'CH': {'MWST', 'TVA', 'IVA'}, 'LI': {'MWST'},
    'NL': {'BTW'}, 'ES': {'IVA'}, 'IT': {'IVA'}, 'PT': {'IVA'},
    'PL': {'PTU', 'PODATEK'}, 'CZ': {'DPH'}, 'SK': {'DPH'},
    'SE': {'MOMS'}, 'DK': {'MOMS'}, 'NO': {'MVA'},
    'GB': set(), 'IE': set(), 'US': {'Sales tax'},
}


class Stopwatch:
    """Times the steps of a scan, for the log."""

    def __init__(self, on_lap=None):
        self.started = self.last = time.time()
        self.laps = []
        # Called at the end of each step to move the displayed progress on.
        self.on_lap = on_lap

    def lap(self, name):
        now = time.time()
        self.laps.append((name, now - self.last))
        self.last = now
        if self.on_lap:
            self.on_lap(name)

    def sum(self, *names):
        return sum(duration for name, duration in self.laps if name in names)

    @property
    def total(self):
        return self.last - self.started

    def __str__(self):
        return " ".join("%s=%.2f" % lap for lap in self.laps) + " total=%.2f" % self.total


class HrExpense(models.Model):
    _inherit = 'hr.expense'
    # Newest first, as in Odoo, but the receipts of a single day follow the
    # printed time rather than the order of entry. Without a known time
    # (manual entry), the expense comes after those of the day.
    _order = 'date desc, scan_datetime desc nulls last, id desc'

    scan_state = fields.Selection(
        selection=[
            ('none', "Not scanned"),
            ('done', "Scanned"),
            ('partial', "To check"),
            ('running', "Scanning"),
            ('error', "Scan failed"),
        ],
        string="Receipt scan",
        default='none',
        readonly=True,
        copy=False,
        index=True,
    )
    scan_message = fields.Char(string="Scan details", readonly=True, copy=False)
    scan_todo = fields.Char(string="Fields to check", readonly=True, copy=False)
    #: One code per point listed in ``scan_todo``, in the same order
    #: ("reinvoice,tax_amount"), to know which ones are still open once the
    #: expense is edited. ``static`` marks the points that cannot be
    #: reopened automatically (date, category...): they stay open, as before
    #: this field existed.
    expense_scan_todo_codes = fields.Char(string="Points to check (codes)", readonly=True,
                                          copy=False)
    #: Help sentence of each point, as JSON: ``{code: sentence}``.
    expense_scan_hints = fields.Text(readonly=True, copy=False)
    #: Values set by the scan, as JSON, to recognise a correction.
    expense_scan_read_values = fields.Text(readonly=True, copy=False)
    #: Type and checksum of the main receipt, for the phone preview: the
    #: checksum changes when the image is retouched in place and versions
    #: its address.
    expense_scan_main_mimetype = fields.Char(compute='_compute_expense_scan_main_file')
    expense_scan_main_checksum = fields.Char(compute='_compute_expense_scan_main_file')
    # Fields entered by hand before the scan (comma-separated list): a new
    # scan does not replace them either.
    expense_scan_manual_fields = fields.Char(string="Fields entered by hand", readonly=True,
                                             copy=False)
    expense_scan_hint_date = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_total = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_tax = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_category = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_reinvoice = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_todo_unplaced = fields.Boolean(compute='_compute_expense_scan_field_hints')
    expense_scan_todo_pending = fields.Boolean(
        string="Points to check still open",
        compute='_compute_expense_scan_todo_pending', store=True,
        help="False as soon as every point listed in \"Fields to check\" is "
             "resolved: the review banner relies on it to go away without "
             "waiting for a new scan.",
    )
    scan_engine = fields.Char(string="Engine", readonly=True, copy=False)
    scan_duration = fields.Float(string="Duration (s)", readonly=True, copy=False)
    scan_score = fields.Float(string="Reading confidence", readonly=True, copy=False,
                              help="Average character recognition score, as a percentage.")
    scan_detected_tax = fields.Char(string="Tax read on the receipt", readonly=True, copy=False)
    expense_scan_mixed_rates = fields.Boolean(
        string="Several tax rates", readonly=True, copy=False,
        help="The receipt prints several tax rates: none of them holds for the whole expense.")
    scan_tax_amount = fields.Monetary(
        string="Receipt tax",
        currency_field='currency_id',
        copy=False,
        help="Tax amount printed on the receipt. It prevails: the tax of the "
             "expense and of the journal entry take this value, whatever the "
             "rate shown next to it. This is what lets a receipt mixing 5.5%, "
             "10% and 20% produce exactly its tax.\n\n"
             "Empty: the tax follows the rate, as in standard Odoo. A receipt "
             "without deductible tax (card slip, tax not readable) has no tax "
             "selected.\n\n"
             "The rate only does two things: carry the tax tags of the entry, "
             "and cap this amount. A tax higher than the rate applied to the "
             "receipt total is necessarily an error, and choosing a lower "
             "rate trims the amount accordingly.",
    )
    scan_time = fields.Char(string="Receipt time", readonly=True, copy=False)
    scan_datetime = fields.Datetime(
        string="Receipt timestamp",
        readonly=True,
        copy=False,
        index=True,
        help="Date and time printed on the receipt. An expense date has no "
             "time: this field orders several receipts of the same day.",
    )
    scan_raw_text = fields.Text(string="Text read", readonly=True, copy=False)
    scan_original_attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string="Original photo",
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    expense_scan_own = fields.Boolean(
        string="Own expense",
        compute='_compute_expense_scan_own',
        help="True when the expense belongs to the user viewing it. The form "
             "uses it to hide the \"Employee\" field, which only matters when "
             "a manager enters an expense for someone else.",
    )
    expense_scan_expense_manager = fields.Boolean(
        string="Expense manager",
        compute='_compute_expense_scan_expense_manager',
        help="True when the user viewing the expense manages expenses. The "
             "account and the analytic distribution are only shown to them, "
             "on top of the accounting groups Odoo requires.",
    )
    expense_scan_one_payment_method = fields.Boolean(
        string="Single payment method",
        compute='_compute_expense_scan_one_payment_method',
        help="True when the company has only one possible payment method. The "
             "form then hides the field, which Odoo fills on its own: a choice "
             "with a single option is no choice.",
    )
    scan_cropped_attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string="Cropped receipt",
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    #: The displayed receipt was retouched by hand (Retouch tool): the scan
    #: reads it as it is, without automatic cropping or straightening.
    expense_scan_manual_retouch = fields.Boolean(string="Retouched by hand", readonly=True,
                                                 copy=False)
    #: Settings of the last manual retouch, as JSON: ``quarter`` (clockwise
    #: quarter turns), ``fine`` (degrees, clockwise), ``crop`` (frame
    #: ``[x0, y0, x1, y1]`` as fractions of the rotated image). The editor
    #: applies them again to the source image when it reopens.
    expense_scan_retouch_params = fields.Text(readonly=True, copy=False)
    #: The scanned receipt was deleted: the next receipt attached is scanned
    #: (see expense_scan_receipt_attached).
    expense_scan_receipt_removed = fields.Boolean(string="Receipt deleted", readonly=True,
                                                  copy=False)

    # ------------------------------------------------------------------
    # Review banner: closes once the fields are corrected
    #
    # Without recomputation, the banner stayed after the correction until
    # the next scan. expense_scan_todo_codes keeps one code per point, next
    # to the displayed text; the banner is recomputed whenever a watched
    # field changes, even before saving.
    # ------------------------------------------------------------------

    #: A point is resolved when its watched field takes a value that clears
    #: the doubt the scan raised. For date, total, category and currency, the
    #: value must differ from the one the scan set (it was corrected).
    #: "static" points (older scans) never resolve on their own.
    TODO_RESOLVED_WHEN = {
        'reinvoice': lambda expense: expense.reinvoice_mode != 'todo',
        'tax_category': lambda expense: bool(expense.tax_ids),
        # Foreign/none/incompatible: all three are resolved by entering the
        # tax amount of the receipt (scan_tax_amount).
        'tax_amount': lambda expense: bool(expense.scan_tax_amount),
        'tax_total': lambda expense: bool(expense.total_amount_currency),
        'date': lambda expense: expense._expense_scan_changed('date'),
        'total': lambda expense: expense._expense_scan_changed('total_amount_currency'),
        'category': lambda expense: expense._expense_scan_changed('product_id'),
        'currency': lambda expense: expense._expense_scan_changed('currency_id'),
        'currency_rate': lambda expense: not expense._expense_scan_missing_rate(),
    }
    #: Field concerned by each point.
    TODO_FIELDS = {
        'date': 'date',
        'total': 'total_amount_currency',
        'category': 'product_id',
        'currency': 'currency_id',
        'reinvoice': 'reinvoice_mode',
        'tax_amount': 'scan_tax_amount',
    }
    #: Field under which the hint of each point is shown.
    HINT_PLACES = {
        'date': 'date',
        'total': 'total', 'currency': 'total', 'currency_rate': 'total',
        'tax_amount': 'tax', 'tax_total': 'tax', 'tax_category': 'tax',
        'category': 'category',
        'reinvoice': 'reinvoice',
    }
    #: Fields whose value set by the scan is kept, to detect a later
    #: correction.
    READ_VALUE_FIELDS = ('date', 'total_amount_currency', 'product_id', 'currency_id')

    @api.depends('expense_scan_todo_codes', 'expense_scan_read_values', 'reinvoice_mode',
                 'tax_ids', 'scan_tax_amount', 'total_amount_currency', 'date',
                 'product_id', 'currency_id')
    def _compute_expense_scan_todo_pending(self):
        for expense in self:
            expense.expense_scan_todo_pending = bool(expense._expense_scan_open_codes())

    def _expense_scan_open_codes(self):
        """Codes of the points still to check, in their order."""
        self.ensure_one()
        codes = [code for code in (self.expense_scan_todo_codes or '').split(',') if code]
        return [code for code in codes
                if not self.TODO_RESOLVED_WHEN.get(code, lambda e: False)(self)]

    def _expense_scan_read_value(self, name):
        """Value the scan had set on this field, ``None`` otherwise."""
        try:
            return json.loads(self.expense_scan_read_values or '{}').get(name)
        except ValueError:
            return None

    def _expense_scan_changed(self, name):
        """Tell whether the field has left the value the scan set."""
        read = self._expense_scan_read_value(name)
        if read is None:
            return False
        return read != self._expense_scan_comparable(name)

    def _expense_scan_comparable(self, name):
        """Value of the field in a form comparable to the one kept."""
        value = self[name]
        if isinstance(value, models.BaseModel):
            return value.id or False
        if name == 'date':
            return fields.Date.to_string(value) if value else False
        if isinstance(value, float):
            return round(value, 2)
        return value

    @api.depends('expense_scan_hints', 'expense_scan_todo_codes', 'expense_scan_read_values',
                 'reinvoice_mode', 'tax_ids', 'scan_tax_amount', 'total_amount_currency',
                 'date', 'product_id', 'currency_id', 'state')
    def _compute_expense_scan_field_hints(self):
        """A hint under each field to check, as long as it needs checking.

        Replaces the "check these fields" banner: the hint is shown where
        the user corrects and goes away with the correction. The form
        recomputes these fields on every change, before saving.
        """
        for expense in self:
            texts = {}
            if expense.state == 'draft':
                try:
                    hints = json.loads(expense.expense_scan_hints or '{}')
                except ValueError:
                    hints = {}
                for code in expense._expense_scan_open_codes():
                    place = self.HINT_PLACES.get(code)
                    if place and hints.get(code):
                        texts.setdefault(place, []).append(hints[code])
            for place in ('date', 'total', 'tax', 'category', 'reinvoice'):
                expense['expense_scan_hint_%s' % place] = " ".join(texts.get(place, [])) or False
            # The banner only stays for points without a field of their own
            # (scans made before the per-field hints).
            expense.expense_scan_todo_unplaced = any(
                code not in self.HINT_PLACES for code in expense._expense_scan_open_codes())

    # ------------------------------------------------------------------
    # Receipt tax: the amount prevails over the rate
    #
    # Odoo derives the tax from the rate applied to the total. A till
    # receipt prints an amount, often from several mixed rates, and that
    # amount is what must reach the accounts. The rate stays selected for
    # the tax tags, but no longer sets the amount.
    # ------------------------------------------------------------------

    @api.depends('total_amount_currency', 'tax_ids', 'scan_tax_amount')
    def _compute_tax_amount_currency(self):
        super()._compute_tax_amount_currency()
        for expense in self:
            if expense.scan_tax_amount:
                expense.tax_amount_currency = expense.scan_tax_amount
                expense.untaxed_amount_currency = (
                    expense.total_amount_currency - expense.scan_tax_amount)

    @api.depends('total_amount', 'currency_rate', 'tax_ids', 'is_multiple_currency',
                 'scan_tax_amount')
    def _compute_tax_amount(self):
        super()._compute_tax_amount()
        for expense in self:
            if not expense.scan_tax_amount:
                continue
            rate = expense.currency_rate or 1.0
            company_tax = expense.company_currency_id.round(expense.scan_tax_amount / rate)
            expense.tax_amount = company_tax
            expense.untaxed_amount = expense.total_amount - company_tax

    @api.depends_context('uid')
    def _compute_expense_scan_expense_manager(self):
        manager = self.env.user.has_group('hr_expense.group_hr_expense_manager')
        for expense in self:
            expense.expense_scan_expense_manager = manager

    @api.depends('employee_id')
    @api.depends_context('uid')
    def _compute_expense_scan_own(self):
        """Tell whether the expense belongs to the user viewing it."""
        mine = self.env.user.employee_ids
        for expense in self:
            expense.expense_scan_own = expense.employee_id in mine

    @api.depends('selectable_payment_method_line_ids')
    def _compute_expense_scan_one_payment_method(self):
        """Tell whether there is no payment method to choose (only one possible).

        Computed here rather than in the view: the client's expression
        evaluator does not know ``len``.
        """
        for expense in self:
            expense.expense_scan_one_payment_method = (
                len(expense.selectable_payment_method_line_ids) < 2)

    def _expense_scan_max_rate(self):
        """Highest rate kept, or ``None`` without a percentage tax.

        "No tax" and "0% tax" are two different cases: the first allows no
        check, the second sets a zero ceiling.
        """
        self.ensure_one()
        rates = self.tax_ids.filtered(
            lambda tax: tax.amount_type == 'percent').mapped('amount')
        return max(rates) if rates else None

    def _expense_scan_tax_ceiling(self):
        """Highest possible tax: the highest rate applied to the total.

        A receipt with several rates carries less tax than if everything were
        at the highest rate. This ceiling catches reading errors without
        blocking a legitimate entry. It is zero on an exempt category, which
        forbids any tax there.
        """
        self.ensure_one()
        rate = self._expense_scan_max_rate()
        if rate is None:
            return None
        return self.total_amount_currency * rate / (100.0 + rate)

    @api.onchange('product_id')
    def _onchange_expense_scan_name(self):
        """An automatic description follows the chosen category.

        "Receipt on 12/09" becomes "Toll on 12/09" when the category is Toll.
        A description written by the employee is left alone.
        """
        for expense in self:
            if expense.date and expense.scan_state != 'none' \
                    and not expense.expense_scan_keep_name \
                    and expense._expense_scan_name_is_automatic():
                expense.name = expense._expense_scan_auto_name(expense.product_id, expense.date)

    @api.onchange('tax_ids', 'product_id', 'total_amount_currency')
    def _onchange_expense_scan_taxes(self):
        """A rate, a category or a total caps the tax, or fills it when empty.

        Capping: switching the expense to "0% EX" left the amount read
        untouched, hence a deductible tax the chosen rate no longer
        justifies. It only works that way: raising the rate does not invent
        any tax, only the receipt says what was paid.

        Filling, only when the field is empty (receipt without readable tax,
        manual entry): the total including tax, the tax is
        total x rate / (100 + rate), not total x rate. The category brings
        its default tax: without filling, the form would show no tax while
        the entry carries the rate's.

        An amount already there is not replaced: it often comes from the
        receipt and stays exact with several rates, which a computation from
        a single rate would spoil.
        """
        for expense in self:
            if expense._expense_scan_clamp_tax():
                continue
            if expense.scan_tax_amount or not expense.currency_id:
                continue
            # Several taxes: Odoo can combine them, a single rate cannot.
            # Odoo's computation then applies.
            percent = expense.tax_ids.filtered(lambda tax: tax.amount_type == 'percent')
            if len(percent) != 1 or len(expense.tax_ids) != 1:
                continue
            ceiling = expense._expense_scan_tax_ceiling()
            if ceiling:
                expense.scan_tax_amount = expense.currency_id.round(ceiling)

    def _expense_scan_clamp_tax(self):
        """Bring the receipt tax down to the rate ceiling when above it.

        Returns ``True`` when the amount was changed.
        """
        self.ensure_one()
        if not self.scan_tax_amount or not self.currency_id:
            return False
        ceiling = self._expense_scan_tax_ceiling()
        if ceiling is None:
            # No percentage tax: no ceiling to apply. The block happens at
            # posting time, where the lack of a rate prevents carrying the
            # tax into the entry.
            return False
        if self.currency_id.compare_amounts(
                self.scan_tax_amount, ceiling + TAX_ROUNDING_MARGIN) > 0:
            self.scan_tax_amount = self.currency_id.round(ceiling)
            return True
        return False

    @api.constrains('tax_ids', 'scan_state')
    def _check_expense_scan_single_tax(self):
        """A scanned expense carries a single tax.

        The split that carries the receipt tax into the entry handles one
        rate only: with two taxes, both would apply to the same base and the
        amount posted would no longer be the receipt's.

        A receipt with several rates is handled with a single rate kept and
        the exact amount entered next to it.
        """
        for expense in self:
            if expense.scan_state == 'none':
                continue
            if len(expense.tax_ids) > 1:
                raise ValidationError(_(
                    "A scanned expense can only carry one tax: the amount in "
                    "the \"Receipt tax\" field prevails, and the rate only "
                    "carries the tax tags.\n\n"
                    "For a receipt mixing several rates, keep the highest and "
                    "leave the amount read on the receipt."))

    @api.constrains('scan_tax_amount', 'total_amount_currency', 'tax_ids')
    def _check_scan_tax_amount(self):
        for expense in self:
            if not expense.scan_tax_amount:
                continue
            if expense.scan_tax_amount < 0:
                raise ValidationError(_("The receipt tax cannot be negative."))
            ceiling = expense._expense_scan_tax_ceiling()
            if ceiling is None:
                # No tax kept: no ceiling to check. The entry is accepted (the
                # amount stays a piece of information from the receipt); the
                # block happens at accounting validation, where the lack of a
                # rate prevents carrying the tax.
                continue
            if expense.currency_id.compare_amounts(
                    expense.scan_tax_amount, ceiling + TAX_ROUNDING_MARGIN) > 0:
                money = expense._expense_scan_money
                raise ValidationError(_(
                    "Impossible receipt tax: %(entered)s is above the maximum of "
                    "%(ceiling)s, which is the rate of %(rate)s %% applied "
                    "to the whole %(total)s of the receipt.\n\n"
                    "Choose an expense category whose rate covers this tax, or "
                    "empty the \"Receipt tax\" field if this expense gives no "
                    "right to deduction.",
                    entered=money(expense.scan_tax_amount), ceiling=money(ceiling),
                    rate=expense._expense_scan_rate_text(expense._expense_scan_max_rate()),
                    total=money(expense.total_amount_currency)))

    def _prepare_receipts_vals(self):
        """Split the base so that the entry carries the receipt tax.

        Odoo builds the entry by applying the rate to the total: the amount
        read on the receipt would not appear. Adjusting the tax line
        afterwards is fragile (it is recomputed on every change). The base
        is therefore split in two: the part that produces exactly the wanted
        amount at the chosen rate, and the rest without tax.

        The entry stays balanced, the deductible tax is the receipt's and
        the tax tags are set by the usual tax engine.
        """
        vals_list = super()._prepare_receipts_vals()
        # _prepare_receipts_vals groups by employee and creates one line per
        # expense, in that order: the same grouping is done again here to
        # match each line with its expense.
        for vals, expenses in zip(vals_list, self.sudo().grouped('employee_id').values()):
            extra_lines = []
            for command, expense in zip(vals.get('line_ids') or [], expenses):
                remainder = expense._expense_scan_split_base(command)
                if remainder is not None:
                    extra_lines.append(remainder)
            if extra_lines:
                vals['line_ids'] = list(vals['line_ids']) + extra_lines
        return vals_list

    def _expense_scan_split_base(self, command):
        """Adjust the base line and return the remainder line if there is one."""
        self.ensure_one()
        if not self.scan_tax_amount:
            return None
        rate = self._expense_scan_max_rate()
        if not rate:
            # A missing rate blocks here: without a rate, the receipt tax
            # cannot go into the entry, and posting without it would lose it
            # without warning.
            raise UserError(_(
                "Expense \"%(name)s\" carries a receipt tax of %(amount)s, "
                "but no tax with a usable rate. Select the matching tax on the "
                "expense (or on its category, for the next ones), or empty "
                "the \"Receipt tax\" field.",
                name=self.name, amount=self._expense_scan_money(self.scan_tax_amount)))

        line_vals = command[2]
        currency = self.company_currency_id
        # Odoo treats the price of an entry line carrying an expense_id as
        # tax included (the expense convention, explicit in
        # hr_expense/models/account_move_line.py). It must therefore get the
        # tax-included amount of the taxed part, not its base, otherwise the
        # tax would be removed a second time.
        taxed_total = currency.round(self.tax_amount * (100.0 + rate) / rate)
        remainder = currency.round(self.total_amount - taxed_total)

        line_vals['quantity'] = 1
        line_vals['price_unit'] = taxed_total
        if currency.is_zero(remainder):
            return None

        untaxed = dict(line_vals)
        untaxed['quantity'] = 1
        untaxed['price_unit'] = remainder
        untaxed['tax_ids'] = [Command.set([])]
        untaxed['tax_tag_ids'] = [Command.set([])]
        untaxed['name'] = _("%s (without tax)", line_vals.get('name') or self.name)
        return Command.create(untaxed)

    def _expense_scan_tax_follows_rate(self):
        """Tell whether the receipt tax is the one the rate gives.

        Odoo's own computation then already posts it, whatever the path.
        """
        self.ensure_one()
        ceiling = self._expense_scan_tax_ceiling()
        percent = self.tax_ids.filtered(lambda tax: tax.amount_type == 'percent')
        return (ceiling is not None and len(self.tax_ids) == 1 and len(percent) == 1
                and self.currency_id.compare_amounts(
                    self.scan_tax_amount, self.currency_id.round(ceiling)) == 0)

    def _prepare_payments_vals(self):
        """Refuse to post a receipt tax that cannot be carried.

        The "paid by the company" path builds its entry lines with explicit
        balances, without the base split above. It would post the tax of the
        rate instead of the receipt's without warning.
        """
        if self.scan_tax_amount and not self._expense_scan_tax_follows_rate():
            raise UserError(_(
                "The receipt tax (%(amount)s) cannot yet be carried on an "
                "expense paid by the company. Set the expense back to "
                "\"Employee (to reimburse)\", or empty the \"Receipt tax\" field "
                "to let Odoo compute it from the rate.",
                amount=self._expense_scan_money(self.scan_tax_amount)))
        return super()._prepare_payments_vals()

    # ------------------------------------------------------------------
    # Entry point: receipt uploaded from the expense list
    # ------------------------------------------------------------------

    @api.model
    def create_expense_from_attachments(self, attachment_ids=None, view_type='list'):
        """Scan each receipt right after the expense is created.

        Odoo already creates an empty expense per attachment: the scan fills
        it, without rewriting that behaviour.
        """
        expense_ids = super().create_expense_from_attachments(
            attachment_ids=attachment_ids, view_type=view_type)
        expenses = self.browse(expense_ids)
        company = self.env.company

        # Odoo picks the category by the internal reference "EXP_GEN",
        # otherwise the first in alphabetical order ("Gift", for instance):
        # renaming that reference redirects every receipt. The company
        # setting does not depend on any reference.
        if company.expense_scan_product_id:
            expenses.product_id = company.expense_scan_product_id

        if company.expense_scan_enabled:
            if self.env.context.get('expense_scan_async') and len(expenses) == 1:
                # A single receipt, from the interface: the form opens at once
                # and starts the scan while showing its progress
                # (action_expense_scan_start).
                expenses.write({'scan_state': 'running'})
            else:
                expenses._expense_scan_run()
        return expense_ids

    # ------------------------------------------------------------------
    # Scan started by the form, with its progress
    # ------------------------------------------------------------------

    #: Delay (minutes) after which a pending scan is picked up by the
    #: scheduled task: the app may have closed before starting it.
    PENDING_SCAN_MINUTES = 3
    #: Delay (minutes) after which the scan is given up. A receipt that gets
    #: its worker killed (memory, CPU) would otherwise do so on every
    #: attempt: the form restarts the scan each time it opens, the scheduled
    #: task every five minutes. A time limit rather than a number of
    #: attempts: Odoo retries a conflicting request by itself, which would
    #: skew a counter, and the counter would have to be written outside the
    #: transaction.
    EXPIRE_SCAN_MINUTES = 15

    def _expense_scan_expire(self):
        """Put the scans running for too long in error.

        Returns the expenses concerned.
        """
        limit = fields.Datetime.subtract(fields.Datetime.now(), minutes=self.EXPIRE_SCAN_MINUTES)
        stale = self.filtered(lambda e: e.scan_state == 'running' and e.create_date < limit)
        stale.sudo().write({
            'scan_state': 'error',
            'scan_message': _("The scan of this receipt did not finish. "
                              "Enter the expense by hand."),
            'scan_todo': False,
            'expense_scan_todo_codes': False,
            'expense_scan_hints': False,
        })
        return stale

    def action_expense_scan_start(self, specification=None):
        """Scan the receipt the form just opened, step by step.

        Each step is announced to the user with the first values read, so
        that the form fills in during the scan. Returns the final values in
        the ``web_read`` format (``specification``: the form fields); the
        form applies them without overwriting the user's changes.

        The row lock guarantees a single scan at a time. It also holds the
        form's save until the end of the scan: the user's corrections
        therefore come after it.
        """
        self.ensure_one()
        # Access rights first: the row lock is taken in SQL, without the
        # ORM's access control.
        self.check_access('write')
        if self._expense_scan_expire():
            return {'started': False, 'values': self._expense_scan_web_values(specification)}
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM hr_expense WHERE id = %s FOR UPDATE NOWAIT", [self.id])
        except psycopg2.errors.LockNotAvailable:
            return {'started': False, 'busy': True}
        self.invalidate_recordset(['scan_state'])
        if self.scan_state != 'running':
            return {'started': False, 'values': self._expense_scan_web_values(specification)}
        self._expense_scan_run(force=True, progress=self._expense_scan_progress_sender())
        return {'started': True, 'values': self._expense_scan_web_values(specification)}

    def _expense_scan_web_values(self, specification):
        """Values of the form, in the format it expects."""
        if not specification:
            return {}
        return self.web_read(specification)[0]

    def _expense_scan_progress_sender(self):
        """Function that announces a scan step to the user.

        It uses a separate cursor, committed at once: a notification sent in
        the scan's transaction would only arrive at the end, all steps at
        once.
        """
        registry, uid = self.env.registry, self.env.uid
        partner_id, expense_id = self.env.user.partner_id.id, self.id

        def send(step, values=None):
            if odoo_module.current_test:
                return  # a test commits nothing outside its transaction
            try:
                with registry.cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    env['bus.bus']._sendone(
                        env['res.partner'].browse(partner_id), 'expense_scan/progress',
                        {'expense_id': expense_id, 'step': step, 'values': values or {}})
            except Exception:  # noqa: BLE001 (progress is only a convenience)
                _logger.debug("Scan progress not sent", exc_info=True)
        return send

    def _expense_scan_preview(self, result):
        """First values read, shown before the end of the scan."""
        values = {}
        if result.value('date'):
            values['date'] = fields.Date.to_string(result.value('date'))
        if result.value('total'):
            values['total_amount_currency'] = result.value('total')
        if result.value('merchant'):
            values['expense_scan_merchant'] = result.value('merchant')
        return values

    @api.model
    def _cron_expense_scan_pending(self):
        """Pick up the scans left pending (form closed before the end)."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), minutes=self.PENDING_SCAN_MINUTES)
        running = self.search([('scan_state', '=', 'running')])
        running -= running._expense_scan_expire()
        pending = running.filtered(lambda e: e.write_date < limit)
        for expense in pending:
            expense._expense_scan_run(force=True)
            if not odoo_module.current_test:
                self.env.cr.commit()  # a finished scan is not lost with the next one

    @api.model
    def _cron_expense_scan_purge_texts(self, batch=1000):
        """Erase the text read on expenses submitted long enough ago.

        The text of a receipt holds personal data (names, addresses, last
        digits of a card) that is useless once the expense is submitted: it
        only serves the scan and the linking of neighbouring trips. The delay
        is a company setting.
        """
        for company in self.env['res.company'].search([]):
            days = company.expense_scan_text_retention_days
            if days <= 0:
                continue
            limit = fields.Date.subtract(fields.Date.context_today(self), days=days)
            old = self.sudo().search([
                ('company_id', '=', company.id),
                ('scan_raw_text', '!=', False),
                ('state', 'in', CONFIRMED_STATES),
                ('date', '<', limit),
            ], limit=batch)
            old.with_context(tracking_disable=True).write({'scan_raw_text': False})
            if not odoo_module.current_test:
                self.env.cr.commit()

    def _get_employee_from_email(self, email_address):
        """Also recognise the sender by their private address.

        Odoo only looks at the work email and at the user account's. An
        employee forwarding a receipt from their personal phone is not
        recognised: Odoo creates an expense without an employee, to fix by
        hand, without warning.

        The private address of the employee record covers that case and
        already exists. An extra field on ``hr.employee`` would be unknown to
        the public employee profile, which would then refuse any read to
        non-HR users.

        Read with elevated rights: the private address is restricted to the
        HR group, and the caller is the mail gateway.
        """
        employee = super()._get_employee_from_email(email_address)
        if employee:
            return employee

        normalized = email_normalize(email_address)
        if not normalized:
            return employee

        Employee = self.env['hr.employee'].sudo()
        if 'private_email' not in Employee._fields:
            return employee

        # The "ilike" only narrows the search; the deciding comparison is on
        # the whole address, normalised on both sides (a fragment identifies
        # nobody).
        for candidate in Employee.search([('private_email', 'ilike', normalized)]):
            if email_normalize(candidate.private_email) == normalized:
                return candidate
        return employee

    def _alias_get_error(self, message, message_dict, alias):
        """Let the private address through to the "employees" alias.

        The HR module's filter, applied before any creation, only knows the
        work email and the account's: a message from the private address was
        refused before reaching the recognition above.
        """
        error = super()._alias_get_error(message, message_dict, alias)
        if error and alias.alias_contact == 'employees':
            email_from = tools.mail.decode_message_header(message, 'From')
            if self._get_employee_from_email(email_normalize(email_from, strict=False)):
                return False
        return error

    # ------------------------------------------------------------------
    # Manual retouch
    # ------------------------------------------------------------------

    def _expense_scan_main_is_derived(self):
        """Tell whether the displayed receipt is the image taken from the original photo.

        Image cropped by the scan or retouched by hand. If the user deleted
        it and attached another receipt, the original photo, the retouch
        settings and the manual retouch flag concern a receipt that is no
        longer displayed.
        """
        self.ensure_one()
        main = self.message_main_attachment_id
        return bool(main) and main == self.scan_cropped_attachment_id

    def _expense_scan_original(self):
        """Original photo of the displayed receipt, if it was taken from it."""
        self.ensure_one()
        if self._expense_scan_main_is_derived():
            return self.scan_original_attachment_id
        return self.env['ir.attachment']

    def _expense_scan_forget_original(self):
        """Delete an original photo that no longer matches the displayed receipt.

        A field attachment, invisible in the list of receipts: the user
        cannot delete it themselves.
        """
        self.ensure_one()
        original = self.scan_original_attachment_id
        if original and not self._expense_scan_main_is_derived():
            self.write({
                'scan_original_attachment_id': False,
                'expense_scan_manual_retouch': False,
                'expense_scan_retouch_params': False,
            })
            original.sudo().unlink()

    def _expense_scan_retouch_source(self):
        """Starting image of the retouch: the original photo, otherwise the displayed one."""
        self.ensure_one()
        source = self._expense_scan_original() or self.message_main_attachment_id
        if not source:
            raise UserError(_("No receipt to retouch."))
        return source

    def _expense_scan_retouch_image(self):
        """Starting image of the retouch: ``(bytes, MIME type)``.

        A PDF is rendered as an image (first page), as for the scan.
        """
        source = self._expense_scan_retouch_source()
        if not self._expense_scan_is_pdf(source):
            return source.raw, source.mimetype or 'image/jpeg'
        data = preprocess.pdf_first_page_to_image_bytes(source.raw)
        if not data:
            raise UserError(_(
                "The PDF could not be converted. Install \"pdf2image\" and the "
                "\"poppler-utils\" system package to retouch PDFs."))
        return data, 'image/png'

    def _expense_scan_multipage_pdf(self, attachment):
        """Tell whether the attachment is a PDF of more than one page."""
        return bool(attachment) and self._expense_scan_is_pdf(attachment) \
            and preprocess.pdf_page_count(attachment.raw) > 1

    def _expense_scan_check_retouchable(self, source):
        """Refuse the retouch of a PDF of several pages.

        The retouch replaces the receipt with an image of its first page:
        the other pages, often the one with the total, would be lost.
        """
        if self._expense_scan_multipage_pdf(source):
            raise UserError(_(
                "This PDF has several pages: it stays the receipt as it is. "
                "Retouch applies to photos and single-page PDFs."))

    def expense_scan_retouch_data(self):
        """Starting image and settings of the last retouch, for the editor.

        An image is served by its address; a PDF, rendered on the fly, is
        returned as a data URL.
        """
        self.ensure_one()
        self.check_access('read')
        source = self._expense_scan_retouch_source()
        self._expense_scan_check_retouchable(source)
        if self._expense_scan_is_pdf(source):
            data, mimetype = self._expense_scan_retouch_image()
            url = 'data:%s;base64,%s' % (mimetype, base64.b64encode(data).decode())
        else:
            url = '/web/image/%d' % source.id
        params = None
        if self._expense_scan_main_is_derived():
            try:
                params = json.loads(self.expense_scan_retouch_params or 'null')
            except ValueError:
                params = None
        return {'url': url, 'params': params}

    @api.depends('message_main_attachment_id')
    def _compute_expense_scan_main_file(self):
        for expense in self:
            attachment = expense.message_main_attachment_id.sudo()
            expense.expense_scan_main_mimetype = attachment.mimetype or False
            expense.expense_scan_main_checksum = attachment.checksum or False

    def expense_scan_pdf_preview(self):
        """First page of the main receipt, when it is a PDF, as a data URL.

        Phone browsers do not display a PDF inside a page: the preview shows
        this image.
        """
        self.ensure_one()
        self.check_access('read')
        attachment = self.message_main_attachment_id
        if not attachment or not self._expense_scan_is_pdf(attachment):
            return False
        data = preprocess.pdf_first_page_to_image_bytes(attachment.raw, dpi=PDF_PREVIEW_DPI)
        if not data:
            return False
        return 'data:image/png;base64,%s' % base64.b64encode(data).decode()

    def expense_scan_auto_retouch_params(self):
        """Settings the automatic retouch would suggest, for the editor.

        Computed on the editor's starting image, from the OCR boxes: quarter
        turn and straightening as in the scan, frame around the text as in
        ``preprocess.crop_to_text``. The scan's perspective correction has
        no equivalent in the editor (rotation and rectangle): the frame
        suggested approximates it.
        """
        self.ensure_one()
        self.check_access('read')
        ok, message = preprocess.dependencies_status()
        if not ok:
            raise UserError(message)
        company = self.company_id or self.env.company
        data, _mimetype = self._expense_scan_retouch_image()
        image = preprocess.load_image(data)
        engine = engines.resolve_engine(
            company.expense_scan_engine, **company._expense_scan_engine_options())
        words = [word for word in engine.recognize(image) if word.text.strip()]
        height, width = image.shape[:2]
        quarters = 0
        if words:
            quarters = self._expense_scan_quarters(
                words, image,
                decide_180=not self._expense_scan_is_pdf(self._expense_scan_retouch_source()))
        turned = preprocess.rotate_words_quarters(words, quarters, width, height)
        # Same convention as the scan: preprocess.rotate turns
        # anticlockwise, the editor clockwise.
        skew = preprocess.skew_angle_from_words(turned)
        if not preprocess.MIN_DESKEW_ANGLE < abs(skew) <= preprocess.MAX_TEXT_DESKEW_ANGLE:
            skew = 0.0
        fine = max(-45.0, min(45.0, round(-skew * 2) / 2))
        return {
            'quarter': quarters,
            'fine': fine,
            'crop': self._expense_scan_text_frame(words, width, height, quarters * 90 + fine),
        }

    @staticmethod
    def _expense_scan_text_frame(words, width, height, angle, margin_ratio=0.035, min_score=0.5):
        """Frame of the text in the image rotated by ``angle`` degrees (clockwise).

        The rotated image is placed as in the editor: rotated about its
        centre, inside the rectangle that holds it whole. Returns the frame
        as fractions of that rectangle; the whole image without text.
        """
        kept = preprocess.text_inliers([word for word in words if word.score >= min_score])
        if len(kept) < 3:
            return [0.0, 0.0, 1.0, 1.0]
        rad = math.radians(angle)
        cos, sin = math.cos(rad), math.sin(rad)
        bound_w = width * abs(cos) + height * abs(sin)
        bound_h = width * abs(sin) + height * abs(cos)
        xs, ys = [], []
        for word in kept:
            for x, y in ((word.left, word.top), (word.right, word.top),
                         (word.right, word.bottom), (word.left, word.bottom)):
                dx, dy = x - width / 2.0, y - height / 2.0
                xs.append(dx * cos - dy * sin + bound_w / 2.0)
                ys.append(dx * sin + dy * cos + bound_h / 2.0)
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        margin_x = (right - left) * margin_ratio + 8
        margin_y = (bottom - top) * margin_ratio + 8
        return [
            max(left - margin_x, 0.0) / bound_w,
            max(top - margin_y, 0.0) / bound_h,
            min(right + margin_x, bound_w) / bound_w,
            min(bottom + margin_y, bound_h) / bound_h,
        ]

    @staticmethod
    def _expense_scan_clean_retouch_params(params):
        """Retouch settings received from the browser, checked, as JSON; False otherwise."""
        try:
            crop = [min(max(float(value), 0.0), 1.0) for value in params['crop']]
            if len(crop) != 4 or crop[0] >= crop[2] or crop[1] >= crop[3]:
                return False
            return json.dumps({
                'quarter': int(params['quarter']) % 4,
                'fine': min(max(float(params['fine']), -45.0), 45.0),
                'crop': crop,
            })
        except (KeyError, TypeError, ValueError):
            return False

    def action_expense_scan_retouch(self, image_base64, params=None):
        """Replace the displayed receipt with an image retouched by hand.

        The retouch (rotation, crop) is done in the browser, from the
        starting image (``expense_scan_retouch_data``); the server receives
        the result as JPEG, and the settings (``params``) to suggest at the
        next retouch.
        The original photo is never changed: the result replaces the cropped
        image, or becomes a new displayed attachment if there is none yet
        (same layout as ``_expense_scan_store_image``).

        Does not start a new scan: the "Scan again" button does, and then
        reads the retouched image without retouching it automatically.
        """
        self.ensure_one()
        self.check_access('write')
        main = self.message_main_attachment_id
        if not main:
            raise UserError(_("No receipt to retouch."))
        self._expense_scan_check_retouchable(self._expense_scan_retouch_source())
        self._expense_scan_forget_original()
        try:
            raw = base64.b64decode(image_base64)
        except (ValueError, TypeError) as error:
            raise UserError(_("Unreadable retouched image.")) from error
        if not raw:
            raise UserError(_("Unreadable retouched image."))
        if len(raw) > preprocess.MAX_FILE_BYTES:
            raise UserError(_(
                "The retouched image is too large (%(size)d MB, limit %(max)d MB).",
                size=len(raw) // (1024 * 1024),
                max=preprocess.MAX_FILE_BYTES // (1024 * 1024)))
        original = self._expense_scan_original() or main
        values = {
            'expense_scan_manual_retouch': True,
            'expense_scan_retouch_params': self._expense_scan_clean_retouch_params(params),
        }
        if main != original:
            main.write({'raw': raw, 'mimetype': 'image/jpeg'})
        else:
            stem = os.path.splitext(original.name or 'ticket')[0]
            retouched = self.env['ir.attachment'].create({
                'name': _("%s (retouched).jpg", stem),
                'raw': raw,
                'mimetype': 'image/jpeg',
                'res_model': 'hr.expense',
                'res_id': self.id,
            })
            self.sudo()._message_set_main_attachment_id(retouched, force=True)
            # As for the image cropped by the scan: the original becomes a
            # field attachment, left out of the list of receipts and of the
            # journal entry, reachable through "Original photo".
            original.sudo().write({'res_field': 'scan_original_attachment_id'})
            values.update({
                'scan_original_attachment_id': original.id,
                'scan_cropped_attachment_id': retouched.id,
            })
        self.write(values)
        return True

    # ------------------------------------------------------------------
    # Receipt attached to a new expense
    # ------------------------------------------------------------------

    @api.model
    def expense_scan_receipt_defaults(self):
        """Values that let a new expense be saved before a receipt is attached.

        The chatter saves the form before uploading a file; the description
        and the category are required. The form only sets these values on
        fields still empty.
        """
        company = self.env.company
        product = (company._expense_scan_default_product()
                   or self.env['product.product'].search([('can_be_expensed', '=', True)],
                                                         limit=1))
        today = format_date(self.env, fields.Date.context_today(self))
        return {
            'name': self._get_untitled_expense_name(today),
            'product_id': {'id': product.id, 'display_name': product.display_name}
            if product else False,
        }

    #: Fields the scan can fill and the user may have entered on the form
    #: before attaching the receipt.
    KEEPABLE_FIELDS = ('date', 'total_amount_currency', 'product_id', 'currency_id',
                       'tax_ids', 'scan_tax_amount', 'vendor_id', 'expense_scan_nights')

    def expense_scan_analyze_new_receipt(self, changed=None):
        """Scan the receipt attached to an expense saved for it.

        Only if the expense was never scanned. ``changed`` lists the fields
        edited by hand on the new form: the scan does not fill them. The
        temporary description and the default category, set so that the
        form could be saved, remain to be filled.
        """
        self.ensure_one()
        self.check_access('write')
        if self.scan_state != 'none' \
                or not (self.company_id or self.env.company).expense_scan_enabled:
            return True
        changed = set(changed or ())
        keep = changed & set(self.KEEPABLE_FIELDS)
        if changed & set(self.REINVOICE_FIELDS):
            keep |= set(self.REINVOICE_FIELDS)
        self.with_context(expense_scan_new_receipt=True,
                          expense_scan_keep_fields=sorted(keep))._expense_scan_run()
        return True

    def _expense_scan_kept_fields(self):
        """Fields entered or corrected by hand, which the scan does not fill.

        Those entered on the new form before uploading the receipt and, on a
        new scan, those entered that way the first time or corrected since
        the previous scan.
        """
        keep = set(self.env.context.get('expense_scan_keep_fields') or ())
        keep |= set(filter(None, (self.expense_scan_manual_fields or '').split(',')))
        keep |= {name for name in self.READ_VALUE_FIELDS if self._expense_scan_changed(name)}
        return keep & set(self.KEEPABLE_FIELDS + self.REINVOICE_FIELDS)

    # ------------------------------------------------------------------
    # Receipt deleted, then replaced
    # ------------------------------------------------------------------

    #: What the scan noted about the receipt it read.
    READING_FIELDS = (
        'scan_message', 'scan_todo', 'expense_scan_todo_codes', 'expense_scan_hints',
        'expense_scan_read_values', 'scan_engine', 'scan_duration', 'scan_score',
        'scan_detected_tax', 'expense_scan_mixed_rates', 'scan_time', 'scan_datetime',
        'scan_raw_text', 'expense_scan_merchant_read', 'expense_scan_guessed_product_id',
        'expense_scan_category_reason')

    def _expense_scan_receipt_removed(self, kept):
        """Forget the reading of a receipt the user deleted.

        Its points to check and its figures describe a receipt that is gone;
        its text would still link the expense to a trip, and the merchant
        read would teach a wrong category on submission. The fields of the
        expense keep their values until the next receipt is scanned, except
        ``kept``, the fields entered or corrected by hand, which that scan
        does not fill.
        """
        self.ensure_one()
        values = dict.fromkeys(self.READING_FIELDS, False)
        values.update({
            'scan_state': 'none',
            'expense_scan_manual_fields': ','.join(sorted(kept)) or False,
            'expense_scan_receipt_removed': True,
        })
        self.sudo().write(values)

    def expense_scan_receipt_attached(self):
        """Scan the receipt attached in place of a deleted one.

        Called by the form after an upload. A receipt attached to an expense
        entered by hand is not scanned: the scan would overwrite what the
        user entered.
        """
        self.ensure_one()
        self.check_access('write')
        if not self.expense_scan_receipt_removed:
            return False
        if not self._expense_scan_image_attachments():
            return False  # a file the engine cannot read: wait for the next one
        self.expense_scan_receipt_removed = False
        if self.scan_state != 'none' \
                or not (self.company_id or self.env.company).expense_scan_enabled:
            return False
        self._expense_scan_lock()
        self.with_context(expense_scan_new_receipt=True)._expense_scan_run_pieces()
        return True

    def _expense_scan_lock(self):
        """Lock the expense for the time of a scan.

        Two scans of the same expense at once (a double click, a second tab)
        would write the same row and fail on a deadlock, one of them after
        a long wait.
        """
        # Access rights first: the row lock is taken in SQL, without the
        # ORM's access control.
        self.check_access('write')
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM hr_expense WHERE id IN %s FOR UPDATE NOWAIT",
                    [tuple(self.ids)])
        except psycopg2.errors.LockNotAvailable:
            raise UserError(_("This receipt is already being scanned. "
                              "Try again in a moment.")) from None

    def action_expense_scan_rescan(self):
        """Scan the current receipt(s) again.

        The scan does not start from the original photo: the user has most
        often retouched or replaced the cropped receipt, and starting from
        the original would lose that correction.

        With several pieces, they go through the piece comparison: adding a
        second photo, then scanning again, says that the two belong
        together.
        """
        for expense in self:
            if not expense._expense_scan_image_attachments():
                raise UserError(_("No receipt to scan: attach one first."))
        self._expense_scan_lock()
        moved = self.browse()
        for expense in self:
            expense.expense_scan_receipt_removed = False
            moved |= expense._expense_scan_run_pieces(force=True, from_original=False)
        if not moved:
            return True
        # A receipt leaves the form under the user's eyes: say where it went.
        if len(moved) == 1:
            message = _("A receipt with a different total or date was moved to "
                        "its own expense: %s")
            links = [{'label': moved.name, 'url': '/odoo/hr.expense/%d' % moved.id}]
        else:
            message = _("%(count)d receipts with a different total or date were moved "
                        "to their own expenses.", count=len(moved))
            links = []
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning',
                'sticky': True,
                'message': message,
                'links': links,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_expense_scan_done(self):
        """Mark the review as done and go back to the list.

        Finishing counts as a review: the hints still shown under the fields
        (a date judged hard to read but correct, for instance) are cleared.
        The text of the points stays, for the record.
        """
        self.filtered(lambda e: e.state == 'draft' and e.expense_scan_todo_codes).write({
            'expense_scan_todo_codes': False,
            'expense_scan_hints': False,
        })
        return self._expense_scan_expense_list()

    def action_expense_scan_drop(self):
        """Delete the scanned expense and go back to the list."""
        action = self._expense_scan_expense_list()
        self.unlink()
        return action

    def _expense_scan_expense_list(self):
        """The "My Expenses" action, or an equivalent if the screen changed.

        ``target: main`` resets the breadcrumbs. Without it, the form left or
        deleted stays there, with a link that leads nowhere.
        """
        action = self.env.ref('hr_expense.hr_expense_actions_my_all',
                              raise_if_not_found=False)
        if action:
            action = action.sudo().read()[0]
        else:
            action = {
                'type': 'ir.actions.act_window',
                'name': _("My Expenses"),
                'res_model': 'hr.expense',
                'view_mode': 'kanban,list,form',
            }
        action['target'] = 'main'
        return action

    # ------------------------------------------------------------------
    # Processing chain
    # ------------------------------------------------------------------

    def _expense_scan_run(self, force=False, from_original=True, progress=None):
        """Scan the expenses without interrupting the upload.

        An unreadable receipt or a missing dependency does not make the photo
        upload fail: the error is stored on the expense and the user enters
        the data by hand.
        """
        for expense in self:
            attachment = expense._expense_scan_source_attachment(from_original)
            if not attachment:
                continue
            if not force and expense.scan_state in ('done', 'partial'):
                continue
            try:
                # Writing the values is protected too: a model constraint must
                # not make the photo upload fail, and the user has no control
                # over it. The savepoint lets the error be written afterwards
                # on a clean cursor.
                with self.env.cr.savepoint():
                    result = expense._expense_scan_process(attachment, progress=progress)
                    if progress:
                        progress('values', expense._expense_scan_preview(result))
                    expense._expense_scan_apply(result, attachment)
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                # Another request wrote the same row during the scan (the form
                # opening sets an access token on the photo, for instance).
                # Not a scan failure: Odoo retries the whole request, scan
                # included.
                raise
            except Exception as error:  # noqa: BLE001
                _logger.exception("Receipt scan failed (expense %s)", expense.id)
                expense.write({
                    'scan_state': 'error',
                    'scan_message': str(error)[:250],
                    'scan_todo': False,
                    'expense_scan_todo_codes': False,
                    'expense_scan_hints': False,
                })

    def _expense_scan_source_attachment(self, from_original=True):
        """Attachment to read.

        First pass: the original photo, which has the full resolution; the
        module straightens it. New scan: the current receipt, which the user
        may have retouched or replaced; reading the original again would
        undo that correction.
        """
        self.ensure_one()
        attachment = self.message_main_attachment_id
        if from_original:
            attachment = self._expense_scan_original() or attachment
        if not attachment:
            attachment = self.attachment_ids[:1]
        if not attachment or not self._expense_scan_readable(attachment):
            return self.env['ir.attachment']
        return attachment

    @staticmethod
    def _expense_scan_readable(attachment):
        """Tell whether the engine can read the attachment."""
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        return bool(mimetype.startswith(SUPPORTED_IMAGE_PREFIX)
                    or mimetype == PDF_MIMETYPE or name.endswith('.pdf'))

    @staticmethod
    def _expense_scan_is_pdf(attachment):
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        return mimetype == PDF_MIMETYPE or name.endswith('.pdf')

    def _expense_scan_image_bytes(self, attachment):
        """Usable image bytes, converting a PDF if needed."""
        self.ensure_one()
        if attachment.file_size and attachment.file_size > preprocess.MAX_FILE_BYTES:
            raise UserError(_(
                "The receipt is too large (%(size)d MB, limit %(max)d MB).",
                size=attachment.file_size // (1024 * 1024),
                max=preprocess.MAX_FILE_BYTES // (1024 * 1024)))
        data = attachment.raw
        if not data:
            raise UserError(_("The receipt is empty."))
        if self._expense_scan_is_pdf(attachment):
            converted = preprocess.pdf_first_page_to_image_bytes(data)
            if not converted:
                raise UserError(_(
                    "The PDF could not be converted. Install \"pdf2image\" and "
                    "the \"poppler-utils\" system package to scan PDFs."))
            return converted
        return data

    def _expense_scan_process(self, attachment, progress=None):
        """Preprocess the image, read it, then extract the fields.

        ``progress``: called at the end of each step with its name (see
        _expense_scan_progress_sender).
        """
        self.ensure_one()
        ok, message = preprocess.dependencies_status()
        if not ok:
            raise UserError(message)

        company = self.company_id or self.env.company
        timer = Stopwatch(on_lap=progress)
        data = self._expense_scan_image_bytes(attachment)
        timer.lap('file')

        # Image retouched by hand: read as it is, without automatic cropping,
        # straightening or rotation, and without being replaced.
        manual = (self.expense_scan_manual_retouch
                  and attachment == self.scan_cropped_attachment_id)
        image, info = preprocess.prepare(
            data,
            autocrop=company.expense_scan_autocrop and not manual,
            deskew=company.expense_scan_deskew and not manual,
        )
        timer.lap('prepare')

        engine = engines.resolve_engine(
            company.expense_scan_engine, **company._expense_scan_engine_options())

        # One reading, then straightening from the boxes: the engine
        # straightens each box before recognising it (it reads a line in any
        # direction) and the boxes can be transposed without reading again. A
        # second reading only happens when it brings something (see
        # _expense_scan_straighten).
        words = engine.recognize(image)
        timer.lap('read')
        reread = False
        if not manual:
            image, words, reread = self._expense_scan_straighten(
                engine, image, words, info, company)
        if reread:
            words = engine.recognize(image)
            timer.lap('reread')
        else:
            timer.lap('straighten')
        duration = timer.sum('read', 'reread')

        # Final decision on the orientation, from the boxes of the final
        # reading: more numerous and better placed than those of the first
        # attempt, they correct a badly chosen quarter turn.
        #
        # Exception: the upside-down case (180°) of a PDF. Unlike a photo,
        # whose orientation the phone does not always record, a PDF page is
        # rendered as the document declares it (checked). Deciding anyway, on
        # dense text where the OCR varies from one reading to the next, would
        # sometimes flip a page that does not need it.
        if company.expense_scan_auto_rotate and not manual:
            words, image = self._expense_scan_reorient(
                words, image, info, decide_180=not self._expense_scan_is_pdf(attachment))

        # Second layout pass, guided by the recognised text. Edge detection
        # before OCR fails on a light receipt on a light background; the
        # position of the text is now known. The OCR does not run again: the
        # reading is done, this is about producing the image the user will
        # see to check the fields.
        if not manual:
            image = self._expense_scan_tighten(image, words, info, company)
        timer.lap('crop')

        parse_kwargs = dict(
            max_age_days=company.expense_scan_max_age_days or 730,
            default_currency=company.currency_id.name or 'EUR',
            country=company.country_id.code or None,
            buyers=[name for name in (self.employee_id.name, company.name) if name],
        )
        result = parser.parse(words, **parse_kwargs)
        if self._expense_scan_is_pdf(attachment) and parser.fields_to_check(result, names=('total',)):
            # Fallback: most PDFs give their total on the first page, the
            # only one read so far. A multi-page invoice sometimes carries it
            # at the foot of the last one.
            words = self._expense_scan_extra_pdf_pages(attachment, engine, words, timer)
            result = parser.parse(words, **parse_kwargs)
        result.engine = getattr(engine, 'description', engine.label)
        result.duration = duration
        result.preprocess = info
        # A PDF of several pages stays the receipt as it is: an image of its
        # first page would hide the others, often the one with the total.
        if (info.changed or info.rotated_quarters) and not manual \
                and not self._expense_scan_multipage_pdf(attachment):
            result.image_bytes = preprocess.encode_jpeg(image)
        timer.lap('parse')
        result.timer = timer
        return result

    def _expense_scan_extra_pdf_pages(self, attachment, engine, words, timer):
        """Add the words of the following pages of a PDF to those read.

        Called when the first page gives no total: a multi-page invoice
        often carries it at the foot of the last one. The added pages do not
        get the treatments reserved for the displayed image (straightening,
        cropping): they are not hand-held photos, and they only serve the
        scan, not the preview.
        """
        self.ensure_one()
        data = attachment.raw
        count = preprocess.pdf_page_count(data)
        if count <= 1:
            return words
        # Large vertical offset per page: the words of two pages must not mix
        # into a line of the other.
        step = max((word.bottom for word in words), default=0.0) + 1000.0
        combined = list(words)
        for page in range(2, min(count, PDF_MAX_PAGES_READ) + 1):
            page_bytes = preprocess.pdf_page_to_image_bytes(data, page)
            if not page_bytes:
                continue
            try:
                page_image = preprocess.load_image(page_bytes)
            except Exception:  # noqa: BLE001 (an unreadable page does not stop the others)
                _logger.warning("PDF page %s unreadable", page, exc_info=True)
                continue
            page_words = engine.recognize(page_image)
            combined.extend(preprocess.shift_words(page_words, step * (page - 1)))
        timer.lap('extra_pages')
        return combined

    #: Median word height (engine working pixels) below which reading gets
    #: worse: receipt photographed from far away, full A4 page of dense text.
    SMALL_TEXT_PX = 20
    #: Minimum text enlargement that justifies reading the cropped receipt
    #: again: below it, the second reading costs as much as the first for a
    #: negligible gain.
    REREAD_MIN_GAIN = 1.3
    #: Tilt (degrees) beyond which the straightened image is read again: the
    #: boxes of the first reading are upright and, on slanted text, they
    #: overlap from one line to the next, which blurs their grouping.
    REREAD_ANGLE = 4.0

    def _expense_scan_straighten(self, engine, image, words, info, company):
        """Straighten the receipt from the boxes of the first reading.

        Any orientation comes down to a quarter turn plus a remainder within
        ±45°. The quarter turn is deduced from the **shape of the boxes**,
        not from the reading quality: the engine straightens each box before
        recognising it, so it reads in all four directions. The image and the
        boxes turn together, without reading again.

        Upside down (180°) is not decided here (see _expense_scan_quarters):
        it is decided once, later.

        Returns ``(image, words, reread)``. Reading again only happens in two
        cases: a clearly slanted receipt, or text too small in the whole
        photo that cropping would enlarge. As the engine scales the image
        down to its working size, that budget is better spent on the receipt
        than on the table. Everything else reads correctly the first time:
        the former trial reading doubled the scan time for nothing.
        """
        if not company.expense_scan_auto_rotate:
            return image, words, False

        quarters = self._expense_scan_quarters(words, image, decide_180=False)
        if quarters:
            words = preprocess.rotate_words_quarters(
                words, quarters, image.shape[1], image.shape[0])
            image = preprocess.rotate_quarters(image, quarters)
            info.rotated_quarters = quarters

        angle = 0.0
        if company.expense_scan_deskew:
            angle = preprocess.skew_angle_from_words(words)
            if preprocess.MIN_DESKEW_ANGLE < abs(angle) <= preprocess.MAX_TEXT_DESKEW_ANGLE:
                matrix, _size = preprocess.rotation_matrix(
                    (image.shape[1], image.shape[0]), angle)
                words = preprocess.rotate_words(words, matrix, angle)
                image = preprocess.rotate(image, angle)
                info.deskew_angle = angle
            else:
                angle = 0.0

        # A turned receipt is always read again: the engine reads a vertical
        # line badly ("aiement P" for "Paiement"), even when it finds its
        # position correctly. The boxes are enough to choose the quarter
        # turn, not to read the text.
        reread = bool(quarters) or abs(angle) > self.REREAD_ANGLE
        if company.expense_scan_autocrop:
            # On the boxes kept for the angle: a character of the background
            # would stretch the frame far beyond the receipt.
            inliers = preprocess.text_inliers(words)
            small = self._expense_scan_text_px(engine, image, words) < self.SMALL_TEXT_PX
            if reread or (small and self._expense_scan_crop_gain(
                    engine, image, inliers) >= self.REREAD_MIN_GAIN):
                # Wide margin: a word missed at the edge of the receipt will
                # come back on the second reading.
                image, cropped = preprocess.crop_to_text(image, inliers, margin_ratio=0.08)
                info.cropped = info.cropped or cropped
                reread = True

        info.changed = info.changed or bool(
            info.rotated_quarters or info.deskew_angle or info.cropped)
        info.reread = reread
        return image, words, reread

    @staticmethod
    def _expense_scan_working_scale(engine, width, height):
        """Scale factor the engine applies to this image."""
        side = engine.working_side
        longest = max(width, height)
        return min(1.0, side / float(longest)) if side and longest else 1.0

    def _expense_scan_text_px(self, engine, image, words):
        """Median height of the words, as the engine saw them."""
        heights = sorted(word.bottom - word.top for word in words if word.text.strip())
        if not heights:
            return 0.0
        scale = self._expense_scan_working_scale(engine, image.shape[1], image.shape[0])
        return heights[len(heights) // 2] * scale

    def _expense_scan_crop_gain(self, engine, image, words):
        """Text enlargement that a cropped second reading would bring."""
        boxes = [word for word in words if word.text.strip()]
        if not boxes:
            return 1.0
        width = max(word.right for word in boxes) - min(word.left for word in boxes)
        height = max(word.bottom for word in boxes) - min(word.top for word in boxes)
        before = self._expense_scan_working_scale(engine, image.shape[1], image.shape[0])
        after = self._expense_scan_working_scale(engine, width * 1.16, height * 1.16)
        return after / before if before else 1.0

    def _expense_scan_reorient(self, words, image, info, decide_180=True):
        """Apply the missing quarter turn, without reading the image again.

        The only place that decides upside down (``decide_180``): on the
        boxes of the final reading, far more numerous and better placed than
        those of the first attempt. A single decision, not cross-checked
        with an earlier one (see _expense_scan_quarters).
        """
        quarters = self._expense_scan_quarters(words, image, decide_180=decide_180)
        if not quarters:
            return words, image
        height, width = image.shape[:2]
        info.rotated_quarters = (info.rotated_quarters + quarters) % 4
        info.changed = True
        return (preprocess.rotate_words_quarters(words, quarters, width, height),
                preprocess.rotate_quarters(image, quarters))

    def _expense_scan_quarters(self, words, image, decide_180=True):
        """Quarter turn to apply, deduced from the box shapes alone.

        Two criteria, in this order. **Line direction** first, given by the
        detector for each box: it separates even from odd quarters, as a
        standing receipt does not have the same direction as a lying one.
        **Reading direction** next, to tell right side up from upside down:
        an amount follows its label, the merchant is at the top, the payment
        at the bottom.

        The image is not read again: the boxes are transposed and their text
        is already right, the engine straightening each box before reading
        it. The recognition quality says nothing about the orientation; this
        method replaced four readings that could not decide.

        ``decide_180=False``: only corrects the first criterion (standing
        becomes lying) and leaves the second to a later call, on better
        boxes. Flipping upside down brings nothing before the final reading
        (a lying line reads as well either way). Ignoring it here prevents a
        decision taken on the first attempt (low resolution, where a dense
        page reads badly) from being "confirmed" by a second decision on the
        same clues, just as misleading once the page has been turned by the
        first.
        """
        if not words:
            return 0
        height, width = image.shape[:2]
        candidates = []
        for quarters in range(4):
            turned = preprocess.rotate_words_quarters(words, quarters, width, height)
            if preprocess.horizontal_text_score(turned) <= 0:
                continue  # standing lines: not this quarter
            candidates.append((quarters, turned))

        if not decide_180 and any(quarters == 0 for quarters, _turned in candidates):
            # Already lying in its original orientation: nothing to do,
            # upside down will be decided once, later.
            return 0

        # Final reading direction of each lying candidate: -1 (upside down),
        # 0 (no opinion), 1 (right side up).
        verdicts = [(quarters, parser.reading_direction(parser.build_lines(turned)))
                    for quarters, turned in candidates]
        chosen = self._expense_scan_pick_quarter(verdicts)
        # One line per scan: enough to replay a disputed orientation decision
        # without scanning again in debug mode.
        _logger.info("expense_scan: orientation (decide_180=%s) candidates=%s chosen=%s",
                     decide_180, verdicts, chosen)
        return chosen

    @api.model
    def _expense_scan_pick_quarter(self, verdicts):
        """Choose the quarter from the reading direction of each candidate.

        ``verdicts``: ``[(quarter, -1|0|1), ...]``, in the order tried (0
        first). A clear opinion (+1) wins at once. Otherwise the first
        quarter without a clear doubt (-1) is kept. The first of the list is
        not taken without looking at its own opinion: a receipt whose
        direction is only half readable (a toll, for instance) must not be
        flipped on a barely negative balance when the original orientation
        raises no doubt.
        """
        for quarters, verdict in verdicts:
            if verdict > 0:
                return quarters
        neutral_or_better = [quarters for quarters, verdict in verdicts if verdict >= 0]
        if neutral_or_better:
            return neutral_or_better[0]
        return verdicts[0][0] if verdicts else 0

    def _expense_scan_tighten(self, image, words, info, company):
        """Straighten, then crop, once the OCR is done.

        The order matters: the rotation enlarges the frame so as not to cut
        anything, so cropping before it leaves margins. Straightening comes
        first (the word boxes follow), then cropping on their new position.
        """
        if company.expense_scan_deskew:
            # The tilt is measured on the recognised text lines, not on the
            # image: the paper edges are straighter lines than the print, and
            # rarely parallel to it. The detector's box angle first; the
            # regression on words is a fallback for an engine like Tesseract,
            # which only returns upright rectangles.
            angle = preprocess.skew_angle_from_words(words) \
                or preprocess.skew_angle_from_lines(parser.build_lines(words))
            if preprocess.MIN_DESKEW_ANGLE < abs(angle) <= preprocess.MAX_DESKEW_ANGLE:
                matrix, _size = preprocess.rotation_matrix(
                    (image.shape[1], image.shape[0]), angle)
                image = preprocess.rotate(image, angle)
                words = preprocess.rotate_words(words, matrix, angle)
                info.deskew_angle += angle
                info.changed = True

        if company.expense_scan_autocrop:
            image, tightened = preprocess.crop_to_text(
                image, preprocess.text_inliers(words))
            if tightened:
                info.cropped = True
                info.changed = True

        info.final_size = (image.shape[1], image.shape[0])
        return image

    # ------------------------------------------------------------------
    # Writing the result on the expense
    # ------------------------------------------------------------------

    #: Fields deduced from the project, restricted to the Sales and Analytic
    #: groups. Written separately, with elevated rights.
    REINVOICE_FIELDS = ('project_id', 'reinvoice_mode',
                        'analytic_distribution', 'sale_order_id', 'expense_scan_task_id')

    def _expense_scan_apply(self, result, attachment):
        """Write the fields read and prepare the review message."""
        self.ensure_one()
        company = self.company_id or self.env.company
        foreign = self._expense_scan_foreign_tax(result, company)
        values = self._expense_scan_field_values(result, company, foreign=foreign)
        notes = values.pop('_expense_scan_notes', [])
        if result.timer:
            result.timer.lap('category')

        # (text, code) per point to check. The code, kept apart from the
        # displayed text, tells whether the point is still open (see
        # TODO_RESOLVED_WHEN and _compute_expense_scan_todo_pending). "static"
        # marks the points that cannot be reopened automatically: they stay
        # shown until the next scan, as before this mechanism.
        #
        # Each point also carries its help sentence, shown under the field
        # concerned (see _compute_expense_scan_field_hints): (short label,
        # code, sentence).
        items = []
        for name, found, missing in (
                ('date', _("Date hard to read on the receipt: check it."),
                 _("Date not found on the receipt: enter it.")),
                ('total', _("Amount hard to read on the receipt: check it."),
                 _("Amount not found on the receipt: enter it."))):
            if parser.fields_to_check(result, names=(name,)):
                label = _("Date") if name == 'date' else _("Total")
                items.append((label, name, missing if result.value(name) is None else found))
        if result.value('date') is None and result.value('total') is None:
            # A logo, a photo of something else: one clear point rather than
            # two, and the expense does not look like a receipt read at 0.
            items = [(_("Receipt?"), 'total',
                      _("Neither amount nor date found: check that this image is "
                        "a receipt, or enter the expense by hand."))]
        if not values.get('expense_scan_guessed_product_id') \
                and self._expense_scan_category_is_free(company):
            # Nothing certain on the receipt: the default category is only a
            # starting point.
            items.append((_("Category"), 'category',
                          _("Category not recognised on the receipt: choose it.")))
        code = result.value('currency')
        if code and code != company.currency_id.name and not values.get('currency_id') \
                and result.confidence('currency') >= 0.5:
            # Receipt in zlotys, currency inactive: the amount would be counted
            # in the company currency without warning.
            items.append((_("Currency (%s to activate in Odoo)", code), 'currency',
                          _("Receipt in %s: activate this currency in Odoo, "
                            "otherwise the amount counts in the company currency.", code)))
        elif values.get('currency_id') and self._expense_scan_missing_rate(values['currency_id']):
            # Odoo counts one for one: 40 USD would be reimbursed 40 EUR.
            code = self.env['res.currency'].browse(values['currency_id']).name
            items.append((_("Currency (%s without an exchange rate)", code), 'currency_rate',
                          _("No exchange rate for %s in Odoo: the amount counts one "
                            "for one in the company currency until a rate is entered.",
                            code)))
        if company.expense_scan_reinvoice and not values.get('project_id'):
            # No project covers this date, or several do: the employee
            # decides (including to say that the expense is not re-invoiced).
            # Resolved as soon as they choose.
            items.append((_("Re-invoice"), 'reinvoice',
                          _("No project found for this date: "
                            "say whether this expense is re-invoiced.")))
        check_category_tax = False
        target = self._expense_scan_target_product(values)
        if company.expense_scan_apply_tax and not self._expense_scan_product_no_vat(target):
            if foreign:
                items.append((_("Foreign tax (%s), not deducted", foreign), 'tax_amount',
                              _("Foreign tax (%s): it cannot be deducted on your "
                                "tax return, leave it at zero.", foreign)))
            elif not result.value('tax_amount'):
                items.append((_("Tax (none on the receipt)"), 'tax_amount',
                              _("No tax read on the receipt: enter it if it is printed.")))
            elif not (values.get('total_amount_currency') or self.total_amount_currency):
                read = self._expense_scan_number(result.value('tax_amount'))
                items.append((_("Tax (%s read, to carry with the total)", read), 'tax_total',
                              _("Tax of %s read: enter the total to carry it.", read)))
            elif not values.get('scan_tax_amount'):
                # Reading dismissed because it is above the rate ceiling:
                # usually another amount read instead of the tax.
                items.append((_("Tax (reading inconsistent with the rate)"), 'tax_amount',
                              _("The tax read does not match the rate: "
                                "enter the amount printed on the receipt.")))
            else:
                # The tax read can only be posted with a tax carrying tax
                # tags: checked after writing, since the recognised category
                # may bring one.
                check_category_tax = True
        # A field entered by hand before the scan only needs checking when it
        # differs from the receipt.
        keep = self._expense_scan_kept_fields()
        items = self._expense_scan_kept_differences(result, keep) + [
            item for item in items if self.TODO_FIELDS.get(item[1]) not in keep]
        values.update({
            'scan_state': 'partial' if items else 'done',
            'scan_engine': result.engine,
            'scan_duration': result.duration,
            'scan_score': round(result.mean_score * 100.0, 1),
            'scan_raw_text': result.raw_text,
            **self._expense_scan_todo_values(items),
            'scan_message': self._expense_scan_summary(result),
            'scan_detected_tax': self._expense_scan_tax_label(result),
            'expense_scan_mixed_rates': result.value('tax_rate') is None
                                        and result.value('tax_rate_max') is not None,
        })
        values.update(self._expense_scan_store_image(result, attachment, company))
        if result.timer:
            result.timer.lap('image')

        # The project fields are written with elevated rights, as they are
        # read: they belong to the Sales and Analytic groups, which the
        # employee photographing their receipt is not part of. The module
        # deduces them (the user does not choose them) and they are set on
        # the employee's own draft expense.
        reinvoice_values = {name: values.pop(name)
                            for name in self.REINVOICE_FIELDS if name in values}
        self.write(values)
        if reinvoice_values:
            self.sudo().write(reinvoice_values)
        if result.timer:
            result.timer.lap('save')
        after = {}
        if check_category_tax and not self.tax_ids:
            # Resolved as soon as a tax is set, by hand or by a change of
            # category that brings one.
            items.append((_("Tax (none on the category)"), 'tax_category',
                          _("This category has no tax: choose the rate so that "
                            "the tax read is deducted.")))
            after.update({'scan_state': 'partial', **self._expense_scan_todo_values(items)})
        # Values set by the scan, to recognise a correction later.
        after['expense_scan_read_values'] = json.dumps({
            name: self._expense_scan_comparable(name) for name in self.READ_VALUE_FIELDS})
        after['expense_scan_manual_fields'] = ','.join(sorted(keep)) or False
        self.write(after)
        if not company.expense_scan_apply_tax and 'scan_tax_amount' not in keep:
            # The tax read is not used: the field shows the tax the rate
            # gives, which the entry will carry, rather than an empty 0.00.
            self._onchange_expense_scan_taxes()
        for note in notes:
            self.message_post(body=note, message_type='comment', subtype_xmlid='mail.mt_note')
        if result.timer:
            result.timer.lap('write')
            _logger.info("expense_scan: timings expense=%s %s", self.id, result.timer)

    def _expense_scan_missing_rate(self, currency_id=None):
        """Tell whether the expense currency has no exchange rate in Odoo.

        Odoo then converts one for one, without a warning.
        """
        self.ensure_one()
        currency = self.env['res.currency'].browse(currency_id) if currency_id \
            else self.currency_id
        company = self.company_id or self.env.company
        if not currency or currency == company.currency_id:
            return False
        return not self.env['res.currency.rate'].sudo().search_count([
            ('currency_id', '=', currency.id),
            '|', ('company_id', '=', False), ('company_id', 'parent_of', company.id),
        ], limit=1)

    def _expense_scan_kept_differences(self, result, keep):
        """Points to check: date or amount entered that differ from the receipt."""
        items = []
        date = result.value('date')
        if 'date' in keep and date and date != self.date:
            shown = format_date(self.env, date)
            items.append((_("Date (%s on the receipt)", shown), 'date',
                          _("The receipt is dated %s: check the date entered.", shown)))
        total = result.value('total')
        if 'total_amount_currency' in keep and total \
                and self.currency_id.compare_amounts(total, self.total_amount_currency):
            shown = formatLang(self.env, total, currency_obj=self.currency_id)
            items.append((_("Amount (%s on the receipt)", shown), 'total',
                          _("The receipt says %s: check the amount entered.", shown)))
        return items

    @staticmethod
    def _expense_scan_todo_values(items):
        """Fields describing the points to check: labels, codes, sentences."""
        return {
            'scan_todo': ", ".join(text for text, _code, _hint in items) or False,
            'expense_scan_todo_codes': ",".join(code for _text, code, _hint in items) or False,
            'expense_scan_hints': json.dumps({code: hint for _text, code, hint in items})
            if items else False,
        }

    def _expense_scan_foreign_tax(self, result, company):
        """Name of the tax when the receipt comes from abroad, ``False`` otherwise.

        VAT paid abroad is not deducted on the domestic return: it is
        reclaimed, if at all, from the country concerned. Carrying it as
        deductible VAT would make the accounts wrong.

        Three clues: a tax not called VAT (IVA, MwSt, PTU...), a foreign
        currency, or a rate no tax of the company knows (Belgian VAT at 21%,
        printed "TVA", for instance).
        """
        label = result.value('tax_label')
        if not (label or result.value('tax_amount') or result.value('tax_rate_max')):
            return False
        country = (company.account_fiscal_country_id or company.country_id).code
        domestic = DOMESTIC_TAX_LABELS.get(country)
        # Unknown country: the name proves nothing, the other clues decide.
        if label and domestic is not None and label not in domestic | {'VAT'}:
            return label
        code = result.value('currency')
        if code and company.currency_id and code != company.currency_id.name \
                and result.confidence('currency') >= 0.5:
            return label or _("VAT")
        rate = result.value('tax_rate_max')
        if rate is not None:
            known = self.env['account.tax'].search([
                ('company_id', '=', company.id),
                ('type_tax_use', '=', 'purchase'),
                ('amount_type', '=', 'percent'),
            ]).mapped('amount')
            if known and not any(abs(amount - rate) < 0.01 for amount in known):
                return _("%(label)s %(rate)s %%", label=label or _("VAT"), rate=rate)
        return False

    def _expense_scan_automatic_labels(self):
        """Labels of an automatic description, in lower case.

        Category names and "Receipt", in every installed language: a scan
        run without a language (scheduled task) may have written "Meals on
        23/09/2026". Computed once for all the expenses examined: looking
        for a trip goes through dozens of them.
        """
        labels = set()
        Product = self.env['product.product'].sudo().with_context(active_test=False)
        for code, _name in self.env['res.lang'].get_installed():
            labels.add(self.with_context(lang=code).env._("Receipt").lower())
            products = Product.with_context(lang=code).search([('can_be_expensed', '=', True)])
            labels.update(name.lower() for name in products.mapped('name') if name)
        return labels

    def _expense_scan_name_is_automatic(self, labels=None):
        """Tell whether the description is still the one set by Odoo or the scan.

        Employees write the project there ("Setup at Acme"), which a new scan
        must not overwrite. ``labels``: result of
        ``_expense_scan_automatic_labels``, computed on demand.
        """
        self.ensure_one()
        name = (self.name or '').strip()
        if not name:
            return True
        untitled = self._get_untitled_expense_name('').strip()
        if name.startswith(untitled) \
                or name == (self.product_id.display_name or '') \
                or name == (self.expense_scan_merchant or ''):
            return True
        # "Receipt on 12/09/2026", "Toll on 12/09/2026": the name of any
        # category (it may have changed since), followed by a date. Written
        # in the employee's language, which may not be the current one.
        match = next(filter(None, (re.fullmatch(pattern, name)
                                   for pattern in self._expense_scan_date_name_patterns())), None)
        if not match:
            return False
        # Category name in any installed language. When it was not
        # recognised, an automatic description in English passed for the
        # purpose of a trip and spread to the neighbouring expenses.
        if labels is None:
            labels = self._expense_scan_automatic_labels()
        return match.group('label').strip().lower() in labels

    def _expense_scan_date_name_patterns(self):
        """Patterns of an automatic description, one per installed language."""
        patterns = []
        for code, _name in self.env['res.lang'].get_installed():
            text = self.with_context(lang=code)._expense_scan_date_name('XCATEGORYX', 'XDATEX')
            pattern = re.escape(text).replace('XCATEGORYX', r'(?P<label>.+?)')
            pattern = pattern.replace('XDATEX', r'.*\d.*')
            if pattern not in patterns:
                patterns.append(pattern)
        return patterns

    def _expense_scan_home_places(self):
        """Places unrelated to a trip: the company, the employee's home.

        The company name and address appear at the foot of every invoice
        addressed to it: its postal code would link all the expenses.
        """
        self.ensure_one()
        employee = self.employee_id.sudo()
        partners = [self.company_id.partner_id]
        zips = {self.company_id.zip}
        cities = {self.company_id.city}
        if 'private_zip' in employee._fields:
            zips.add(employee.private_zip)
            cities.add(employee.private_city)
        zips.update(partner.zip for partner in partners)
        cities.update(partner.city for partner in partners)
        home_cities = set()
        for city in filter(None, cities):
            home_cities.update(parser.normalize(city).replace("-", " ").split())
        return ({"cp:%s" % zip_code.strip() for zip_code in zips if zip_code},
                {city for city in home_cities if len(city) >= 4})

    def _expense_scan_trip_neighbour(self, scan_date, lines):
        """Neighbouring expense of the same trip, whose description to take up.

        All the expenses of a trip share its purpose ("Sales visit Acme").
        Part of the same trip, within a few days:

        * an expense of the same day;
        * an expense of the same place: same postal code, same toll station
          (outward and return), or a city of one cited by the other (ticket
          "Lille to Bordeaux", hotel in Bordeaux, toll "Exit Bordeaux");
        * a day framed by two expenses with the same purpose.

        Each expense that takes up the purpose serves in turn as a relay: a
        long trip is covered step by step. The city of the company and that
        of the employee's home link nothing: they appear on every receipt.
        Nor does an expense whose date was not read on its receipt: its date
        is only the day it was entered.
        """
        self.ensure_one()
        if not (scan_date and self.employee_id):
            return self.browse()
        neighbours = self.sudo().search([
            ('id', '!=', self._origin.id or 0),
            ('employee_id', '=', self.employee_id.id),
            ('date', '>=', scan_date - timedelta(days=TRIP_DAYS)),
            ('date', '<=', scan_date + timedelta(days=TRIP_DAYS)),
            ('approval_state', '!=', 'refused'),
        ])
        labels = self._expense_scan_automatic_labels()
        neighbours = neighbours.filtered(
            lambda e: (e.scan_state == 'none' or e.scan_datetime)
            and not e._expense_scan_name_is_automatic(labels))
        if not neighbours:
            return self.browse()

        def most_common(expenses):
            # Most frequent purpose; on a tie, the closest in date.
            counts = Counter(expenses.mapped('name'))
            return max(expenses, key=lambda e: (
                counts[e.name], -abs((e.date - scan_date).days), e.id))

        same_day = neighbours.filtered(lambda e: e.date == scan_date)
        if same_day:
            return most_common(same_day)

        home_places, home_cities = self._expense_scan_home_places()
        places, cities = parser.trip_places(lines)
        places -= home_places
        cities -= home_cities

        def same_place(expense):
            other_lines = (expense.scan_raw_text or '').splitlines()
            other_places, other_cities = parser.trip_places(other_lines)
            return bool(
                places & (other_places - home_places)
                or any(parser.cites_city(city, other_lines) for city in cities)
                or any(parser.cites_city(city, lines) for city in other_cities - home_cities))

        linked = neighbours.filtered(same_place)
        if linked:
            return most_common(linked)

        before = set(neighbours.filtered(lambda e: e.date < scan_date).mapped('name'))
        after = set(neighbours.filtered(lambda e: e.date > scan_date).mapped('name'))
        between = neighbours.filtered(lambda e: e.name in before & after)
        return most_common(between) if between else self.browse()

    def _expense_scan_date_name(self, label, date_text):
        """"Toll on 12/09/2026": the category, then the date of the receipt."""
        return self.env._("%(category)s on %(date)s", category=label, date=date_text)

    def _expense_scan_auto_name(self, product, scan_date):
        """Description set automatically: the recognised category, otherwise "Receipt".

        Written in the employee's language: the scan may run without a
        language (scheduled task).
        """
        lang = self.employee_id.user_id.lang or self.env.user.lang or self.env.lang
        expense = self.with_context(lang=lang)
        company_default = (self.company_id or self.env.company)._expense_scan_default_product()
        label = (product.with_context(lang=lang).name
                 if product and product != company_default else expense.env._("Receipt"))
        return expense._expense_scan_date_name(label, format_date(expense.env, scan_date))

    def _expense_scan_field_values(self, result, company, foreign=None):
        """Turn the parser result into Odoo field values."""
        values = {}
        keep = self._expense_scan_kept_fields()
        if foreign is None:
            foreign = self._expense_scan_foreign_tax(result, company)

        # Category first: its tax is the fallback for checking the VAT.
        values.update(self._expense_scan_category_values(result, company))
        guessed = self.env['product.product'].browse(
            values.get('expense_scan_guessed_product_id') or [])

        scan_date = result.value('date')
        if scan_date and 'date' not in keep:
            values['date'] = scan_date
        # Date of the expense: one entered by hand wins over the receipt's for
        # the description and the project search.
        expense_date = self.date if 'date' in keep and self.date else scan_date

        scan_time = result.value('time')
        if scan_time:
            values['scan_time'] = scan_time.strftime('%H:%M')
        if scan_date:
            values['scan_datetime'] = self._expense_scan_moment(scan_date, scan_time)

        # The description belongs to the employee (most often the purpose of
        # the trip). The merchant has its own field; the description gets at
        # most the date of the receipt, as long as nobody wrote in it. Once
        # the category is no longer the company's generic one, it names the
        # expense: "Toll on 12/09/2026".
        if expense_date and not self.expense_scan_keep_name \
                and self._expense_scan_name_is_automatic():
            # The purpose of the same trip, already written elsewhere, wins
            # over the automatic description. A date not read on the receipt
            # places the expense on no trip.
            neighbour = self._expense_scan_trip_neighbour(
                expense_date if 'date' in keep or scan_date else None,
                [line.text for line in result.lines])
            values['name'] = neighbour.name or self._expense_scan_auto_name(
                self._expense_scan_target_product(values), expense_date)
            if neighbour:
                values['_expense_scan_notes'] = [_(
                    "Description taken from the expense of the same trip: %s",
                    neighbour._get_html_link(title=neighbour.name))]

        nights = result.value('nights')
        if nights and self._expense_scan_target_product(values).expense_scan_nights_required \
                and 'expense_scan_nights' not in keep:
            values['expense_scan_nights'] = nights

        currency = self._expense_scan_currency(result, company)
        if currency and 'currency_id' not in keep:
            if not currency.active:
                # Left in the company currency, 12.50 EUR would count as
                # 12.50 USD. The currency is activated; a missing exchange
                # rate is flagged as a point to check.
                currency.sudo().active = True
                values.setdefault('_expense_scan_notes', []).append(_(
                    "Currency %s activated in Odoo: the receipt is in this currency.",
                    currency.name))
                if company.expense_scan_ecb_rates:
                    # Its rates come with the next run of the task, started
                    # now; the scan itself makes no network request.
                    cron = self.env.ref('expense_scan.ir_cron_expense_scan_ecb_rates',
                                        raise_if_not_found=False)
                    if cron:
                        cron.sudo()._trigger()
            values['currency_id'] = currency.id

        total = result.value('total')
        if total and 'total_amount_currency' not in keep:
            # Computed but editable field: the entry point Odoo provides for
            # an amount entered as is, tax included.
            values['total_amount_currency'] = total

        no_vat = self._expense_scan_product_no_vat(self._expense_scan_target_product(values))
        if company.expense_scan_apply_tax and (foreign or no_vat):
            # Foreign VAT, or a category without recoverable VAT (hotel,
            # passenger transport): no tax, no deductible amount.
            values['tax_ids'] = [Command.clear()]
            values['scan_tax_amount'] = 0.0
        elif company.expense_scan_apply_tax:
            # The rate read on the receipt wins over the category's: a meal at
            # 10% must not be declared at 20% because the generic category
            # says so. Condition: a single tax matches this rate; on a crowded
            # chart of accounts, choosing between goods and services is the
            # accountant's call.
            category = self._expense_scan_target_product(values)
            tax = self._expense_scan_tax(result.value('tax_rate'), company, category)
            if not tax and result.value('tax_rate_max'):
                # Several rates on the same receipt (meal at 5.5% and 10%):
                # none holds for the whole expense, but a tax must be set.
                # The highest is kept (the cautious choice for deductible
                # VAT; the accountant can correct it).
                tax = self._expense_scan_tax(result.value('tax_rate_max'), company, category)
            if tax:
                values['tax_ids'] = [Command.set(tax.ids)]
                effective_rate = tax.amount
            else:
                # Rate not found in the chart of accounts: the expense keeps
                # the tax of its category, which carries the entry. To judge
                # the VAT read, the receipt's ceiling is better than the
                # category's: a meal at 10% + 20% exceeds the ceiling of a
                # 10% category without being wrong.
                category_rates = (guessed.supplier_taxes_id.filtered(
                    lambda t: t.company_id == company and t.amount_type == 'percent'
                ).mapped('amount') if guessed else None)
                effective_rate = (result.value('tax_rate_max')
                                  or (max(category_rates) if category_rates else None)
                                  or self._expense_scan_max_rate())

            tax_amount = result.value('tax_amount')
            total_known = values.get('total_amount_currency') or self.total_amount_currency
            # A misread VAT must not make the whole scan fail on the module's
            # constraint: it is dismissed and reported rather than written
            # with a value that cannot be saved.
            if tax_amount and not total_known:
                # Unreadable total: no ceiling can judge the VAT, and
                # comparing it to a zero total would call it "inconsistent".
                # Nothing is changed; the employee enters both.
                pass
            elif tax_amount and self._expense_scan_tax_fits(
                    tax_amount, values.get('total_amount_currency'), effective_rate):
                values['scan_tax_amount'] = tax_amount
            else:
                # No usable VAT: the receipt carries none (card slip) or the
                # reading is inconsistent. In both cases, keeping the
                # category's tax would show deductible VAT computed from a
                # rate that nothing on the receipt supports. No readable VAT:
                # zero.
                values['tax_ids'] = [Command.clear()]
                values['scan_tax_amount'] = 0.0

        for name in ('tax_ids', 'scan_tax_amount'):
            if name in keep:
                values.pop(name, None)

        if company.expense_scan_reinvoice and not keep & set(self.REINVOICE_FIELDS):
            # The project is looked up at the date of the receipt, not of the
            # entry: an expense scanned on Monday may date from Friday, on
            # another project.
            # "No" is the employee's decision: the project found then only
            # serves budget tracking, without re-invoicing.
            values.update(self._expense_scan_project_values(
                self._expense_scan_find_project(expense_date),
                reinvoice=self.reinvoice_mode != 'none'))

        if company.expense_scan_set_vendor and 'vendor_id' not in keep:
            # The merchant recognised from history is better spelt than the
            # raw reading.
            known = values.get('expense_scan_merchant') \
                if values.get('expense_scan_merchant') != result.value('merchant') else None
            vendor = self._expense_scan_vendor(result, company, merchant=known)
            if vendor:
                values['vendor_id'] = vendor.id

        return values

    def _expense_scan_moment(self, scan_date, scan_time):
        """Time stamp of the receipt, converted to UTC as Odoo stores it.

        The printed time is a local time: storing it as is would shift the
        display by two hours in summer.
        """
        moment = datetime.combine(scan_date, scan_time or dtime(0, 0))
        zone = self.env.user.tz or self.env.context.get('tz')
        if not zone:
            return moment
        try:
            return timezone(zone).localize(moment).astimezone(utc).replace(tzinfo=None)
        except Exception:  # noqa: BLE001 - time zone unknown to the server
            _logger.warning("Unusable time zone: %s", zone)
            return moment

    def _expense_scan_tax(self, rate, company, category=None):
        """Purchase tax at the rate read.

        Most charts of accounts hold several taxes at one rate: goods and
        services, purchases from another EU country (Sweden: "12% G",
        "12% S", "12% EU G"...). The category's own tax comes first;
        otherwise the first ordinary tax of the chart. A reverse-charge tax,
        whose tax lines cancel out, never applies to a receipt; a tax
        included in the price only when no other is left.
        """
        if rate is None:
            return self.env['account.tax']
        taxes = self.env['account.tax'].search([
            ('company_id', '=', company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', rate),
        ], order='sequence, id')
        if len(taxes) <= 1:
            return taxes
        own = taxes & (category.supplier_taxes_id if category else taxes.browse())
        if own:
            return own[:1]

        def ordinary(tax):
            lines = tax.invoice_repartition_line_ids.filtered(
                lambda line: line.repartition_type == 'tax')
            return abs(sum(lines.mapped('factor_percent')) - 100.0) < 0.01

        plain = taxes.filtered(ordinary)
        return ((plain.filtered(lambda tax: not tax.price_include) or plain) or taxes)[:1]

    def _expense_scan_tax_fits(self, amount, total=None, rate=None):
        """Tell whether the VAT read fits under the ceiling of the expense rate.

        The total and the rate are passed as arguments: when the decision is
        taken, neither is written on the expense yet, and a ceiling computed
        on the old values would be wrong.
        """
        self.ensure_one()
        if rate is None:
            rate = self._expense_scan_max_rate()
        if rate is None:
            return True  # no rate: blocked at validation, not here
        total = self.total_amount_currency if total is None else total
        # Tills round the tax line by line: 9.41 printed for a ceiling of
        # 9.40 is not a misreading.
        ceiling = total * rate / (100.0 + rate) + TAX_ROUNDING_MARGIN
        return self.currency_id.compare_amounts(amount, ceiling) <= 0

    def _expense_scan_currency(self, result, company):
        """Currency matching the code read, active or not."""
        code = result.value('currency')
        if not code or result.confidence('currency') < 0.5:
            return self.env['res.currency']
        if company.currency_id.name == code:
            return self.env['res.currency']  # already the default currency
        return self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', code)], limit=1)

    def _expense_scan_vendor(self, result, company, merchant=None):
        """Vendor contact whose name matches the merchant read."""
        if not merchant:
            merchant = result.value('merchant')
            if not merchant or result.confidence('merchant') < parser.LOW_CONFIDENCE:
                return self.env['res.partner']
        partners = self.env['res.partner'].search([
            ('name', '=ilike', merchant),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], limit=2)
        return partners if len(partners) == 1 else self.env['res.partner']

    def _expense_scan_store_image(self, result, attachment, company):
        """Attach the cropped image and make it the main preview."""
        self.ensure_one()
        if not result.image_bytes:
            return {}

        if not company.expense_scan_keep_original:
            attachment.write({
                'raw': result.image_bytes,
                'mimetype': 'image/jpeg',
            })
            return {}

        # New scan of the current receipt: the image read is already the one
        # the form displays. It is corrected in place: creating another one
        # would make it the "original photo" instead of the real one, which
        # would be lost for good.
        if attachment == self.scan_cropped_attachment_id:
            attachment.write({'raw': result.image_bytes, 'mimetype': 'image/jpeg'})
            return {}

        # The original photo of a receipt deleted since has no reason to
        # stay: ``attachment`` becomes the original.
        if self.scan_original_attachment_id != attachment:
            self._expense_scan_forget_original()

        # A new scan produces a new image: it replaces the previous one
        # instead of adding attachments to the expense. The original photo
        # is kept (see ir_attachment).
        previous = self.scan_cropped_attachment_id
        if previous and previous != attachment:
            previous.with_context(expense_scan_keep_original=True).unlink()

        stem = os.path.splitext(attachment.name or 'ticket')[0]
        cropped = self.env['ir.attachment'].create({
            'name': _("%s (cropped).jpg", stem),
            'raw': result.image_bytes,
            'mimetype': 'image/jpeg',
            'res_model': 'hr.expense',
            'res_id': self.id,
        })
        # The form preview follows the main attachment: the user must see the
        # straightened receipt to check the fields, not the crooked photo.
        self.sudo()._message_set_main_attachment_id(cropped, force=True)

        # The original photo becomes a field attachment: Odoo leaves those
        # out of its searches, so it disappears from the list of receipts and
        # is no longer copied onto the journal entry. It stays reachable in
        # full through the "Original photo" field, which points to it by id.
        attachment.sudo().write({'res_field': 'scan_original_attachment_id'})
        return {
            'scan_original_attachment_id': attachment.id,
            'scan_cropped_attachment_id': cropped.id,
            'expense_scan_manual_retouch': False,
        }

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------

    def _expense_scan_summary(self, result):
        """Information line shown under the form."""
        parts = [result.engine or ""]
        parts.append(_("%s s", self._expense_scan_number(result.duration, 1)))
        if result.value('date') is not None or result.value('total') is not None:
            # On an image that is not a receipt, the few words read say
            # nothing of the reading.
            parts.append(_("confidence %d %%", round(result.mean_score * 100)))
        info = result.preprocess
        if info:
            steps = []
            if info.cropped:
                steps.append(_("cropped"))
            if info.deskew_angle:
                steps.append(_("straightened by %s°",
                               self._expense_scan_number(info.deskew_angle, 1)))
            if info.rotated_quarters:
                steps.append(_("rotated by %d°", info.rotated_quarters * 90))
            if info.reread:
                steps.append(_("read again"))
            if steps:
                parts.append(", ".join(steps))
        return " · ".join(part for part in parts if part)[:250]

    def _expense_scan_tax_label(self, result):
        """Summary of the VAT read, for information.

        A receipt with several rates gives none for the expense, but the
        mention is needed: without it, the receipt VAT would look
        inconsistent with the rate shown on the form.
        """
        rate = result.value('tax_rate')
        amount = result.value('tax_amount')
        if rate is None and result.value('tax_rate_max') is not None:
            rate_text = _("several rates, up to %s %%",
                          self._expense_scan_rate_text(result.value('tax_rate_max')))
        elif rate is not None:
            rate_text = _("%s %%", self._expense_scan_rate_text(rate))
        else:
            rate_text = False
        if not rate_text and amount is None:
            return False
        if rate_text and amount is not None:
            return _("%(rate)s — %(amount)s", rate=rate_text,
                     amount=self._expense_scan_number(amount))
        if rate_text:
            return rate_text
        return self._expense_scan_number(amount)

    def _expense_scan_number(self, value, digits=2):
        """A number written the way the user's language writes it."""
        return formatLang(self.env, value, digits=digits)

    def _expense_scan_money(self, amount):
        """An amount in the expense currency, with its symbol."""
        return formatLang(self.env, amount, currency_obj=self.currency_id or None)

    def _expense_scan_rate_text(self, rate):
        """A tax rate with its own decimals only: "20", "5,5", "8,875"."""
        decimals = ('%.3f' % rate).rstrip('0').partition('.')[2]
        return self._expense_scan_number(rate, len(decimals))
