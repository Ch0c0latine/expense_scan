# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Conserve le bandeau de relecture des dépenses déjà signalées.

Le champ ``expense_scan_todo_codes`` est nouveau : sans valeur, une dépense
« À vérifier » perdrait son bandeau au premier calcul, alors qu'elle n'a pas
été relue. Il est fixé à ``static`` (jamais résolu automatiquement, comme
avant ce mécanisme) jusqu'au prochain scan.
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
