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

        ``expense_scan_keep_original``: the scan replaces the cropped image
        with a new one taken from the same original.
        """
        expenses = self.env['hr.expense']
        if self.ids and not self.env.context.get('expense_scan_keep_original'):
            expenses = expenses.sudo().search(
                [('scan_cropped_attachment_id', 'in', self.ids)])
        originals = expenses.scan_original_attachment_id - self
        result = super().unlink()
        if expenses:
            expenses.exists().write({
                'scan_original_attachment_id': False,
                'expense_scan_manual_retouch': False,
                'expense_scan_retouch_params': False,
            })
            originals.exists().unlink()
        return result
