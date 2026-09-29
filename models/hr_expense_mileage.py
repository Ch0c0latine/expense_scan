# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Prix unitaire des catégories à coût fixe, en particulier le kilométrage.

Odoo recalcule le prix d'après le total, ce qui écrase le tarif saisi. Sur
une catégorie à coût fixe, le prix n'est plus recalculé une fois posé : il
reçoit une valeur initiale (tarif du salarié pour une distance, coût de la
catégorie sinon), puis la saisie fait foi et le total en découle.

Repartir du total arrondirait le prix au centime : 0,636 €/km deviendrait
0,64 €.
"""
from odoo import Command, api, fields, models


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_no_vat = fields.Boolean(
        string="Sans TVA",
        compute='_compute_expense_scan_no_vat',
        help="Vrai pour une dépense sans TVA récupérable : kilométrage, ou "
             "catégorie cochée « Forfait sans TVA » (barèmes Urssaf, "
             "indemnités). Le formulaire en masque alors les champs de TVA.",
    )

    @api.depends('product_id', 'product_uom_id', 'product_id.expense_scan_no_vat')
    def _compute_expense_scan_no_vat(self):
        for expense in self:
            expense.expense_scan_no_vat = expense._expense_scan_no_vat()

    def _expense_scan_no_vat(self):
        """Vrai si la dépense est un forfait sans TVA récupérable.

        Le kilométrage l'est d'office (le barème est un forfait). Pour les
        autres forfaits, l'unité ne suffit pas : la case de la catégorie
        décide.
        """
        self.ensure_one()
        return bool(self.product_id.expense_scan_no_vat) or self._expense_scan_is_distance()

    @api.depends('product_id', 'company_id')
    def _compute_tax_ids(self):
        """Aucune taxe sur un forfait.

        Une indemnité kilométrique ou un barème Urssaf n'ont pas de TVA à
        récupérer, même si la catégorie porte une taxe par défaut.
        """
        super()._compute_tax_ids()
        for expense in self:
            if expense._expense_scan_no_vat():
                expense.tax_ids = [Command.clear()]

    @api.depends('product_id')
    def _compute_from_product(self):
        """Une distance se saisit en quantité et prix unitaire.

        Odoo n'affiche ces deux champs que pour les catégories ayant un coût.
        Une catégorie kilométrique à 0 € (le tarif est porté par le salarié)
        n'aurait qu'un total : ni kilomètres à saisir, ni tarif proposé.
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
            # Un prix déjà défini est conservé. La valeur initiale n'est posée
            # que sur une dépense qui n'en a pas.
            expense.price_unit = expense.price_unit \
                or expense._expense_scan_default_unit_price()

    @api.onchange('total_amount_currency')
    def _inverse_total_amount_currency(self):
        """Ne pas recalculer le prix d'une catégorie à coût fixe.

        Odoo divise un total arrondi par la quantité, ce qui fait dériver le
        prix. Sur une catégorie à coût fixe, le total découle du prix ; comme
        cette méthode se déclenche aussi quand le total change à cause du
        prix, elle réécrivait la saisie.

        Le décorateur est répété : sans lui, la méthode d'Odoo ne serait plus
        enregistrée comme onchange pour les autres dépenses.
        """
        fixed = self.filtered('product_has_cost')
        return super(HrExpense, self - fixed)._inverse_total_amount_currency()

    @api.onchange('product_id', 'employee_id')
    def _onchange_expense_scan_unit_price(self):
        """Changer de catégorie ou de salarié rétablit le tarif initial.

        Sans cela, une dépense passée en kilométrage garderait le prix issu
        du total saisi avant.
        """
        for expense in self:
            if expense.state != 'draft' or not expense.product_id:
                continue
            if expense._expense_scan_no_vat():
                # Une TVA saisie avant le passage à un forfait n'a plus lieu
                # d'être : taux et montant sont vidés.
                expense.tax_ids = [Command.clear()]
                expense.scan_tax_amount = 0.0
            if not expense.product_has_cost:
                continue  # pas une catégorie à coût fixe
            expense.price_unit = expense._expense_scan_default_unit_price()

    def _expense_scan_default_unit_price(self):
        """Tarif du salarié pour une distance, coût de la catégorie sinon."""
        self.ensure_one()
        product = self.product_id
        if self._expense_scan_is_distance():
            # sudo : le tarif est réservé aux RH, alors que le salarié saisit
            # lui-même sa dépense.
            rate = self.employee_id.sudo().expense_mileage_rate
            if rate:
                return rate
        return product._price_compute(
            'standard_price',
            uom=self.product_uom_id,
            company=self.company_id,
        )[product.id]

    def _expense_scan_is_distance(self):
        """Vrai si l'unité de la catégorie est le kilomètre ou le mile.

        Le tarif du salarié ne s'applique qu'aux distances : une indemnité de
        repas à coût fixe ne doit pas prendre le prix du kilomètre.
        """
        self.ensure_one()
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        uom = self.product_uom_id or self.product_id.uom_id
        return bool(uom and uom in distances)
