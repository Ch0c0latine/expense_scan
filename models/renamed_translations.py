# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Names of records the module renames, in each language.

A record renamed in English keeps the name it has in the other languages (the
update does not overwrite a translation that exists), and a record of another
module (the menu group) is not exported with this module's translations. They
are set here, at installation and at update.
"""

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




def apply_renamed_translations(env):
    installed = {code for code, _name in env['res.lang'].get_installed()}
    for xmlid, values in (('expense_scan.action_expense_scan_pay', ACTION),
                          ('hr_expense.menu_hr_expense_my_expenses', MENU)):
        record = env.ref(xmlid, raise_if_not_found=False)
        if record:
            record.update_field_translations(
                'name', {code: text for code, text in values.items() if code in installed})
