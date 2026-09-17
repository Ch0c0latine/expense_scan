# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Nombre de nuitées des dépenses d'hébergement.

Une note d'hôtel sans nombre de nuits ne se contrôle pas : ni le prix par
nuit, ni la cohérence avec la durée de la mission. Seules les catégories
d'hôtel l'exigent ; la dépense l'impose alors, à une nuit par défaut.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_nights = fields.Integer(
        string="Nuitées",
        default=1,
        copy=False,
        help="Nombre de nuits réglées par ce justificatif.",
    )
    expense_scan_nights_required = fields.Boolean(
        related='product_id.expense_scan_nights_required',
        string="Nuitées requises",
    )

    @api.onchange('product_id')
    def _onchange_expense_scan_nights(self):
        """Une nuit au moins en passant sur l'hôtel, si le champ était vide."""
        for expense in self:
            if expense.expense_scan_nights_required and expense.expense_scan_nights < 1:
                expense.expense_scan_nights = 1

    @api.constrains('expense_scan_nights', 'product_id')
    def _check_expense_scan_nights(self):
        """Une nuit au moins, dès que la catégorie l'exige.

        Contrôlé côté serveur et non seulement dans le formulaire : un
        champ entier vaut zéro par défaut, et le client tient zéro pour une
        valeur saisie — l'obligation de la vue seule laisserait passer une
        note d'hôtel sans nuitée.
        """
        for expense in self:
            if expense.expense_scan_nights_required and expense.expense_scan_nights < 1:
                raise ValidationError(_(
                    "« %(expense)s » relève de la catégorie « %(category)s », qui "
                    "demande le nombre de nuitées. Indiquez-le avant "
                    "d'enregistrer.",
                    expense=expense.name, category=expense.product_id.name))
