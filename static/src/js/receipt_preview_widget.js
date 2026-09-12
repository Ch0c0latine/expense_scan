/**
 * Aperçu du ticket sur écran étroit.
 *
 * Sur grand écran, Odoo affiche le justificatif dans un volet à gauche du
 * formulaire ; sur téléphone ce volet n'existe pas, et la vérification se
 * ferait alors à l'aveugle. Ce widget rétablit l'aperçu sous la forme d'un
 * bandeau collé en haut de la fiche : le ticket reste visible pendant que
 * l'on fait défiler les champs, et un appui l'ouvre en plein écran.
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

export class ExpenseScanReceipt extends Component {
    static template = "expense_scan.ReceiptPreview";
    static props = { ...standardWidgetProps };

    setup() {
        this.ui = useService("ui");
        this.fileViewer = useFileViewer();
        this.state = useState({ size: this.ui.size, expanded: false });

        this.onResize = useDebounced(() => {
            this.state.size = this.ui.size;
        }, 200);
        browser.addEventListener("resize", this.onResize);
        onWillDestroy(() => browser.removeEventListener("resize", this.onResize));
    }

    /**
     * Largeur à partir de laquelle le volet natif d'Odoo prend le relais.
     * Elle suit le réglage de société exposé dans la session.
     */
    get splitThreshold() {
        return session.expense_scan_wide_split ? SIZES.MD : SIZES.XXL;
    }

    /** L'aperçu ne sert que là où le volet natif est absent. */
    get visible() {
        return Boolean(this.attachment) && this.state.size < this.splitThreshold;
    }

    /**
     * La pièce jointe principale, quelle que soit la forme sous laquelle
     * le modèle web la restitue.
     */
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

    /** Ouvre la visionneuse d'Odoo : zoom, rotation, plein écran. */
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
        {
            name: "message_main_attachment_id",
            type: "many2one",
            relation: "ir.attachment",
            readonly: true,
        },
    ],
};

registry.category("view_widgets").add("expense_scan_receipt", expenseScanReceiptWidget);
