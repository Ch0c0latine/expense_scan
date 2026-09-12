/**
 * Odoo n'affiche le justificatif à côté du formulaire qu'à partir de la
 * taille XXL (1400 px). Un ordinateur portable classique en 1366 px n'y a
 * donc jamais droit, alors que c'est précisément l'écran sur lequel on
 * relit une note de frais.
 *
 * On abaisse ce seuil à MD (768 px) pour les seules dépenses, sans toucher
 * au comportement des autres modèles. Le seuil porte sur la largeur seule,
 * jamais sur le rapport largeur/hauteur : une fenêtre étroite et haute —
 * un navigateur en demi-écran, une tablette à la verticale — reste bien
 * assez large pour deux colonnes, et c'est une disposition courante pour
 * relire un ticket.
 *
 * En deçà de 768 px, c'est le widget `expense_scan_receipt` qui prend le
 * relais et pose l'aperçu en bandeau au-dessus des champs : sur un
 * téléphone, deux colonnes ne seraient lisibles ni l'une ni l'autre.
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
            // Chatter en bas, justificatif sur le côté.
            return "COMBO";
        }
        return super.mailLayout(...arguments);
    },
});
