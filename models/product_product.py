# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Order of the expense categories in the dropdown.

Odoo sorts products by internal reference. For expense categories, "GIFT"
and "PHONE" then come before meals or train tickets, which end up behind
"Search More". The order follows the sequence set in the category list
(drag and drop).
"""
import unicodedata

from odoo import api, fields, models

# Words that name a hotel night, without accents or case, in the main
# languages. "Logement" (housing) is not one of them: a flat housing
# allowance is not counted in nights paid on a receipt.
HOTEL_WORDS = ('hotel', 'heberg', 'bnb', 'nuitee', 'lodging', 'accommodation', 'albergo',
               'alojamiento', 'alojamento', 'hospedaje', 'unterkunft', 'ubernachtung',
               'nocleg', 'overnachting', 'overnatning', 'overnattning', 'overnatting', 'alloggio',
               'pernottamento', 'hospedagem', 'hotell', 'logies')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    expense_scan_no_vat = fields.Boolean(
        string="Flat rate without VAT",
        help="Expense category without recoverable VAT: flat allowances and "
             "official scales, but also lodging or passenger transport where "
             "VAT cannot be deducted. Expenses in this category hide the VAT "
             "fields and carry no VAT.\n\n"
             "No need to tick it for mileage: a category counted in "
             "kilometres or miles is handled this way automatically.",
    )
    expense_scan_nights_required = fields.Boolean(
        string="Number of nights required",
        compute='_compute_expense_scan_nights_required',
        help="True for hotel categories, recognised by their name or "
             "reference: their expenses give the number of nights.",
    )

    @api.depends('name', 'default_code')
    def _compute_expense_scan_nights_required(self):
        """True for hotel categories only.

        Derived from the name or reference rather than a checkbox: a box on
        every category would invite asking for nights on a taxi or a meal.
        The name is read in every installed language: the answer must not
        depend on the language of the user who opens the expense.
        """
        languages = [code for code, _label in self.env['res.lang'].get_installed()]
        for template in self:
            names = {template.with_context(lang=code).name for code in languages}
            text = ' '.join(filter(None, list(names) + [template.default_code]))
            text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
            template.expense_scan_nights_required = any(word in text for word in HOTEL_WORDS)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.onchange('expense_scan_no_vat')
    def _onchange_expense_scan_no_vat(self):
        """Remove the default vendor taxes of a flat rate without VAT.

        Kept, they would suggest that VAT applies and would come back on any
        expense entered outside the form, which hides the VAT fields.
        """
        for product in self:
            if product.expense_scan_no_vat:
                product.supplier_taxes_id = False

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        if not self.env.context.get('expense_scan_category_order'):
            return super().name_search(name, domain, operator, limit)

        # All matches first, sorted afterwards: truncating before sorting
        # would keep the first ones by reference, not by sequence. The
        # expense category filter keeps the number of results small.
        found = super().name_search(name, domain, operator, limit=None)
        products = self.browse([product_id for product_id, _name in found])
        rank = {
            product.id: (product.sequence, product.display_name or '', product.id)
            for product in products
        }
        found.sort(key=lambda pair: rank[pair[0]])
        return found[:limit] if limit else found

    @api.model
    @api.readonly
    def web_search_read(self, domain, specification, offset=0, limit=None,
                        order=None, count_limit=None):
        """Apply the sequence order to the full search dialog (mobile).

        On a touch keyboard, the field opens a full-screen list instead of
        its suggestions. That list goes through ``web_search_read``, not
        ``name_search``, and stayed sorted by reference.
        """
        if self.env.context.get('expense_scan_category_order'):
            order = 'sequence, name'
        return super().web_search_read(
            domain, specification, offset=offset, limit=limit,
            order=order, count_limit=count_limit)
