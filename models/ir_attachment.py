# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Deleting the displayed receipt of a scanned expense."""
from odoo import models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def unlink(self):
        """Also delete the original photo of a deleted receipt.

        After a scan, the displayed receipt is the cropped or retouched
        image; the original photo is a field attachment the user does not
        see. When the user deletes the receipt (the wrong ticket, say), the
        original must go with it: the retouch tool would otherwise reopen it
        instead of the receipt attached next. The retouch settings, which
        belong to that image, are cleared.

        When no receipt is left, what the scan read no longer describes
        anything: see ``_expense_scan_receipt_removed``.

        ``expense_scan_keep_original``: the scan replaces the cropped image
        with a new one taken from the same original.
        """
        Expense = self.env['hr.expense'].sudo()
        expenses = displayed = Expense
        if self.ids and not self.env.context.get('expense_scan_keep_original'):
            expenses = Expense.search([('scan_cropped_attachment_id', 'in', self.ids)])
            displayed = Expense.search([
                ('scan_state', '!=', 'none'),
                '|', ('message_main_attachment_id', 'in', self.ids),
                ('scan_cropped_attachment_id', 'in', self.ids)])
        originals = expenses.scan_original_attachment_id - self
        # Fields kept by hand, measured before the receipt they were
        # compared with disappears.
        kept = {expense.id: expense._expense_scan_kept_fields() for expense in displayed}
        result = super().unlink()
        if expenses:
            expenses.exists().write({
                'scan_original_attachment_id': False,
                'expense_scan_manual_retouch': False,
                'expense_scan_retouch_params': False,
            })
            originals.exists().unlink()
        displayed = displayed.exists()
        displayed.invalidate_recordset(['attachment_ids', 'message_main_attachment_id'])
        for expense in displayed:
            if not expense._expense_scan_image_attachments():
                expense._expense_scan_receipt_removed(kept[expense.id])
        return result
