# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""An expense without a project is not re-invoiced: nothing is left "to decide" for it.

Until now every new expense started "to decide" and blocked its approval. The
ones still waiting without a project become "No"; those with a project keep the
open question.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    expenses = env['hr.expense'].with_context(tracking_disable=True, mail_notrack=True).search([
        ('reinvoice_mode', '=', 'todo'), ('project_id', '=', False),
        ('state', 'in', ('draft', 'submitted')),
    ])
    expenses.write({'reinvoice_mode': 'none'})
