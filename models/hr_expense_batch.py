# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Batch actions on the expense list.

Odoo can submit and approve several expenses at once, but not reset them to
draft. This action applies Odoo's reset, with its checks, to each eligible
expense and reports the ones it skips instead of refusing everything because
of one.
"""
from odoo import _, models


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    def action_expense_scan_reset_batch(self):
        """Reset the selected expenses to draft when possible."""
        to_reset = self.filtered(lambda expense: expense.state != 'draft')
        # A posted journal entry must first be cancelled in Accounting: Odoo
        # refuses the reset otherwise.
        posted = to_reset.filtered(lambda expense: any(
            state not in (False, 'draft')
            for state in expense.sudo().account_move_id.mapped('state')))
        forbidden = (to_reset - posted).filtered(lambda expense: not expense.can_reset)
        allowed = to_reset - posted - forbidden
        if allowed:
            allowed.action_reset()

        lines = [_("%s expense(s) reset to draft.", len(allowed))]
        if posted:
            lines.append(_(
                "%(count)s skipped: their journal entry is posted "
                "(%(names)s). Cancel it in Accounting first.",
                count=len(posted), names=", ".join(posted.mapped('name'))))
        if forbidden:
            lines.append(_(
                "%(count)s skipped for lack of access rights (%(names)s).",
                count=len(forbidden), names=", ".join(forbidden.mapped('name'))))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Reset to draft"),
                'message': "\n".join(lines),
                'type': 'warning' if (posted or forbidden) else 'success',
                'sticky': bool(posted or forbidden),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }
