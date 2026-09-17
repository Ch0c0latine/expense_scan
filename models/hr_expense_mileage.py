# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Prix unitaire des catégories à coût fixe — le kilométrage au premier chef.

Odoo recalcule ce prix chaque fois que le total change, et le ramène alors
au coût standard de la catégorie (`_needs_product_price_computation`). Or le
total dépend lui-même du prix : saisir un tarif relançait la boucle, et le
tarif saisi retombait aussitôt à la valeur de la catégorie.

Repartir du total ne vaudrait pas mieux : ce chemin arrondit le prix au
centime, et un barème de 0,636 €/km glisserait vers 0,64 € — quarante
centimes d'écart sur cent kilomètres.

Le prix d'une catégorie à coût fixe n'est donc plus recalculé du tout une
fois posé. Il reçoit une valeur initiale — le tarif du salarié pour une
distance, le coût de la catégorie sinon — puis c'est la saisie qui fait foi.
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
        """Cette dépense est-elle un forfait, sans TVA à récupérer ?

        Le kilométrage l'est d'office — le barème est un forfait. Les autres
        forfaits, eux, ne se reconnaissent pas à leur unité : c'est la
        catégorie qui le dit.
        """
        self.ensure_one()
        return bool(self.product_id.expense_scan_no_vat) or self._expense_scan_is_distance()

    @api.depends('product_id', 'company_id')
    def _compute_tax_ids(self):
        """Aucune taxe sur un forfait.

        Une indemnité kilométrique ou un barème Urssaf n'ont pas de TVA à
        récupérer : une catégorie qui porterait une taxe par défaut en
        ferait apparaître une que rien ne justifie.
        """
        super()._compute_tax_ids()
        for expense in self:
            if expense._expense_scan_no_vat():
                expense.tax_ids = [Command.clear()]

    @api.depends('total_amount', 'total_amount_currency')
    def _compute_price_unit(self):
        fixed = self.filtered(lambda expense: expense.product_has_cost
                              and expense.state == 'draft'
                              and expense.company_id)
        super(HrExpense, self - fixed)._compute_price_unit()
        for expense in fixed:
            # Un prix déjà posé est celui du salarié : on le laisse. Seule
            # une fiche qui n'en a pas encore reçoit la valeur initiale.
            expense.price_unit = expense.price_unit \
                or expense._expense_scan_default_unit_price()

    @api.onchange('total_amount_currency')
    def _inverse_total_amount_currency(self):
        """Ne déduit plus le prix du total sur une catégorie à coût fixe.

        Odoo remonte ici du total au prix, en divisant un total arrondi au
        centime par la quantité — d'où un prix à quatre décimales pour cent
        kilomètres, et un prix qui dérive à chaque aller-retour. Or sur une
        catégorie à coût fixe, c'est le total qui découle du prix, jamais
        l'inverse ; et comme cette méthode se déclenche aussi quand le total
        change *parce que* le prix a changé, elle réécrivait la saisie même.

        Le décorateur est répété à dessein : sans lui, la méthode d'Odoo
        cesserait d'être enregistrée comme onchange pour toutes les autres
        dépenses.
        """
        fixed = self.filtered('product_has_cost')
        return super(HrExpense, self - fixed)._inverse_total_amount_currency()

    @api.onchange('product_id', 'employee_id')
    def _onchange_expense_scan_unit_price(self):
        """Changer de catégorie ou de salarié repose le tarif initial.

        Sans quoi une dépense passée en kilométrage garderait le prix issu
        du total saisi avant — cinquante euros le kilomètre.
        """
        for expense in self:
            if expense.state != 'draft' or not expense.product_id:
                continue
            if expense._expense_scan_no_vat():
                # Une TVA saisie avant de passer sur un forfait n'a plus
                # d'objet : on la vide, taux et montant.
                expense.tax_ids = [Command.clear()]
                expense.scan_tax_amount = 0.0
            if expense.company_currency_id.is_zero(expense.product_id.standard_price):
                continue  # pas une catégorie à coût fixe
            expense.price_unit = expense._expense_scan_default_unit_price()

    def _expense_scan_default_unit_price(self):
        """Tarif du salarié pour une distance, coût de la catégorie sinon."""
        self.ensure_one()
        product = self.product_id
        if self._expense_scan_is_distance():
            # Droits élevés : le tarif est réservé aux RH, et c'est le
            # salarié lui-même qui saisit sa dépense.
            rate = self.employee_id.sudo().expense_mileage_rate
            if rate:
                return rate
        return product._price_compute(
            'standard_price',
            uom=self.product_uom_id,
            company=self.company_id,
        )[product.id]

    def _expense_scan_is_distance(self):
        """La catégorie se compte-t-elle en kilomètres ou en miles ?

        Le tarif du salarié ne vaut que pour les distances : une indemnité
        de repas à coût fixe ne doit pas prendre le prix du kilomètre.
        """
        self.ensure_one()
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        uom = self.product_uom_id or self.product_id.uom_id
        return bool(uom and uom in distances)
