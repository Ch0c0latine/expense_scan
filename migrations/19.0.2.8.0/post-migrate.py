# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mark the receipts already scanned that print several tax rates, and check
the expenses against the rules again.

Until now the expense sheet recognised the receipts with several rates from
the text of the tax read, written in French.

The rule findings are now written in the employee's language, with a sign
per kind, and "Outside the rules" only counts the breaches.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute("""
        UPDATE hr_expense
           SET expense_scan_mixed_rates = TRUE
         WHERE scan_detected_tax LIKE 'plusieurs taux%'
            OR scan_detected_tax LIKE 'several rates%'
    """)
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['hr.expense'].with_context(active_test=False).search([])._expense_scan_recompute_policy()
