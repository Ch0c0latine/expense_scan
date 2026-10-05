# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Defects found by the quality sweeps: Excel formulas, several companies, kanban thumbnail."""
from unittest.mock import patch

from openpyxl import Workbook

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestReviewFixes(common.TransactionCase):

    def test_a_typed_text_starting_with_an_equal_sign_stays_text(self):
        sheet = Workbook().active
        Sheet = self.env['expense.scan.sheet']
        for text in ("=1+1 taxi", "=HYPERLINK(\"http://example.com\", \"x\")", "=> client"):
            cell = sheet['A1']
            Sheet._set_cell(cell, text)
            self.assertEqual(cell.data_type, 's', text)
            self.assertEqual(cell.value, text)
        Sheet._set_cell(sheet['A2'], 12.5)
        self.assertEqual(sheet['A2'].data_type, 'n')
        Sheet._set_cell(sheet['A3'], None)
        self.assertIsNone(sheet['A3'].value)

    def test_the_rules_and_templates_of_another_company_are_not_readable(self):
        other = self.env['res.company'].create({'name': "Another company"})
        mine = self.env.company
        Policy = self.env['expense.scan.policy']
        policy_mine = Policy.create({'name': "Mine", 'company_id': mine.id})
        policy_other = Policy.create({'name': "Other", 'company_id': other.id})
        policy_shared = Policy.create({'name': "Shared"})
        user = self.env['res.users'].create({
            'name': "Employee of my company", 'login': 'only_mine_review',
            'company_id': mine.id, 'company_ids': [(6, 0, [mine.id])],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        visible = Policy.with_user(user).search([('id', 'in', (policy_mine | policy_other | policy_shared).ids)])
        self.assertEqual(visible, policy_mine | policy_shared)
        template = self.env['expense.scan.export.template'].create({'name': "Other template", 'company_id': other.id})
        self.assertFalse(self.env['expense.scan.export.template'].with_user(user).search([('id', '=', template.id)]))

    def test_the_kanban_thumbnail_is_for_images_only(self):
        view = self.env.ref('expense_scan.hr_expense_view_kanban_scan')
        self.assertIn("startsWith('image/')", view.arch_db)
        self.assertIn('name="expense_scan_main_mimetype"', view.arch_db)

    def test_a_pdf_preview_is_drawn_once(self):
        from odoo.addons.expense_scan.models import hr_expense
        from odoo.addons.expense_scan.ocr import preprocess
        employee = self.env['hr.employee'].create({'name': "Preview"})
        expense = self.env['hr.expense'].create({'name': "Pdf", 'employee_id': employee.id})
        attachment = self.env['ir.attachment'].create(
            {'name': "r.pdf", 'raw': b'%PDF-1.4 preview-cache-test', 'mimetype': 'application/pdf',
             'res_model': 'hr.expense', 'res_id': expense.id})
        expense.message_main_attachment_id = attachment
        hr_expense._PDF_PREVIEWS.clear()
        with patch.object(preprocess, 'pdf_first_page_to_image_bytes', return_value=b'png') as draw:
            first = expense.expense_scan_pdf_preview()
            second = expense.expense_scan_pdf_preview()
        self.assertTrue(first.startswith('data:image/png;base64,'))
        self.assertEqual(first, second)
        self.assertEqual(draw.call_count, 1)
