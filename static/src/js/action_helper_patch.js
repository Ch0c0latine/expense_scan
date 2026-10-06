// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * An empty expense list tells a new user how to add a receipt.
 *
 * Odoo's message ("Upload or drop an expense receipt") does not name the
 * button on a phone, where it is "Scan", nor the way to enter an expense by
 * hand. One line is added under it; the other lists are not changed.
 */
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ActionHelper } from "@web/views/action_helper";

patch(ActionHelper.prototype, {
    setup() {
        super.setup(...arguments);
        this.expenseScanUi = useService("ui");
    },

    get expenseScanHint() {
        if (!(this.props.noContentHelp || "").includes("o_view_nocontent_expense_receipt")) {
            return "";
        }
        return this.expenseScanUi.isSmall
            ? _t('Tap "Scan" to photograph a receipt, or "New" to enter an expense by hand.')
            : _t('Click "Upload" to choose a receipt, or "New" to enter an expense by hand.');
    },
});
