/** @odoo-module **/
// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Download the expense sheet produced, then close the export dialog.
 *
 * A plain link left the dialog open; a new tab would be stopped by popup
 * blockers, since the opening is no longer tied to the click once the file
 * is produced.
 */
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";

async function expenseScanDownload(env, action) {
    await download({ url: action.params.url, data: {} });
    return { type: "ir.actions.act_window_close" };
}

registry.category("actions").add("expense_scan_download", expenseScanDownload);
