# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Changes to Odoo's own menus, actions and search view, and how to undo them.

The records below belong to ``hr_expense``. They are not modified by the data
files of this module: each change is an option of the Expenses settings, off
on a new installation, applied and undone from here. Uninstalling the module
undoes all of them (``restore``), so that nothing it did to Odoo outlives it.
"""
import ast

from .renamed_translations import MENU

PARAMETERS = {
    'menu': 'expense_scan.tidy_menu',
    'month': 'expense_scan.month_default',
    'filters': 'expense_scan.tidy_filters',
}

MENU_XMLID = 'hr_expense.menu_hr_expense_my_expenses'
ROOT_MENU_XMLID = 'hr_expense.menu_hr_expense_root'
FILTERS_VIEW_XMLID = 'expense_scan.hr_expense_view_search_tidy'
ACTIONS = (
    'hr_expense.hr_expense_actions_my_all',
    'hr_expense.hr_expense_actions_to_process',
)
MONTH_KEY = 'search_default_current_month'
MENU_NAME = "Expense follow-up"
ORIGINAL_MENU_NAME = "My Expenses"
ORIGINAL_ICON = 'hr_expense,static/description/icon.png'
MODULE_ICON = 'expense_scan,static/description/icon.png'


def _enabled(env, option):
    return bool(env['ir.config_parameter'].sudo().get_param(PARAMETERS[option]))


def _set_context_key(action, wanted):
    context = ast.literal_eval(action.context or '{}')
    if wanted:
        context[MONTH_KEY] = 1
    else:
        context.pop(MONTH_KEY, None)
    value = repr(context)
    if value != action.context:
        action.context = value


def _rename_menu(env, menu, tidy):
    if not tidy:
        if menu.with_context(lang='en_US').name == ORIGINAL_MENU_NAME:
            return
        menu.with_context(lang='en_US').name = ORIGINAL_MENU_NAME
        others = [code for code, _name in env['res.lang'].get_installed() if code != 'en_US']
        menu.update_field_translations('name', {code: False for code in others})
        # The translations Odoo ships for its own menu come back.
        env['ir.module.module']._get('hr_expense')._update_translations()
        return
    menu.with_context(lang='en_US').name = MENU_NAME
    installed = {code for code, _name in env['res.lang'].get_installed()}
    menu.update_field_translations(
        'name', {code: text for code, text in MENU.items() if code in installed})


def apply(env):
    """Bring the records in line with the three options."""
    env = env(su=True)
    menu = env.ref(MENU_XMLID, raise_if_not_found=False)
    if menu:
        _rename_menu(env, menu, _enabled(env, 'menu'))
    for xmlid in ACTIONS:
        action = env.ref(xmlid, raise_if_not_found=False)
        if action:
            _set_context_key(action, _enabled(env, 'month'))
    view = env.ref(FILTERS_VIEW_XMLID, raise_if_not_found=False)
    if view and view.active != _enabled(env, 'filters'):
        view.active = _enabled(env, 'filters')


def restore(env):
    """Give Odoo back what the module changed (uninstallation)."""
    env = env(su=True)
    params = env['ir.config_parameter']
    for name in PARAMETERS.values():
        params.search([('key', '=', name)]).unlink()
    apply(env)
    root = env.ref(ROOT_MENU_XMLID, raise_if_not_found=False)
    if root and root.web_icon == MODULE_ICON:
        root.web_icon = ORIGINAL_ICON


def keep_current_behaviour(env):
    """An upgrade keeps what the module already did on this database.

    Before the options existed, the three changes were always applied.
    """
    params = env['ir.config_parameter'].sudo()
    for name in PARAMETERS.values():
        params.set_param(name, '1')
    apply(env)
