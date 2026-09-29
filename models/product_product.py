# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Ordre des catégories de dépenses dans la liste déroulante.

Odoo trie les articles par référence interne. Pour des catégories de
dépenses, « CADEAU » et « COMM » passent avant le repas ou le train, qui se
retrouvent derrière « Rechercher plus ». L'ordre suit la séquence définie
dans la liste des catégories (glisser-déposer).
"""
import unicodedata

from odoo import api, fields, models

# Mots qui désignent une nuit d'hôtel, sans accent ni casse. « Logement »
# n'en est pas : l'indemnité forfaitaire de logement ne se compte pas en
# nuits réglées sur justificatif.
HOTEL_WORDS = ('hotel', 'heberg', 'bnb', 'nuitee')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    expense_scan_no_vat = fields.Boolean(
        string="Forfait sans TVA",
        help="Catégorie de dépense sans TVA récupérable : indemnités "
             "forfaitaires, barèmes Urssaf, mais aussi hébergement ou "
             "transport de personnes, dont la TVA n'est pas déductible. "
             "La saisie d'une dépense de cette catégorie masque les champs "
             "de TVA et n'en reporte aucune.\n\n"
             "Inutile de le cocher pour le kilométrage : une catégorie "
             "comptée en kilomètres ou en miles est traitée ainsi d'office.",
    )
    expense_scan_nights_required = fields.Boolean(
        string="Nombre de nuitées requis",
        compute='_compute_expense_scan_nights_required',
        help="Vrai pour les catégories d'hôtel, reconnues à leur nom ou à "
             "leur référence : leurs dépenses indiquent le nombre de nuits.",
    )

    @api.depends('name', 'default_code')
    @api.depends_context('lang')
    def _compute_expense_scan_nights_required(self):
        """Vrai pour les seules catégories d'hôtel.

        Déduit du nom ou de la référence plutôt que d'une case à cocher : une
        case sur chaque catégorie inviterait à demander des nuitées pour un
        taxi ou un repas.
        """
        for template in self:
            text = ' '.join(filter(None, (template.name, template.default_code)))
            text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
            template.expense_scan_nights_required = any(word in text for word in HOTEL_WORDS)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.onchange('expense_scan_no_vat')
    def _onchange_expense_scan_no_vat(self):
        """Retire les taxes fournisseur par défaut d'un forfait sans TVA.

        Conservées, elles feraient croire qu'une TVA s'applique et
        réapparaîtraient sur toute dépense saisie hors du formulaire, qui
        masque les champs de TVA.
        """
        for product in self:
            if product.expense_scan_no_vat:
                product.supplier_taxes_id = False

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        if not self.env.context.get('expense_scan_category_order'):
            return super().name_search(name, domain, operator, limit)

        # Toutes les correspondances d'abord, triées ensuite : tronquer avant
        # de trier garderait les premières par référence, pas par séquence.
        # Le filtre des catégories de dépenses limite le nombre de résultats.
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
        """Applique l'ordre par séquence à la fenêtre de recherche complète (mobile).

        Le clavier tactile ouvre une liste plein écran au lieu des suggestions
        du champ. Cette liste passe par ``web_search_read`` et non par
        ``name_search``, et restait triée par référence.
        """
        if self.env.context.get('expense_scan_category_order'):
            order = 'sequence, name'
        return super().web_search_read(
            domain, specification, offset=offset, limit=limit,
            order=order, count_limit=count_limit)
