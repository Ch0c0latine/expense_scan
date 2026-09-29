// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Aperçu du ticket sur écran étroit.
 *
 * Sur petit écran, Odoo n'affiche pas le volet du justificatif. Ce widget
 * l'affiche en bandeau fixé en haut de la fiche, visible pendant le
 * défilement des champs ; un appui l'ouvre en plein écran.
 *
 * Un navigateur de téléphone n'affiche pas un PDF dans une page : le
 * bandeau montre sa première page, rendue par le serveur.
 */
import { Component, onWillDestroy, useEffect, useState } from "@odoo/owl";

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
        this.dialog = useService("dialog");
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

    /** Rendu de la première page du PDF ; une réponse périmée est ignorée. */
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
     * Largeur à partir de laquelle le volet latéral est affiché : 768 px si
     * le réglage de la société est actif, sinon 1400 px (seuil d'Odoo).
     */
    get splitThreshold() {
        return session.expense_scan_wide_split ? SIZES.MD : SIZES.XXL;
    }

    /** Affiché seulement quand le volet d'Odoo ne l'est pas. */
    get visible() {
        return Boolean(this.attachment) && this.state.size < this.splitThreshold;
    }

    /** Pièce jointe principale (tuple ou objet selon le modèle relationnel). */
    get attachment() {
        const value = this.props.record.data.message_main_attachment_id;
        if (!value) {
            return null;
        }
        if (Array.isArray(value)) {
            return { id: value[0], name: value[1] };
        }
        return { id: value.id, name: value.display_name || value.name || "Ticket" };
    }

    get mimetype() {
        return this.props.record.data.expense_scan_main_mimetype || "image/jpeg";
    }

    get isPdf() {
        return this.mimetype.startsWith("application/pdf");
    }

    /** Empreinte du fichier : l'adresse change quand l'image est retouchée. */
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
        return this.state.expanded ? "Réduire l'aperçu" : "Agrandir l'aperçu";
    }

    onToggle() {
        this.state.expanded = !this.state.expanded;
    }

    /** Bouton « Retouche » : sur une dépense modifiable, avec un justificatif. */
    get canRetouch() {
        return Boolean(this.props.record.data.is_editable)
            && hasReceipt(this.props.record);
    }

    onRetouch() {
        openRetouchDialog({ dialog: this.dialog, orm: this.orm }, this.props.record);
    }

    /** Ouvre la visionneuse d'Odoo (zoom, rotation, plein écran). */
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
