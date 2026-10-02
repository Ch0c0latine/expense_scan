# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Renamed records keep the translation they had.

A record whose English name changes (the "Pay the employee" action, the "My
Expenses" menu group) keeps the name it already has in the other languages:
the update does not overwrite a translation that exists. They are set here.
"""
from odoo import SUPERUSER_ID, api

ACTION = {
    'fr_FR': "Rembourser le salarié", 'de_DE': "Mitarbeitende erstatten",
    'es_ES': "Reembolsar al empleado", 'it_IT': "Rimborsa il dipendente",
    'nl_NL': "Medewerker terugbetalen", 'pt_PT': "Reembolsar o funcionário",
    'pl_PL': "Zwróć pracownikowi", 'sv_SE': "Ersätt den anställde",
    'nb_NO': "Refunder den ansatte", 'da_DK': "Refundér medarbejderen",
}
MENU = {
    'fr_FR': "Suivi des frais", 'de_DE': "Ausgabenverfolgung",
    'es_ES': "Seguimiento de gastos", 'it_IT': "Monitoraggio spese",
    'nl_NL': "Opvolging declaraties", 'pt_PT': "Acompanhamento de despesas",
    'pl_PL': "Śledzenie wydatków", 'sv_SE': "Utläggsuppföljning",
    'nb_NO': "Utleggsoppfølging", 'da_DK': "Udlægsopfølgning",
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    installed = {code for code, _name in env['res.lang'].get_installed()}
    for xmlid, values in (('expense_scan.action_expense_scan_pay', ACTION),
                          ('hr_expense.menu_hr_expense_my_expenses', MENU)):
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.update_field_translations(
                'name', {code: text for code, text in values.items() if code in installed})
