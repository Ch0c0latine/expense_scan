# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""A flat-rate expense entered for several days, spread one line per day."""
from datetime import date, datetime

from odoo.exceptions import UserError
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestSpread(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Nathan Réparti"})
        cls.allowance = cls.env['product.product'].create({
            'name': "Indemnité logement répartie", 'can_be_expensed': True, 'standard_price': 48.30})

    def expense(self, day, quantity):
        return self.env['hr.expense'].create({
            'name': "IGD logement", 'employee_id': self.employee.id, 'product_id': self.allowance.id,
            'date': day, 'quantity': quantity})

    def test_one_line_per_working_day(self):
        # Monday 3 March 2031, a week without public holiday.
        first = self.expense(date(2031, 3, 3), 5)
        spread = first.action_expense_scan_spread_days()
        lines = self.env['hr.expense'].search(spread['domain'], order='date')
        self.assertEqual(lines.mapped('date'), [date(2031, 3, d) for d in (3, 4, 5, 6, 7)])
        self.assertEqual(set(lines.mapped('quantity')), {1.0})
        self.assertIn(first, lines)

    def test_days_already_covered_are_skipped(self):
        self.expense(date(2031, 3, 5), 1)
        first = self.expense(date(2031, 3, 3), 4)
        spread = first.action_expense_scan_spread_days()
        days = sorted(self.env['hr.expense'].search(spread['domain']).mapped('date'))
        self.assertEqual(days, [date(2031, 3, 3), date(2031, 3, 4), date(2031, 3, 6), date(2031, 3, 7)])

    def test_weekends_are_skipped(self):
        first = self.expense(date(2031, 3, 6), 3)  # Thursday
        spread = first.action_expense_scan_spread_days()
        days = sorted(self.env['hr.expense'].search(spread['domain']).mapped('date'))
        self.assertEqual(days, [date(2031, 3, 6), date(2031, 3, 7), date(2031, 3, 10)])

    def test_only_flat_rate_drafts(self):
        free = self.env['product.product'].create({'name': "Repas libre", 'can_be_expensed': True})
        expense = self.env['hr.expense'].create({
            'name': "Repas", 'employee_id': self.employee.id, 'product_id': free.id,
            'date': date(2031, 3, 3), 'total_amount_currency': 30.0})
        with self.assertRaises(UserError):
            expense.action_expense_scan_spread_days()
        with self.assertRaises(UserError):
            self.expense(date(2031, 3, 3), 1).action_expense_scan_spread_days()

    def test_a_holiday_stored_in_utc_removes_its_own_day(self):
        """1 May in Paris starts at 22:00 UTC on 30 April: 30 April stays a working day."""
        calendar = self.employee.resource_calendar_id or self.employee.company_id.resource_calendar_id
        if 'tz' in calendar._fields:
            calendar.tz = 'Europe/Paris'
        self.employee.tz = 'Europe/Paris'
        self.env['resource.calendar.leaves'].create({
            'name': "Férié test", 'company_id': self.employee.company_id.id, 'calendar_id': False,
            'date_from': datetime(2031, 4, 30, 22, 0), 'date_to': datetime(2031, 5, 1, 21, 59, 59)})
        first = self.expense(date(2031, 4, 30), 3)  # Wednesday
        spread = first.action_expense_scan_spread_days()
        days = sorted(self.env['hr.expense'].search(spread['domain']).mapped('date'))
        self.assertEqual(days, [date(2031, 4, 30), date(2031, 5, 2), date(2031, 5, 5)])

    def test_the_holiday_of_another_schedule_is_ignored(self):
        other = self.env['resource.calendar'].create({'name': "Autre horaire"})
        self.env['resource.calendar.leaves'].create({
            'name': "Fermeture autre horaire", 'company_id': self.employee.company_id.id,
            'calendar_id': other.id, 'date_from': datetime(2031, 3, 3, 23, 0),
            'date_to': datetime(2031, 3, 4, 22, 59, 59)})
        first = self.expense(date(2031, 3, 3), 5)
        spread = first.action_expense_scan_spread_days()
        days = sorted(self.env['hr.expense'].search(spread['domain']).mapped('date'))
        self.assertEqual(days, [date(2031, 3, d) for d in (3, 4, 5, 6, 7)])
