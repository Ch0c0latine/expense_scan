// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Odoo only shows the receipt next to the form from the XXL size (1400 px),
 * so not on a 1366 px laptop screen.
 *
 * For expenses only, and if the company setting is on, the threshold goes
 * down to MD (768 px). Only the width counts, not the aspect ratio: a
 * half-screen window or a portrait tablet also shows both columns.
 *
 * Below 768 px, the `expense_scan_receipt` widget shows the preview as a
 * strip above the fields.
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
            // Chatter at the bottom, receipt on the side.
            return "COMBO";
        }
        return super.mailLayout(...arguments);
    },
});
