# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Re-invoicing the expenses of a project on one line of its sales order.

Odoo's own mechanism (``sale_expense``) adds one order line per expense, once
the expense is posted. Here the approved expenses of a project are added up on
the order's expense line (the line whose product can be expensed): its
quantity is the amount incl. tax, at a unit price of 1. The next invoice takes
what has not been invoiced yet and posts the expenses it covers.

Everything is optional. Without Sales, without a project or with the company
setting off, nothing happens.
"""
import logging

from odoo import Command, _, api, fields, models
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
        help="Customer invoice that carries this expense, draft or posted. "
             "Cleared if the invoice is deleted, cancelled or goes back to "
             "draft.",
    )

    expense_scan_reinvoiced_amount = fields.Monetary(
        string="Re-invoiced amount",
        currency_field='company_currency_id',
        compute='_compute_expense_scan_reinvoiced_amount',
        store=True,
        help="Amount incl. tax of a re-invoiced expense; zero otherwise. Its "
             "total at the bottom of the list is what goes to the customers.",
    )

    @api.depends('reinvoice_mode', 'total_amount')
    def _compute_expense_scan_reinvoiced_amount(self):
        for expense in self:
            expense.expense_scan_reinvoiced_amount = (
                expense.total_amount if expense.reinvoice_mode == 'project' else 0.0)

    #: Fields whose change can move an expense onto or off the order line.
    SYNC_FIELDS = frozenset({
        'reinvoice_mode', 'project_id', 'approval_state', 'total_amount',
        'total_amount_currency', 'tax_ids', 'quantity', 'price_unit',
        'currency_id', 'company_id', 'date', 'product_id', 'employee_id',
    })

    #: Fields that cannot change while an invoice carries the expense.
    LOCKED_FIELDS = frozenset({
        'reinvoice_mode', 'project_id', 'total_amount', 'total_amount_currency',
        'tax_ids', 'quantity', 'price_unit', 'currency_id', 'date', 'product_id',
        'employee_id', 'company_id', 'payment_mode',
    })

    # ------------------------------------------------------------------
    # Keeping the order line up to date
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any('expense_scan_invoice_id' in vals for vals in vals_list):
            raise AccessError(_("The invoice of an expense is set by the invoicing, not by hand."))
        expenses = super().create(vals_list)
        expenses._expense_scan_sync_after()
        return expenses

    def write(self, vals):
        if 'expense_scan_invoice_id' in vals and not self.env.su:
            raise AccessError(_("The invoice of an expense is set by the invoicing, not by hand."))
        self._expense_scan_check_not_invoiced(vals)
        watch = bool(self.SYNC_FIELDS & set(vals)) and not self.env.context.get('expense_scan_no_sync')
        before = self._expense_scan_order_ids() if watch else set()
        result = super().write(vals)
        if watch:
            self.env['hr.expense']._expense_scan_sync_orders(before | self._expense_scan_order_ids())
        return result

    def _expense_scan_check_not_invoiced(self, vals):
        """An expense on an invoice stays as it is.

        The invoice bills it as approved and re-invoiced: taking it back or
        changing it would leave the invoice and the expenses disagreeing. The
        way out is to delete (or cancel) the invoice, change the expenses,
        then invoice again.
        """
        # The flag only counts for the server's own calls (they run as superuser): a
        # client can send any context.
        if self.env.context.get('expense_scan_free') and self.env.su:
            return
        touches = bool(self.LOCKED_FIELDS & set(vals)) or (
            'approval_state' in vals and vals['approval_state'] != 'approved')
        if not touches:
            return
        invoiced = self.sudo().filtered('expense_scan_invoice_id')
        if invoiced:
            expense = invoiced[:1]
            raise UserError(_(
                "%(expense)s is on the invoice %(invoice)s. Delete or cancel the invoice first, "
                "change the expenses, then invoice again.",
                expense=expense.name or expense.display_name,
                invoice=expense.expense_scan_invoice_id.display_name))

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
        projects = Project.search(domain + ['|'] * (len(links) - 1) + links)
        # A project is re-invoiced on one order: the one `_expense_scan_orders_of` gives it.
        return projects.filtered(lambda p: order in self._expense_scan_orders_of(p))

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
    def _expense_scan_in_period(self, expenses, order):
        """Expenses that belong to the period of the order (all, unless a module narrows it)."""
        return expenses

    @api.model
    def _expense_scan_counted(self, projects, company, order=None):
        """Approved expenses to re-invoice on these projects, within the period of the order."""
        expenses = self.sudo().search([
            ('company_id', '=', company.id),
            ('project_id', 'in', projects.ids),
            ('reinvoice_mode', '=', 'project'),
            ('approval_state', '=', 'approved'),
        ])
        if 'sale_order_line_id' in self._fields:
            # Already carried by an order line of Odoo's own mechanism.
            expenses = expenses.filtered(lambda e: not e.sale_order_line_id)
        return self._expense_scan_in_period(expenses, order) if order else expenses

    @api.model
    def _expense_scan_waiting(self, projects, company, order=None):
        """Expenses of these projects that hold the order line back.

        Those waiting for the manager, and those whose re-invoicing is still
        to decide: the line only moves once they are all settled. With an
        order, only those of its period.
        """
        base = [('company_id', '=', company.id), ('project_id', 'in', projects.ids)]
        Expense = self.sudo()
        waiting = Expense.search(base + [('state', '=', 'submitted'), ('reinvoice_mode', '!=', 'none')]) \
            | Expense.search(base + [('reinvoice_mode', '=', 'todo'), ('state', 'not in', ('refused',))])
        return self._expense_scan_in_period(waiting, order) if order else waiting

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
    def _expense_scan_flag_waiting(self, order, waiting):
        """An activity on the order while expenses hold its line back."""
        activity_type = self.env.ref('expense_scan.mail_activity_type_waiting', raise_if_not_found=False)
        if not activity_type or 'activity_ids' not in order._fields:
            return
        existing = order.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        if not waiting:
            existing.unlink()
            return
        note = _("%(count)s expense(s) wait for the manager or for a decision. The expense line "
                 "of this order does not move until they are settled.", count=len(waiting))
        if existing:
            existing.write({'note': note})
        else:
            order.activity_schedule(
                'expense_scan.mail_activity_type_waiting',
                summary=_("Expenses to settle before invoicing"), note=note,
                user_id=(order.user_id or self.env.user).id)

    @api.model
    def _expense_scan_hold_line(self, order, line, amount):
        """While expenses wait, the line goes down but never up.

        Nothing new reaches the invoice before every expense is settled; an
        expense taken back (unapproved, switched to "no") leaves it at once.
        A product invoiced on the ordered quantity would otherwise bill the
        figure typed in the quotation.
        """
        if not line or order.locked:
            return
        unit = line.price_unit if line.price_unit > 0 else 1.0
        held = max(min(line.qty_delivered, amount / unit), line.qty_invoiced)
        values = {}
        if line.qty_delivered_method == 'manual' and float_compare(line.qty_delivered, held, precision_digits=2):
            values['qty_delivered'] = held
        if line.product_id.invoice_policy == 'order' and float_compare(line.product_uom_qty, held, precision_digits=2):
            values['product_uom_qty'] = held
        if values:
            line.sudo().write(values)

    @api.model
    def _expense_scan_sync_order(self, order):
        company = order.company_id
        projects = self._expense_scan_projects_of(order)
        if not projects:
            return
        waiting = self._expense_scan_waiting(projects, company, order)
        self._expense_scan_flag_waiting(order, waiting)
        counted = self._expense_scan_counted(projects, company, order)
        # Refunds can leave a negative total: the line never goes below zero.
        amount = max(sum(counted.mapped('total_amount')), 0.0)
        if order.currency_id != company.currency_id:
            amount = company.currency_id._convert(
                amount, order.currency_id, company, order.date_order or fields.Date.context_today(self))
        amount = order.currency_id.round(amount)

        line = self._expense_scan_order_line(order)
        if waiting:
            self._expense_scan_hold_line(order, line, amount)
            return
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
                # The amount already includes tax: no tax on it a second time.
                'tax_ids': [Command.clear()],
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
    # Taking an expense back
    # ------------------------------------------------------------------

    def _do_reset_approval(self):
        """Back to draft: only an expense off any draft invoice.

        On a posted invoice, the expense leaves it with its accounting
        entries (the accountant reversed them); the invoice itself stays as
        it was.
        """
        linked = self.sudo().filtered('expense_scan_invoice_id')
        on_draft = linked.filtered(lambda e: e.expense_scan_invoice_id.state == 'draft')
        if on_draft:
            raise UserError(_(
                "%(expense)s is on the draft invoice %(invoice)s. Delete the draft first.",
                expense=on_draft[0].name or on_draft[0].display_name,
                invoice=on_draft[0].expense_scan_invoice_id.display_name))
        linked.with_context(expense_scan_free=True, expense_scan_no_sync=True).write(
            {'expense_scan_invoice_id': False})
        return super()._do_reset_approval()

    def action_expense_scan_unapprove(self):
        """Take back an approval made by mistake.

        The expense waits for the manager again: neither refused nor back to
        draft. Not possible once it is posted or on an invoice.
        """
        self._check_can_approve()
        for expense in self:
            if expense.state != 'approved':
                raise UserError(_("%s is not approved: only an approval can be taken back.",
                                  expense.name or expense.display_name))
        self._expense_scan_check_not_invoiced({'approval_state': 'submitted'})
        self.sudo().write({'approval_state': 'submitted', 'approval_date': False})
        self.sudo().update_activities_and_mails()
        return True

    @api.model
    def expense_scan_order_warning(self, order_id, button_name=None):
        """Text to confirm before invoicing an order, or False.

        Asked by the "Create Invoice" button of the sales order: expenses of
        its missions still wait for the manager or for a decision, and the
        expense line will not carry them.
        """
        if 'sale.order' not in self.env:
            return False
        action = self.env.ref('sale.action_view_sale_advance_payment_inv', raise_if_not_found=False)
        if not action or str(action.id) != str(button_name):
            return False
        order = self.env['sale.order'].browse(order_id).exists()
        if not order or not order.company_id.expense_scan_reinvoice:
            return False
        projects = self._expense_scan_projects_of(order)
        waiting = self._expense_scan_waiting(projects, order.company_id, order) if projects else False
        if not waiting:
            return False
        return _(
            "%(count)s expense(s) of this order's missions wait for the manager or for a "
            "decision. The expense line does not include them yet.",
            count=len(waiting))

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

    def action_expense_scan_set_reinvoice(self, mode=None):
        """Set the re-invoicing of the expenses from the list.

        Without ``mode``, the next answer in the loop "to decide, yes, no".
        "Yes" looks for the project of the receipt date, as the scan does;
        without a project found, the expense has to be opened. An approved
        expense stays decided, so the loop skips "to decide" for it.
        """
        if mode not in (None, 'project', 'none', 'todo'):
            raise UserError(_("Unknown choice."))
        for expense in self:
            expense._expense_scan_apply_reinvoice(mode or expense._expense_scan_next_reinvoice())
        return True

    def action_expense_scan_set_reinvoice_many(self, mode):
        """The same answer for a selection; those that cannot take it are left.

        Returns the names of the expenses left aside, with the reason, so
        that the list can say which ones to open.
        """
        if mode not in ('project', 'none', 'todo'):
            raise UserError(_("Unknown choice."))
        left = []
        for expense in self:
            try:
                with self.env.cr.savepoint():
                    expense._expense_scan_apply_reinvoice(mode)
            except (UserError, AccessError) as error:
                left.append(str(error.args[0] if error.args else error))
        return {'done': len(self) - len(left), 'left': left}

    def _expense_scan_apply_reinvoice(self, choice):
        self.ensure_one()
        if not self.is_editable and not self.env.su:
            raise AccessError(_("You cannot edit this expense."))
        if self.state in ('posted', 'in_payment', 'paid', 'refused') or self.expense_scan_invoice_id:
            raise UserError(_("The re-invoicing of %s can no longer change.", self.name))
        if choice == 'todo' and self.state == 'approved':
            raise UserError(_("%s is approved: its re-invoicing has to stay decided.", self.name))
        values = {'reinvoice_mode': choice}
        if choice == 'project':
            project = self.project_id or self._expense_scan_find_project(self.date)
            if not project:
                raise UserError(_(
                    "No project found for %s: open the expense and choose one.", self.name))
            values = self._expense_scan_project_values(project, reinvoice=True)
        # "To decide" keeps the project: the loop must be able to come back to "yes".
        self.write(values)

    # ------------------------------------------------------------------
    # Reimbursing the employee
    # ------------------------------------------------------------------

    def action_expense_scan_pay(self):
        """Reimburse the employee for a selection in one go.

        Approved expenses are posted first; then one payment per employee
        settles them together with the expenses already posted (those an
        invoice posted, for instance).
        """
        if not self.env.su and not self.env.user.has_group('account.group_account_invoice'):
            raise AccessError(_("Only accountants can reimburse the employees."))
        expenses = self.filtered(lambda e: e.payment_mode == 'own_account')
        if not expenses:
            raise UserError(_("Select expenses paid by the employee."))
        wrong = expenses.filtered(lambda e: e.state not in ('approved', 'posted'))
        if wrong:
            raise UserError(_(
                "Only approved or posted expenses can be paid:\n%s",
                "\n".join("- %s" % (e.name or e.display_name) for e in wrong[:20])))
        to_post = expenses.filtered(lambda e: e.state == 'approved')
        if to_post:
            to_post._post_without_wizard()
        moves = expenses.account_move_id.filtered(
            lambda m: m.state == 'posted' and m.payment_state in ('not_paid', 'partial'))
        if not moves:
            raise UserError(_("These expenses are already paid."))
        action = moves.action_register_payment()
        action['context'] = dict(action.get('context') or {}, default_group_payment=True)
        return action

    def _expense_scan_next_reinvoice(self):
        """To decide, then yes, then no, then to decide again."""
        self.ensure_one()
        order = {'todo': 'project', 'project': 'none', 'none': 'todo'}
        choice = order.get(self.reinvoice_mode, 'project')
        if choice == 'todo' and self.state == 'approved':
            choice = 'project'
        return choice
