# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Retention of the text read raised to 10 years (French Commercial Code, art. L123-22).

Only changes the companies still on the former default (365 days): a value
chosen by hand is kept.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    companies = env['res.company'].search([('expense_scan_text_retention_days', '=', 365)])
    companies.expense_scan_text_retention_days = 3650
