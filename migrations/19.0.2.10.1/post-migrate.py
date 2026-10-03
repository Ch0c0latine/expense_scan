# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The changes to Odoo's menus and filters became options: keep the ones in use."""
from odoo import SUPERUSER_ID, api

from odoo.addons.expense_scan.models import native_tweaks


def migrate(cr, version):
    native_tweaks.keep_current_behaviour(api.Environment(cr, SUPERUSER_ID, {}))
