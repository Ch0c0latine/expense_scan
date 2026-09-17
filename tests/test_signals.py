# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Codes d'activité, SIRET, base Sirene et marques connues.

SIRET d'essai : 123 456 782 00010, dont la clé de Luhn est juste.
"""
import csv
import io
import os
import tempfile
import zipfile

from odoo.tests import common, tagged

from ..ocr import lexicon, parser
from .test_parser import words_from_text


def reading(text):
    return parser.parse(words_from_text(text))


@tagged('post_install', '-at_install')
class TestPrintedCodes(common.TransactionCase):

    def test_ape_code(self):
        result = reading("BRASSERIE DU PORT\nSIRET 123 456 782 00010 - APE 5610A\nTOTAL 12,00")
        self.assertEqual(result.value('activity'), "NAF:5610A")
        self.assertEqual(result.value('company_number'), "12345678200010")

    def test_misread_ape_letter(self):
        result = reading("HOTEL\nCode NAF : 55.102\nTOTAL 80,00")
        self.assertEqual(result.value('activity'), "NAF:5510Z")

    def test_mcc_code(self):
        result = reading("CARTE BANCAIRE\nMCC 7011\nMONTANT 80,00 EUR")
        self.assertEqual(result.value('activity'), "MCC:7011")

    def test_misread_siret_is_refused(self):
        result = reading("SIRET 123 456 789 00010\nTOTAL 12,00")
        self.assertIsNone(result.value('company_number'))

    def test_siren_from_rcs(self):
        result = reading("SAS AU CAPITAL DE 1000 EUROS\nRCS LYON B 123 456 782\nTOTAL 12,00")
        self.assertEqual(result.value('company_number'), "123456782")

    def test_activity_families(self):
        self.assertEqual(lexicon.activity_family("NAF:5610A"), 'meal')
        self.assertEqual(lexicon.activity_family("NAF:5510Z"), 'lodging')
        self.assertEqual(lexicon.activity_family("NAF:5221Z"), 'toll_parking')
        self.assertEqual(lexicon.activity_family("MCC:7011"), 'lodging')
        self.assertEqual(lexicon.activity_family("MCC:3100"), 'train_air')
        self.assertIsNone(lexicon.activity_family("NAF:6201Z"))
        self.assertIsNone(lexicon.activity_family(None))


@tagged('post_install', '-at_install')
class TestBrands(common.TransactionCase):

    def test_known_brands_open_the_header(self):
        self.assertEqual(lexicon.brand_family(["ORLEN STACJA NR 123", "TOTAL 12,00"])[0], 'fuel')
        self.assertEqual(lexicon.brand_family(["LIDL", "12 RUE DU PORT"])[0], 'meal')

    def test_common_words_are_not_brands(self):
        for lines in (["TOTAL 12,00"], ["BP 10017 VILLEBOURG"], ["PAUL DURAND"], ["NETTO 2,24"]):
            self.assertEqual(lexicon.brand_family(lines), (None, None), lines)

    def test_brand_in_the_middle_of_a_line_is_ignored(self):
        self.assertEqual(lexicon.brand_family(["MERCI DE VOTRE VISITE ORLEN"]), (None, None))

    def test_the_index_is_filled(self):
        self.assertGreater(len(lexicon.brand_index()), 1000)


@tagged('post_install', '-at_install')
class TestSirene(common.TransactionCase):

    def test_lookup_by_siret_and_siren(self):
        Sirene = self.env['expense.scan.sirene']
        Sirene.create({'siret': '12345678200010', 'naf': '5510Z'})
        Sirene.create({'siret': '12345678200028', 'naf': '5510Z'})
        self.assertEqual(Sirene._expense_scan_activity('12345678200010'), '5510Z')
        self.assertEqual(Sirene._expense_scan_activity('123456782'), '5510Z')
        self.assertFalse(Sirene._expense_scan_activity('999999999'))
        self.assertFalse(Sirene._expense_scan_activity(False))

    def test_import_keeps_only_useful_active_establishments(self):
        header = ['siren', 'nic', 'siret', 'etatAdministratifEtablissement',
                  'activitePrincipaleEtablissement',
                  'nomenclatureActivitePrincipaleEtablissement']
        rows = [
            ['123456782', '00010', '12345678200010', 'A', '55.10Z', 'NAFRev2'],
            ['123456782', '00028', '12345678200028', 'F', '55.10Z', 'NAFRev2'],
            ['111111118', '00010', '11111111800010', 'A', '62.01Z', 'NAFRev2'],
            ['222222226', '00010', '22222222600010', 'A', '56.10A', 'NAFRev2'],
        ]
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(header)
        writer.writerows(rows)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'StockEtablissement_utf8.zip')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('StockEtablissement_utf8.csv', text.getvalue())
            kept = self.env['expense.scan.sirene']._expense_scan_import(path)
        self.assertEqual(kept, 2)
        records = self.env['expense.scan.sirene'].search([])
        self.assertEqual(sorted(records.mapped('siret')), ['12345678200010', '22222222600010'])
        self.assertEqual(records.filtered(lambda r: r.siret.startswith('2222')).naf, '5610A')


@tagged('post_install', '-at_install')
class TestCombinedSignals(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.default = cls.env['product.product'].create(
            {'name': "Divers signaux", 'can_be_expensed': True})
        cls.company.expense_scan_product_id = cls.default
        cls.employee = cls.env['hr.employee'].create({'name': "Dominique Signaux"})
        families = cls.env['product.template']._expense_scan_family_templates()
        cls.family = {family: template.product_variant_id
                      for template, family in families.items()}

    def recognize(self, text):
        expense = self.env['hr.expense'].create({
            'name': "Ticket", 'employee_id': self.employee.id,
            'product_id': self.default.id,
        })
        return expense._expense_scan_category_values(reading(text), self.company)

    def test_printed_ape_code_decides(self):
        if 'lodging' not in self.family:
            return
        values = self.recognize("QZXW SARL\nAPE 5510Z\nTOTAL 80,00")
        self.assertEqual(values['product_id'], self.family['lodging'].id)
        self.assertIn("5510Z", values['expense_scan_category_reason'])

    def test_siret_found_in_sirene_decides(self):
        if 'meal' not in self.family:
            return
        self.env['expense.scan.sirene'].create({'siret': '12345678200010', 'naf': '5610A'})
        values = self.recognize("QZXW SARL\nSIRET 12345678200010\nTOTAL 18,00")
        self.assertEqual(values['product_id'], self.family['meal'].id)

    def test_known_brand_decides(self):
        if 'fuel' not in self.family:
            return
        values = self.recognize("ORLEN STACJA 42\nSUMA PLN 120,00")
        self.assertEqual(values['product_id'], self.family['fuel'].id)
