# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Where each function is found, who may use it, and in which language it reads.

Access is tested with ``with_user``: constraints and the tests themselves run
as the superuser, which would let any refusal pass unseen.
"""
import glob
import io
import os

import polib
from lxml import etree

from odoo import Command, addons
from odoo.exceptions import AccessError, UserError
from odoo.tests import common, tagged
from odoo.tools.translate import trans_export

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGUAGES = ['fr_FR', 'de_DE', 'es_ES', 'it_IT', 'nl_NL', 'pt_PT', 'pl_PL', 'sv_SE', 'nb_NO', 'da_DK']
#: Advanced settings: an ordinary user never needs them.
ADVANCED_FIELDS = [
    'expense_scan_engine', 'expense_scan_tesseract_lang', 'expense_scan_threads',
    'expense_scan_model_dir', 'expense_scan_autocrop', 'expense_scan_deskew',
    'expense_scan_auto_rotate', 'expense_scan_keep_original', 'expense_scan_wide_split',
    'expense_scan_max_age_days', 'expense_scan_text_retention_days',
    'expense_scan_tidy_menu', 'expense_scan_month_default', 'expense_scan_tidy_filters',
]
BASIC_FIELDS = [
    'expense_scan_enabled', 'expense_scan_reinvoice', 'expense_scan_limit_projects',
    'expense_scan_apply_tax', 'expense_scan_set_vendor', 'expense_scan_ecb_rates',
    'expense_scan_product_id',
]


def _arch(env, model, view_type, view_id=False):
    """Architecture of a view as the user of ``env`` receives it."""
    views = env[model].get_views([(view_id, view_type)])['views']
    return etree.fromstring(views[view_type]['arch'])


@tagged('post_install', '-at_install')
class TestRoles(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.company = cls.env.company
        cls.users = {}
        groups = {
            'employee': [],
            'approver': ['hr_expense.group_hr_expense_team_approver'],
            'manager': ['hr_expense.group_hr_expense_manager'],
            'accountant': ['account.group_account_invoice'],
            'advisor': ['account.group_account_manager', 'account.group_account_user'],
            'projectuser': ['project.group_project_user'],
        }
        for login, names in groups.items():
            user = cls.env['res.users'].with_context(no_reset_password=True).create({
                'name': 'Ergo %s' % login, 'login': 'ergo_%s' % login,
                'group_ids': [Command.set([cls.env.ref('base.group_user').id]
                                          + [cls.env.ref(name).id for name in names])],
            })
            cls.env['hr.employee'].create({'name': user.name, 'user_id': user.id})
            cls.users[login] = user
        cls.product = cls.env['product.product'].create({
            'name': "Repas ergonomie", 'can_be_expensed': True, 'standard_price': 0.0,
            'supplier_taxes_id': [Command.clear()]})
        cls.employee = cls.users['employee'].employee_id
        cls.employee.expense_manager_id = cls.users['approver']
        cls.expense = cls.env['hr.expense'].create({
            'name': "Repas", 'employee_id': cls.employee.id, 'product_id': cls.product.id,
            'total_amount_currency': 30.0, 'reinvoice_mode': 'none'})

    def as_(self, role):
        return self.env(user=self.users[role])

    # -- Who may do what ----------------------------------------------------------------------

    def test_rules_and_templates_are_read_by_all_and_changed_by_the_manager_only(self):
        for role in ('employee', 'approver', 'accountant', 'advisor'):
            env = self.as_(role)
            env['expense.scan.policy'].search([])
            env['expense.scan.export.template'].search([])
            with self.assertRaises(AccessError, msg=role):
                env['expense.scan.policy'].create({'name': "x"})
            with self.assertRaises(AccessError, msg=role):
                env['expense.scan.export.template'].create({'name': "x"})
        env = self.as_('manager')
        policy = env['expense.scan.policy'].create({'name': "Mine"})
        policy.active = False
        self.assertFalse(policy.active)

    def test_only_accountants_reimburse(self):
        for role in ('employee', 'approver', 'manager'):
            with self.assertRaises(AccessError, msg=role):
                self.expense.with_user(self.users[role]).action_expense_scan_pay()
        # An accountant gets past the access check (the expense is a draft, which the next one refuses).
        with self.assertRaises(UserError):
            self.expense.with_user(self.users['accountant']).action_expense_scan_pay()

    def test_only_accounting_managers_correct_the_vat(self):
        for role in ('employee', 'approver', 'manager', 'accountant'):
            with self.assertRaises(AccessError, msg=role):
                self.expense.with_user(self.users[role]).action_expense_scan_correct_vat()

    def test_project_figures_are_for_the_expense_users(self):
        project = self.env['project.project'].create({'name': "Ergonomie", 'expense_scan_budget': 100.0})
        with self.assertRaises(AccessError):
            project.with_user(self.users['employee']).expense_scan_spent
        self.assertEqual(project.with_user(self.users['manager']).expense_scan_budget, 100.0)
        self.assertEqual(project.with_user(self.users['manager']).expense_scan_spent, 0.0)

    def test_the_team_is_for_approvers(self):
        Expense = self.env['hr.expense']
        self.assertFalse(Expense.with_user(self.users['employee'])._expense_scan_team_employees())
        self.assertIn(self.employee,
                      Expense.with_user(self.users['approver'])._expense_scan_team_employees())

    def test_the_sheet_dialog_opens_for_everybody_on_the_expenses_to_report(self):
        for role in self.users:
            env = self.as_(role)
            env = env(context=dict(env.context, default_period='current'))
            wizard = env['expense.scan.sheet.wizard'].create({})
            self.assertEqual(wizard.scope, 'all', "the re-invoiced only choice made the usual sheet empty")
            self.assertTrue(wizard.selected_summary)

    def test_the_sheet_dialog_from_the_list_does_not_hide_the_selection(self):
        wizard = self.env['expense.scan.sheet.wizard'].with_context(
            active_model='hr.expense', active_ids=self.expense.ids).create({})
        self.assertEqual(wizard._selected(), self.expense)

    # -- Where the functions are --------------------------------------------------------------

    def test_reimburse_is_a_button_on_the_form_and_on_the_list_for_accountants(self):
        for role, expected in (('accountant', True), ('advisor', True), ('employee', False),
                               ('approver', False), ('manager', False)):
            env = self.as_(role)
            for view_type in ('form', 'list'):
                arch = _arch(env, 'hr.expense', view_type)
                buttons = arch.xpath("//button[@name='action_expense_scan_pay']")
                self.assertEqual(bool(buttons), expected, "%s on %s" % (role, view_type))

    def test_save_and_close_is_not_a_second_main_button(self):
        arch = _arch(self.as_('employee'), 'hr.expense', 'form')
        button = arch.xpath("//button[@name='action_expense_scan_done']")[0]
        self.assertEqual(button.get('string'), "Save and close")
        self.assertNotIn('oe_highlight', button.get('class') or '')

    def test_the_technical_reason_of_a_failed_scan_is_for_the_managers(self):
        """An employee reads "enter the expense by hand", not a Python error or a download address."""
        banner = "//div[@name='expense_scan_error']//field[@name='scan_message']"
        self.assertFalse(_arch(self.as_('employee'), 'hr.expense', 'form').xpath(banner))
        self.assertTrue(_arch(self.as_('manager'), 'hr.expense', 'form').xpath(banner))

    def test_the_project_budget_has_a_tab_of_its_own(self):
        arch = _arch(self.as_('manager'), 'project.project', 'form')
        self.assertTrue(arch.xpath("//page[@name='expenses']//field[@name='expense_scan_budget']"))
        self.assertFalse(arch.xpath("//page[@name='settings']//field[@name='expense_scan_budget']"),
                         "the budget is not hidden among the technical settings")
        # Not shown to those who do not handle expenses.
        arch = _arch(self.as_('projectuser'), 'project.project', 'form')
        self.assertFalse(arch.xpath("//field[@name='expense_scan_budget']"))

    def test_the_project_says_where_to_turn_on_re_invoicing(self):
        project = self.env['project.project'].create({'name': "Sans refacturation"})
        self.company.expense_scan_reinvoice = False
        self.assertFalse(project.expense_scan_reinvoice_on)
        arch = _arch(self.as_('manager'), 'project.project', 'form')
        hint = arch.xpath("//div[@name='expense_scan_reinvoice_hint']")
        self.assertTrue(hint)
        self.assertEqual(hint[0].get('invisible'), 'expense_scan_reinvoice_on')
        self.company.expense_scan_reinvoice = True
        project.invalidate_recordset()
        self.assertTrue(project.expense_scan_reinvoice_on)

    def test_menus_say_what_they_open(self):
        sheet = self.env.ref('expense_scan.menu_expense_scan_sheets')
        self.assertEqual(sheet.with_context(lang='en_US').name, "Expense Sheet")
        templates = self.env.ref('expense_scan.menu_expense_scan_export_templates')
        self.assertEqual(templates.with_context(lang='en_US').name, "Excel Export Templates")
        team = self.env['hr.expense'].with_user(self.users['approver']).action_expense_scan_team()
        menu = self.env.ref('expense_scan.menu_expense_scan_team')
        self.assertEqual(team['name'], menu.with_context(lang='en_US').name)

    def test_an_empty_team_page_says_who_is_listed(self):
        action = self.env['hr.expense'].with_user(self.users['approver']).action_expense_scan_team()
        self.assertIn('o_view_nocontent_smiling_face', action['help'])
        self.assertIn("approve", action['help'])

    def test_menus_are_not_visible_without_the_right_to_open_them(self):
        policies = self.env.ref('expense_scan.menu_expense_scan_policies').id
        templates = self.env.ref('expense_scan.menu_expense_scan_export_templates').id
        team = self.env.ref('expense_scan.menu_expense_scan_team').id
        sheets = self.env.ref('expense_scan.menu_expense_scan_sheets').id
        for role, configuration, has_team in (
                ('employee', False, False), ('approver', False, True), ('manager', True, True)):
            visible = self.env['ir.ui.menu'].with_user(self.users[role])._visible_menu_ids()
            self.assertEqual(policies in visible, configuration, role)
            self.assertEqual(templates in visible, configuration, role)
            self.assertEqual(team in visible, has_team, role)
            self.assertIn(sheets, visible, role)

    def test_the_refusal_to_approve_says_where_to_decide(self):
        self.company.expense_scan_reinvoice = True
        self.expense.write({'reinvoice_mode': 'todo'})
        self.expense.action_submit()
        with self.assertRaises(UserError) as caught:
            self.expense.sudo()._do_approve()
        self.assertIn("Re-invoice", str(caught.exception))
        self.assertIn("Repas", str(caught.exception))

    # -- Rules: suspending a set is one click away ---------------------------------------------

    def test_a_rule_set_is_switched_off_from_the_list(self):
        action = self.env.ref('expense_scan.expense_scan_policy_action')
        self.assertIn("'active_test': False", action.context)
        arch = _arch(self.as_('manager'), 'expense.scan.policy', 'list')
        toggle = arch.xpath("//field[@name='active']")
        self.assertTrue(toggle)
        self.assertEqual(toggle[0].get('widget'), 'boolean_toggle')
        policy = self.env['expense.scan.policy'].search([('apply_to_all', '=', True)], limit=1)
        if not policy:
            self.skipTest("no delivered rule set")
        policy.with_user(self.users['manager']).active = False
        found = self.env['expense.scan.policy'].with_context(active_test=False).search([('id', '=', policy.id)])
        self.assertFalse(found.active)
        policy.with_user(self.users['manager']).active = True
        self.assertTrue(policy.active)

    def test_the_delivered_rules_are_not_overwritten_by_an_update(self):
        data = self.env['ir.model.data'].search([
            ('module', '=', 'expense_scan'), ('model', '=', 'expense.scan.policy')])
        if not data:
            self.skipTest("no delivered rule set")
        for record in data:
            self.assertTrue(record.noupdate, record.name)


@tagged('post_install', '-at_install')
class TestSettings(common.TransactionCase):

    def setUp(self):
        super().setUp()
        self.arch = _arch(self.env, 'res.config.settings', 'form')

    def _block_of(self, field):
        nodes = self.arch.xpath("//app[@name='hr_expense']//field[@name='%s']" % field)
        self.assertTrue(nodes, "%s is on the Expenses settings page" % field)
        for node in nodes:
            for ancestor in node.iterancestors('block'):
                return ancestor.get('name') or ancestor.get('title')
        self.fail("%s is in no block" % field)

    def test_everyday_settings_come_first(self):
        for field in BASIC_FIELDS:
            self.assertEqual(self._block_of(field), 'expenses_setting_container', field)

    def test_settings_nobody_needs_to_touch_are_under_advanced_options(self):
        for field in ADVANCED_FIELDS:
            self.assertEqual(self._block_of(field), 'expense_scan_advanced_container', field)

    def test_every_setting_has_a_name_and_a_sentence(self):
        for setting in self.arch.xpath("//app[@name='hr_expense']//block[@name='expense_scan_advanced_container']//setting"
                                       "|//app[@name='hr_expense']//setting[starts-with(@id, 'expense_scan_')]"):
            self.assertTrue(setting.get('string'), setting.get('id'))
            self.assertTrue(setting.get('help'), setting.get('id'))

    def test_defaults_suit_most_shops(self):
        Company = self.env['res.company']
        for name, expected in (
                ('expense_scan_enabled', True), ('expense_scan_reinvoice', False),
                ('expense_scan_limit_projects', True), ('expense_scan_apply_tax', True),
                ('expense_scan_set_vendor', False), ('expense_scan_engine', 'auto'),
                ('expense_scan_threads', 4), ('expense_scan_max_age_days', 730),
                ('expense_scan_text_retention_days', 3650), ('expense_scan_keep_original', True)):
            self.assertEqual(Company._fields[name].default(Company), expected, name)

    def test_the_data_files_do_not_set_what_an_administrator_chose(self):
        """An update reloads the data files: they must not carry the settings."""
        manifest = eval(open(os.path.join(MODULE_DIR, '__manifest__.py')).read())  # noqa: S307
        for path in manifest['data']:
            if not path.endswith('.xml'):
                continue
            tree = etree.parse(os.path.join(MODULE_DIR, path))
            models = set(tree.xpath('//record/@model'))
            self.assertFalse(models & {'res.company', 'res.config.settings', 'ir.config_parameter'}, path)

    def test_the_settings_tell_where_the_rules_and_templates_are(self):
        buttons = self.arch.xpath("//setting[@id='expense_scan_sheet_setting']//button/@name")
        self.assertEqual(len(buttons), 2)


@tagged('post_install', '-at_install')
class TestLanguages(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env['res.lang']
        cls.languages = []
        for code in LANGUAGES:
            Lang._activate_lang(code)
            cls.languages.append(code)
        cls.env['ir.module.module']._get('expense_scan')._update_translations(cls.languages)
        cls.manager = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Ergo langues", 'login': 'ergo_langues',
            'group_ids': [Command.set([cls.env.ref(name).id for name in (
                'base.group_user', 'base.group_system', 'hr_expense.group_hr_expense_manager',
                'account.group_account_manager', 'project.group_project_manager')])]})

    def test_the_catalogues_load(self):
        """Odoo's own reader: a stray line made seven of ten files unreadable."""
        paths = sorted(glob.glob(os.path.join(MODULE_DIR, 'i18n', '*.po')))
        self.assertEqual(len(paths), 10)
        for path in paths:
            polib.pofile(path)

    def test_every_text_is_translated_in_every_language(self):
        # The export reads every copy of the module found on the addons path: an older copy kept
        # beside this one would bring its own texts.
        copies = [path for path in addons.__path__ if os.path.isdir(os.path.join(path, 'expense_scan'))]
        if len(copies) > 1:
            self.skipTest("another copy of the module is on the addons path")
        buffer = io.BytesIO()
        trans_export(False, ['expense_scan'], buffer, 'po', self.env)
        template = polib.pofile(buffer.getvalue().decode('utf-8'))
        wanted = {(entry.msgctxt, entry.msgid) for entry in template
                  # What the demo data of the module says is not a screen text.
                  if not all(o[0].startswith('model:expense.scan.policy') for o in entry.occurrences)}
        for path in sorted(glob.glob(os.path.join(MODULE_DIR, 'i18n', '*.po'))):
            catalogue = polib.pofile(path)
            translated = {(entry.msgctxt, entry.msgid) for entry in catalogue
                          if entry.msgstr or any(entry.msgstr_plural.values())}
            self.assertFalse(wanted - translated, "%s: %s" % (
                os.path.basename(path), sorted(msgid for _ctx, msgid in wanted - translated)[:5]))

    def test_the_main_screens_load_in_every_language(self):
        views = [
            ('hr.expense', ['list', 'form', 'kanban', 'search']),
            ('project.project', ['form']),
            ('res.config.settings', ['form']),
            ('expense.scan.policy', ['list', 'form', 'search']),
            ('expense.scan.export.template', ['list', 'form']),
            ('expense.scan.sheet.wizard', ['form']),
            ('expense.scan.vat.wizard', ['form']),
        ]
        for code in self.languages:
            env = self.env(user=self.manager, context=dict(self.env.context, lang=code))
            for model, types in views:
                result = env[model].get_views([(False, view_type) for view_type in types])
                for view_type in types:
                    etree.fromstring(result['views'][view_type]['arch'])

    def test_the_new_texts_read_in_the_language_of_the_user(self):
        menu = self.env.ref('expense_scan.menu_expense_scan_sheets')
        done = {code: menu.with_context(lang=code).name for code in self.languages}
        # "Expense Sheet" has a translation of its own in every language.
        for code, name in done.items():
            self.assertTrue(name, code)
        self.assertNotEqual(len(set(done.values())), 1)
        arch = _arch(self.env(user=self.manager, context=dict(self.env.context, lang='fr_FR')),
                     'hr.expense', 'form')
        button = arch.xpath("//button[@name='action_expense_scan_done']")[0]
        self.assertEqual(button.get('string'), "Enregistrer et fermer")
