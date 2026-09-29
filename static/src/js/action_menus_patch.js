/** @odoo-module **/
// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * On the expense list, the "Expense Sheet" button replaces the "Print" menu
 * (Odoo's report, one page per expense). The action is removed from the
 * "Actions" menu so that it does not appear twice.
 */
import { ActionMenus } from "@web/search/action_menus/action_menus";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

patch(ActionMenus.prototype, {
    /** "Expense Sheet" action, if it is in this bar. */
    get expenseSheetAction() {
        const actionId = session.expense_scan_sheet_action_id;
        if (this.props.resModel !== "hr.expense" || !actionId) {
            return null;
        }
        return (this.props.items.action || []).find((action) => action.id === actionId) || null;
    },

    async getActionItems(props) {
        const items = await super.getActionItems(props);
        const actionId = session.expense_scan_sheet_action_id;
        if (props.resModel !== "hr.expense" || !actionId) {
            return items;
        }
        return items.filter((item) => item.action?.id !== actionId);
    },

    onExpenseSheet() {
        this.executeAction(this.expenseSheetAction);
    },
});
