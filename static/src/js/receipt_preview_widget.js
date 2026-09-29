// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Aperçu du ticket sur écran étroit.
 *
 * Sur petit écran, Odoo n'affiche pas le volet du justificatif. Ce widget
 * l'affiche en bandeau fixé en haut de la fiche, visible pendant le
 * défilement des champs ; un appui l'ouvre en plein écran.
 */
import { Component, onWillDestroy, useState } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { FileModel } from "@web/core/file_viewer/file_model";
import { useFileViewer } from "@web/core/file_viewer/file_viewer_hook";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { SIZES } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

import { openRetouchDialog, retouchSourceId } from "@expense_scan/js/retouch_dialog";

export class ExpenseScanReceipt extends Component {
    static template = "expense_scan.ReceiptPreview";
    static props = { ...standardWidgetProps };

    setup() {
        this.ui = useService("ui");
        this.dialog = useService("dialog");
        this.orm = useService("orm");
        this.fileViewer = useFileViewer();
        this.state = useState({ size: this.ui.size, expanded: false });

        this.onResize = useDebounced(() => {
            this.state.size = this.ui.size;
        }, 200);
        browser.addEventListener("resize", this.onResize);
        onWillDestroy(() => browser.removeEventListener("resize", this.onResize));
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

    get imageUrl() {
        return `/web/image/${this.attachment.id}`;
    }

    get toggleLabel() {
        return this.state.expanded ? "Réduire l'aperçu" : "Agrandir l'aperçu";
    }

    onToggle() {
        this.state.expanded = !this.state.expanded;
    }

    /** Bouton « Retouche » : image, sur une dépense modifiable. */
    get canRetouch() {
        return Boolean(this.props.record.data.is_editable)
            && Boolean(retouchSourceId(this.props.record));
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
            mimetype: "image/jpeg",
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
            name: "scan_original_attachment_id",
            type: "many2one",
            relation: "ir.attachment",
            readonly: true,
        },
        {
            name: "message_main_attachment_id",
            type: "many2one",
            relation: "ir.attachment",
            readonly: true,
        },
    ],
};

registry.category("view_widgets").add("expense_scan_receipt", expenseScanReceiptWidget);
