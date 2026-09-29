# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Analyse lancée par la fiche : la dépense naît avant d'être lue."""
import base64

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import common, tagged

from ..ocr import preprocess
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

    def test_a_concurrent_write_is_retried_not_recorded_as_a_failure(self):
        """La fiche écrit la photo pendant l'analyse : Odoo doit rejouer."""
        import psycopg2
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            expense = self.upload(expense_scan_async=True)
        with patch.object(type(self.Expense), '_expense_scan_process', autospec=True,
                          side_effect=psycopg2.errors.SerializationFailure("concurrent")):
            with self.assertRaises(psycopg2.errors.SerializationFailure):
                expense._expense_scan_run(force=True)
        self.assertNotEqual(expense.scan_state, 'error')

    def test_a_stuck_analysis_is_given_up(self):
        """Un justificatif qui tue son worker n'est pas repris indéfiniment."""
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            expense = self.upload(expense_scan_async=True)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hr_expense SET create_date = now() - interval '1 hour',"
            " write_date = now() - interval '1 hour' WHERE id = %s", [expense.id])
        self.env.invalidate_all()
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True) as run:
            self.Expense._cron_expense_scan_pending()
        run.assert_not_called()
        self.assertEqual(expense.scan_state, 'error')

    def test_the_form_does_not_restart_a_stuck_analysis(self):
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            expense = self.upload(expense_scan_async=True)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE hr_expense SET create_date = now() - interval '1 hour' WHERE id = %s",
            [expense.id])
        self.env.invalidate_all()
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True) as run:
            result = expense.action_expense_scan_start({'scan_state': {}})
        run.assert_not_called()
        self.assertFalse(result['started'])
        self.assertEqual(result['values']['scan_state'], 'error')

    def test_rights_are_checked_before_the_row_is_locked(self):
        """Personne ne verrouille la dépense d'un autre sans y avoir droit."""
        from odoo.exceptions import AccessError
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            expense = self.upload(expense_scan_async=True)
        stranger = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Passant", 'login': 'expense_scan_passant',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            expense.with_user(stranger).action_expense_scan_start({'scan_state': {}})

    def test_old_submitted_texts_are_erased(self):
        """Le texte lu est une donnée personnelle : il ne se garde pas indéfiniment."""
        old = self.Expense.create({
            'name': "Vieux ticket", 'employee_id': self.employee.id,
            'product_id': self.env.company.expense_scan_product_id.id or
            self.env['product.product'].search([('can_be_expensed', '=', True)], limit=1).id,
            'date': '2020-01-15', 'total_amount_currency': 10.0, 'scan_raw_text': "Jean Dupont 12 rue X CB **** 1234"})
        old.write({'approval_state': 'submitted'})
        draft = self.Expense.create({
            'name': "Brouillon", 'employee_id': self.employee.id,
            'product_id': old.product_id.id,
            'date': '2020-01-15', 'total_amount_currency': 10.0, 'scan_raw_text': "reste"})
        self.env.company.expense_scan_text_retention_days = 365
        self.Expense._cron_expense_scan_purge_texts()
        self.assertFalse(old.scan_raw_text)
        self.assertEqual(draft.scan_raw_text, "reste")  # un brouillon garde le sien

    def test_zero_days_keeps_texts_forever(self):
        old = self.Expense.create({
            'name': "Vieux ticket", 'employee_id': self.employee.id,
            'product_id': self.env['product.product'].search(
                [('can_be_expensed', '=', True)], limit=1).id,
            'date': '2020-01-15', 'total_amount_currency': 10.0, 'scan_raw_text': "reste"})
        old.write({'approval_state': 'submitted'})
        self.env.company.expense_scan_text_retention_days = 0
        self.Expense._cron_expense_scan_purge_texts()
        self.assertEqual(old.scan_raw_text, "reste")


@tagged('post_install', '-at_install')
class TestRetouch(common.TransactionCase):
    """Retouche manuelle : rotation fine et recadrage faits dans le navigateur.

    Le serveur reçoit le résultat en JPEG. Les tests portent sur la
    correction du justificatif sur place, pas sur le canevas côté client.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Léon Retouche"})
        cls.Expense = cls.env['hr.expense']

    def expense_with_attachment(self):
        with patch.object(type(self.Expense), '_expense_scan_run', autospec=True):
            ids = self.Expense.with_context(
                default_employee_id=self.employee.id
            ).create_expense_from_attachments(
                [self.env['ir.attachment'].create({
                    'name': "ticket.png", 'raw': png(),
                    'res_model': 'hr.expense', 'res_id': 0}).id], 'list')
        return self.Expense.browse(ids)

    def test_the_current_attachment_is_corrected_in_place(self):
        expense = self.expense_with_attachment()
        attachment = expense.message_main_attachment_id
        retouched = png(color=(10, 200, 10))
        expense.action_expense_scan_retouch(base64.b64encode(retouched).decode())
        self.assertEqual(expense.message_main_attachment_id, attachment)  # même pièce
        self.assertEqual(attachment.raw, retouched)
        self.assertEqual(attachment.mimetype, 'image/jpeg')

    def test_without_attachment_it_refuses(self):
        expense = self.Expense.create({'name': "Sans photo", 'employee_id': self.employee.id})
        with self.assertRaises(UserError):
            expense.action_expense_scan_retouch(base64.b64encode(png()).decode())

    def test_garbled_data_is_refused(self):
        expense = self.expense_with_attachment()
        with self.assertRaises(UserError):
            expense.action_expense_scan_retouch("ceci n'est pas du base64 valide%%%")

    def test_an_oversized_image_is_refused(self):
        expense = self.expense_with_attachment()
        with patch.object(preprocess, 'MAX_FILE_BYTES', 10):
            with self.assertRaises(UserError):
                expense.action_expense_scan_retouch(base64.b64encode(png()).decode())

    def test_a_stranger_cannot_retouch(self):
        expense = self.expense_with_attachment()
        stranger = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Passant Retouche", 'login': 'expense_scan_retouch_passant',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        with self.assertRaises(AccessError):
            expense.with_user(stranger).action_expense_scan_retouch(
                base64.b64encode(png()).decode())
