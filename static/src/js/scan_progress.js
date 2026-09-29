// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Scan progress, shown on the form opened after the upload.
 *
 * The form opens before anything is read. The widget starts the scan
 * (action_expense_scan_start), follows the steps announced by the server and
 * fills the fields as it goes: date, amount and merchant as soon as they are
 * read, the rest at the end. Fields changed by the user during the scan are
 * not replaced, neither on screen nor in the database.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { getFieldsSpec } from "@web/model/relational_model/utils";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/** Steps shown, and the server steps that end them. */
const STEPS = [
    { key: "prepare", label: _t("Preparing the image"), ends: ["prepare"] },
    { key: "read", label: _t("Reading the text"), ends: ["read"] },
    {
        key: "straighten",
        label: _t("Straightening and cropping"),
        ends: ["reread", "straighten", "crop"],
    },
    { key: "values", label: _t("Date, amount and tax"), ends: ["parse", "values"] },
    { key: "category", label: _t("Category and project"), ends: ["category"] },
    { key: "save", label: _t("Saving"), ends: ["write"] },
];

export class ScanProgress extends Component {
    static template = "expense_scan.ScanProgress";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.bus = useService("bus_service");
        this.notification = useService("notification");
        this.state = useState({ active: false, done: 0 });
        this.onProgress = this.onProgress.bind(this);

        onMounted(() => {
            if (this.props.record.data.scan_state === "running") {
                this.start();
            }
        });
        onWillUnmount(() => this.bus.unsubscribe("expense_scan/progress", this.onProgress));
    }

    get steps() {
        return STEPS.map((step, index) => ({
            ...step,
            done: index < this.state.done,
            current: index === this.state.done,
        }));
    }

    async start() {
        const record = this.props.record;
        this.state.active = true;
        this.bus.subscribe("expense_scan/progress", this.onProgress);
        try {
            const specification = getFieldsSpec(
                record.activeFields, record.fields, record.evalContext);
            const result = await this.orm.call(
                "hr.expense", "action_expense_scan_start", [[record.resId]], { specification });
            if (result.busy) {
                // Scan already started elsewhere (another tab): delayed
                // reload to get the results.
                setTimeout(() => record.model.load(), 3000);
                return;
            }
            if (await record.isDirty()) {
                // Unsaved input: only the untouched fields are updated.
                this.apply(result.values);
            } else {
                // No input: full reload, which also updates the title and the
                // cropped receipt in the panel.
                await record.model.load();
            }
        } catch (error) {
            this.notification.add(_t("The receipt scan failed: start it again from the form."), {
                type: "danger",
            });
            throw error;
        } finally {
            this.state.active = false;
            this.bus.unsubscribe("expense_scan/progress", this.onProgress);
        }
    }

    onProgress(payload) {
        if (payload.expense_id !== this.props.record.resId) {
            return;
        }
        const index = STEPS.findIndex((step) => step.ends.includes(payload.step));
        if (index >= 0) {
            this.state.done = Math.max(this.state.done, index + 1);
        }
        if (payload.values && Object.keys(payload.values).length) {
            this.apply(payload.values);
        }
    }

    /**
     * Apply the server values like a reload: Odoo puts them under the user's
     * pending changes, which stay displayed and are the only ones saved.
     */
    apply(values) {
        const record = this.props.record;
        const known = Object.fromEntries(
            Object.entries(values || {}).filter(([name]) => name in record.activeFields));
        if (Object.keys(known).length) {
            record._applyValues(known);
        }
    }
}

registry.category("view_widgets").add("expense_scan_progress", {
    component: ScanProgress,
});
