# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Installing the module alone changes nothing of what Odoo does unasked.

The changes to Odoo's own menus, actions and filters are options of the
settings, and uninstalling gives them back. The features that are optional
(expense rules, re-invoicing, project limit) really are.
"""
from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import common, tagged
from odoo.tools import mute_logger

from ..models import native_tweaks


class NativeRecords(common.TransactionCase):
    """Access to the records of Odoo that the options change."""

    def menu_name(self):
        return self.env.ref(native_tweaks.MENU_XMLID).with_context(lang='en_US').name

    def contexts(self):
        return [self.env.ref(xmlid).context for xmlid in native_tweaks.ACTIONS]

    def filters_view(self):
        return self.env.ref(native_tweaks.FILTERS_VIEW_XMLID)

    def search_arch(self):
        return self.env['hr.expense'].get_view(view_type='search')['arch']

    def set_options(self, **values):
        params = self.env['ir.config_parameter'].sudo()
        for option, name in native_tweaks.PARAMETERS.items():
            params.set_param(name, '1' if values.get(option) else False)
        native_tweaks.apply(self.env)


@tagged('post_install', '-at_install')
class TestNativeTweaks(NativeRecords):

    def test_nothing_changes_by_default(self):
        self.set_options()
        self.assertEqual(self.menu_name(), native_tweaks.ORIGINAL_MENU_NAME)
        for context in self.contexts():
            self.assertNotIn(native_tweaks.MONTH_KEY, context)
        self.assertFalse(self.filters_view().active)
        arch = self.search_arch()
        for name in ('my_team_expenses', 'by_company', 'by_employee'):
            self.assertIn('name="%s"' % name, arch)

    def test_the_options_apply_and_undo(self):
        self.set_options(menu=True, month=True, filters=True)
        self.assertEqual(self.menu_name(), native_tweaks.MENU_NAME)
        for context in self.contexts():
            self.assertIn(native_tweaks.MONTH_KEY, context)
        self.assertTrue(self.filters_view().active)
        arch = self.search_arch()
        for name in ('my_team_expenses', 'by_company', 'by_employee'):
            self.assertNotIn('name="%s"' % name, arch)
        # The existing keys of the action stay.
        self.assertIn('search_default_my_open_expenses', self.contexts()[0])
        self.assertIn('searchpanel_default_state', self.contexts()[1])

        self.set_options()
        self.assertEqual(self.menu_name(), native_tweaks.ORIGINAL_MENU_NAME)
        self.assertFalse(self.filters_view().active)
        self.assertEqual(self.contexts()[1], "{'searchpanel_default_state': ['submitted']}")

    def test_the_options_are_independent(self):
        self.set_options(month=True)
        self.assertEqual(self.menu_name(), native_tweaks.ORIGINAL_MENU_NAME)
        self.assertFalse(self.filters_view().active)
        self.assertIn(native_tweaks.MONTH_KEY, self.contexts()[0])

    def test_the_settings_screen_applies_the_options(self):
        settings = self.env['res.config.settings'].create({
            'expense_scan_tidy_menu': True, 'expense_scan_month_default': True,
            'expense_scan_tidy_filters': True})
        settings.execute()
        self.assertEqual(self.menu_name(), native_tweaks.MENU_NAME)
        self.assertTrue(self.filters_view().active)
        settings = self.env['res.config.settings'].create({
            'expense_scan_tidy_menu': False, 'expense_scan_month_default': False,
            'expense_scan_tidy_filters': False})
        settings.execute()
        self.assertEqual(self.menu_name(), native_tweaks.ORIGINAL_MENU_NAME)
        self.assertFalse(self.filters_view().active)

    def test_uninstalling_gives_everything_back(self):
        self.set_options(menu=True, month=True, filters=True)
        self.env.ref(native_tweaks.ROOT_MENU_XMLID).web_icon = native_tweaks.MODULE_ICON
        native_tweaks.restore(self.env)
        self.assertEqual(self.menu_name(), native_tweaks.ORIGINAL_MENU_NAME)
        for context in self.contexts():
            self.assertNotIn(native_tweaks.MONTH_KEY, context)
        self.assertEqual(self.env.ref(native_tweaks.ROOT_MENU_XMLID).web_icon,
                         native_tweaks.ORIGINAL_ICON)
        params = self.env['ir.config_parameter'].sudo()
        for name in native_tweaks.PARAMETERS.values():
            self.assertFalse(params.get_param(name))

    def test_an_upgrade_keeps_what_the_database_had(self):
        self.set_options()
        native_tweaks.keep_current_behaviour(self.env)
        self.assertEqual(self.menu_name(), native_tweaks.MENU_NAME)
        self.assertTrue(self.filters_view().active)
        for context in self.contexts():
            self.assertIn(native_tweaks.MONTH_KEY, context)


@tagged('post_install', '-at_install')
class TestOptionalFeatures(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('en_US')
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.employee = cls.env['hr.employee'].create({'name': "Alix Livraison"})
        cls.employee.work_contact_id.lang = 'en_US'
        cls.meal = cls.env['product.product'].create(
            {'name': "Repas livraison", 'can_be_expensed': True})
        cls.policy = cls.env.ref('expense_scan.policy_good_practice')

    def expense(self, **values):
        return self.env['hr.expense'].create(dict({
            'name': "Dîner", 'employee_id': self.employee.id, 'product_id': self.meal.id,
            'date': date(2026, 9, 1), 'total_amount_currency': 30.0,
            'scan_raw_text': "PARKING\nFINE 35,00",
        }, **values))

    def test_the_delivered_rules_can_be_archived(self):
        expense = self.expense()
        self.assertTrue(self.policy.active)
        self.assertIn("Fines and personal", expense.expense_scan_policy_alert)
        self.policy.action_archive()
        self.assertFalse(expense.expense_scan_policy_alert)
        self.assertFalse(expense.expense_scan_policy_status)
        self.assertFalse(self.expense().expense_scan_policy_alert)

    def test_re_invoicing_is_off_for_a_new_company(self):
        company = self.env['res.company'].create({'name': "Société neuve"})
        self.assertFalse(company.expense_scan_reinvoice)
        self.assertTrue(company.expense_scan_limit_projects)

    def test_nothing_of_re_invoicing_shows_when_it_is_off(self):
        self.env.company.expense_scan_reinvoice = False
        arch = self.env['hr.expense'].get_view(view_type='list')['arch']
        self.assertNotIn('expense_scan_reinvoiced_amount', arch)
        expense = self.expense()
        self.assertFalse(expense.project_id)
        self.assertFalse(expense.expense_scan_invoice_id)

    def test_the_project_limit_is_an_option(self):
        user = self.env['res.users'].create({
            'name': "Employé Zz", 'login': 'employe_zz_limit',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        employee = self.env['hr.employee'].create({'name': "Employé Zz", 'user_id': user.id})
        expense = self.env['hr.expense'].new({'employee_id': employee.id})
        expense = expense.with_user(user)
        self.assertIn("'id', 'in'", expense.expense_scan_project_domain)
        self.env.company.expense_scan_limit_projects = False
        expense.invalidate_recordset()
        self.assertEqual(expense.expense_scan_project_domain, "[]")


@tagged('post_install', '-at_install')
class TestRpcAccess(common.TransactionCase):
    """A user cannot make the module touch another employee's expense."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('base.group_user')
        cls.users = cls.env['res.users']
        cls.employees = cls.env['hr.employee']
        for name in ("Ana", "Ben"):
            user = cls.env['res.users'].create({
                'name': "%s Accès" % name, 'login': '%s_access_zz' % name.lower(),
                'group_ids': [(6, 0, group.ids)]})
            cls.users |= user
            cls.employees |= cls.env['hr.employee'].create(
                {'name': "%s Accès" % name, 'user_id': user.id})
        cls.expense = cls.env['hr.expense'].create({
            'name': "Ticket d'Ana", 'employee_id': cls.employees[0].id,
            'total_amount_currency': 12.0})
        cls.env['ir.attachment'].create({
            'name': 'ticket.txt', 'raw': b'x', 'res_model': 'hr.expense',
            'res_id': cls.expense.id})

    @mute_logger('odoo.addons.base.models.ir_rule')
    def test_the_rescan_of_another_employee_is_refused(self):
        other = self.expense.with_user(self.users[1])
        with self.assertRaises(AccessError):
            other.action_expense_scan_rescan()
        with self.assertRaises(AccessError):
            other.action_expense_scan_done()
        with self.assertRaises(AccessError):
            other.action_expense_scan_set_reinvoice('none')

    @mute_logger('odoo.addons.base.models.ir_rule')
    def test_the_owner_is_not_refused(self):
        own = self.expense.with_user(self.users[0])
        own.check_access('write')
        own.action_expense_scan_done()

    def test_only_accountants_reimburse(self):
        with self.assertRaises(AccessError):
            self.expense.with_user(self.users[0]).action_expense_scan_pay()
