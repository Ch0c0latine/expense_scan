# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Justificatifs en plusieurs morceaux : comparaison et fusion.

Ces règles décident si deux photos jointes au même courriel décrivent un
seul achat ou deux, sans intervention de l'utilisateur. Une erreur passe
donc inaperçue : la séparation n'a lieu que sur une différence franche.
"""
from datetime import date
from unittest.mock import patch

from odoo.tests import common, tagged

from ..ocr.types import ExtractedField, ScanResult
from .test_sheet import png


def reading(**values):
    """Construit une lecture sans passer par l'OCR."""
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

    # -- Comparaison ------------------------------------------------------

    def test_card_slip_and_till_receipt_are_one(self):
        """Le cas courant : même montant, même jour, deux morceaux."""
        till = reading(total=33.10, date=date(2026, 9, 7), merchant="Les 3 Brasseurs")
        card = reading(total=33.10, date=date(2026, 9, 7))
        self.assertTrue(self.same(till, card))

    def test_rounding_gap_is_not_a_difference(self):
        """Deux centimes d'écart de lecture ne font pas deux dépenses."""
        self.assertTrue(self.same(reading(total=33.10), reading(total=33.08)))

    def test_clearly_different_totals_split(self):
        self.assertFalse(self.same(reading(total=33.10), reading(total=8.40)))

    def test_different_dates_split(self):
        self.assertFalse(self.same(
            reading(total=20.00, date=date(2026, 9, 7)),
            reading(total=20.00, date=date(2026, 9, 8)),
        ))

    def test_missing_total_keeps_them_together(self):
        """Le doute profite au regroupement.

        Une bande de carte bancaire n'imprime souvent qu'un montant, sans
        date ni enseigne. Séparer sur cette absence produirait une dépense
        fantôme à chaque paiement par carte.
        """
        self.assertTrue(self.same(
            reading(total=33.10, date=date(2026, 9, 7)),
            reading(merchant="Les 3 Brasseurs"),
        ))

    def test_big_amounts_tolerate_a_bigger_gap(self):
        """Deux euros d'écart sur mille ne prouvent rien ; sur dix, si."""
        self.assertTrue(self.same(reading(total=1000.00), reading(total=1002.00)))
        self.assertFalse(self.same(reading(total=10.00), reading(total=12.00)))

    # -- Fusion -----------------------------------------------------------

    def test_merge_keeps_the_richest_reading(self):
        poor = reading(total=33.10)
        rich = reading(total=33.10, date=date(2026, 9, 7),
                       merchant="Les 3 Brasseurs", tax_amount=3.32)
        merged = self.Expense._expense_scan_merge_pieces([poor, rich])
        self.assertEqual(merged.value('merchant'), "Les 3 Brasseurs")
        self.assertEqual(merged.value('tax_amount'), 3.32)

    def test_merge_fills_the_gaps_only(self):
        """Les morceaux se complètent sans se contredire.

        L'heure vient de la bande de carte et l'enseigne du ticket ; le
        total de la lecture la plus complète est conservé, même si l'autre
        en affiche un différent.
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
        """Dépense déjà analysée : son justificatif reste la base."""
        main = reading(total=33.15, time="20:42")
        added = reading(total=33.10, date=date(2026, 9, 7), merchant="Les 3 Brasseurs")
        merged = self.Expense._expense_scan_merge_pieces([main, added], main_first=True)
        self.assertEqual(merged.value('total'), 33.15)
        self.assertEqual(merged.value('merchant'), "Les 3 Brasseurs")

    def test_the_main_receipt_is_read_first(self):
        """Un justificatif joint après coup ne prend pas la place du principal."""
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

    # -- Regroupement -----------------------------------------------------

    def test_grouping_separates_two_receipts(self):
        """Trois pièces, deux achats : deux groupes, dans l'ordre d'arrivée."""
        pieces = [
            (None, reading(total=33.10, date=date(2026, 9, 7))),
            (None, reading(total=8.40, date=date(2026, 9, 7))),
            (None, reading(total=33.10)),
        ]
        groups = self.Expense._expense_scan_group_pieces(pieces)
        self.assertEqual([len(group) for group in groups], [2, 1])

    def test_a_split_receipt_does_not_inherit_the_description_and_category(self):
        """Achat sans rapport : ni la description ni la catégorie ne le décrivent."""
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
