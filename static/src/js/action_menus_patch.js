/** @odoo-module **/
// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Sur les dépenses, « Fiche de frais » prend la place du menu « Imprimer ».
 *
 * La fiche récapitule, numérote et rassemble les justificatifs : c'est
 * l'impression dont on a besoin, et le rapport d'origine d'Odoo, dépense
 * par dépense, n'apporte rien de plus. L'action quitte aussi le menu
 * « Actions », pour ne pas y figurer en double.
 */
import { ActionMenus } from "@web/search/action_menus/action_menus";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

patch(ActionMenus.prototype, {
    /** L'action « Fiche de frais », si cette barre la propose. */
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
