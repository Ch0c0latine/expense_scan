# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Analyse lancée par la fiche : la dépense naît avant d'être lue."""
from unittest.mock import patch

from odoo.tests import common, tagged

from .test_sheet import png


@tagged('post_install', '-at_install')
class TestAsyncScan(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.expense_scan_enabled = True
        cls.employee = cls.env['hr.employee'].create({'name': "Camille Attente"})
        cls.Expense = cls.env['hr.expense']

    def upload(self, **context):
        attachment = self.env['ir.attachment'].create({
            'name': "ticket.png", 'raw': png(), 'res_model': 'hr.expense', 'res_id': 0})
        ids = self.Expense.with_context(
            default_employee_id=self.employee.id, **context
        ).create_expense_from_attachments([attachment.id], 'list')
        return self.Expense.browse(ids)

    def test_a_single_upload_waits_for_its_form(self):
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True) as run:
            expense = self.upload(expense_scan_async=True)
        run.assert_not_called()
        self.assertEqual(expense.scan_state, 'running')

    def test_other_uploads_are_read_at_once(self):
        """Mail, envoi multiple, appel sans le drapeau : comme avant."""
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True) as run:
            self.upload()
        run.assert_called_once()

    def test_the_form_starts_the_analysis_once(self):
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            expense = self.upload(expense_scan_async=True)

        def finish(records, **kwargs):
            records.write({'scan_state': 'done'})

        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True,
                          side_effect=finish) as run:
            result = expense.action_expense_scan_start({'scan_state': {}})
            again = expense.action_expense_scan_start({'scan_state': {}})
        run.assert_called_once()
        self.assertTrue(result['started'])
        self.assertEqual(result['values']['scan_state'], 'done')
        self.assertFalse(again['started'])

    def test_a_forgotten_analysis_is_picked_up(self):
        """L'application s'est fermée avant de lancer l'analyse."""
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            expense = self.upload(expense_scan_async=True)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hr_expense SET write_date = now() - interval '1 hour' WHERE id = %s",
            [expense.id])
        self.env.invalidate_all()
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True) as run:
            self.Expense._cron_expense_scan_pending()
        run.assert_called_once()
