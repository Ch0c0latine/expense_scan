// Copyright 2026 T.T.C. SAS
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Choice of the receipt source: camera, gallery or files.
 *
 * On a phone, a single file input does not offer these three choices
 * everywhere: the Odoo app opens the gallery without the camera, and the
 * "image" filter hides PDFs. The input is set up for the chosen source, then
 * opened in the same gesture: the browser refuses to open a picker outside a
 * user action.
 */
import { _t } from "@web/core/l10n/translation";
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** File input settings for each source. */
export const RECEIPT_SOURCES = {
    // `capture` opens the rear camera directly.
    camera: { accept: "image/*", capture: "environment" },
    gallery: { accept: "image/*" },
    // With a non-image type, Android opens its file picker, which gives
    // access to downloaded PDFs.
    files: { accept: "image/*,application/pdf" },
};

/**
 * Set up the file input for the chosen source.
 *
 * @param {HTMLInputElement} input
 * @param {keyof RECEIPT_SOURCES} source
 */
export function configureReceiptInput(input, source) {
    const settings = RECEIPT_SOURCES[source];
    input.accept = settings.accept;
    if (settings.capture) {
        input.setAttribute("capture", settings.capture);
    } else {
        input.removeAttribute("capture");
    }
}

export class ReceiptSourceDialog extends Component {
    static template = "expense_scan.ReceiptSourceDialog";
    static components = { Dialog };
    static props = {
        choose: Function,
        close: Function,
    };

    get title() {
        return _t("Add a receipt");
    }

    /** Open the picker before closing the dialog, during the user action. */
    pick(source) {
        this.props.choose(source);
        this.props.close();
    }
}
