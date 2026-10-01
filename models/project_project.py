# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Expense budget of a project.

A budget is a planned amount excl. tax. It is compared with the approved
expenses of the project, re-invoiced or not, and with those still waiting for
the manager.
"""
from odoo import api, fields, models
from odoo.tools import float_compare


class ProjectProject(models.Model):
    _inherit = 'project.project'

    expense_scan_currency_id = fields.Many2one(
        comodel_name='res.currency',
        compute='_compute_expense_scan_currency_id',
        string="Expense currency",
    )
    expense_scan_budget = fields.Monetary(
        string="Expense budget",
        currency_field='expense_scan_currency_id',
        help="Planned expenses of the project, excl. tax. Leave empty for no budget.",
    )
    expense_scan_spent = fields.Monetary(
        string="Approved expenses",
        currency_field='expense_scan_currency_id',
        compute='_compute_expense_scan_amounts',
        compute_sudo=True,
        help="Expenses of the project approved by the manager, excl. tax.",
    )
    expense_scan_waiting = fields.Monetary(
        string="Expenses to approve",
        currency_field='expense_scan_currency_id',
        compute='_compute_expense_scan_amounts',
        compute_sudo=True,
        help="Expenses of the project waiting for the manager, excl. tax.",
    )
    expense_scan_left = fields.Monetary(
        string="Budget left",
        currency_field='expense_scan_currency_id',
        compute='_compute_expense_scan_amounts',
        compute_sudo=True,
        help="Budget minus approved expenses and expenses to approve.",
    )
    expense_scan_progress = fields.Float(
        string="Budget used",
        compute='_compute_expense_scan_amounts',
        compute_sudo=True,
        help="Approved expenses and expenses to approve, as a percentage of the budget.",
    )
    expense_scan_over_budget = fields.Boolean(
        string="Over budget",
        compute='_compute_expense_scan_amounts',
        compute_sudo=True,
        search='_search_expense_scan_over_budget',
    )
    expense_scan_count = fields.Integer(
        string="Expenses",
        compute='_compute_expense_scan_amounts',
        compute_sudo=True,
    )
    expense_scan_held_back = fields.Integer(
        string="Expenses holding back the invoice",
        compute='_compute_expense_scan_held_back',
        compute_sudo=True,
        help="Expenses waiting for the manager, or whose re-invoicing is "
             "still to decide. While there are any, the expense line of the "
             "sales order does not move.",
    )

    @api.depends('company_id')
    def _compute_expense_scan_currency_id(self):
        for project in self:
            project.expense_scan_currency_id = (
                project.company_id.currency_id or self.env.company.currency_id)

    @api.depends('expense_scan_budget')
    def _compute_expense_scan_amounts(self):
        data = {}
        if self.ids:
            groups = self.env['hr.expense'].sudo()._read_group(
                [('project_id', 'in', self.ids), ('approval_state', 'in', ('approved', 'submitted'))],
                groupby=['project_id', 'approval_state'],
                aggregates=['untaxed_amount:sum', '__count'])
            for project, state, amount, count in groups:
                data.setdefault(project.id, {})[state] = (amount, count)
        for project in self:
            figures = data.get(project.id, {})
            spent, spent_count = figures.get('approved', (0.0, 0))
            waiting, waiting_count = figures.get('submitted', (0.0, 0))
            budget = project.expense_scan_budget
            project.expense_scan_spent = spent
            project.expense_scan_waiting = waiting
            project.expense_scan_count = spent_count + waiting_count
            project.expense_scan_left = budget - spent - waiting if budget else 0.0
            project.expense_scan_progress = (spent + waiting) / budget * 100.0 if budget else 0.0
            project.expense_scan_over_budget = bool(budget) and float_compare(
                spent + waiting, budget,
                precision_rounding=project.expense_scan_currency_id.rounding) > 0

    def _search_expense_scan_over_budget(self, operator, value):
        projects = self.sudo().search([('expense_scan_budget', '>', 0)])
        over = projects.filtered('expense_scan_over_budget')
        positive = (operator == '=') == bool(value)
        return [('id', 'in' if positive else 'not in', over.ids)]

    def _compute_expense_scan_held_back(self):
        Expense = self.env['hr.expense'].sudo()
        for project in self:
            base = [('project_id', '=', project.id)]
            project.expense_scan_held_back = Expense.search_count(
                base + [('state', '=', 'submitted'), ('reinvoice_mode', '!=', 'none')]
            ) + Expense.search_count(
                base + [('reinvoice_mode', '=', 'todo'), ('state', '!=', 'refused')])

    def action_expense_scan_open_expenses(self):
        """Expenses of the project, newest first."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('hr_expense.hr_expense_actions_to_process')
        action.update({
            'name': self.env._("Expenses of %s", self.display_name),
            'domain': [('project_id', '=', self.id)],
            'context': {'default_project_id': self.id},
        })
        return action
