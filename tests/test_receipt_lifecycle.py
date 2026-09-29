# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Vie du justificatif après l'analyse : suppression, remplacement, retouche.

Après l'analyse, le justificatif affiché est une image tirée de la photo
d'origine (recadrée, ou retouchée à la main) ; l'original est une pièce
jointe de champ, invisible. Ces scénarios vérifient que l'original, les
réglages de retouche et le repère de retouche manuelle suivent le
justificatif affiché quand l'utilisateur le supprime ou en joint un autre.
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
        """Dépense analysée : image recadrée affichée, photo d'origine cachée."""
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
        """L'utilisateur supprime le justificatif affiché et en joint un autre."""
        expense.message_main_attachment_id.unlink()
        return self.attach(expense, "nouveau.png", (30, 30, 200), main=True)

    def prepare_options(self, expense, attachment):
        """Options de préparation de l'image retenues par l'analyse."""
        from ..ocr.types import PreprocessInfo
        options = {}

        def prepare(data, **kwargs):
            options.update(kwargs)
            raise RuntimeError("arrêt après la préparation")

        with patch.object(preprocess, 'dependencies_status', return_value=(True, "")), \
                patch.object(preprocess, 'prepare', side_effect=prepare):
            with self.assertRaises(RuntimeError):
                expense._expense_scan_process(attachment)
        return options

    # -- Suppression du justificatif affiché ---------------------------------

    def test_deleting_the_shown_receipt_deletes_its_original(self):
        expense, original = self.scanned()
        expense.message_main_attachment_id.unlink()
        self.assertFalse(original.exists())
        self.assertFalse(expense.scan_original_attachment_id)

    def test_the_editor_opens_the_new_receipt(self):
        """Le cas signalé : analyse, suppression, nouveau justificatif, retouche."""
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

    def test_a_new_receipt_is_cropped_again(self):
        """Après une retouche manuelle supprimée, l'analyse recadre le nouveau."""
        expense, _original = self.scanned()
        self.retouch(expense)
        new = self.replace_receipt(expense)
        self.assertEqual(self.prepare_options(expense, new), {'autocrop': True, 'deskew': True})

    # -- Données laissées par une version précédente -------------------------

    def stale(self):
        """Justificatif remplacé avant ce correctif : l'original est resté lié."""
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

    # -- Ce qui doit rester en place ----------------------------------------

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
