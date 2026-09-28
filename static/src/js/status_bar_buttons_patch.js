// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Sur petit écran, Odoo n'affiche qu'un bouton d'en-tête et place les
 * autres dans un menu « … ». Pour les dépenses, quatre boutons restent
 * affichés, les secondaires réduits à leur icône ; au-delà, le menu est
 * utilisé comme dans Odoo. Les autres modèles ne sont pas concernés.
 */
import { patch } from "@web/core/utils/patch";
import { StatusBarButtons } from "@web/views/form/status_bar_buttons/status_bar_buttons";

patch(StatusBarButtons.prototype, {
    get expenseScanMaxSlots() {
        // Ce composant sert aussi hors d'un formulaire, où `env.model`
        // n'existe pas : comportement d'Odoo dans ce cas.
        return this.env.model?.root?.resModel === "hr.expense" ? 4 : 1;
    },
});
