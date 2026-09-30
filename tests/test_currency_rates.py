# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Exchange rates of the European Central Bank, without network access."""
from datetime import date
from unittest.mock import patch

from odoo.tests import common, tagged

from ..models.currency_rates import parse_ecb

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-09-02">
      <Cube currency="USD" rate="1.1000"/><Cube currency="SEK" rate="11.00"/>
      <Cube currency="XTZ" rate="%(xtz)s"/>
    </Cube>
    <Cube time="2026-09-01">
      <Cube currency="USD" rate="1.0900"/><Cube currency="SEK" rate="11.10"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


@tagged('post_install', '-at_install')
class TestEcbRates(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Currency = cls.env['res.currency']
        # Test currency, without any rate: never touched by other data.
        cls.xtz = cls.Currency.create({'name': 'XTZ', 'symbol': 'Tz', 'active': True})
        cls.employee = cls.env['hr.employee'].create({'name': "Nils Kurs"})
        cls.product = cls.env['product.product'].create({
            'name': "Kurs", 'can_be_expensed': True, 'supplier_taxes_id': [(5, 0, 0)]})

    def daily(self):
        """Two days of rates; XTZ worth half the company currency."""
        base = self.company.currency_id.name
        values = {date(2026, 9, 2): {'EUR': 1.0, 'USD': 1.1, 'SEK': 11.0},
                  date(2026, 9, 1): {'EUR': 1.0, 'USD': 1.09, 'SEK': 11.1}}
        if base not in values[date(2026, 9, 2)]:
            self.skipTest("company currency not in the sample")
        values[date(2026, 9, 2)]['XTZ'] = 2.0 * values[date(2026, 9, 2)][base]
        return values

    def test_the_ecb_file_is_read(self):
        rates = parse_ecb(ECB_XML % {'xtz': '2.0'})
        self.assertEqual(rates[date(2026, 9, 2)]['USD'], 1.1)
        self.assertEqual(rates[date(2026, 9, 1)]['EUR'], 1.0)
        self.assertNotIn('XTZ', rates[date(2026, 9, 1)])

    def test_rates_are_created_once(self):
        created = self.Currency._expense_scan_create_ecb_rates(
            self.daily(), self.company, self.xtz)
        self.assertEqual(len(created), 1)
        self.assertAlmostEqual(created.rate, 2.0)
        again = self.Currency._expense_scan_create_ecb_rates(
            self.daily(), self.company, self.xtz)
        self.assertFalse(again)

    def test_a_draft_expense_counted_one_for_one_is_converted(self):
        """10 XTZ recorded before any rate: 5 in the company currency afterwards."""
        expense = self.env['hr.expense'].create({
            'name': "Kurs", 'employee_id': self.employee.id, 'product_id': self.product.id,
            'currency_id': self.xtz.id, 'total_amount_currency': 10.0,
            'date': date(2026, 9, 2)})
        self.assertEqual(expense.total_amount, 10.0)
        self.Currency._expense_scan_create_ecb_rates(self.daily(), self.company, self.xtz)
        self.assertAlmostEqual(expense.total_amount, 5.0)

    def test_the_task_needs_no_network_to_fail_quietly(self):
        self.company.expense_scan_ecb_rates = True
        Model = type(self.Currency)
        with patch.object(Model, '_expense_scan_ecb_download', autospec=True,
                          side_effect=OSError("no network")):
            self.Currency._cron_expense_scan_ecb_rates()  # no exception
        base = self.company.currency_id.name
        if base not in ('EUR', 'USD', 'SEK'):
            self.skipTest("company currency not in the sample")
        xtz = {'EUR': '2.0', 'USD': '2.2', 'SEK': '22.0'}[base]
        with patch.object(Model, '_expense_scan_ecb_download', autospec=True,
                          return_value=ECB_XML % {'xtz': xtz}):
            self.Currency._cron_expense_scan_ecb_rates()
        rate = self.env['res.currency.rate'].search([
            ('currency_id', '=', self.xtz.id), ('company_id', '=', self.company.id)])
        self.assertAlmostEqual(rate.rate, 2.0)

    def test_an_old_expense_gets_the_rate_of_its_day(self):
        """A receipt of 2024: converted at its day's rate, not at the oldest rate known."""
        import io
        import zipfile
        base = self.company.currency_id.name
        unit = {'EUR': 1.0, 'USD': 1.05, 'SEK': 11.5}.get(base)
        if not unit:
            self.skipTest("company currency not in the sample")
        self.company.expense_scan_ecb_rates = True
        expense = self.env['hr.expense'].create({
            'name': "Gammal", 'employee_id': self.employee.id, 'product_id': self.product.id,
            'currency_id': self.xtz.id, 'total_amount_currency': 10.0,
            'date': date(2024, 11, 28)})
        history = io.BytesIO()
        with zipfile.ZipFile(history, 'w') as archive:
            archive.writestr('eurofxref-hist.csv', (
                "Date,USD,SEK,XTZ,\n"
                # XTZ worth half the company currency on 2024-11-28.
                "2024-11-28,1.05,11.5,%s,\n" % (2 * unit)
                + "2024-11-27,1.04,11.4,N/A,\n"))
        daily = ECB_XML % {'xtz': {'EUR': '3.0', 'USD': '3.3', 'SEK': '33.0'}[base]}
        Model = type(self.Currency)
        with patch.object(Model, '_expense_scan_ecb_download', autospec=True, return_value=daily), \
                patch.object(Model, '_expense_scan_ecb_history_download', autospec=True,
                             return_value=history.getvalue()):
            self.Currency._cron_expense_scan_ecb_rates()
        self.assertAlmostEqual(expense.total_amount, 5.0)
