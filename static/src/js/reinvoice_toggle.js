// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * "Re-invoicable" column of the expense lists.
 *
 * A box checked for "yes", empty for "no", a question mark for "to decide".
 * A click moves to the next answer (to decide, yes, no, to decide...) and
 * saves at once, so a manager settles a whole list without opening the
 * expenses.
 *
 * On a row of a selection, the click applies to every selected expense: the
 * next answer when they all have the same one, otherwise the answer chosen
 * in a dialog. Expenses that cannot take it (no project at their date,
 * already invoiced...) are left as they are and listed.
 */
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const FINAL_STATES = ["posted", "in_payment", "paid", "refused"];
const NEXT = { todo: "project", project: "none", none: "todo" };

export class ExpenseScanReinvoiceChoice extends Component {
    static template = "expense_scan.ReinvoiceChoice";
    static components = { Dialog };
    static props = { count: Number, choose: Function, close: Function };

    pick(mode) {
        this.props.choose(mode);
        this.props.close();
    }
}

export class ExpenseScanReinvoice extends Component {
    static template = "expense_scan.ReinvoiceToggle";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
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
        return _t("To decide: click to change");
    }

    /** Selected rows, when the clicked row is one of them. */
    get selection() {
        const record = this.props.record;
        const selection = record.model?.root?.selection || [];
        return record.selected && selection.length > 1 ? selection : [];
    }

    async onClick() {
        const selection = this.selection;
        if (selection.length) {
            return this.applyToSelection(selection);
        }
        if (this.locked) {
            return;
        }
        // The server knows the next answer: to decide, yes, no, to decide...
        await this.orm.call("hr.expense", "action_expense_scan_set_reinvoice", [
            [this.props.record.resId],
        ]);
        await this.props.record.load();
    }

    async applyToSelection(selection) {
        const modes = new Set(selection.map((record) => record.data[this.props.name]));
        const mode =
            modes.size === 1 ? NEXT[this.mode] || "project" : await this.askMode(selection.length);
        if (!mode) {
            return;
        }
        const result = await this.orm.call("hr.expense", "action_expense_scan_set_reinvoice_many", [
            selection.map((record) => record.resId),
            mode,
        ]);
        await this.props.record.model.root.load();
        if (result.left.length) {
            this.notification.add(result.left.join("\n"), {
                title: _t("%(count)s expense(s) left as they were", { count: result.left.length }),
                type: "warning",
                sticky: true,
            });
        }
    }

    askMode(count) {
        return new Promise((resolve) => {
            this.dialog.add(
                ExpenseScanReinvoiceChoice,
                { count, choose: resolve },
                { onClose: () => resolve(false) }
            );
        });
    }
}

registry.category("fields").add("expense_scan_reinvoice", {
    component: ExpenseScanReinvoice,
    displayName: _t("Re-invoice toggle"),
    supportedTypes: ["selection"],
});
