# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Keep the review banner of the expenses already flagged.

The ``expense_scan_todo_codes`` field is new: without a value, an expense "To
check" would lose its banner at the first computation, although it has not
been reviewed. It is set to ``static`` (never resolved automatically, as
before this mechanism) until the next scan.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    expenses = env['hr.expense'].search([
        ('scan_state', '=', 'partial'),
        ('scan_todo', '!=', False),
        ('expense_scan_todo_codes', '=', False),
    ])
    if expenses:
        expenses.write({'expense_scan_todo_codes': 'static'})
