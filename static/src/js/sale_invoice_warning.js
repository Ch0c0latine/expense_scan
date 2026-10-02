// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Warning before invoicing a sales order.
 *
 * The expense line of an order does not move while expenses of its missions
 * wait for the manager or for a decision. When the "Create Invoice" button is
 * pressed, the server says whether some do; the person then confirms or
 * cancels.
 *
 * Without Sales, no form of that model exists and this patch does nothing.
 */
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

patch(FormController.prototype, {
    /** @override */
    async beforeExecuteActionButton(clickParams) {
        const proceed = await super.beforeExecuteActionButton(...arguments);
        if (
            proceed === false ||
            this.props.resModel !== "sale.order" ||
            clickParams.type !== "action" ||
            !this.model.root.resId
        ) {
            return proceed;
        }
        const warning = await this.orm.call("hr.expense", "expense_scan_order_warning", [
            this.model.root.resId,
            String(clickParams.name),
        ]);
        if (!warning) {
            return proceed;
        }
        return new Promise((resolve) => {
            this.dialogService.add(ConfirmationDialog, {
                title: _t("Expenses to settle"),
                body: warning,
                confirmLabel: _t("Create the invoice anyway"),
                confirm: () => resolve(proceed),
                cancelLabel: _t("Cancel"),
                cancel: () => resolve(false),
                dismiss: () => resolve(false),
            });
        });
    },
});
