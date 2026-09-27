# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Le bandeau de relecture garde sa liste, pour les dépenses déjà signalées.

``expense_scan_todo_codes`` est nouveau : sans lui, une dépense déjà
« À vérifier » perdrait son bandeau au premier calcul, alors que rien n'a
été relu. On la marque ``static`` — jamais résolue toute seule, comme le
bandeau se comportait avant ce mécanisme — en attendant un nouveau scan.
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
