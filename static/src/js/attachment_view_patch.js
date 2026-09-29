// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Volet d'aperçu d'une dépense : barre d'outils PDF réduite et bouton
 * « Retouche ».
 *
 * De la barre de PDF.js, seuls restent le zoom, la page et la rotation :
 * impression, téléchargement, mode présentation, navigation, modes de
 * défilement et vignettes sont masqués. Odoo ne masque que le
 * téléchargement sur mobile (`hidePDFJSButtons`). Les autres modèles
 * gardent la barre complète.
 */
import { AttachmentView } from "@mail/core/common/attachment_view";
import { patch } from "@web/core/utils/patch";
import { useEffect } from "@odoo/owl";

import { hasReceipt, openRetouchDialog } from "@expense_scan/js/retouch_dialog";

//: Boutons masqués. La rotation (`pageRotateCw/Ccw`) reste affichée.
const HIDDEN_PDF_TOOLS = [
    "button#printButton", "button#secondaryPrint",
    "button#downloadButton", "button#secondaryDownload",
    "button#presentationMode",
    "#firstPage", "#secondaryFirstPage", "#lastPage", "#secondaryLastPage",
    "#cursorHandTool", "#cursorSelectTool",
    // Identifiants de la version de PDF.js livrée avec Odoo 19.
    "#scrollPage", "#scrollVertical", "#scrollHorizontal", "#scrollWrapped",
    "#spreadNone", "#spreadOdd", "#spreadEven",
    "#secondaryToolbar .horizontalToolbarSeparator",
    // Barre principale : bouton du volet de vignettes.
    "#sidebarToggleButton", "#sidebarToggle",
];

function hideExpenseScanPdfTools(rootElement) {
    const iframe = rootElement.tagName === "IFRAME"
        ? rootElement : rootElement.querySelector("iframe");
    if (!iframe || iframe.dataset.expenseScanHideTools) {
        return;
    }
    iframe.dataset.expenseScanHideTools = "true";
    iframe.addEventListener("load", () => {
        if (!iframe.contentDocument?.head) {
            return;
        }
        const style = document.createElement("style");
        style.textContent = `${HIDDEN_PDF_TOOLS.join(", ")} { display: none !important; }`;
        iframe.contentDocument.head.appendChild(style);
    });
}

// PopoutAttachmentView (fenêtre détachée) reste sur les outils d'Odoo :
// AbstractAttachmentView, dont les deux héritent, n'est pas exportée par
// le cœur, et la fenêtre détachée est un usage marginal pour une dépense.
patch(AttachmentView.prototype, {
    setup() {
        super.setup();
        if (this.props.threadModel !== "hr.expense") {
            return;
        }
        useEffect(
            (el) => el && hideExpenseScanPdfTools(el),
            () => [this.iframeViewerPdfRef.el]
        );
    },

    /**
     * Enregistrement du formulaire qui affiche ce volet, s'il s'agit de la
     * même dépense. Nécessaire pour recharger la fiche après la retouche.
     */
    get expenseScanRecord() {
        const root = this.env.model?.root;
        if (this.props.threadModel !== "hr.expense" || root?.resId !== this.props.threadId) {
            return null;
        }
        return root;
    },

    /** Bouton « Retouche » : sur une dépense modifiable, avec un justificatif. */
    get expenseScanCanRetouch() {
        const record = this.expenseScanRecord;
        return Boolean(record?.data.is_editable && hasReceipt(record));
    },

    onExpenseScanRetouch() {
        openRetouchDialog(this.env, this.expenseScanRecord);
    },
});
