// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * "Current month" and "Previous month" beside the search bar of the expense
 * lists, instead of two entries buried in the "Expense Date" menu.
 *
 * They toggle the hidden filters of the search view (``current_month`` and
 * ``previous_month``); one excludes the other.
 */
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { ControlPanel } from "@web/search/control_panel/control_panel";

const MONTHS = [
    ["current_month", _t("Current month")],
    ["previous_month", _t("Previous month")],
];

patch(ControlPanel.prototype, {
    get expenseMonthButtons() {
        const model = this.env.searchModel;
        if (!model || model.resModel !== "hr.expense") {
            return [];
        }
        const items = Object.values(model.searchItems);
        const buttons = [];
        for (const [name, label] of MONTHS) {
            const item = items.find((i) => i.type === "filter" && i.name === name);
            if (item) {
                const active = model.query.some((q) => q.searchItemId === item.id);
                buttons.push({ id: item.id, label, active });
            }
        }
        return buttons;
    },

    toggleExpenseMonth(button) {
        const model = this.env.searchModel;
        for (const other of this.expenseMonthButtons) {
            if (other.active && other.id !== button.id) {
                model.toggleSearchItem(other.id);
            }
        }
        model.toggleSearchItem(button.id);
        this.render();
    },
});
