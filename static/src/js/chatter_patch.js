// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Receipt attached to a new expense, before it is first saved.
 *
 * The chatter saves the form before uploading a file. A new expense cannot
 * be saved without a description and a category: the file then went without
 * an expense id and the server failed. For an expense, the required fields
 * still empty get a provisional description and the default category; once
 * uploaded, the receipt is scanned like any other. The fields already
 * entered on the form are passed to the server: the scan does not fill them.
 */
import { Chatter } from "@mail/chatter/web_portal/chatter";
// Loaded before this patch: it redefines onClickAttachFile and onUploaded
// without calling the previous version.
import "@mail/chatter/web/chatter_patch";
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

/**
 * Receipt waiting to be scanned: ``{record, changed}``.
 *
 * Kept outside the component: on a phone, the chatter that receives the
 * click is not always the one that finishes the upload, once the form is
 * saved.
 */
let pendingReceipt = null;

patch(Thread.prototype, {
    // The rights on a record not yet saved are unknown: Odoo greys out
    // "Attach files". Whoever creates the expense may attach its receipt.
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
        // Service taken outside the component: saving the form recreates
        // the chatter, and a call made through useService never completes
        // once the component is destroyed.
        this.expenseScanOrm = this.env.services.orm;
        this.state.expenseScanHistory = false;
    },

    async onClickAttachFile(ev) {
        const record = this.props.record;
        if (!this.state.thread.id && record?.resModel === "hr.expense") {
            // Fields changed by the user since the form opened, noted
            // before the provisional values are set.
            const changed = Object.keys(record._changes || {});
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
            pendingReceipt = { record, changed };
        }
        return super.onClickAttachFile(...arguments);
    },

    // Deleting the displayed receipt also deletes its original photo (see
    // models/ir_attachment.py): the form is reloaded so that it no longer
    // links to a missing attachment.
    async unlinkAttachment(attachment) {
        await super.unlinkAttachment(...arguments);
        if (this.props.record?.resModel === "hr.expense"
                && !this.props.hasParentReloadOnAttachmentsChanged) {
            await this.reloadParentView();
        }
    },

    onUploaded(data, { thread } = {}) {
        return async (...args) => {
            // The upload button is rendered with the thread of the new form,
            // without an id; the form has just been saved, the file goes to
            // the thread of the created expense.
            const current = this.state.thread;
            const target = !thread?.id && current?.id ? current : thread;
            await super.onUploaded(data, { thread: target })(...args);
            const record = this.props.record;
            if (record?.resModel !== "hr.expense" || !record.resId) {
                return;
            }
            const pending = pendingReceipt?.record.resId === record.resId ? pendingReceipt : null;
            if (!pending) {
                // Receipt attached to a saved expense: scanned if it replaces
                // a deleted one, then the form is reloaded to show its
                // preview.
                const scanned = await this.expenseScanOrm.call(
                    "hr.expense", "expense_scan_receipt_attached", [[record.resId]]);
                if (scanned) {
                    await record.model.load();
                } else if (!this.props.hasParentReloadOnAttachmentsChanged) {
                    await this.reloadParentView();
                }
                return;
            }
            pendingReceipt = null;
            await this.expenseScanOrm.call(
                "hr.expense", "expense_scan_analyze_new_receipt", [[record.resId], pending.changed]);
            await record.model.load();
        };
    },
});
