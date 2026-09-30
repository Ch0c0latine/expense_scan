# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Delivered examples: workbook of the basic template, rule check.

At installation, the data file takes care of it; on update, its calls are not
replayed, hence this script.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['expense.scan.export.template']._expense_scan_fill_blank_files()
    env['expense.scan.policy']._expense_scan_recheck_all()
