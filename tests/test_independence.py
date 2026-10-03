# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The module stands alone: Odoo apps only, and an API a companion module may use."""
import importlib.util
import io
import os
import re
from datetime import date

from odoo.modules.module import get_manifest
from odoo.tests import common, tagged

MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _migration(version, name):
    path = os.path.join(MODULE_DIR, 'migrations', version, name)
    spec = importlib.util.spec_from_file_location('expense_scan_migration_%s' % version.replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png():
    from PIL import Image
    output = io.BytesIO()
    Image.new('RGB', (60, 90), (200, 30, 30)).save(output, format='PNG')
    return output.getvalue()


@tagged('post_install', '-at_install')
class TestIndependence(common.TransactionCase):

    def test_depends_on_odoo_apps_only(self):
        self.assertEqual(sorted(get_manifest('expense_scan')['depends']), ['hr_expense', 'project'])
        module = self.env['ir.module.module']._get('expense_scan')
        upstream = module.upstream_dependencies(exclude_states=('uninstallable',)).mapped('name')
        self.assertIn('hr_expense', upstream)
        for name in ('mission_report', 'sale', 'sale_project', 'sale_expense'):
            self.assertNotIn(name, upstream)

    def test_no_reference_to_other_modules_in_the_data(self):
        """No xmlid of Sales or of a companion module in the data, views or code it loads.

        Sales is used only when it is there, by model name and ``raise_if_not_found=False``.
        """
        data = re.compile(r"\b(?:sale|sale_\w+|mission_report)\.\w+")
        code = re.compile(r"mission_report\.|odoo\.addons\.(?:mission_report|sale)\b|mission\.activity")
        found = []
        for folder in ('models', 'views', 'data', 'security', 'report', os.path.join('static', 'src')):
            for root, _dirs, files in os.walk(os.path.join(MODULE_DIR, folder)):
                for name in files:
                    pattern = data if name.endswith(('.xml', '.csv')) else \
                        code if name.endswith(('.py', '.js')) else None
                    if not pattern:
                        continue
                    path = os.path.join(root, name)
                    with open(path, encoding='utf-8') as source:
                        found += ["%s: %s" % (os.path.relpath(path, MODULE_DIR), match.group(0))
                                  for match in pattern.finditer(source.read())]
        self.assertFalse(found)

    def test_sheet_files_of_an_invoice(self):
        """``expense_scan_sheet_files``: the Excel table on the model of the project, the receipts."""
        template = self.env.ref('expense_scan.export_template_basic')
        project = self.env['project.project'].create({
            'name': "Mission autonome", 'expense_scan_sheet_template_id': template.id})
        employee = self.env['hr.employee'].create({'name': "Alix Autonome"})
        category = self.env['product.product'].create({'name': "Hôtel autonome", 'can_be_expensed': True})
        expense = self.env['hr.expense'].create({
            'name': "Hôtel", 'employee_id': employee.id, 'product_id': category.id,
            'date': date(2026, 9, 8), 'total_amount_currency': 120.0, 'project_id': project.id})
        self.env['ir.attachment'].create({
            'name': "recu.png", 'raw': png(), 'mimetype': 'image/png',
            'res_model': 'hr.expense', 'res_id': expense.id})
        invoice = self.env['account.move'].new({'move_type': 'out_invoice'})

        self.assertEqual(invoice.expense_scan_sheet_files(), [], "an invoice that bills no expense")
        files = invoice.expense_scan_sheet_files(expense, project)
        self.assertEqual([mimetype for _name, _content, mimetype in files], [XLSX, 'application/pdf'])
        self.assertTrue(files[0][0].endswith('.xlsx'))
        self.assertTrue(files[1][1].startswith(b'%PDF'))
        # The projects default to those of the expenses.
        self.assertEqual([name for name, *_rest in invoice.expense_scan_sheet_files(expense)],
                         [name for name, *_rest in files])

        project.expense_scan_sheet_template_id = False
        files = invoice.expense_scan_sheet_files(expense, project)
        self.assertEqual([mimetype for _name, _content, mimetype in files], ['application/pdf'],
                         "no model on the project: the receipts only")

    def test_migration_moves_the_excel_model_to_the_project(self):
        """The model a companion module kept on the sales order goes to the project of the order."""
        if 'sale.order' not in self.env or 'project_id' not in self.env['sale.order']._fields:
            self.skipTest("Sales is not installed")
        Template = self.env['expense.scan.export.template']
        template, own = Template.create([{'name': "Modèle du client"}, {'name': "Modèle du projet"}])
        partner = self.env['res.partner'].create({'name': "Client autonome"})
        mission, kept, alone = self.env['project.project'].create([
            {'name': "Mission reprise"}, {'name': "Mission qui a son modèle",
                                          'expense_scan_sheet_template_id': own.id},
            {'name': "Mission sans commande"}])
        orders = self.env['sale.order'].create([
            {'partner_id': partner.id, 'project_id': mission.id},
            {'partner_id': partner.id, 'project_id': kept.id}])
        cr = self.env.cr
        self.env.flush_all()
        cr.execute("ALTER TABLE sale_order ADD COLUMN mission_expense_template_id integer")
        cr.execute("UPDATE sale_order SET mission_expense_template_id = %s WHERE id IN %s",
                   (template.id, tuple(orders.ids)))

        migration = _migration('19.0.2.11.0', 'post-migrate.py')
        for _again in range(2):
            migration.migrate(cr, '19.0.2.10.0')
            self.env.invalidate_all()
            self.assertEqual(mission.expense_scan_sheet_template_id, template)
            self.assertEqual(kept.expense_scan_sheet_template_id, own, "a model already chosen stays")
            self.assertFalse(alone.expense_scan_sheet_template_id)

    def test_migration_without_the_old_column(self):
        """A new database, or one without the companion module: nothing to do, no error."""
        cr = self.env.cr
        cr.execute("SELECT 1 FROM information_schema.columns "
                   "WHERE table_name = 'sale_order' AND column_name = 'mission_expense_template_id'")
        if cr.fetchone():
            self.skipTest("the old column is still there")
        project = self.env['project.project'].create({'name': "Mission neuve"})
        self.env.flush_all()
        _migration('19.0.2.11.0', 'post-migrate.py').migrate(cr, '19.0.2.10.0')
        self.env.invalidate_all()
        self.assertFalse(project.expense_scan_sheet_template_id)
