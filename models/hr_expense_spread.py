# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Spreading a flat-rate expense over the days it covers.

A flat-rate category (a daily allowance, a scale amount) is often entered
once for several days: "11 x lodging allowance". One line per day reads
better and checks against the presence of the employee. The expense keeps
the first day; copies take the following days, one unit each.

The days are those of the employee's missions (time off entries carrying a
project, when an assignment module provides them), otherwise the working days
of their schedule, without public holidays and time off. A day that already
holds the same category is skipped.
"""
from datetime import timedelta

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

#: How far ahead days are looked for, in calendar days.
HORIZON = 120


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    def action_expense_scan_spread_days(self):
        """One line per day for a flat-rate expense entered for several days."""
        result = self.env['hr.expense']
        for expense in self:
            result |= expense._expense_scan_spread()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.expense',
            'name': _("Spread expenses"),
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('id', 'in', result.ids)],
        }

    def _expense_scan_spread(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_("Only a draft expense can be spread over days."))
        if not self.product_has_cost:
            raise UserError(_("Only an expense of a flat-rate category can be spread over days."))
        count = int(round(self.quantity))
        if count < 2 or not float_is_zero(self.quantity - count, precision_digits=2):
            raise UserError(_("Enter a whole number of days of at least 2 in the quantity."))
        days = self._expense_scan_presence_days(self.employee_id, self.date, count, self.product_id)
        if len(days) < count:
            raise UserError(_(
                "Only %(found)s free day(s) found from %(date)s for %(count)s: lower the quantity "
                "or move the date.", found=len(days), date=self.date, count=count))
        self.write({'date': days[0], 'quantity': 1})
        copies = self
        for day in days[1:]:
            copies |= self.copy({'date': day, 'quantity': 1})
        return copies

    @api.model
    def _expense_scan_presence_days(self, employee, start, count, product=None):
        """The first ``count`` days of presence from ``start`` (included)."""
        if not employee or not start:
            return []
        end = start + timedelta(days=HORIZON)
        working = self._expense_scan_working_days(employee, start, end)
        missions = self._expense_scan_mission_days(employee, start, end)
        candidates = sorted(missions & working) if missions else sorted(working)
        taken = set()
        if product:
            taken = set(self.sudo().search([
                ('employee_id', '=', employee.id), ('product_id', '=', product.id),
                ('date', '>=', start), ('date', '<=', end), ('state', '!=', 'refused'),
                ('id', 'not in', self.ids),
            ]).mapped('date'))
        return [day for day in candidates if day not in taken][:count]

    @api.model
    def _expense_scan_working_days(self, employee, start, end):
        """Working days of the employee's schedule, without public holidays and time off."""
        calendar = employee.resource_calendar_id or employee.company_id.resource_calendar_id
        attendances = calendar.attendance_ids.filtered(lambda a: not a.display_type) if calendar else False
        weekdays = {int(a.dayofweek) for a in attendances} if attendances else {0, 1, 2, 3, 4}
        days = set()
        day = start
        while day <= end:
            if day.weekday() in weekdays:
                days.add(day)
            day += timedelta(days=1)
        holidays = self.env['resource.calendar.leaves'].sudo().search([
            ('resource_id', '=', False),
            ('company_id', 'in', [False, employee.company_id.id]),
            ('date_from', '<=', end), ('date_to', '>=', start),
        ])
        for holiday in holidays:
            day = holiday.date_from.date()
            while day <= holiday.date_to.date():
                days.discard(day)
                day += timedelta(days=1)
        if 'hr.leave' in self.env:
            Leave = self.env['hr.leave'].sudo()
            domain = [('employee_id', '=', employee.id), ('state', '=', 'validate'),
                      ('request_date_from', '<=', end), ('request_date_to', '>=', start)]
            if 'project_id' in Leave._fields:
                domain.append(('project_id', '=', False))
            for leave in Leave.search(domain):
                day = leave.request_date_from
                while day <= leave.request_date_to:
                    days.discard(day)
                    day += timedelta(days=1)
        return days

    @api.model
    def _expense_scan_mission_days(self, employee, start, end):
        """Days covered by the employee's missions, when they are recorded."""
        if 'hr.leave' not in self.env or 'project_id' not in self.env['hr.leave']._fields:
            return set()
        days = set()
        for leave in self.env['hr.leave'].sudo().search([
                ('employee_id', '=', employee.id), ('project_id', '!=', False),
                ('state', 'not in', ('refuse', 'cancel')),
                ('request_date_from', '<=', end), ('request_date_to', '>=', start)]):
            day = max(leave.request_date_from, start)
            while day <= min(leave.request_date_to, end):
                days.add(day)
                day += timedelta(days=1)
        return days
