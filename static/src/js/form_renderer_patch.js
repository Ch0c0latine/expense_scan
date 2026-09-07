/**
 * Odoo n'affiche le justificatif à côté du formulaire qu'à partir de la
 * taille XXL (1400 px). Un ordinateur portable classique en 1366 px n'y a
 * donc jamais droit, alors que c'est précisément l'écran sur lequel on
 * relit une note de frais.
 *
 * On abaisse ce seuil à LG (992 px) pour les seules dépenses, sans toucher
 * au comportement des autres modèles.
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
            this.uiService.size >= SIZES.LG &&
            this.uiService.size < SIZES.XXL &&
            this.hasFile()
        ) {
            // Chatter en bas, justificatif sur le côté.
            return "COMBO";
        }
        return super.mailLayout(...arguments);
    },
});
