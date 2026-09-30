// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * On a small screen, Odoo shows only one header button and puts the others
 * in a "..." menu. For expenses, four buttons stay visible, the secondary
 * ones reduced to their icon; beyond that, the menu is used as in Odoo.
 * Other models are not affected.
 */
import { patch } from "@web/core/utils/patch";
import { StatusBarButtons } from "@web/views/form/status_bar_buttons/status_bar_buttons";

patch(StatusBarButtons.prototype, {
    get expenseScanMaxSlots() {
        // This component is also used outside a form, where `env.model`
        // does not exist: Odoo's behaviour in that case.
        return this.env.model?.root?.resModel === "hr.expense" ? 4 : 1;
    },
});
