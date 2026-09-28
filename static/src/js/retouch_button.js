// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Bouton « Retoucher l'image », en tête de la fiche, sur grand écran.
 *
 * Le justificatif y est affiché dans le volet d'Odoo, à côté des champs :
 * l'outil doit être à portée dès l'ouverture de la fiche, sans avoir à
 * sortir puis revenir. Sur téléphone, c'est le bandeau d'aperçu qui porte
 * le même bouton (receipt_preview_widget.js) : celui-ci ne rend rien.
 *
 * N'apparaît que pour une image : le canevas de retouche ne sait pas
 * dessiner un PDF, dont la visionneuse garde sa propre rotation.
 */
import { Component, onWillDestroy, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { SIZES } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

import { openRetouchDialog, retouchableAttachmentId } from "@expense_scan/js/retouch_dialog";

export class ExpenseScanRetouchButton extends Component {
    static template = "expense_scan.RetouchButton";
    static props = { ...standardWidgetProps };

    setup() {
        this.dialog = useService("dialog");
        this.orm = useService("orm");
        this.ui = useService("ui");
        this.state = useState({ size: this.ui.size });
        this.onResize = useDebounced(() => {
            this.state.size = this.ui.size;
        }, 200);
        browser.addEventListener("resize", this.onResize);
        onWillDestroy(() => browser.removeEventListener("resize", this.onResize));
    }

    /** Même seuil que le bandeau d'aperçu, qui prend le relais en deçà. */
    get splitThreshold() {
        return session.expense_scan_wide_split ? SIZES.MD : SIZES.XXL;
    }

    get visible() {
        return Boolean(this.props.record.data.is_editable)
            && Boolean(retouchableAttachmentId(this.props.record))
            && this.state.size >= this.splitThreshold;
    }

    open() {
        openRetouchDialog({ dialog: this.dialog, orm: this.orm }, this.props.record);
    }
}

registry.category("view_widgets").add("expense_scan_retouch_button", {
    component: ExpenseScanRetouchButton,
    fieldDependencies: [
        { name: "is_editable", type: "boolean", readonly: true },
        {
            name: "message_main_attachment_id",
            type: "many2one",
            relation: "ir.attachment",
            readonly: true,
        },
    ],
});
