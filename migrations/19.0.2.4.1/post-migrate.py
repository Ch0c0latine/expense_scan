# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Receipt words added to the categories already filled."""
from odoo import SUPERUSER_ID, api

from odoo.addons.expense_scan.ocr import lexicon


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['product.template']._expense_scan_add_keywords(lexicon.ADDED_KEYWORDS['19.0.2.4.1'])
