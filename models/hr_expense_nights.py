# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Number of nights on lodging expenses.

Without the number of nights, a hotel bill can be checked neither for its
price per night nor against the length of the trip. Only hotel categories
require it; the default is one night.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_nights = fields.Integer(
        string="Nights",
        default=1,
        copy=False,
        help="Number of nights paid with this receipt.",
    )
    expense_scan_nights_required = fields.Boolean(
        related='product_id.expense_scan_nights_required',
        string="Nights required",
    )

    @api.onchange('product_id')
    def _onchange_expense_scan_nights(self):
        """At least one night when switching to a hotel category."""
        for expense in self:
            # While the receipt is being read, the scan brings the number of nights: a 1 written here would
            # look like a correction by hand and replace the number printed on the bill.
            if expense.scan_state == 'running':
                continue
            if expense.expense_scan_nights_required and expense.expense_scan_nights < 1:
                expense.expense_scan_nights = 1

    @api.constrains('expense_scan_nights', 'product_id')
    def _check_expense_scan_nights(self):
        """At least one night when the category requires it.

        Checked on the server: an integer field defaults to 0 and the client
        counts 0 as a value, so the view's required flag alone would let a
        hotel bill through without any night.
        """
        for expense in self:
            if expense.expense_scan_nights_required and expense.expense_scan_nights < 1:
                raise ValidationError(_(
                    "\"%(expense)s\" is in the \"%(category)s\" category, which "
                    "requires the number of nights. Enter it before saving.",
                    expense=expense.name, category=expense.product_id.name))
