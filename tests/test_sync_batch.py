# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The expense line of an order updated once at the end of a batch of expenses.

Each scenario runs twice, on two orders set up alike: once with the line
updated at each change of an expense (the batches turned off), once with the
batches. The orders, their activities and the expenses must end up the same.
"""
import collections
import contextlib
import unittest
from datetime import date
from unittest.mock import patch

import psycopg2

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import common, tagged

from .tax_setup import ensure_fiscal_country


@tagged('post_install', '-at_install')
class TestSyncBatch(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_fiscal_country(cls.env)
        if 'sale.order' not in cls.env or 'reinvoiced_sale_order_id' not in cls.env['project.project']._fields:
            raise unittest.SkipTest("Sales is not installed")
        cls.env.company.expense_scan_reinvoice = True
        cls.Expense = type(cls.env['hr.expense'])
        cls.manager = cls.env.ref('base.user_admin')
        cls.category = cls.env['product.product'].create(
            {'name': "Hotel groupe", 'can_be_expensed': True, 'standard_price': 0.0,
             'supplier_taxes_id': [Command.clear()]})
        cls.allowance = cls.env['product.product'].create(
            {'name': "Indemnite groupee", 'can_be_expensed': True, 'standard_price': 15.0,
             'supplier_taxes_id': [Command.clear()]})
        cls.service = cls.env['product.product'].create({
            'name': "Assistance groupee", 'type': 'service', 'list_price': 500.0,
            'invoice_policy': 'delivery'})
        cls.expense_product = cls.env['product.product'].create({
            'name': "Depenses groupees", 'type': 'service', 'list_price': 1.0,
            'can_be_expensed': True, 'expense_policy': 'cost', 'invoice_policy': 'order'})
        cls.partner = cls.env['res.partner'].create({'name': "Client Groupe"})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [
                Command.create({'product_id': self.service.id, 'product_uom_qty': 20.0}),
                Command.create({'product_id': self.expense_product.id, 'product_uom_qty': 1.0}),
            ],
        })
        order.action_confirm()
        return order

    def world(self):
        """Two orders: the first one with two projects, the second with one."""
        first, second = self.order(), self.order()
        Project = self.env['project.project']
        return {
            # Its own employee: spreading skips the days the employee already has.
            'employee': self.env['hr.employee'].create({
                'name': "Lucie Groupee", 'expense_manager_id': self.manager.id}),
            'orders': {'first': first, 'second': second},
            'projects': {
                'a': Project.create({'name': "Mission A", 'reinvoiced_sale_order_id': first.id}),
                'b': Project.create({'name': "Mission B", 'reinvoiced_sale_order_id': first.id}),
                'c': Project.create({'name': "Mission C", 'reinvoiced_sale_order_id': second.id}),
            },
            'expenses': {},
        }

    def line(self, order):
        return order.order_line.filtered(lambda l: l.product_id == self.expense_product)

    def expense(self, world, name, amount, project='a', mode='project', submit=True, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': name, 'employee_id': world['employee'].id, 'product_id': self.category.id,
            'total_amount_currency': amount, 'reinvoice_mode': mode,
            'project_id': world['projects'][project].id if project else False,
        }, **values))
        if submit:
            expense.action_submit()
        world['expenses'][name] = expense
        return expense

    def expenses(self, world, *names):
        return self.env['hr.expense'].concat(*(world['expenses'][name] for name in names))

    def approve(self, expenses):
        expenses.sudo()._do_approve()

    def snapshot(self, world):
        """What the orders and the expenses look like, without ids."""
        self.env.invalidate_all()
        activity_type = self.env.ref('expense_scan.mail_activity_type_waiting')
        project_keys = {project: key for key, project in world['projects'].items()}
        orders = {}
        for key, order in world['orders'].items():
            line = self.line(order)
            activities = order.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
            orders[key] = {
                'lines': len(order.order_line),
                'ordered': line.product_uom_qty,
                'delivered': line.qty_delivered,
                'invoiced': line.qty_invoiced,
                'activities': sorted(str(activity.note) for activity in activities),
                'invoices': sorted((move.state, move.amount_untaxed) for move in order.invoice_ids),
            }
        expenses = {
            name: (expense.state, expense.reinvoice_mode, project_keys.get(expense.project_id),
                   bool(expense.expense_scan_invoice_id), expense.expense_scan_invoice_id.state)
            for name, expense in world['expenses'].items()}
        return {'orders': orders, 'expenses': expenses}

    @contextlib.contextmanager
    def immediate(self):
        """The line updated at each change, as without the batches."""
        with patch.object(self.Expense, '_expense_scan_batch_sync', lambda model: contextlib.nullcontext()):
            yield

    def same_with_and_without_batch(self, prepare, run):
        """``prepare(world)`` then ``run(world)``: the same end with the line updated at once or at the end."""
        results = {}
        for batched in (False, True):
            world = self.world()
            prepare(world)
            if batched:
                with self.env['hr.expense']._expense_scan_batch_sync():
                    run(world)
            else:
                with self.immediate():
                    run(world)
            results[batched] = self.snapshot(world)
        self.assertEqual(results[True], results[False])
        return results[True]

    @contextlib.contextmanager
    def counting(self):
        """Count the updates of the line, per order."""
        counts = collections.Counter()
        update = self.Expense._expense_scan_sync_order

        def sync_order(model, order):
            counts[order.id] += 1
            return update(model, order)

        with patch.object(self.Expense, '_expense_scan_sync_order', sync_order):
            yield counts

    # ------------------------------------------------------------------
    # One update per order
    # ------------------------------------------------------------------

    def test_one_update_per_order_for_a_batch(self):
        world = self.world()
        first, second = world['orders']['first'], world['orders']['second']
        waiting = [self.expense(world, "Lot %s" % index, 10.0 + index, project='abc'[index % 3])
                   for index in range(12)]
        with self.counting() as counts:
            with self.env['hr.expense']._expense_scan_batch_sync():
                for expense in waiting:
                    self.approve(expense)
                for index in range(6):
                    self.expense(world, "Neuf %s" % index, 5.0, project='ac'[index % 2], submit=False)
                for expense in waiting[:4]:
                    expense.sudo().write({'total_amount_currency': expense.total_amount_currency + 1.0})
        self.assertEqual(dict(counts), {first.id: 1, second.id: 1})
        # 10+11+...+21, plus 1 on four of them.
        self.assertEqual(self.line(first).product_uom_qty + self.line(second).product_uom_qty, 186.0 + 4.0)

    def test_the_lots_of_the_module_update_each_order_once(self):
        world = self.world()
        order = world['orders']['first']
        lot = self.env['hr.expense'].concat(*(
            self.expense(world, "Lot %s" % index, 10.0) for index in range(8)))
        with self.counting() as counts:
            self.approve(lot)
        self.assertEqual(counts[order.id], 1, "approval of a lot")
        self.assertEqual(self.line(order).product_uom_qty, 80.0)

        kept = self.env['hr.expense'].concat(*(
            self.expense(world, "Garde %s" % index, 5.0, mode='none') for index in range(5)))
        self.approve(kept)
        with self.counting() as counts:
            result = kept.sudo().action_expense_scan_set_reinvoice_many('project')
        self.assertEqual(result['done'], 5)
        self.assertEqual(counts[order.id], 1, "re-invoicing a selection")
        self.assertEqual(self.line(order).product_uom_qty, 105.0)

        flat = self.env['hr.expense'].concat(*(self.env['hr.expense'].create({
            'name': "Forfait %s" % index, 'product_id': self.allowance.id, 'quantity': 3,
            'employee_id': self.env['hr.employee'].create({'name': "Forfait %s" % index}).id,
            'date': date(2031, 3, 3), 'reinvoice_mode': 'project',
            'project_id': world['projects']['a'].id}) for index in range(4)))
        with self.counting() as counts:
            flat.action_expense_scan_spread_days()
        self.assertEqual(counts[order.id], 1, "spreading flat rates")
        self.assertEqual(len(self.env['hr.expense'].search([('name', 'like', 'Forfait')])), 12)

    def test_outside_a_batch_each_change_updates_at_once(self):
        world = self.world()
        expense = self.expense(world, "Seule", 40.0)
        with self.counting() as counts:
            expense.sudo().write({'approval_state': 'approved'})
            self.assertEqual(self.line(world['orders']['first']).product_uom_qty, 40.0)
            expense.sudo().write({'total_amount_currency': 45.0})
            self.assertEqual(self.line(world['orders']['first']).product_uom_qty, 45.0)
        self.assertEqual(counts[world['orders']['first'].id], 2)

    def test_nested_batches_update_at_the_end_of_the_outermost(self):
        world = self.world()
        order = world['orders']['first']
        expense = self.expense(world, "Imbriquee", 30.0)
        Expense = self.env['hr.expense']
        with self.counting() as counts:
            with Expense._expense_scan_batch_sync():
                with Expense._expense_scan_batch_sync():
                    self.approve(expense)
                self.assertEqual(self.line(order).product_uom_qty, 0.0, "the inner block leaves it to the outer one")
            self.assertEqual(self.line(order).product_uom_qty, 30.0)
        self.assertEqual(counts[order.id], 1)

    def test_pending_lines_on_demand(self):
        world = self.world()
        order = world['orders']['first']
        first, second = self.expense(world, "Avant", 30.0), self.expense(world, "Apres", 20.0)
        Expense = self.env['hr.expense']
        with Expense._expense_scan_batch_sync():
            self.approve(first)
            Expense._expense_scan_sync_pending()
            self.assertEqual(self.line(order).product_uom_qty, 0.0, "the second one still waits")
            self.approve(second)
            Expense._expense_scan_sync_pending()
            self.assertEqual(self.line(order).product_uom_qty, 50.0)
            third = self.expense(world, "Encore", 5.0, submit=False)
            third.sudo().write({'approval_state': 'approved'})
            self.assertEqual(self.line(order).product_uom_qty, 50.0)
        self.assertEqual(self.line(order).product_uom_qty, 55.0)

    def test_an_invoice_made_in_a_batch_sees_the_line_up_to_date(self):
        world = self.world()
        order = world['orders']['first']
        first, second = self.expense(world, "Facturee", 70.0), self.expense(world, "Plus tard", 30.0)
        Expense = self.env['hr.expense']
        with Expense._expense_scan_batch_sync():
            self.approve(first | second)
            # A customer invoice brings the pending lines up to date before Sales reads them.
            self.env['account.move'].create({'move_type': 'out_invoice', 'partner_id': self.partner.id})
            self.assertEqual(self.line(order).product_uom_qty, 100.0)
            invoice = order._create_invoices()
            self.assertEqual((first | second).expense_scan_invoice_id, invoice)
            invoice.action_post()
            self.assertEqual(first.state, 'posted')
            late = self.expense(world, "Apres facture", 12.0, submit=False)
            late.sudo().write({'approval_state': 'approved'})
            # Cancelling the invoice brings the line up to date first, as at once.
            invoice.button_draft()
            invoice.button_cancel()
            self.assertFalse(first.expense_scan_invoice_id)
        self.assertEqual(self.line(order).product_uom_qty, 112.0)

    def test_an_error_leaving_the_batch_still_updates_the_lines(self):
        """A caller that catches the error keeps the changes: the line follows them."""
        world = self.world()
        expense = self.expense(world, "Erreur", 25.0)
        try:
            with self.env['hr.expense']._expense_scan_batch_sync():
                self.approve(expense)
                raise UserError("stop")
        except UserError:
            pass
        self.assertEqual(self.line(world['orders']['first']).product_uom_qty, 25.0)

    def test_a_database_error_leaves_the_lines_to_the_rollback(self):
        world = self.world()
        expense = self.expense(world, "Base", 25.0)
        with self.counting() as counts:
            with self.assertRaises(psycopg2.Error):
                with self.env['hr.expense']._expense_scan_batch_sync():
                    self.approve(expense)
                    raise psycopg2.Error("aborted")
        self.assertFalse(counts)

    def test_an_order_in_error_does_not_stop_the_others(self):
        world = self.world()
        first, second = world['orders']['first'], world['orders']['second']
        lot = self.expense(world, "Sur A", 40.0) | self.expense(world, "Sur C", 60.0, project='c')
        update = self.Expense._expense_scan_sync_order

        def sync_order(model, order):
            if order == first:
                raise ValueError("broken order")
            return update(model, order)

        with patch.object(self.Expense, '_expense_scan_sync_order', sync_order), \
                self.assertLogs('odoo.addons.expense_scan.models.hr_expense_invoicing', 'WARNING') as logs:
            self.approve(lot)
        self.assertIn(first.name, logs.output[0])
        self.assertEqual(lot.mapped('state'), ['approved', 'approved'], "the approval stands")
        self.assertEqual(self.line(first).product_uom_qty, 0.0, "as it was, held back by the expense waiting")
        self.assertEqual(self.line(second).product_uom_qty, 60.0)

    # ------------------------------------------------------------------
    # Same end as with the line updated at each change
    # ------------------------------------------------------------------

    def test_same_end_creation(self):
        def run(world):
            for index in range(4):
                expense = self.expense(world, "Cree %s" % index, 10.0 * (index + 1), submit=False)
                expense.sudo().write({'approval_state': 'approved'})
            self.expense(world, "Non refacturee", 50.0, mode='none')
            self.expense(world, "En attente", 5.0, project='c')
        result = self.same_with_and_without_batch(lambda world: None, run)
        self.assertEqual(result['orders']['first']['ordered'], 100.0)
        self.assertEqual(len(result['orders']['second']['activities']), 1, "an expense waits on the second order")

    def test_same_end_approval(self):
        def prepare(world):
            self.approve(self.expense(world, "Deja", 15.0))
            for index in range(5):
                self.expense(world, "A approuver %s" % index, 10.0, project='ab'[index % 2])
            self.expense(world, "Reste", 7.0, project='c')

        def run(world):
            for index in range(5):
                self.approve(world['expenses']["A approuver %s" % index])
            self.approve(self.expenses(world, "Reste"))
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['orders']['first']['ordered'], 65.0)
        self.assertEqual(result['orders']['second']['ordered'], 7.0)
        self.assertFalse(result['orders']['first']['activities'])

    def test_same_end_approval_with_expenses_still_waiting(self):
        def prepare(world):
            self.approve(self.expense(world, "Deja", 15.0))
            for index in range(4):
                self.expense(world, "A approuver %s" % index, 10.0)

        def run(world):
            self.approve(self.expenses(world, "A approuver 0", "A approuver 1"))
            self.approve(world['expenses']["A approuver 2"])
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['orders']['first']['ordered'], 15.0, "one still waits: the line holds")
        self.assertEqual(len(result['orders']['first']['activities']), 1)

    def test_same_end_refusal(self):
        def prepare(world):
            for index in range(4):
                self.expense(world, "Refus %s" % index, 20.0 + index)
            self.approve(self.expenses(world, *("Refus %s" % index for index in range(4))))

        def run(world):
            world['expenses']["Refus 1"].sudo().write({'approval_state': 'refused'})
            world['expenses']["Refus 3"].sudo()._do_refuse("hors mission")
            world['expenses']["Refus 2"].sudo().action_expense_scan_unapprove()
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['orders']['first']['ordered'], 20.0)
        self.assertEqual(len(result['orders']['first']['activities']), 1, "the unapproved one waits")

    def test_a_batch_keeps_the_line_held_while_an_expense_waits_at_its_end(self):
        """The line follows the state at the end of the batch, not each step on the way.

        Expenses waiting hold the line back: it goes down, never up. When the
        last one waiting is settled in the middle of a batch and another one
        waits again before its end, a line updated at each change lets the
        amount of that moment through; the batch keeps the line held, as one
        change of all the expenses together would.
        """
        def prepare(world):
            for index in range(4):
                self.expense(world, "Refus %s" % index, 20.0 + index)
            self.approve(self.expenses(world, *("Refus %s" % index for index in range(3))))

        def run(world):
            world['expenses']["Refus 1"].sudo().write({'approval_state': 'refused'})
            world['expenses']["Refus 3"].sudo()._do_refuse("hors mission")  # nothing waits
            world['expenses']["Refus 2"].sudo().action_expense_scan_unapprove()  # waits again

        ends = {}
        for batched in (False, True):
            world = self.world()
            prepare(world)
            self.assertEqual(self.line(world['orders']['first']).product_uom_qty, 0.0, "held back")
            with (self.env['hr.expense']._expense_scan_batch_sync() if batched else self.immediate()):
                run(world)
            ends[batched] = self.line(world['orders']['first']).product_uom_qty
        self.assertEqual(ends, {False: 20.0, True: 0.0})

    def test_same_end_reset_refused_for_an_invoiced_expense(self):
        def prepare(world):
            self.approve(self.expense(world, "Facturee", 100.0) | self.expense(world, "Libre", 40.0, mode='none'))
            world['orders']['first']._create_invoices()
            self.expense(world, "Nouvelle", 30.0)

        def run(world):
            invoiced, free = world['expenses']["Facturee"], world['expenses']["Libre"]
            with self.assertRaises(UserError):
                invoiced.sudo()._do_reset_approval()
            self.assertEqual(invoiced.state, 'approved')
            (invoiced | free).sudo().action_expense_scan_reset_batch()
            self.approve(world['expenses']["Nouvelle"])
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['expenses']["Facturee"][:2], ('approved', 'project'))
        self.assertEqual(result['expenses']["Libre"][0], 'draft')
        self.assertEqual(result['orders']['first']['ordered'], 130.0)

    def test_same_end_expense_taken_off_a_project_or_an_order(self):
        def prepare(world):
            for index in range(5):
                self.expense(world, "Mobile %s" % index, 10.0 + index)
            self.approve(self.expenses(world, *("Mobile %s" % index for index in range(5))))

        def run(world):
            projects = world['projects']
            world['expenses']["Mobile 0"].sudo().write({'project_id': projects['c'].id})
            world['expenses']["Mobile 1"].sudo().write({'project_id': projects['b'].id})
            world['expenses']["Mobile 2"].sudo().write({'reinvoice_mode': 'none'})
            world['expenses']["Mobile 3"].sudo().write({'project_id': False, 'reinvoice_mode': 'none'})
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['orders']['first']['ordered'], 25.0)
        self.assertEqual(result['orders']['second']['ordered'], 10.0)

    def test_same_end_two_projects_of_one_order(self):
        def run(world):
            for index in range(6):
                expense = self.expense(world, "Deux %s" % index, 5.0 * (index + 1), project='ab'[index % 2])
                self.approve(expense)
            world['expenses']["Deux 5"].sudo().write({'total_amount_currency': 1.0})
        result = self.same_with_and_without_batch(lambda world: None, run)
        self.assertEqual(result['orders']['first']['ordered'], 76.0)

    def test_same_end_two_orders(self):
        def prepare(world):
            for index in range(3):
                self.expense(world, "Premiere %s" % index, 10.0, mode='todo', project=False, submit=False)
                self.expense(world, "Seconde %s" % index, 20.0, project='c')

        def run(world):
            first = self.expenses(world, *("Premiere %s" % index for index in range(3)))
            first.sudo().action_expense_scan_set_reinvoice_many('none')
            first.sudo().write({'project_id': world['projects']['a'].id})
            first.sudo().action_expense_scan_set_reinvoice_many('project')
            first.action_submit()
            self.approve(first | self.expenses(world, *("Seconde %s" % index for index in range(3))))
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['orders']['first']['ordered'], 30.0)
        self.assertEqual(result['orders']['second']['ordered'], 60.0)

    def test_same_end_spread_and_invoice(self):
        def prepare(world):
            self.approve(self.expense(world, "Mission", 80.0))
            world['expenses']["Forfait"] = self.env['hr.expense'].create({
                'name': "Forfait", 'product_id': self.allowance.id, 'quantity': 4,
                'employee_id': world['employee'].id, 'date': date(2031, 3, 3),
                'reinvoice_mode': 'project', 'project_id': world['projects']['a'].id})

        def run(world):
            spread = self.env['hr.expense'].search(
                world['expenses']["Forfait"].action_expense_scan_spread_days()['domain'], order='date')
            for index, expense in enumerate(spread):
                world['expenses']["Forfait %s" % index] = expense
            spread.action_submit()
            self.approve(spread)
            # Sales reads the line to invoice: the pending lines first.
            self.env['hr.expense']._expense_scan_sync_pending()
            invoice = world['orders']['first']._create_invoices()
            invoice.action_post()
        result = self.same_with_and_without_batch(prepare, run)
        self.assertEqual(result['orders']['first']['ordered'], 140.0)
        self.assertEqual(result['orders']['first']['invoiced'], 140.0)
        self.assertTrue(all(expense[3] for expense in result['expenses'].values()), "all on the invoice")
