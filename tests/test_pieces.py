# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Receipts in several pieces: comparison and merging.

These rules decide whether two photos attached to the same e-mail describe
one purchase or two, without the user stepping in. A mistake therefore goes
unnoticed: pieces are only split on a clear difference.
"""
from datetime import date
from unittest.mock import patch

from odoo.tests import common, tagged

from ..ocr.types import ExtractedField, ScanResult
from .test_sheet import png


def reading(**values):
    """Build a reading without going through the OCR."""
    return ScanResult(fields={
        name: ExtractedField(value=value, confidence=0.9)
        for name, value in values.items()
    })


@tagged('post_install', '-at_install')
class TestExpenseScanPieces(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Expense = cls.env['hr.expense']

    def same(self, first, second):
        return self.Expense._expense_scan_same_receipt(first, second)

    # -- Comparison -------------------------------------------------------

    def test_card_slip_and_till_receipt_are_one(self):
        """The usual case: same amount, same day, two pieces."""
        till = reading(total=33.10, date=date(2026, 9, 7), merchant="Les 3 Brasseurs")
        card = reading(total=33.10, date=date(2026, 9, 7))
        self.assertTrue(self.same(till, card))

    def test_rounding_gap_is_not_a_difference(self):
        """Two cents of reading gap do not make two expenses."""
        self.assertTrue(self.same(reading(total=33.10), reading(total=33.08)))

    def test_clearly_different_totals_split(self):
        self.assertFalse(self.same(reading(total=33.10), reading(total=8.40)))

    def test_different_dates_split(self):
        self.assertFalse(self.same(
            reading(total=20.00, date=date(2026, 9, 7)),
            reading(total=20.00, date=date(2026, 9, 8)),
        ))

    def test_missing_total_keeps_them_together(self):
        """When in doubt, pieces stay together.

        A card slip often prints only an amount, without a date or a
        merchant. Splitting on that absence would produce a ghost expense
        for every card payment.
        """
        self.assertTrue(self.same(
            reading(total=33.10, date=date(2026, 9, 7)),
            reading(merchant="Les 3 Brasseurs"),
        ))

    def test_big_amounts_tolerate_a_bigger_gap(self):
        """A two euro gap on a thousand proves nothing; on ten, it does."""
        self.assertTrue(self.same(reading(total=1000.00), reading(total=1002.00)))
        self.assertFalse(self.same(reading(total=10.00), reading(total=12.00)))

    # -- Merging ----------------------------------------------------------

    def test_merge_keeps_the_richest_reading(self):
        poor = reading(total=33.10)
        rich = reading(total=33.10, date=date(2026, 9, 7),
                       merchant="Les 3 Brasseurs", tax_amount=3.32)
        merged = self.Expense._expense_scan_merge_pieces([poor, rich])
        self.assertEqual(merged.value('merchant'), "Les 3 Brasseurs")
        self.assertEqual(merged.value('tax_amount'), 3.32)

    def test_merge_fills_the_gaps_only(self):
        """The pieces complete each other without contradicting each other.

        The time comes from the card slip and the merchant from the till
        receipt; the total of the most complete reading is kept, even if the
        other shows a different one.
        """
        card = reading(total=33.15, time="20:42")
        till = reading(total=33.10, date=date(2026, 9, 7),
                       merchant="Les 3 Brasseurs", tax_amount=3.32)
        merged = self.Expense._expense_scan_merge_pieces([card, till])
        self.assertEqual(merged.value('total'), 33.10)
        self.assertEqual(merged.value('time'), "20:42")

    def test_merge_of_a_single_reading(self):
        alone = reading(total=6.80)
        self.assertEqual(
            self.Expense._expense_scan_merge_pieces([alone]).value('total'), 6.80)

    def test_merge_can_keep_the_main_reading(self):
        """Expense already scanned: its receipt stays the base."""
        main = reading(total=33.15, time="20:42")
        added = reading(total=33.10, date=date(2026, 9, 7), merchant="Les 3 Brasseurs")
        merged = self.Expense._expense_scan_merge_pieces([main, added], main_first=True)
        self.assertEqual(merged.value('total'), 33.15)
        self.assertEqual(merged.value('merchant'), "Les 3 Brasseurs")

    def test_the_main_receipt_is_read_first(self):
        """A receipt attached afterwards does not take the place of the main one."""
        expense = self.Expense.create({
            'name': "Deux justificatifs",
            'employee_id': self.env['hr.employee'].create({'name': "Nina"}).id})
        Attachment = self.env['ir.attachment']
        first, added = [Attachment.create({
            'name': name, 'raw': png(), 'mimetype': 'image/png',
            'res_model': 'hr.expense', 'res_id': expense.id,
        }) for name in ("premier.png", "second.png")]
        expense._message_set_main_attachment_id(first, force=True)
        self.assertEqual(expense._expense_scan_image_attachments(), first | added)
        self.assertEqual(expense._expense_scan_image_attachments()[0], first)

    # -- Grouping ---------------------------------------------------------

    def test_grouping_separates_two_receipts(self):
        """Three pieces, two purchases: two groups, in order of arrival."""
        pieces = [
            (None, reading(total=33.10, date=date(2026, 9, 7))),
            (None, reading(total=8.40, date=date(2026, 9, 7))),
            (None, reading(total=33.10)),
        ]
        groups = self.Expense._expense_scan_group_pieces(pieces)
        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_a_split_receipt_does_not_inherit_the_description_and_category(self):
        """Unrelated purchase: neither the description nor the category describes it."""
        chosen = self.env['product.product'].create({
            'name': "Catégorie choisie", 'can_be_expensed': True})
        expense = self.Expense.create({
            'name': "Repas client Dupont", 'product_id': chosen.id,
            'employee_id': self.env['hr.employee'].create({'name': "Nina"}).id})
        attachment = self.env['ir.attachment'].create({
            'name': "second.png", 'raw': png(), 'mimetype': 'image/png',
            'res_model': 'hr.expense', 'res_id': expense.id})
        with patch.object(type(self.Expense), '_expense_scan_apply_group', autospec=True):
            other = expense._expense_scan_split_off([(attachment, reading(total=15.0))])
        self.assertNotEqual(other.name, "Repas client Dupont")
        self.assertTrue(other._expense_scan_name_is_automatic())
        self.assertTrue(other._expense_scan_category_is_free(other.company_id))
        self.assertEqual(attachment.res_id, other.id)

    def test_scanning_again_says_where_a_receipt_went(self):
        """The receipt leaves the form under the user's eyes: both sides say it."""
        expense = self.Expense.create({
            'name': "Deux achats",
            'employee_id': self.env['hr.employee'].create({'name': "Nina"}).id})
        Attachment = self.env['ir.attachment']
        first, added = [Attachment.create({
            'name': name, 'raw': png(), 'mimetype': 'image/png',
            'res_model': 'hr.expense', 'res_id': expense.id,
        }) for name in ("premier.png", "second.png")]
        expense._message_set_main_attachment_id(first, force=True)
        readings = {first: reading(total=33.10), added: reading(total=8.40)}
        Model = type(self.Expense)
        with patch.object(Model, '_expense_scan_process', autospec=True,
                          side_effect=lambda record, attachment: readings[attachment]), \
                patch.object(Model, '_expense_scan_apply', autospec=True):
            action = expense.action_expense_scan_rescan()
        other = added.res_id and self.Expense.browse(added.res_id)
        self.assertNotEqual(other, expense)
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['links'][0]['url'], '/odoo/hr.expense/%d' % other.id)

        def links_to(record, target):
            return record.message_ids.filtered(
                lambda m: 'data-oe-id' in (m.body or '') and str(target.id) in m.body)
        self.assertTrue(links_to(expense, other))
        self.assertTrue(links_to(other, expense))
