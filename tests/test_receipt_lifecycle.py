# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Life of the receipt after the scan: deletion, replacement, retouch.

After the scan, the displayed receipt is an image taken from the original
photo (cropped, or retouched by hand); the original is a hidden field
attachment. These scenarios check that the original, the retouch settings
and the manual retouch flag follow the displayed receipt when the user
deletes it or attaches another one.
"""
import base64
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests import common, tagged

from ..ocr import preprocess
from .test_sheet import png


@tagged('post_install', '-at_install')
class TestReceiptLifecycle(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.expense_scan_keep_original = True
        cls.company.expense_scan_autocrop = True
        cls.employee = cls.env['hr.employee'].create({'name': "Léa Justificatif"})
        cls.Expense = cls.env['hr.expense']
        cls.Attachment = cls.env['ir.attachment']

    def attach(self, expense, name, color, main=False):
        attachment = self.Attachment.create({
            'name': name, 'raw': png(color=color), 'mimetype': 'image/png',
            'res_model': 'hr.expense', 'res_id': expense.id})
        if main:
            expense._message_set_main_attachment_id(attachment, force=True)
        return attachment

    def scanned(self):
        """Scanned expense: cropped image displayed, original photo hidden."""
        expense = self.Expense.create({'name': "Ticket", 'employee_id': self.employee.id})
        original = self.attach(expense, "ticket.png", (200, 30, 30), main=True)
        self.store(expense, original, (180, 40, 40))
        self.assertEqual(expense.scan_original_attachment_id, original)
        self.assertEqual(expense.message_main_attachment_id, expense.scan_cropped_attachment_id)
        return expense, original

    def store(self, expense, attachment, color):
        result = SimpleNamespace(image_bytes=png(color=color))
        expense.write(expense._expense_scan_store_image(result, attachment, self.company))

    def retouch(self, expense, color=(10, 200, 10), params=None):
        expense.action_expense_scan_retouch(
            base64.b64encode(png(color=color)).decode(),
            params or {'quarter': 1, 'fine': 0, 'crop': [0, 0, 1, 1]})

    def replace_receipt(self, expense):
        """The user deletes the displayed receipt and attaches another one."""
        expense.message_main_attachment_id.unlink()
        return self.attach(expense, "nouveau.png", (30, 30, 200), main=True)

    def prepare_options(self, expense, attachment):
        """Image preparation options chosen by the scan."""
        from ..ocr.types import PreprocessInfo
        options = {}

        def prepare(data, **kwargs):
            options.update(kwargs)
            raise RuntimeError("stop after the preparation")

        with patch.object(preprocess, 'dependencies_status', return_value=(True, "")), \
                patch.object(preprocess, 'prepare', side_effect=prepare):
            with self.assertRaises(RuntimeError):
                expense._expense_scan_process(attachment)
        return options

    # -- Deleting the displayed receipt -------------------------------------

    def test_deleting_the_shown_receipt_deletes_its_original(self):
        expense, original = self.scanned()
        expense.message_main_attachment_id.unlink()
        self.assertFalse(original.exists())
        self.assertFalse(expense.scan_original_attachment_id)

    def test_the_editor_opens_the_new_receipt(self):
        """The reported case: scan, deletion, new receipt, retouch."""
        expense, _original = self.scanned()
        new = self.replace_receipt(expense)
        self.assertEqual(expense.expense_scan_retouch_data(),
                         {'url': '/web/image/%d' % new.id, 'params': None})

    def test_a_retouched_receipt_deleted_takes_its_settings_along(self):
        expense, original = self.scanned()
        self.retouch(expense)
        self.assertTrue(expense.expense_scan_manual_retouch)
        new = self.replace_receipt(expense)
        self.assertFalse(original.exists())
        self.assertFalse(expense.expense_scan_manual_retouch)
        self.assertFalse(expense.expense_scan_retouch_params)
        self.assertEqual(expense.expense_scan_retouch_data()['url'], '/web/image/%d' % new.id)

    def read(self, expense):
        """The scan has read the displayed receipt; the amount was corrected since."""
        expense.write({
            'scan_state': 'partial', 'scan_todo': "Date", 'expense_scan_todo_codes': 'date',
            'scan_raw_text': "CAFE EXEMPLE\nTOTAL 3,00", 'total_amount_currency': 3.0,
            'expense_scan_merchant_read': "CAFE EXEMPLE"})
        expense.expense_scan_read_values = '{"total_amount_currency": 3.0}'
        expense.total_amount_currency = 4.5

    def test_deleting_the_last_receipt_forgets_its_reading(self):
        expense, _original = self.scanned()
        self.read(expense)
        expense.message_main_attachment_id.unlink()
        self.assertEqual(expense.scan_state, 'none')
        self.assertFalse(expense.scan_todo or expense.expense_scan_todo_codes)
        self.assertFalse(expense.scan_raw_text or expense.expense_scan_merchant_read)
        self.assertTrue(expense.expense_scan_receipt_removed)
        self.assertEqual(expense.expense_scan_manual_fields, 'total_amount_currency')
        self.assertEqual(expense.total_amount_currency, 4.5)

    def test_deleting_a_second_receipt_keeps_the_reading(self):
        expense, _original = self.scanned()
        self.read(expense)
        self.attach(expense, "second.png", (30, 200, 30)).unlink()
        self.assertEqual(expense.scan_state, 'partial')
        self.assertFalse(expense.expense_scan_receipt_removed)

    def test_the_receipt_attached_next_is_scanned(self):
        """The fields corrected by hand are kept by the new scan."""
        self.company.expense_scan_enabled = True
        expense, _original = self.scanned()
        self.read(expense)
        contexts = []
        run = patch.object(type(expense), '_expense_scan_run_pieces', autospec=True,
                           side_effect=lambda record: contexts.append(
                               (record._expense_scan_kept_fields(), dict(record.env.context))))
        with run:
            self.assertFalse(expense.expense_scan_receipt_attached())
            self.replace_receipt(expense)
            self.assertTrue(expense.expense_scan_receipt_attached())
            self.assertFalse(expense.expense_scan_receipt_attached())
        self.assertEqual(len(contexts), 1)
        kept, context = contexts[0]
        self.assertEqual(kept, {'total_amount_currency'})
        self.assertTrue(context['expense_scan_new_receipt'])

    def test_scanning_again_without_a_receipt_says_so(self):
        from odoo.exceptions import UserError
        expense = self.Expense.create({'name': "Sans ticket", 'employee_id': self.employee.id})
        with self.assertRaises(UserError):
            expense.action_expense_scan_rescan()

    def test_an_image_that_is_not_a_receipt_says_so(self):
        """A logo: one point to check, and no reading confidence shown."""
        from ..ocr import parser
        from .test_parser import words_from_text
        expense = self.Expense.create({'name': "Logo", 'employee_id': self.employee.id})
        result = parser.parse(words_from_text("EXEMPLE SARL\nINGENIERIE"))
        with patch.object(type(expense), '_expense_scan_store_image', autospec=True,
                          return_value={}):
            expense.with_context(lang='en_US')._expense_scan_apply(result, self.Attachment)
        codes = expense.expense_scan_todo_codes.split(',')
        self.assertIn('total', codes)
        self.assertNotIn('date', codes)
        self.assertIn("Receipt?", expense.scan_todo)
        self.assertNotIn("%", expense.scan_message)

    def test_a_new_receipt_is_cropped_again(self):
        """After a deleted manual retouch, the scan crops the new receipt."""
        expense, _original = self.scanned()
        self.retouch(expense)
        new = self.replace_receipt(expense)
        self.assertEqual(self.prepare_options(expense, new), {'autocrop': True, 'deskew': True})

    # -- Data left by a previous version -------------------------------------

    def stale(self):
        """Receipt replaced before this fix: the original stayed linked."""
        expense, original = self.scanned()
        self.retouch(expense)
        cropped = expense.scan_cropped_attachment_id
        new = self.attach(expense, "nouveau.png", (30, 30, 200), main=True)
        cropped.with_context(expense_scan_keep_original=True).unlink()
        self.assertEqual(expense.scan_original_attachment_id, original)
        return expense, original, new

    def test_a_stale_original_is_not_opened_by_the_editor(self):
        expense, _original, new = self.stale()
        self.assertEqual(expense.expense_scan_retouch_data(),
                         {'url': '/web/image/%d' % new.id, 'params': None})

    def test_a_retouch_never_overwrites_the_new_receipt(self):
        expense, original, new = self.stale()
        photo = new.raw
        self.retouch(expense, color=(0, 0, 0))
        self.assertEqual(new.raw, photo)
        self.assertEqual(expense.scan_original_attachment_id, new)
        self.assertNotEqual(expense.message_main_attachment_id, new)
        self.assertFalse(original.exists())

    def test_a_stale_retouch_flag_does_not_block_the_crop(self):
        expense, _original, new = self.stale()
        self.assertEqual(self.prepare_options(expense, new), {'autocrop': True, 'deskew': True})

    def test_a_new_analysis_replaces_a_stale_original(self):
        expense, original, new = self.stale()
        self.store(expense, new, (60, 60, 60))
        self.assertFalse(original.exists())
        self.assertEqual(expense.scan_original_attachment_id, new)
        self.assertEqual(expense.message_main_attachment_id, expense.scan_cropped_attachment_id)

    # -- What must stay in place --------------------------------------------

    def test_a_second_receipt_leaves_the_original_in_place(self):
        expense, original = self.scanned()
        shown = expense.message_main_attachment_id
        self.attach(expense, "second.png", (30, 200, 30))
        self.assertEqual(expense.message_main_attachment_id, shown)
        self.assertEqual(expense.expense_scan_retouch_data()['url'], '/web/image/%d' % original.id)

    def test_a_new_analysis_from_the_original_keeps_it(self):
        expense, original = self.scanned()
        first = expense.scan_cropped_attachment_id
        self.store(expense, original, (90, 90, 90))
        self.assertTrue(original.exists())
        self.assertFalse(first.exists())
        self.assertEqual(expense.scan_original_attachment_id, original)
        self.assertEqual(expense.message_main_attachment_id, expense.scan_cropped_attachment_id)

    def test_the_editor_keeps_its_settings_while_the_retouch_is_shown(self):
        expense, original = self.scanned()
        params = {'quarter': 1, 'fine': -1.5, 'crop': [0.1, 0.1, 0.9, 0.9]}
        self.retouch(expense, params=params)
        self.assertEqual(expense.expense_scan_retouch_data(),
                         {'url': '/web/image/%d' % original.id, 'params': params})

    def test_deleting_a_second_receipt_leaves_the_original(self):
        expense, original = self.scanned()
        second = self.attach(expense, "second.png", (30, 200, 30))
        second.unlink()
        self.assertTrue(original.exists())
        self.assertEqual(expense.scan_original_attachment_id, original)

    def test_deleting_the_expense_is_not_blocked(self):
        expense, original = self.scanned()
        self.retouch(expense)
        expense.unlink()
        self.assertFalse(expense.exists())

    def test_a_guessed_category_does_not_outlive_its_reading(self):
        """New reading without a recognised category: back to the default category."""
        from ..ocr import parser
        from .test_parser import words_from_text
        Product = self.env['product.product']
        default = Product.create({'name': "Catégorie par défaut", 'can_be_expensed': True})
        guessed = Product.create({'name': "Catégorie devinée", 'can_be_expensed': True})
        chosen = Product.create({'name': "Catégorie choisie", 'can_be_expensed': True})
        self.company.expense_scan_product_id = default
        expense = self.Expense.create({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': guessed.id, 'expense_scan_guessed_product_id': guessed.id})
        unknown = parser.parse(words_from_text("QWZX KLOMP\nTOTAL 5,00"))
        values = expense._expense_scan_category_values(unknown, self.company)
        self.assertEqual(values['product_id'], default.id)
        expense.product_id = chosen
        values = expense._expense_scan_category_values(unknown, self.company)
        self.assertNotIn('product_id', values)


@tagged('post_install', '-at_install')
class TestAutomaticName(common.TransactionCase):
    """Automatic description: written in the employee's language, recognised in all."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('fr_FR')
        cls.product = cls.env['product.product'].with_context(lang='en_US').create({
            'name': "Zorblax Meals", 'can_be_expensed': True})
        cls.product.with_context(lang='fr_FR').name = "Zorblax Repas"
        user = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Nina Langue", 'login': 'expense_scan_nina_langue', 'lang': 'fr_FR'})
        cls.employee = cls.env['hr.employee'].create({'name': "Nina Langue", 'user_id': user.id})

    def expense(self, name):
        return self.env['hr.expense'].with_context(lang='fr_FR').create({
            'name': name, 'employee_id': self.employee.id, 'product_id': self.product.id})

    def dated(self, label):
        """Expense named like an automatic description: "<label> on 23/09/2026"."""
        expense = self.expense("Neuve")
        expense.name = expense._expense_scan_date_name(label, "23/09/2026")
        return expense

    def test_an_english_automatic_name_is_recognized(self):
        self.assertTrue(self.dated("Zorblax Meals")._expense_scan_name_is_automatic())
        self.assertTrue(self.dated("Zorblax Repas")._expense_scan_name_is_automatic())
        self.assertFalse(self.dated("Salon Zorblax")._expense_scan_name_is_automatic())

    def test_the_automatic_name_is_in_the_employee_language(self):
        from datetime import date
        expense = self.expense("Neuve").with_context(lang='en_US')
        name = expense._expense_scan_auto_name(self.product, date(2026, 9, 23))
        self.assertTrue(name.startswith("Zorblax Repas"), name)
