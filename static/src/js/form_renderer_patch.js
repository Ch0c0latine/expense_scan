// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Odoo n'affiche le justificatif à côté du formulaire qu'à partir de la
 * taille XXL (1400 px), donc pas sur un écran de portable en 1366 px.
 *
 * Pour les dépenses seulement, et si le réglage de la société est actif, le
 * seuil passe à MD (768 px). Seule la largeur compte, pas le rapport
 * largeur/hauteur : une fenêtre en demi-écran ou une tablette verticale
 * affiche aussi les deux colonnes.
 *
 * Sous 768 px, le widget `expense_scan_receipt` affiche l'aperçu en bandeau
 * au-dessus des champs.
 */
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { SIZES } from "@web/core/ui/ui_service";
import { FormRenderer } from "@web/views/form/form_renderer";

patch(FormRenderer.prototype, {
    /** @override */
    mailLayout(hasAttachmentContainer) {
        if (
            session.expense_scan_wide_split &&
            this.props.record?.resModel === "hr.expense" &&
            hasAttachmentContainer &&
            this.mailStore &&
            !this.mailPopoutService.externalWindow &&
            this.uiService.size >= SIZES.MD &&
            this.uiService.size < SIZES.XXL &&
            this.hasFile()
        ) {
            // Discussion en bas, justificatif sur le côté.
            return "COMBO";
        }
        return super.mailLayout(...arguments);
    },
});
