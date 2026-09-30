# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """Give the split view setting to the web client without an extra request.

        The JavaScript patch that lowers the receipt display threshold runs
        on every form render.
        """
        result = super().session_info()
        result['expense_scan_wide_split'] = self.env.company.expense_scan_wide_split
        # The expense list replaces "Print" with "Expense sheet": the client
        # recognises the action by its id.
        sheet = self.env.ref('expense_scan.expense_scan_sheet_wizard_action',
                             raise_if_not_found=False)
        result['expense_scan_sheet_action_id'] = sheet.id if sheet else False
        return result
