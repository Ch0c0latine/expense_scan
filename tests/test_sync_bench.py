# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Benchmark: updating the expense line of a sales order for lots of expenses.

Out of the normal suite (tag ``-standard``); to run it:

    odoo-bin -d DB --test-enable --test-tags expense_scan_bench --stop-after-init

Each scenario starts from a project re-invoiced on a confirmed order, runs in a
savepoint rolled back afterwards, and gives a "SYNC-BENCH|" line in the log:
scenario, number of expenses, time, SQL queries, then the quantity of the
expense line, the activities and the messages on the order (to check that a
change leaves the result as it was). The same scenario with the update of the
line turned off gives the time of everything else: the difference is what the
update costs. ``EXPENSE_SCAN_BENCH_SIZES`` changes the sizes (``50,200,1000``
by default).
"""
import logging
import os
import time
import unittest
from contextlib import closing
from datetime import date, timedelta
from unittest.mock import patch

from odoo import Command
from odoo.tests import common, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', '-standard', 'expense_scan_bench')
class TestSyncBench(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'sale.order' not in cls.env or 'reinvoiced_sale_order_id' not in cls.env['project.project']._fields:
            raise unittest.SkipTest("Sales is not installed")
        cls.env.company.expense_scan_reinvoice = True
        cls.sizes = [int(size) for size in os.environ.get('EXPENSE_SCAN_BENCH_SIZES', '50,200,1000').split(',')]
        cls.category = cls.env['product.product'].create(
            {'name': "Hotel banc", 'can_be_expensed': True, 'standard_price': 0.0,
             'supplier_taxes_id': [Command.clear()]})
        cls.allowance = cls.env['product.product'].create(
            {'name': "Indemnite banc", 'can_be_expensed': True, 'standard_price': 20.0,
             'supplier_taxes_id': [Command.clear()]})
        cls.service = cls.env['product.product'].create(
            {'name': "Assistance banc", 'type': 'service', 'list_price': 500.0, 'invoice_policy': 'delivery'})
        cls.expense_product = cls.env['product.product'].create({
            'name': "Depenses banc", 'type': 'service', 'list_price': 1.0,
            'can_be_expensed': True, 'reinvoice_policy': 'cost', 'invoice_policy': 'order'})
        cls.partner = cls.env['res.partner'].create({'name': "Client banc"})
        cls.manager = cls.env.ref('base.user_admin')

    # ------------------------------------------------------------------

    def world(self, employees=1):
        """A project re-invoiced on a confirmed order, and its employees."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [
                Command.create({'product_id': self.service.id, 'product_uom_qty': 20.0}),
                Command.create({'product_id': self.expense_product.id, 'product_uom_qty': 1.0}),
            ],
        })
        order.action_confirm()
        project = self.env['project.project'].create({'name': "Mission banc", 'reinvoiced_sale_order_id': order.id})
        staff = self.env['hr.employee'].create([
            {'name': "Banc %s" % index, 'expense_manager_id': self.manager.id} for index in range(employees)])
        return order, project, staff

    def values(self, project, employee, index, **extra):
        # One expense a day: the expense rules check again the other expenses of the same day.
        return dict({
            'name': "Frais %s" % index, 'employee_id': employee.id, 'product_id': self.category.id,
            'total_amount_currency': 10.0 + index % 7, 'reinvoice_mode': 'project',
            'project_id': project.id, 'date': date(2028, 1, 3) + timedelta(days=index),
        }, **extra)

    def measure(self, name, size, prepare, run):
        """Run ``run(*prepare())`` and log its time and queries; everything is rolled back."""
        with closing(self.env.cr.savepoint()):
            order, *args = prepare(size)
            self.env.flush_all()
            queries = self.env.cr.sql_log_count
            start = time.perf_counter()
            run(*args)
            self.env.flush_all()
            elapsed = time.perf_counter() - start
            queries = self.env.cr.sql_log_count - queries
            line = order.order_line.filtered(lambda l: l.product_id == self.expense_product)
            activities = len(order.activity_ids)
            messages = len(order.message_ids)
            _logger.info("SYNC-BENCH|%s|%s|%.2fs|%s queries|qty=%.2f delivered=%.2f activities=%s messages=%s",
                         name, size, elapsed, queries, line.product_uom_qty, line.qty_delivered,
                         activities, messages)
        self.env.invalidate_all()

    # ------------------------------------------------------------------
    # Scenarios
    # ------------------------------------------------------------------

    def prepare_empty(self, size):
        order, project, staff = self.world()
        return order, project, staff

    def prepare_approved(self, size, **extra):
        order, project, staff = self.world()
        expenses = self.env['hr.expense'].create([
            self.values(project, staff, index, approval_state='approved', **extra) for index in range(size)])
        return order, expenses

    def prepare_submitted(self, size):
        order, project, staff = self.world()
        expenses = self.env['hr.expense'].create([
            self.values(project, staff, index, approval_state='submitted') for index in range(size)])
        return order, expenses

    def prepare_spread(self, size):
        """``size`` approved expenses, and a tenth as many flat-rate drafts of 10 days to spread."""
        order, approved = self.prepare_approved(size)
        project = approved.project_id
        staff = self.env['hr.employee'].create([
            {'name': "Banc reparti %s" % index} for index in range(max(size // 10, 1))])
        vals_list = [self.values(project, employee, index, product_id=self.allowance.id, quantity=10,
                                 date=date(2031, 3, 3))
                     for index, employee in enumerate(staff)]
        for vals in vals_list:
            del vals['total_amount_currency']  # the rate of the category
        return order, self.env['hr.expense'].create(vals_list)

    @staticmethod
    def create_loop(project, staff, size, values):
        Expense = project.env['hr.expense']
        for index in range(size):
            Expense.create(values(project, staff, index, approval_state='approved'))

    @staticmethod
    def write_loop(expenses):
        for expense in expenses:
            expense.write({'total_amount_currency': expense.total_amount_currency + 1.0})

    def batch(self, run):
        """``run`` in a batch, as a module that changes many expenses does."""
        def batched(*args):
            with self.env['hr.expense']._expense_scan_batch_sync():
                run(*args)
        return batched

    def test_bench(self):
        """Each scenario, then the same without any update of the line: what the update costs."""
        Expense = type(self.env['hr.expense'])
        for size in self.sizes:
            scenarios = [
                ("create in a loop", self.prepare_empty,
                 lambda project, staff: self.create_loop(project, staff, size, self.values)),
                ("write in a loop", self.prepare_approved, self.write_loop),
                ("approve a lot", self.prepare_submitted, lambda expenses: expenses.sudo()._do_approve()),
                ("re-invoice a selection", lambda count: self.prepare_approved(count, reinvoice_mode='none'),
                 lambda expenses: expenses.sudo().action_expense_scan_set_reinvoice_many('project')),
                ("spread flat rates", self.prepare_spread, lambda drafts: drafts.action_expense_scan_spread_days()),
            ]
            for name, prepare, run in scenarios:
                self.measure(name, size, prepare, run)
                if 'loop' in name:
                    run = self.batch(run)
                    self.measure(name + ", in a batch", size, prepare, run)
                with patch.object(Expense, '_expense_scan_sync_order', lambda model, order: None):
                    self.measure(name + ", line not updated", size, prepare, run)
