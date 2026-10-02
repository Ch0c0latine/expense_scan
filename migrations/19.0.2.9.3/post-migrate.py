# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Renamed records keep the translation they had: see models/renamed_translations.py."""
from odoo import SUPERUSER_ID, api

from odoo.addons.expense_scan.models.renamed_translations import apply_renamed_translations


def migrate(cr, version):
    apply_renamed_translations(api.Environment(cr, SUPERUSER_ID, {}))
