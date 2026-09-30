# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Exchange rates of the European Central Bank.

Odoo Community updates no exchange rate on its own. Without a rate, a
receipt in euros scanned by a Swedish company is converted one for one: the
list shows 29.19 kr for 29.19 EUR. The reference rates of the ECB, free and
without an account, cover about thirty currencies.

The download is the module's only network request: it happens in a
scheduled task, never while a receipt is read, and sends nothing but the
request for a public file.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import date

import requests

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

#: Reference rates of the last ninety days: a receipt a few weeks old is
#: converted at the rate of its own day.
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_TIMEOUT = 15


def parse_ecb(xml_text):
    """``{date: {code: units per euro}}`` from an ECB reference rates file."""
    rates = {}
    for day in ET.fromstring(xml_text).iter():
        if not day.tag.endswith('Cube') or 'time' not in day.attrib:
            continue
        values = {cube.attrib['currency']: float(cube.attrib['rate'])
                  for cube in day if 'currency' in cube.attrib}
        values['EUR'] = 1.0
        rates[date.fromisoformat(day.attrib['time'])] = values
    return rates


class ResCurrency(models.Model):
    _inherit = 'res.currency'

    @api.model
    def _expense_scan_ecb_download(self):
        response = requests.get(ECB_URL, timeout=ECB_TIMEOUT)
        response.raise_for_status()
        return response.text

    @api.model
    def _cron_expense_scan_ecb_rates(self):
        """Create the missing rates of the active currencies, for each company
        that asked for them."""
        companies = self.env['res.company'].search([('expense_scan_ecb_rates', '=', True)])
        if not companies:
            return
        try:
            daily = parse_ecb(self._expense_scan_ecb_download())
        except Exception:  # noqa: BLE001 - no network, ECB unavailable: next run
            _logger.warning("ECB exchange rates not downloaded", exc_info=True)
            return
        currencies = self.search([])
        for company in companies:
            self._expense_scan_create_ecb_rates(daily, company, currencies)

    @api.model
    def _expense_scan_create_ecb_rates(self, daily, company, currencies):
        """Rates of ``currencies`` against the company currency, for each day
        of ``daily`` not known yet. Returns the rates created."""
        base = company.currency_id.name
        Rate = self.env['res.currency.rate'].sudo()
        existing = {(rate.currency_id.id, rate.name) for rate in Rate.search([
            ('company_id', '=', company.id), ('currency_id', 'in', currencies.ids)])}
        vals_list = []
        for day, values in daily.items():
            if base not in values:
                continue  # company currency not published by the ECB
            for currency in currencies:
                if currency == company.currency_id or currency.name not in values:
                    continue
                if (currency.id, day) in existing:
                    continue
                vals_list.append({
                    'currency_id': currency.id,
                    'company_id': company.id,
                    'name': day,
                    # Odoo: units of this currency for one unit of the
                    # company currency.
                    'rate': values[currency.name] / values[base],
                })
        return Rate.create(vals_list) if vals_list else Rate


class ResCurrencyRate(models.Model):
    _inherit = 'res.currency.rate'

    @api.model_create_multi
    def create(self, vals_list):
        rates = super().create(vals_list)
        rates._expense_scan_convert_draft_expenses()
        return rates

    def _expense_scan_convert_draft_expenses(self):
        """Convert the draft expenses still counted one for one.

        Odoo keeps the conversion of an existing expense: the rate is read
        back from the amount already converted. An expense recorded before
        any rate existed stays at one for one; its conversion is redone once
        a rate arrives. An amount converted by hand (another rate) is left.
        """
        Expense = self.env['hr.expense'].sudo()
        expenses = Expense.search([
            ('currency_id', 'in', self.currency_id.ids), ('state', '=', 'draft')])
        today = fields.Date.context_today(self)
        for expense in expenses:
            company_currency = expense.company_currency_id
            if expense.currency_id == company_currency \
                    or company_currency.compare_amounts(
                        expense.total_amount, expense.total_amount_currency) != 0:
                continue
            converted = expense.currency_id._convert(
                expense.total_amount_currency, company_currency,
                expense.company_id, expense.date or today)
            if company_currency.compare_amounts(converted, expense.total_amount_currency):
                expense.total_amount = converted
