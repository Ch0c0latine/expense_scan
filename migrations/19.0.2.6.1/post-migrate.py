# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Conservation du texte lu portée à 10 ans (Code de commerce, art. L123-22).

Ne modifie que les sociétés restées sur l'ancien défaut (365 jours) : une
valeur choisie manuellement est conservée.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].search([('expense_scan_text_retention_days', '=', 365)])
    companies.expense_scan_text_retention_days = 3650
