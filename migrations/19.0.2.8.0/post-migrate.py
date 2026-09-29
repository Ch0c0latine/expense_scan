# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mark the receipts already scanned that print several tax rates.

Until now the expense sheet recognised them from the text of the tax read,
written in French.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE hr_expense
           SET expense_scan_mixed_rates = TRUE
         WHERE scan_detected_tax LIKE 'plusieurs taux%'
            OR scan_detected_tax LIKE 'several rates%'
    """)
