/** @odoo-module **/
// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Sur la liste des dépenses, le bouton « Fiche de frais » remplace le menu
 * « Imprimer » (rapport d'Odoo, une page par dépense). L'action est retirée
 * du menu « Actions » pour ne pas y figurer deux fois.
 */
import { ActionMenus } from "@web/search/action_menus/action_menus";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

patch(ActionMenus.prototype, {
    /** Action « Fiche de frais », si elle figure dans cette barre. */
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
