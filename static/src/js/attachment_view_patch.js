// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Barre d'outils du PDF allégée sur le volet natif d'une dépense.
 *
 * PDF.js embarque une barre pensée pour un lecteur de documents : imprimer,
 * enregistrer, mode présentation, aller à la première ou la dernière page,
 * outil main, défilement par page/horizontal/par bloc, pages doubles, et
 * un volet de vignettes qui n'a aucun sens sur un justificatif d'une seule
 * page. Rien de tout cela n'aide à vérifier qu'un ticket correspond à la
 * dépense saisie à côté — seuls le zoom, la page et la rotation restent.
 *
 * Odoo masque déjà le téléchargement (sur mobile) via `hidePDFJSButtons` ;
 * on va plus loin ici, et seulement pour les dépenses, sans toucher à
 * l'aperçu des autres modèles (devis, factures…), qui gardent leurs outils.
 */
import { AttachmentView } from "@mail/core/common/attachment_view";
import { patch } from "@web/core/utils/patch";
import { useEffect } from "@odoo/owl";

//: Un sélecteur par bouton à cacher. La rotation (`pageRotateCw/Ccw`) n'y
//: figure pas : c'est le seul outil qu'on garde.
const HIDDEN_PDF_TOOLS = [
    "button#printButton", "button#secondaryPrint",
    "button#downloadButton", "button#secondaryDownload",
    "button#presentationMode",
    "#firstPage", "#secondaryFirstPage", "#lastPage", "#secondaryLastPage",
    // L'outil de sélection n'a de sens qu'en alternative à l'outil main.
    "#cursorHandTool", "#cursorSelectTool",
    // Identifiants de la version de PDF.js livrée avec Odoo 19.
    "#scrollPage", "#scrollVertical", "#scrollHorizontal", "#scrollWrapped",
    "#spreadNone", "#spreadOdd", "#spreadEven",
    "#secondaryToolbar .horizontalToolbarSeparator",
    // Barre principale : le volet de vignettes qu'il ouvre.
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
});
