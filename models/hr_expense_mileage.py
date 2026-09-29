# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Unit price of fixed-cost categories, mileage in particular.

Odoo computes the price back from the total, which overwrites the rate
entered. On a fixed-cost category, the price is no longer recomputed once
set: it gets an initial value (the employee's rate for a distance, the
category cost otherwise), then what the user enters prevails and the total
follows from it.

Starting from the total would round the price to the cent: 0.636 per km
would become 0.64.
"""
from odoo import Command, api, fields, models


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_no_vat = fields.Boolean(
        string="Without VAT",
        compute='_compute_expense_scan_no_vat',
        help="True for an expense without recoverable VAT: mileage, or a "
             "category marked \"Flat rate without VAT\" (official scales, "
             "allowances). The form then hides its VAT fields.",
    )

    @api.depends('product_id', 'product_uom_id', 'product_id.expense_scan_no_vat')
    def _compute_expense_scan_no_vat(self):
        for expense in self:
            expense.expense_scan_no_vat = expense._expense_scan_no_vat()

    def _expense_scan_no_vat(self):
        """True if the expense is a flat rate without recoverable VAT.

        Mileage always is (the scale is a flat rate). For other flat rates
        the unit is not enough: the category checkbox decides.
        """
        self.ensure_one()
        return bool(self.product_id.expense_scan_no_vat) or self._expense_scan_is_distance()

    @api.depends('product_id', 'company_id')
    def _compute_tax_ids(self):
        """No tax on a flat rate.

        A mileage allowance or an official scale has no VAT to recover, even
        when the category carries a default tax.
        """
        super()._compute_tax_ids()
        for expense in self:
            if expense._expense_scan_no_vat():
                expense.tax_ids = [Command.clear()]

    @api.depends('product_id')
    def _compute_from_product(self):
        """A distance is entered as a quantity and a unit price.

        Odoo only shows those two fields for categories that have a cost. A
        mileage category at 0 (the rate is on the employee) would only have
        a total: no kilometres to enter, no rate suggested.
        """
        super()._compute_from_product()
        for expense in self:
            if not expense.product_has_cost and expense.product_id \
                    and expense._expense_scan_is_distance():
                expense.product_has_cost = True

    @api.depends('total_amount', 'total_amount_currency')
    def _compute_price_unit(self):
        fixed = self.filtered(lambda expense: expense.product_has_cost
                              and expense.state == 'draft'
                              and expense.company_id)
        super(HrExpense, self - fixed)._compute_price_unit()
        for expense in fixed:
            # A price already set is kept. The initial value is only given to
            # an expense that has none.
            expense.price_unit = expense.price_unit \
                or expense._expense_scan_default_unit_price()

    @api.onchange('total_amount_currency')
    def _inverse_total_amount_currency(self):
        """Do not recompute the price of a fixed-cost category.

        Odoo divides a rounded total by the quantity, which makes the price
        drift. On a fixed-cost category the total follows from the price; as
        this method also runs when the total changes because of the price, it
        rewrote what the user entered.

        The decorator is repeated: without it, Odoo's method would no longer
        be registered as an onchange for the other expenses.
        """
        fixed = self.filtered('product_has_cost')
        return super(HrExpense, self - fixed)._inverse_total_amount_currency()

    @api.onchange('product_id', 'employee_id')
    def _onchange_expense_scan_unit_price(self):
        """Changing the category or the employee restores the initial rate.

        Otherwise an expense switched to mileage would keep the price derived
        from the total entered before.
        """
        for expense in self:
            if expense.state != 'draft' or not expense.product_id:
                continue
            if expense._expense_scan_no_vat():
                # VAT entered before switching to a flat rate no longer
                # applies: rate and amount are cleared.
                expense.tax_ids = [Command.clear()]
                expense.scan_tax_amount = 0.0
            if not expense.product_has_cost:
                continue  # not a fixed-cost category
            expense.price_unit = expense._expense_scan_default_unit_price()

    def _expense_scan_default_unit_price(self):
        """The employee's rate for a distance, the category cost otherwise."""
        self.ensure_one()
        product = self.product_id
        if self._expense_scan_is_distance():
            # sudo: the rate is restricted to HR, while employees enter their
            # own expenses.
            rate = self.employee_id.sudo().expense_mileage_rate
            if rate:
                return rate
        return product._price_compute(
            'standard_price',
            uom=self.product_uom_id,
            company=self.company_id,
        )[product.id]

    def _expense_scan_is_distance(self):
        """True if the category unit is the kilometre or the mile.

        The employee's rate only applies to distances: a fixed-cost meal
        allowance must not take the price per kilometre.
        """
        self.ensure_one()
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        uom = self.product_uom_id or self.product_id.uom_id
        return bool(uom and uom in distances)
