# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Re-invoiced expenses no longer name a sales order.

They used to get the project's order in the standard "Customer to Reinvoice"
field, which makes Odoo add one order line per posted expense. They now reach
the order's expense line through their project. Expenses whose order line
already exists keep it.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'hr_expense' AND column_name = 'sale_order_id'
    """)
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE hr_expense
           SET sale_order_id = NULL
         WHERE reinvoice_mode = 'project'
           AND sale_order_id IS NOT NULL
           AND sale_order_line_id IS NULL
           AND account_move_id IS NULL
    """)
