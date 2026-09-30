// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Preview panel of an expense: reduced PDF toolbar and "Retouch" button.
 *
 * Of the PDF.js toolbar, only zoom, page and rotation remain: printing,
 * download, presentation mode, navigation, scroll modes and thumbnails are
 * hidden. Odoo only hides the download on mobile (`hidePDFJSButtons`). Other
 * models keep the full toolbar.
 */
import { AttachmentView } from "@mail/core/common/attachment_view";
import { patch } from "@web/core/utils/patch";
import { useEffect } from "@odoo/owl";

import { hasReceipt, openRetouchDialog } from "@expense_scan/js/retouch_dialog";

//: Hidden buttons. Rotation (`pageRotateCw/Ccw`) stays visible.
const HIDDEN_PDF_TOOLS = [
    "button#printButton", "button#secondaryPrint",
    "button#downloadButton", "button#secondaryDownload",
    "button#presentationMode",
    "#firstPage", "#secondaryFirstPage", "#lastPage", "#secondaryLastPage",
    "#cursorHandTool", "#cursorSelectTool",
    // Ids of the PDF.js version shipped with Odoo 19.
    "#scrollPage", "#scrollVertical", "#scrollHorizontal", "#scrollWrapped",
    "#spreadNone", "#spreadOdd", "#spreadEven",
    "#secondaryToolbar .horizontalToolbarSeparator",
    // Main toolbar: thumbnail panel button.
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
        // PDF.js keeps its viewer 350 px wide at least: in the narrow panel
        // of a tablet in portrait, the page overflowed and scrolled sideways.
        // The reduced toolbar fits in less.
        style.textContent = `${HIDDEN_PDF_TOOLS.join(", ")} { display: none !important; }
            #mainContainer { min-width: 0 !important; }`;
        iframe.contentDocument.head.appendChild(style);
    });
}

// PopoutAttachmentView (detached window) keeps Odoo's tools:
// AbstractAttachmentView, which both inherit from, is not exported by the
// core, and the detached window is a marginal use for an expense.
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
     * Record of the form showing this panel, if it is the same expense.
     * Needed to reload the form after the retouch.
     */
    get expenseScanRecord() {
        const root = this.env.model?.root;
        if (this.props.threadModel !== "hr.expense" || root?.resId !== this.props.threadId) {
            return null;
        }
        return root;
    },

    /** "Retouch" button: on an editable expense with a receipt. */
    get expenseScanCanRetouch() {
        const record = this.expenseScanRecord;
        return Boolean(record?.data.is_editable && hasReceipt(record));
    },

    onExpenseScanRetouch() {
        openRetouchDialog(this.env, this.expenseScanRecord);
    },
});
