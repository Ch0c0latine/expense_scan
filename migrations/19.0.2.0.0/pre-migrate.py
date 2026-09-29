# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Detach from the module the rules and export templates it delivered before.

They were specific to one installation: the module no longer delivers any,
apart from demo data. The records already created are kept, detached from
the module so that its update does not delete them.
"""


def migrate(cr, version):
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'expense_scan'
           AND model IN ('expense.scan.policy', 'expense.scan.policy.rule',
                         'expense.scan.export.template', 'expense.scan.export.column')
           AND name NOT LIKE 'demo%'
    """)
