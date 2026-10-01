# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Re-invoicing the expenses of a project on one line of its sales order.

Odoo's own mechanism (``sale_expense``) adds one order line per expense, once
the expense is posted. Here the approved expenses of a project are added up on
the order's expense line (the line whose product can be expensed): its
quantity is the amount excl. tax, at a unit price of 1. The next invoice takes
what has not been invoiced yet and posts the expenses it covers.

Everything is optional. Without Sales, without a project or with the company
setting off, nothing happens.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_round

_logger = logging.getLogger(__name__)

#: Rounding of the amounts moved between expenses and order lines.
CENT = 0.005


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_invoice_id = fields.Many2one(
        comodel_name='account.move',
        string="Re-invoiced on",
        readonly=True,
        copy=False,
        index='btree_not_null',
        ondelete='set null',
        help="Customer invoice that carries this expense. Set when the "
             "invoice is posted; cleared if the invoice goes back to draft.",
    )

    #: Fields whose change can move an expense onto or off the order line.
    SYNC_FIELDS = frozenset({
        'reinvoice_mode', 'project_id', 'approval_state', 'total_amount',
        'total_amount_currency', 'tax_ids', 'quantity', 'price_unit',
        'currency_id', 'company_id',
    })

    # ------------------------------------------------------------------
    # Keeping the order line up to date
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_sync_after()
        return expenses

    def write(self, vals):
        watch = bool(self.SYNC_FIELDS & set(vals)) and not self.env.context.get('expense_scan_no_sync')
        before = self._expense_scan_order_ids() if watch else set()
        result = super().write(vals)
        if watch:
            self.env['hr.expense']._expense_scan_sync_orders(before | self._expense_scan_order_ids())
        return result

    def _expense_scan_sync_after(self):
        order_ids = self._expense_scan_order_ids()
        if order_ids:
            self.env['hr.expense']._expense_scan_sync_orders(order_ids)

    def _expense_scan_order_ids(self):
        """Orders the expenses are re-invoiced on, through their project."""
        if 'sale.order' not in self.env:
            return set()
        projects = self.sudo().filtered(
            lambda e: e.company_id.expense_scan_reinvoice and e.reinvoice_mode == 'project').project_id
        return set(self._expense_scan_orders_of(projects).ids)

    @api.model
    def _expense_scan_orders_of(self, projects):
        """The sales orders a set of projects is re-invoiced on."""
        orders = self.env['sale.order'].sudo()
        for project in projects.sudo():
            for name in ('reinvoiced_sale_order_id', 'sale_order_id'):
                if name in project._fields and project[name]:
                    orders |= project[name]
                    break
        return orders

    @api.model
    def _expense_scan_projects_of(self, order):
        """Projects re-invoiced on this order."""
        Project = self.env['project.project'].sudo()
        domain = [('company_id', 'in', [False, order.company_id.id])]
        names = [name for name in ('reinvoiced_sale_order_id', 'sale_order_id') if name in Project._fields]
        if not names:
            return Project
        links = [(name, '=', order.id) for name in names]
        return Project.search(domain + ['|'] * (len(links) - 1) + links)

    @api.model
    def _expense_scan_sync_orders(self, order_ids):
        """Bring the expense line of each order in line with the expenses."""
        if 'sale.order' not in self.env or not order_ids:
            return
        for order in self.env['sale.order'].sudo().browse(sorted(order_ids)).exists():
            if order.state != 'sale' or not order.company_id.expense_scan_reinvoice:
                continue
            try:
                with self.env.cr.savepoint():
                    self._expense_scan_sync_order(order)
            except Exception:  # noqa: BLE001 - never block an approval
                _logger.warning("Could not update the expense line of %s", order.name, exc_info=True)

    @api.model
    def _expense_scan_sync_all(self):
        """Safety net (cron): every confirmed order that has projects."""
        if 'sale.order' not in self.env:
            return
        Project = self.env['project.project'].sudo()
        names = [name for name in ('reinvoiced_sale_order_id', 'sale_order_id') if name in Project._fields]
        if not names:
            return
        links = [(name, '!=', False) for name in names]
        projects = Project.search(['|'] * (len(links) - 1) + links)
        order_ids = set(self._expense_scan_orders_of(projects).filtered(
            lambda o: o.state == 'sale' and o.company_id.expense_scan_reinvoice).ids)
        self._expense_scan_sync_orders(order_ids)

    @api.model
    def _expense_scan_counted(self, projects, company):
        """Approved expenses to re-invoice on these projects."""
        expenses = self.sudo().search([
            ('company_id', '=', company.id),
            ('project_id', 'in', projects.ids),
            ('reinvoice_mode', '=', 'project'),
            ('approval_state', '=', 'approved'),
        ])
        if 'sale_order_line_id' in self._fields:
            # Already carried by an order line of Odoo's own mechanism.
            expenses = expenses.filtered(lambda e: not e.sale_order_line_id)
        return expenses

    @api.model
    def _expense_scan_waiting(self, projects, company):
        """Expenses of these projects that hold the order line back.

        Those waiting for the manager, and those whose re-invoicing is still
        to decide: the line only moves once they are all settled.
        """
        base = [('company_id', '=', company.id), ('project_id', 'in', projects.ids)]
        Expense = self.sudo()
        return Expense.search(base + [('state', '=', 'submitted'), ('reinvoice_mode', '!=', 'none')]) \
            | Expense.search(base + [('reinvoice_mode', '=', 'todo'), ('state', 'not in', ('refused',))])

    @api.model
    def _expense_scan_order_line(self, order):
        """The order's expense line: its product can be expensed."""
        lines = order.order_line.filtered(
            lambda l: not l.display_type and l.product_id.can_be_expensed and l.product_id.type == 'service')
        return lines.sorted(lambda l: (l.sequence, l.id))[:1]

    @api.model
    def _expense_scan_line_product(self, company):
        """Product of an expense line added by the module, when the order has none."""
        domain = [('can_be_expensed', '=', True), ('type', '=', 'service'),
                  ('expense_policy', 'in', ('cost', 'sales_price')),
                  ('company_id', 'in', [False, company.id])]
        return self.env['product.product'].sudo().search(domain, order='id', limit=1)

    @api.model
    def _expense_scan_hold_line(self, order):
        """While expenses wait, the line bills what it already holds, no more.

        A product invoiced on the ordered quantity would otherwise bill the
        figure typed in the quotation.
        """
        line = self._expense_scan_order_line(order)
        if not line or order.locked or line.product_id.invoice_policy != 'order':
            return
        held = max(line.qty_delivered, line.qty_invoiced)
        if float_compare(line.product_uom_qty, held, precision_digits=2):
            line.sudo().write({'product_uom_qty': held})

    @api.model
    def _expense_scan_sync_order(self, order):
        company = order.company_id
        projects = self._expense_scan_projects_of(order)
        if not projects:
            return
        if self._expense_scan_waiting(projects, company):
            self._expense_scan_hold_line(order)
            return
        counted = self._expense_scan_counted(projects, company)
        amount = sum(counted.mapped('untaxed_amount'))
        if order.currency_id != company.currency_id:
            amount = company.currency_id._convert(
                amount, order.currency_id, company, order.date_order or fields.Date.context_today(self))
        amount = order.currency_id.round(amount)

        line = self._expense_scan_order_line(order)
        if line and not amount and not line.qty_delivered and not line.qty_invoiced:
            # Nothing to say yet: the quotation keeps its own figures.
            return
        if not line:
            if not amount or order.locked:
                return
            product = self._expense_scan_line_product(company)
            if not product:
                return
            last = max(order.order_line.mapped('sequence') or [0])
            self.env['sale.order.line'].sudo().create({
                'order_id': order.id,
                'product_id': product.id,
                'product_uom_qty': amount,
                'price_unit': 1.0,
                'sequence': last + 1,
            })
            return
        if order.locked:
            return

        # A line priced otherwise than 1 keeps its price: the quantity follows.
        unit = line.price_unit if line.price_unit > 0 else 1.0
        quantity = float_round(amount / unit, precision_digits=2)
        quantity = max(quantity, line.qty_invoiced)
        values = {}
        if float_compare(line.product_uom_qty, quantity, precision_digits=2):
            values['product_uom_qty'] = quantity
        if line.qty_delivered_method == 'manual' and float_compare(line.qty_delivered, quantity, precision_digits=2):
            values['qty_delivered'] = quantity
        if line.price_unit <= 0:
            values['price_unit'] = unit
        if values:
            line.sudo().write(values)

    def _expense_scan_post_after_invoice(self, move):
        """Post the expenses an invoice has just covered.

        A failure (no journal, no partner for the employee...) must not undo
        the invoice: the expense stays approved and says why in its chatter.
        """
        for expense in self:
            try:
                with self.env.cr.savepoint():
                    if expense.payment_mode == 'company_account':
                        expense.action_post()
                    else:
                        expense._post_without_wizard()
            except Exception as error:  # noqa: BLE001
                _logger.warning("Could not post expense %s after invoice %s",
                                expense.id, move.name, exc_info=True)
                expense.message_post(body=_(
                    "The invoice %(invoice)s carries this expense, but it could not be "
                    "posted: %(error)s", invoice=move.name, error=error))

    # ------------------------------------------------------------------
    # Deciding at approval
    # ------------------------------------------------------------------

    def _expense_scan_check_decided(self):
        """The manager settles the re-invoicing when approving."""
        undecided = self.filtered(
            lambda e: e.company_id.expense_scan_reinvoice and e.reinvoice_mode == 'todo')
        if undecided:
            raise UserError(_(
                "Decide whether to re-invoice these expenses before approving them:\n%s",
                "\n".join("- %s" % (e.name or e.display_name) for e in undecided[:20])))

    def _do_approve(self, check=True):
        self._expense_scan_check_decided()
        return super()._do_approve(check=check)

    # ------------------------------------------------------------------
    # Toggle in the list
    # ------------------------------------------------------------------

    def action_expense_scan_set_reinvoice(self, mode):
        """Set the re-invoicing of the expenses from the list.

        "Yes" looks for the project of the receipt date, as the scan does;
        without a project found, the expense has to be opened.
        """
        if mode not in ('project', 'none', 'todo'):
            raise UserError(_("Unknown choice."))
        for expense in self:
            if not expense.is_editable and not self.env.su:
                raise AccessError(_("You cannot edit this expense."))
            if expense.state in ('posted', 'in_payment', 'paid', 'refused') or expense.expense_scan_invoice_id:
                raise UserError(_("The re-invoicing of %s can no longer change.", expense.name))
            values = {'reinvoice_mode': mode}
            if mode == 'project':
                project = expense.project_id or expense._expense_scan_find_project(expense.date)
                if not project:
                    raise UserError(_(
                        "No project found for %s: open the expense and choose one.", expense.name))
                values = expense._expense_scan_project_values(project, reinvoice=True)
            elif mode == 'todo':
                values.update(project_id=False, expense_scan_task_id=False)
            expense.write(values)
        return True
