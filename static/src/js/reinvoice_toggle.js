// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * "Re-invoicable" column of the expense lists.
 *
 * A box checked for "yes", empty for "no", a question mark for "to decide".
 * A click switches between yes and no (a question mark becomes yes) and saves
 * at once, so a manager settles a whole list without opening the expenses.
 */
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const FINAL_STATES = ["posted", "in_payment", "paid", "refused"];

export class ExpenseScanReinvoice extends Component {
    static template = "expense_scan.ReinvoiceToggle";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
    }

    get mode() {
        return this.props.record.data[this.props.name];
    }

    /** A posted, refused or already invoiced expense no longer changes. */
    get locked() {
        const data = this.props.record.data;
        return (
            !data.is_editable ||
            FINAL_STATES.includes(data.state) ||
            Boolean(data.expense_scan_invoice_id)
        );
    }

    get title() {
        if (this.mode === "project") {
            return _t("Re-invoiced to the customer");
        }
        if (this.mode === "none") {
            return _t("Not re-invoiced");
        }
        return _t("To decide: click to re-invoice");
    }

    async onClick() {
        if (this.locked) {
            return;
        }
        const next = this.mode === "project" ? "none" : "project";
        await this.orm.call("hr.expense", "action_expense_scan_set_reinvoice", [
            [this.props.record.resId],
            next,
        ]);
        await this.props.record.load();
    }
}

registry.category("fields").add("expense_scan_reinvoice", {
    component: ExpenseScanReinvoice,
    displayName: _t("Re-invoice toggle"),
    supportedTypes: ["selection"],
});
