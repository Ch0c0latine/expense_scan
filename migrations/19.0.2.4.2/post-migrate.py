# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mots du ticket ajoutés ou retirés sur les catégories déjà renseignées."""
from odoo import SUPERUSER_ID, api

from odoo.addons.expense_scan.ocr import lexicon


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    templates = env['product.template']
    templates._expense_scan_add_keywords(lexicon.ADDED_KEYWORDS['19.0.2.4.2'])
    templates._expense_scan_remove_keywords(lexicon.REMOVED_KEYWORDS['19.0.2.4.2'])
