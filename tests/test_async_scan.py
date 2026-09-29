# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Analyse lancée par la fiche : la dépense naît avant d'être lue."""
import base64
import json

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
    """Retouche manuelle : rotation et recadrage faits dans le navigateur.

    Le serveur reçoit le résultat en JPEG. Les tests portent sur le sort des
    pièces jointes et sur l'analyse qui suit, pas sur le canevas client.
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

    def test_the_original_is_kept_and_the_retouch_is_shown(self):
        """Sans image recadrée, la retouche devient une nouvelle pièce affichée."""
        expense = self.expense_with_attachment()
        original = expense.message_main_attachment_id
        photo = original.raw
        retouched = png(color=(10, 200, 10))
        expense.action_expense_scan_retouch(base64.b64encode(retouched).decode())
        self.assertNotEqual(expense.message_main_attachment_id, original)
        self.assertEqual(expense.message_main_attachment_id.raw, retouched)
        self.assertEqual(expense.scan_cropped_attachment_id, expense.message_main_attachment_id)
        self.assertEqual(expense.scan_original_attachment_id, original)
        self.assertEqual(original.raw, photo)
        self.assertTrue(expense.expense_scan_manual_retouch)

    def test_a_second_retouch_replaces_the_first_in_place(self):
        expense = self.expense_with_attachment()
        expense.action_expense_scan_retouch(base64.b64encode(png(color=(10, 200, 10))).decode())
        shown = expense.message_main_attachment_id
        again = png(color=(10, 10, 200))
        expense.action_expense_scan_retouch(base64.b64encode(again).decode())
        self.assertEqual(expense.message_main_attachment_id, shown)
        self.assertEqual(shown.raw, again)

    def test_the_analysis_reads_a_retouched_image_as_it_is(self):
        """Pas de recadrage, redressement ni rotation automatiques après une retouche."""
        from ..ocr.types import OcrWord, PreprocessInfo

        class Engine:
            label = description = "faux moteur"

            def recognize(self, image):
                return [OcrWord(text="TOTAL 12,50", score=0.99,
                                left=0, top=0, right=100, bottom=20)]

        expense = self.expense_with_attachment()
        expense.action_expense_scan_retouch(base64.b64encode(png()).decode())
        options = {}

        def prepare(data, **kwargs):
            options.update(kwargs)
            return "image", PreprocessInfo(changed=True)

        Model = type(self.Expense)
        with patch.object(preprocess, 'dependencies_status', return_value=(True, "")), \
                patch.object(preprocess, 'prepare', side_effect=prepare), \
                patch('odoo.addons.expense_scan.ocr.engines.resolve_engine',
                      return_value=Engine()), \
                patch.object(Model, '_expense_scan_straighten') as straighten, \
                patch.object(Model, '_expense_scan_reorient') as reorient, \
                patch.object(Model, '_expense_scan_tighten') as tighten:
            result = expense._expense_scan_process(expense.message_main_attachment_id)
        self.assertEqual(options, {'autocrop': False, 'deskew': False})
        straighten.assert_not_called()
        reorient.assert_not_called()
        tighten.assert_not_called()
        self.assertIsNone(result.image_bytes)
        self.assertEqual(result.value('total'), 12.5)

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

    def test_the_editor_starts_from_the_uploaded_image_with_the_last_settings(self):
        expense = self.expense_with_attachment()
        original = expense.message_main_attachment_id
        data = expense.expense_scan_retouch_data()
        self.assertEqual(data, {'url': '/web/image/%d' % original.id, 'params': None})
        params = {'quarter': 1, 'fine': -2.5, 'crop': [0.1, 0.2, 0.9, 0.8]}
        expense.action_expense_scan_retouch(base64.b64encode(png()).decode(), params)
        data = expense.expense_scan_retouch_data()
        self.assertEqual(data['url'], '/web/image/%d' % original.id)  # pas l'image retouchée
        self.assertEqual(data['params'], params)

    def test_retouch_settings_are_checked(self):
        clean = type(self.Expense)._expense_scan_clean_retouch_params
        self.assertFalse(clean(None))
        self.assertFalse(clean({'quarter': 0, 'fine': 0, 'crop': [0.5, 0, 0.4, 1]}))
        self.assertEqual(
            json.loads(clean({'quarter': 5, 'fine': 80, 'crop': [-1, 0, 1, 2]})),
            {'quarter': 1, 'fine': 45.0, 'crop': [0.0, 0.0, 1.0, 1.0]})

    def test_a_pdf_is_rendered_for_the_editor(self):
        expense = self.expense_with_attachment()
        expense.message_main_attachment_id.write({'name': "facture.pdf",
                                                  'mimetype': 'application/pdf'})
        with patch.object(preprocess, 'pdf_first_page_to_image_bytes', return_value=b'PNG'):
            data = expense.expense_scan_retouch_data()
        self.assertEqual(data['url'], 'data:image/png;base64,' + base64.b64encode(b'PNG').decode())

    def test_text_frame_follows_the_rotation(self):
        from ..ocr.types import OcrWord
        frame = type(self.Expense)._expense_scan_text_frame
        words = [OcrWord(text="ligne", score=0.9, left=100, top=top, right=300, bottom=top + 20)
                 for top in (100, 150, 200)]
        x0, y0, x1, y1 = frame(words, 400, 1000, 0, margin_ratio=0)
        self.assertAlmostEqual(x0 * 400, 92)   # marge fixe de 8 px
        self.assertAlmostEqual(y1 * 1000, 228)
        # Un quart de tour horaire : l'image fait 1000 × 400, le texte (en
        # haut à gauche de la photo) passe en haut à droite.
        x0, y0, x1, y1 = frame(words, 400, 1000, 90, margin_ratio=0)
        self.assertAlmostEqual(x0 * 1000, 1000 - 220 - 8)
        self.assertAlmostEqual(y0 * 400, 100 - 8)

    def test_auto_retouch_proposes_rotation_and_frame(self):
        from ..ocr.types import OcrWord

        class Engine:
            label = description = "faux moteur"

            def recognize(self, image):
                # Lignes penchées de 3° dans le sens antihoraire de l'image.
                return [OcrWord(text="TOTAL 12,50", score=0.95, angle=3.0,
                                left=10, top=10 + 30 * row, right=50, bottom=30 + 30 * row)
                        for row in range(4)]

        expense = self.expense_with_attachment()
        Model = type(self.Expense)
        with patch.object(preprocess, 'dependencies_status', return_value=(True, "")), \
                patch('odoo.addons.expense_scan.ocr.engines.resolve_engine',
                      return_value=Engine()), \
                patch.object(Model, '_expense_scan_quarters', return_value=0):
            params = expense.expense_scan_auto_retouch_params()
        self.assertEqual(params['quarter'], 0)
        self.assertEqual(params['fine'], -3.0)
        self.assertEqual(len(params['crop']), 4)
        self.assertLess(params['crop'][2] - params['crop'][0], 1.0)


@tagged('post_install', '-at_install')
class TestReceiptOnNewExpense(common.TransactionCase):
    """Justificatif joint depuis le volet de discussion à une dépense neuve."""

    def test_defaults_make_a_new_expense_savable(self):
        Expense = self.env['hr.expense']
        defaults = Expense.expense_scan_receipt_defaults()
        self.assertTrue(defaults['name'])
        self.assertTrue(defaults['product_id']['id'])
        expense = Expense.create({
            'name': defaults['name'], 'product_id': defaults['product_id']['id'],
            'employee_id': self.env['hr.employee'].create({'name': "Nina Neuve"}).id})
        self.assertTrue(expense._expense_scan_name_is_automatic())

    def test_only_a_never_analysed_expense_is_analysed(self):
        self.env.company.expense_scan_enabled = True
        employee = self.env['hr.employee'].create({'name': "Nina Neuve"})
        Expense = self.env['hr.expense']
        fresh = Expense.create({'name': "Neuve", 'employee_id': employee.id})
        done = Expense.create({'name': "Déjà lue", 'employee_id': employee.id,
                               'scan_state': 'done'})
        with patch.object(type(Expense), '_expense_scan_run', autospec=True) as run:
            fresh.expense_scan_analyze_new_receipt()
            done.expense_scan_analyze_new_receipt()
        self.assertEqual([call.args[0] for call in run.call_args_list], [fresh])
