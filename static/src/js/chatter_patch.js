// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Justificatif joint à une dépense neuve, avant son premier enregistrement.
 *
 * Le volet de discussion enregistre la fiche avant d'envoyer un fichier.
 * Une dépense neuve ne s'enregistre pas sans description ni catégorie : le
 * fichier partait alors sans identifiant de dépense et le serveur échouait.
 * Pour une dépense, les champs obligatoires encore vides reçoivent une
 * description provisoire et la catégorie par défaut ; une fois envoyé, le
 * justificatif est analysé comme un ticket scanné.
 */
import { Chatter } from "@mail/chatter/web_portal/chatter";
// Chargé avant ce correctif : il redéfinit onClickAttachFile et onUploaded
// sans appeler la version précédente.
import "@mail/chatter/web/chatter_patch";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.expenseScanOrm = useService("orm");
        this.expenseScanAnalyze = false;
    },

    async onClickAttachFile(ev) {
        const record = this.props.record;
        if (!this.state.thread.id && record?.resModel === "hr.expense") {
            const defaults = await this.expenseScanOrm.call(
                "hr.expense", "expense_scan_receipt_defaults", []);
            const values = {};
            if (!record.data.name) {
                values.name = defaults.name;
            }
            if (!record.data.product_id && defaults.product_id) {
                values.product_id = defaults.product_id;
            }
            if (Object.keys(values).length) {
                await record.update(values);
            }
            this.expenseScanAnalyze = true;
        }
        return super.onClickAttachFile(...arguments);
    },

    onUploaded(data, { thread } = {}) {
        const handler = super.onUploaded(data, { thread });
        return async (...args) => {
            const analyze = this.expenseScanAnalyze;
            await handler(...args);
            const record = this.props.record;
            if (!analyze || record?.resModel !== "hr.expense" || !record.resId) {
                return;
            }
            this.expenseScanAnalyze = false;
            await this.expenseScanOrm.call(
                "hr.expense", "expense_scan_analyze_new_receipt", [[record.resId]]);
            await record.load();
        };
    },
});
