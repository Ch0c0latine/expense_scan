# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mots du ticket ajoutés aux catégories déjà renseignées."""
from odoo import SUPERUSER_ID, api

from odoo.addons.expense_scan.ocr import lexicon


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['product.template']._expense_scan_add_keywords(lexicon.ADDED_KEYWORDS['19.0.2.3.0'])
