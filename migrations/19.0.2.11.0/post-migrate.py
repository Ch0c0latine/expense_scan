# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The Excel model of the expenses moves from the sales order to the project.

A module that sent the invoices used to keep it on the order, in the column
``mission_expense_template_id``, which Odoo leaves in place when that module no longer
declares the field: the value goes to the project of the order when the project has none.
"""


def _has_column(cr, table, column):
    cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
               (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not _has_column(cr, 'sale_order', 'mission_expense_template_id')             or not _has_column(cr, 'project_project', 'expense_scan_sheet_template_id'):
        return
    cr.execute("""
        UPDATE project_project p
           SET expense_scan_sheet_template_id = o.mission_expense_template_id
          FROM sale_order o
         WHERE o.project_id = p.id
           AND o.mission_expense_template_id IS NOT NULL
           AND p.expense_scan_sheet_template_id IS NULL
    """)
