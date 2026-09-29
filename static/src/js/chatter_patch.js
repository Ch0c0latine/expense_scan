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
 * justificatif est analysé comme un ticket scanné. Les champs déjà saisis
 * sur la fiche sont transmis au serveur : l'analyse ne les remplit pas.
 */
import { Chatter } from "@mail/chatter/web_portal/chatter";
// Chargé avant ce correctif : il redéfinit onClickAttachFile et onUploaded
// sans appeler la version précédente.
import "@mail/chatter/web/chatter_patch";
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    // Les droits d'une fiche pas encore enregistrée ne sont pas connus :
    // Odoo grise « Joindre des fichiers ». Qui crée la dépense peut y joindre
    // son justificatif.
    get canPostMessage() {
        if (this.model === "hr.expense" && !this.id) {
            return true;
        }
        return super.canPostMessage;
    },
});

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        // Service pris hors du composant : l'enregistrement de la fiche
        // recrée le volet, et un appel passé par useService ne se termine
        // jamais une fois le composant détruit.
        this.expenseScanOrm = this.env.services.orm;
        this.expenseScanAnalyze = false;
        this.expenseScanChanged = [];
    },

    async onClickAttachFile(ev) {
        const record = this.props.record;
        if (!this.state.thread.id && record?.resModel === "hr.expense") {
            // Champs modifiés par l'utilisateur depuis l'ouverture de la
            // fiche, relevés avant la pose des valeurs provisoires.
            this.expenseScanChanged = Object.keys(record._changes || {});
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

    // Supprimer le justificatif affiché supprime aussi sa photo d'origine
    // (voir models/ir_attachment.py) : la fiche est rechargée pour ne plus
    // afficher de lien vers une pièce disparue.
    async unlinkAttachment(attachment) {
        await super.unlinkAttachment(...arguments);
        if (this.props.record?.resModel === "hr.expense"
                && !this.props.hasParentReloadOnAttachmentsChanged) {
            await this.reloadParentView();
        }
    },

    onUploaded(data, { thread } = {}) {
        return async (...args) => {
            // Le bouton d'envoi est rendu avec le fil de la fiche neuve, sans
            // identifiant ; la fiche vient d'être enregistrée, le fichier va
            // au fil de la dépense créée.
            const current = this.state.thread;
            const target = !thread?.id && current?.id ? current : thread;
            const analyze = this.expenseScanAnalyze;
            await super.onUploaded(data, { thread: target })(...args);
            const record = this.props.record;
            if (!analyze || record?.resModel !== "hr.expense" || !record.resId) {
                return;
            }
            this.expenseScanAnalyze = false;
            await this.expenseScanOrm.call(
                "hr.expense", "expense_scan_analyze_new_receipt",
                [[record.resId], this.expenseScanChanged]);
            await record.model.load();
        };
    },
});
