// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Receipt preview on a narrow screen.
 *
 * On a small screen, Odoo does not show the receipt panel. This widget shows
 * it as a strip pinned at the top of the form, visible while the fields
 * scroll; a tap opens it full screen.
 *
 * A phone browser does not display a PDF inside a page: the strip shows its
 * first page, rendered by the server.
 */
import { Component, onWillDestroy, useEffect, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

import { browser } from "@web/core/browser/browser";
import { FileModel } from "@web/core/file_viewer/file_model";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { SIZES } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

import { hasReceipt, openRetouchDialog } from "@expense_scan/js/retouch_dialog";

export class ExpenseScanReceipt extends Component {
    static template = "expense_scan.ReceiptPreview";
    static props = { ...standardWidgetProps };

    setup() {
        this.ui = useService("ui");
        this.orm = useService("orm");
        this.fileViewer = useFileViewer();
        this.state = useState({ size: this.ui.size, expanded: false, pdfUrl: null });

        this.onResize = useDebounced(() => {
            this.state.size = this.ui.size;
        }, 200);
        browser.addEventListener("resize", this.onResize);
        onWillDestroy(() => browser.removeEventListener("resize", this.onResize));

        useEffect(
            (visible, isPdf) => {
                this.state.pdfUrl = null;
                if (visible && isPdf) {
                    this.loadPdfPreview();
                }
            },
            () => [this.visible, this.isPdf, this.attachment?.id, this.checksum]
        );
    }

    /** Rendering of the first PDF page; a stale answer is ignored. */
    async loadPdfPreview() {
        const key = `${this.attachment.id}-${this.checksum}`;
        this.pdfKey = key;
        const url = await this.orm.call(
            "hr.expense", "expense_scan_pdf_preview", [[this.props.record.resId]]);
        if (this.pdfKey === key) {
            this.state.pdfUrl = url;
        }
    }

    /**
     * Width from which the side panel is shown: 768 px if the company
     * setting is on, otherwise 1400 px (Odoo's threshold).
     */
    get splitThreshold() {
        return session.expense_scan_wide_split ? SIZES.MD : SIZES.XXL;
    }

    /** Shown only when Odoo's panel is not. */
    get visible() {
        return Boolean(this.attachment) && this.state.size < this.splitThreshold;
    }

    /** Main attachment (tuple or object depending on the relational model). */
    get attachment() {
        const value = this.props.record.data.message_main_attachment_id;
        if (!value) {
            return null;
        }
        if (Array.isArray(value)) {
            return { id: value[0], name: value[1] };
        }
        return { id: value.id, name: value.display_name || value.name || "Receipt" };
    }

    get mimetype() {
        return this.props.record.data.expense_scan_main_mimetype || "image/jpeg";
    }

    get isPdf() {
        return this.mimetype.startsWith("application/pdf");
    }

    /** File checksum: the address changes when the image is retouched. */
    get checksum() {
        return this.props.record.data.expense_scan_main_checksum || "";
    }

    get imageUrl() {
        if (this.isPdf) {
            return this.state.pdfUrl;
        }
        return `/web/image/${this.attachment.id}?unique=${this.checksum}`;
    }

    get toggleLabel() {
        return this.state.expanded ? _t("Collapse the preview") : _t("Expand the preview");
    }

    onToggle() {
        this.state.expanded = !this.state.expanded;
    }

    /** "Retouch" button: on an editable expense with a receipt. */
    get canRetouch() {
        return Boolean(this.props.record.data.is_editable)
            && hasReceipt(this.props.record);
    }

    onRetouch() {
        openRetouchDialog(this.env, this.props.record);
    }

    /** Open Odoo's viewer (zoom, rotation, full screen). */
    onOpenViewer() {
        const file = new FileModel();
        Object.assign(file, {
            id: this.attachment.id,
            name: this.attachment.name,
            mimetype: this.mimetype,
            checksum: this.checksum,
            type: "binary",
        });
        this.fileViewer.open(file);
    }
}

export const expenseScanReceiptWidget = {
    component: ExpenseScanReceipt,
    fieldDependencies: [
        { name: "is_editable", type: "boolean", readonly: true },
        {
            name: "message_main_attachment_id",
            type: "many2one",
            relation: "ir.attachment",
            readonly: true,
        },
        { name: "expense_scan_main_mimetype", type: "char", readonly: true },
        { name: "expense_scan_main_checksum", type: "char", readonly: true },
    ],
};

registry.category("view_widgets").add("expense_scan_receipt", expenseScanReceiptWidget);
