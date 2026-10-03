# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Enter expenses for a team member without logging in as them.

A team leader or an administrator opens an employee's list; everything
created there (manual entry or scan) is created on the employee's behalf.
The expense history keeps the actual author.

Logging in as the employee would give access to everything they can see
(time off, messages, personal file) and credit them with every action.
"""
from markupsafe import Markup

from odoo import _, api, models
from odoo.exceptions import AccessError


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    @api.model
    def action_expense_scan_team(self):
        """Team members, to pick the one the expenses are entered for."""
        team = self._expense_scan_team_employees()
        view = self.env.ref('expense_scan.hr_employee_public_view_kanban_expense_team')
        return {
            'type': 'ir.actions.act_window',
            'name': _("My Team's Expenses"),
            # Path of the server action: on reload, Odoo runs the action again
            # instead of failing.
            'path': 'my-team-expenses',
            'res_model': 'hr.employee.public',
            'view_mode': 'kanban',
            'views': [(view.id, 'kanban')],
            'domain': [('id', 'in', team.ids)],
            # An approver with nobody in the team learns who is listed here.
            'help': Markup('<p class="o_view_nocontent_smiling_face">%s</p><p>%s</p>') % (
                _("No team member yet"),
                _("The employees whose expenses you approve, or who report to you, appear here. "
                  "Click one to see their expenses or to enter one in their name.")),
            # No "create" in this context: a card button passes it on to the
            # action it opens, and the "New" button disappeared from the
            # employee's expenses. The view already forbids creating
            # employees through its own attribute.
        }

    @api.model
    def _expense_scan_employee_expense_action(self, employee_id, employee_name):
        """An employee's expense list, entered on their behalf.

        Used by the team card and by the form buttons (Done, Delete) to go
        back to the employee's list rather than the manager's.
        """
        # A saved action, with its path: the page address keeps the employee
        # (active_id) and Odoo rebuilds the list on reload or from the
        # breadcrumbs.
        action = self.env['ir.actions.actions']._for_xml_id(
            'expense_scan.action_expense_scan_employee_expenses')
        title = _("Expenses of %s", employee_name)
        action.update({
            # The web client shows `display_name` when there is one.
            'name': title,
            'display_name': title,
            'domain': [('employee_id', '=', employee_id)],
            # The employee becomes the default one: manual entries and scans
            # both go through `create`, which honours that default.
            'context': {
                'active_id': employee_id,
                'active_model': 'hr.employee.public',
                'default_employee_id': employee_id,
                'expense_scan_acting_for': employee_id,
            },
        })
        return action

    def _expense_scan_expense_list(self):
        """Go back to the employee's list when entering expenses for them.

        Otherwise "Done" took the manager back to their own expenses.
        """
        employee = self[:1].employee_id
        if not employee or employee in self.env.user.employee_ids:
            return super()._expense_scan_expense_list()
        action = self._expense_scan_employee_expense_action(employee.id, employee.name)
        action['target'] = 'main'
        return action

    @api.model
    def _expense_scan_team_employees(self):
        """The user's team, following Odoo's access rules.

        Same criteria as the "Team Approver Expense" rule: employees of the
        departments the user manages, employees below them in the hierarchy,
        employees whose expenses they approve. An approver of all expenses
        sees everyone. No employee is offered whose expenses would then be
        refused.
        """
        user = self.env.user
        Employee = self.env['hr.employee'].sudo()
        domain = [('company_id', 'in', self.env.companies.ids)]
        if not user.has_group('hr_expense.group_hr_expense_user'):
            if not user.has_group('hr_expense.group_hr_expense_team_approver'):
                return Employee.browse()
            domain += ['|', '|',
                       ('department_id.manager_id.user_id', '=', user.id),
                       ('id', 'child_of', user.employee_ids.ids),
                       ('expense_manager_id', '=', user.id)]
        # The user's own expenses already have their menu.
        return Employee.search(domain) - user.employee_ids

    @api.model_create_multi
    def create(self, vals_list):
        """Record on the expense who entered it, and for whom.

        Odoo tracks the author of every change, but creation only shows an
        undistinguished "Expense created". A note is added when the author is
        not the employee.
        """
        expenses = super().create(vals_list)
        author = self.env.user
        # Only a user linked to an employee enters expenses "for" someone;
        # the mail gateway is not one.
        if author.employee_ids and not self.env.su:
            for expense in expenses:
                employee = expense.employee_id
                if employee and employee not in author.employee_ids:
                    expense.message_post(
                        body=_("Entered by %(author)s for %(employee)s.",
                               author=author.name, employee=employee.name),
                        subtype_xmlid='mail.mt_note',
                    )
        return expenses


class HrEmployeePublic(models.Model):
    _inherit = 'hr.employee.public'

    def action_expense_scan_open_expenses(self):
        """This employee's expense list, entered on their behalf."""
        self.ensure_one()
        Expense = self.env['hr.expense']
        if self.id not in Expense._expense_scan_team_employees().ids:
            raise AccessError(_("%s is not in your team.", self.name))
        return Expense._expense_scan_employee_expense_action(self.id, self.name)
