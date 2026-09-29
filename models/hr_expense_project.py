# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Linking an expense to a project, and re-invoicing it.

Kept apart from ``hr_expense.py``: reading the receipt and re-invoicing are
two separate subjects, and the second is optional: it only runs when the
company asks for it.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    reinvoice_mode = fields.Selection(
        selection=[
            # "Yes" first: the most common answer for a project expense.
            ('project', "Yes"),
            ('none', "No"),
            ('todo', "To decide"),
        ],
        string="Re-invoice",
        default='todo',
        required=True,
        help="\"To decide\" flags an expense whose fate is still open, and the "
             "review banner reminds it. \"No\" is a decision, not an omission: "
             "the company bears the expense.",
    )
    project_id = fields.Many2one(
        comodel_name='project.project',
        string="Project",
        ondelete='restrict',
        help="Project this expense belongs to. Setting it books the expense "
             "on the project's analytic account and adds it to the sales order "
             "to re-invoice, so that it appears on the next project invoice.",
    )
    expense_scan_task_id = fields.Many2one(
        comodel_name='project.task',
        string="Task",
        domain="[('project_id', '=', project_id)]",
        ondelete='set null',
        help="Task of the project, for follow-up. The cost goes into the "
             "project budget whether the expense is re-invoiced or not.",
    )
    expense_scan_reinvoice = fields.Boolean(
        related='company_id.expense_scan_reinvoice',
        string="Project link enabled",
    )

    expense_scan_has_tasks = fields.Boolean(
        string="Project with tasks",
        compute='_compute_expense_scan_has_tasks',
        help="The \"Task\" field only shows when there is a choice to make.",
    )

    @api.depends('project_id')
    def _compute_expense_scan_has_tasks(self):
        for expense in self:
            expense.expense_scan_has_tasks = bool(
                expense.project_id and self._expense_scan_open_tasks(expense.project_id))

    @api.model
    def _expense_scan_open_tasks(self, project):
        """Open tasks of a project, in the order set by the user."""
        return self.env['project.task'].sudo().search([
            ('project_id', '=', project._origin.id or project.id),
            ('state', 'not in', ('1_done', '1_canceled')),
        ], order='sequence, id')

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_sync_analytic()
        return expenses

    def write(self, vals):
        syncing = 'project_id' in vals and not self.env.context.get('expense_scan_syncing')
        # Automatic distribution of the previous project, noted before the
        # change: only that one is replaced.
        previous = {expense.id: self._expense_scan_auto_distribution(expense.project_id)
                    for expense in self.sudo()} if syncing else {}
        result = super().write(vals)
        if syncing:
            self._expense_scan_sync_analytic(previous)
        return result

    @api.model
    def _expense_scan_auto_distribution(self, project):
        """The distribution a project implies: everything on its account."""
        project = project.sudo()
        if not project or 'account_id' not in project._fields or not project.account_id:
            return {}
        return {str(project.account_id.id): 100.0}

    def _expense_scan_sync_analytic(self, previous=None):
        """Book the expenses linked to a project on that project.

        A project's cost reaches its dashboard through its analytic account.
        A project without an account gets one, as Odoo does for timesheets.

        ``previous`` gives, per expense, the automatic distribution of the
        project it left. That distribution follows the project change (or
        goes away with it); a distribution entered by hand is left alone.
        """
        previous = previous or {}
        for expense in self.sudo():
            current = expense.analytic_distribution or {}
            automatic = not current or (
                previous.get(expense.id) and self._same_distribution(current, previous[expense.id]))
            if not automatic:
                continue
            project = expense.project_id
            if project and 'account_id' in project._fields and not project.account_id:
                try:
                    with self.env.cr.savepoint():
                        project._create_analytic_account()
                except Exception:  # noqa: BLE001 - no analytic plan
                    _logger.warning("Could not create an analytic account for project %s",
                                    project.display_name, exc_info=True)
            wanted = self._expense_scan_auto_distribution(project)
            if not self._same_distribution(current, wanted):
                expense.with_context(expense_scan_syncing=True).write({
                    'analytic_distribution': wanted or False,
                })

    @staticmethod
    def _same_distribution(left, right):
        """Two identical distributions, give or take rounding."""
        left, right = left or {}, right or {}
        return set(left) == set(right) and all(
            abs(float(left[key]) - float(right[key])) < 0.01 for key in left)

    expense_scan_project_domain = fields.Char(
        string="Suggested projects",
        compute='_compute_expense_scan_project_domain',
        help="Domain of the \"Project\" field. It limits an employee to their "
             "own projects; a project manager or an administrator keeps the "
             "whole list.",
    )

    @api.depends_context('uid')
    @api.depends('employee_id')
    def _compute_expense_scan_project_domain(self):
        """Limit the project choice to those of the expense's employee.

        Without it, an expense could be linked to any project, including
        ones the employee never worked on, and the cost would go into that
        project's profitability and onto the customer invoice. A project
        manager or an administrator sees the whole list.

        The **employee's** projects count, not those of the logged-in user:
        a team leader entering expenses for Leo sees Leo's projects.

        The domain limits what is **suggested**: it filters the interface,
        not the records, and is not a security barrier.
        """
        viewer = self.env.user
        if viewer.has_group('project.group_project_manager') \
                or viewer.has_group('base.group_system'):
            for expense in self:
                expense.expense_scan_project_domain = "[]"
            return

        for expense in self:
            employee = expense.employee_id or viewer.employee_id
            user = employee.user_id
            # The second term covers the projects the employee manages: they
            # may have no task or assignment on them.
            expense.expense_scan_project_domain = str([
                '|', ('id', 'in', self._expense_scan_employee_project_ids(employee)),
                ('user_id', '=', user.id or False),
            ])

    @api.model
    def _expense_scan_employee_project_ids(self, employee):
        """An employee's projects, in the broad sense.

        Three sources: the projects they manage, those where a task is
        assigned to them, and those of their past or future assignments.

        Searched with elevated rights: a team leader does not necessarily
        have access to the tasks or time off of the employee they enter
        expenses for. Only project ids are kept.
        """
        if not employee:
            return []
        sudo = self.sudo()
        user = employee.user_id
        projects = sudo.env['project.project']
        if user:
            projects |= sudo.env['project.project'].search([('user_id', '=', user.id)])
            tasks = sudo.env['project.task'].search([('user_ids', 'in', user.id)])
            projects |= tasks.project_id

        # Assignments are time off entries carrying a project: this needs
        # Time Off (hr_holidays) and a "project_id" field on time off.
        if 'hr.leave' in sudo.env:
            Leave = sudo.env['hr.leave']
            if 'project_id' in Leave._fields:
                missions = Leave.search([
                    ('employee_id', '=', employee.id),
                    ('project_id', '!=', False),
                ])
                projects |= missions.project_id
        return projects.ids

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        """Hide "Customer to Reinvoice": it follows from the project.

        That field comes from ``sale_expense`` and cannot be reached by xpath
        before the view applies. Changing it here, on the assembled tree,
        works in every case and adds no dependency.
        """
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form' and self.env.company.expense_scan_reinvoice:
            for node in arch.xpath("//field[@name='sale_order_id']"):
                node.set('invisible', '1')
        return arch, view

    @api.onchange('reinvoice_mode')
    def _onchange_expense_scan_reinvoice_mode(self):
        """"To decide" frees the project; "No" keeps it, without a sales order.

        An expense that is not re-invoiced can stay linked to its project:
        its cost then goes into the project budget without reaching the
        customer invoice.
        """
        for expense in self:
            if expense.reinvoice_mode == 'todo':
                expense.project_id = False
                expense.expense_scan_task_id = False
            if expense.reinvoice_mode != 'project' and 'sale_order_id' in expense._fields:
                expense.sale_order_id = False
            if expense.reinvoice_mode in ('project', 'none') and expense.project_id:
                for name, value in expense._expense_scan_project_values(
                        expense.project_id, reinvoice=expense.reinvoice_mode == 'project').items():
                    if name not in ('project_id', 'reinvoice_mode'):
                        expense[name] = value

    @api.onchange('project_id')
    def _onchange_expense_scan_project(self):
        """A project chosen by hand has the same consequences."""
        for expense in self:
            if expense.expense_scan_task_id.sudo().project_id != expense.project_id:
                expense.expense_scan_task_id = False
            # "No" is kept when the project is chosen afterwards: it then only
            # serves budget follow-up.
            reinvoice = expense.reinvoice_mode != 'none'
            values = expense._expense_scan_project_values(expense.project_id, reinvoice=reinvoice)
            values.pop('project_id', None)
            for name, value in values.items():
                expense[name] = value

    def _expense_scan_project_values(self, project, reinvoice=True):
        """What linking to a project implies.

        Two consequences, when the matching modules are installed: the
        analytic distribution (the cost goes into the project's
        profitability) and, for a re-invoiced expense only, the sales order
        to re-invoice (the line appears on the next invoice).
        """
        self.ensure_one()
        if not project:
            return {}

        values = {'project_id': project.id,
                  'reinvoice_mode': 'project' if reinvoice else 'none'}

        # Read with elevated rights: the analytic account and the sales
        # order are restricted to the Analytic and Sales groups. Only ids,
        # derived from the project, are kept.
        project = project.sudo()
        distribution = self._expense_scan_auto_distribution(project)
        if distribution:
            values['analytic_distribution'] = distribution
        elif self._same_distribution(self.analytic_distribution,
                                     self._expense_scan_auto_distribution(self._origin.project_id)):
            # Project without an account (created on save): the previous
            # project's distribution must not outlive it.
            values['analytic_distribution'] = False

        # First open task of the project (order set by the user), if the task
        # already chosen does not belong to this project.
        if self.expense_scan_task_id.sudo().project_id != project:
            task = self._expense_scan_open_tasks(project)[:1]
            values['expense_scan_task_id'] = task.id or False

        # The sales order to re-invoice only exists with Sales: without it,
        # neither the model nor these fields are present.
        if reinvoice and 'sale.order' in self.env:
            order = self.env['sale.order']
            if 'reinvoiced_sale_order_id' in project._fields:
                order = project.reinvoiced_sale_order_id
            if not order and 'sale_order_id' in project._fields:
                order = project.sale_order_id
            if order and 'sale_order_id' in self._fields:
                values['sale_order_id'] = order.id
        return values

    def _expense_scan_find_project(self, scan_date):
        """The employee's project running on the receipt date.

        An assignment module may record assignments as time off entries
        carrying a project, recognisable by the project on the time off. If
        several assignments cover the date, the field stays empty.
        """
        self.ensure_one()
        employee = self.employee_id
        if not employee or not scan_date:
            return self.env['project.project']

        projects = self._expense_scan_missions_at(employee, scan_date)
        if len(projects) == 1:
            return projects
        if projects:
            return self.env['project.project']

        return self._expense_scan_only_project_of(employee)

    def _expense_scan_missions_at(self, employee, scan_date):
        """Projects of the assignments covering this date.

        Read with elevated rights: only project ids are kept.
        """
        if 'hr.leave' not in self.env:
            return self.env['project.project']
        Leave = self.env['hr.leave'].sudo()
        if 'project_id' not in Leave._fields:
            return self.env['project.project']  # no assignment module
        missions = Leave.search([
            ('employee_id', '=', employee.id),
            ('project_id', '!=', False),
            ('state', '!=', 'refuse'),
            ('request_date_from', '<=', scan_date),
            ('request_date_to', '>=', scan_date),
        ])
        return missions.project_id.with_env(self.env)

    def _expense_scan_only_project_of(self, employee):
        """Fallback without dated assignments: the employee's only project.

        Criterion: the projects where the employee has a task. Empty when
        there are several.
        """
        user = employee.user_id
        if not user:
            return self.env['project.project']
        tasks = self.env['project.task'].sudo().search([('user_ids', 'in', user.id)])
        projects = tasks.project_id.with_env(self.env)
        return projects if len(projects) == 1 else self.env['project.project']
