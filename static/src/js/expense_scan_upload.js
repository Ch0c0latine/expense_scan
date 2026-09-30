// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * After a receipt is uploaded, Odoo opens the "Generate Expenses" list. For a
 * single receipt (the usual case from a phone), this patch opens the form
 * directly, which serves as the review screen: receipt on one side, fields
 * filled in on the other.
 *
 * A notification shows the wait: reading the receipt takes one or two
 * seconds on the server, with no other sign in the interface.
 */
import { _t } from "@web/core/l10n/translation";
import { Domain } from "@web/core/domain";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

import { ReceiptSourceDialog, configureReceiptInput } from "@expense_scan/js/receipt_source_dialog";

import { ExpenseListController } from "@hr_expense/views/list";
import { ExpenseKanbanController } from "@hr_expense/views/kanban";

/**
 * Build a new patch on each call (no shared object).
 *
 * `patch` redefines the prototype of the object passed so that `super`
 * resolves the original method there. The same object applied to two
 * controllers would make the first one's `super` point to the second one's
 * chain.
 */
const expenseScanUpload = () => ({
    setup() {
        super.setup();
        this.expenseScanDialog = useService("dialog");
        // Uploads actually in progress. Odoo's counter, `uploadsProcessing`,
        // goes up each time the picker opens and does not go down if the
        // picker is closed without a choice: after a cancel, the next upload
        // would count as running alongside another and would stay on the
        // list. That counter is therefore not used here.
        this.expenseScanInFlight = 0;
    },

    /**
     * On a phone, ask for the source (camera, gallery or files) before
     * opening the picker; on a computer, open the picker directly.
     *
     * @override
     */
    uploadDocument() {
        if (!this.env.isSmall) {
            return super.uploadDocument();
        }
        this.expenseScanDialog.add(ReceiptSourceDialog, {
            choose: (source) => {
                configureReceiptInput(this.fileInput.el, source);
                // Must run within the user's gesture, otherwise the browser
                // refuses to open the picker.
                this.fileInput.el.click();
            },
        });
    },

    /**
     * A single receipt: the expense is created without waiting for the scan,
     * which the form starts while showing its progress (expense_scan_progress
     * widget). Several receipts: scanned at creation, the original behaviour;
     * the list cannot show the progress of each one.
     *
     * @override
     */
    async onUpload(attachments) {
        if (attachments.length !== 1 || this.expenseScanInFlight !== 1) {
            return super.onUpload(attachments);
        }
        const createdExpenseIds = await this.orm.call(
            "hr.expense",
            "create_expense_from_attachments",
            [attachments.map((attachment) => attachment.id), this.env.config.viewType],
            { context: { ...this.props.context, expense_scan_async: true } }
        );
        this.createdExpenseIds = [...this.createdExpenseIds, ...createdExpenseIds];
    },

    /**
     * Same logic as Odoo's mixin (hr_expense/mixins/document_upload), with
     * another final destination. `super` is not called: it triggers the
     * navigation, and correcting it afterwards would show two screens in a
     * row.
     *
     * @override
     */
    async onChangeFileInput() {
        // A single receipt is scanned in its form, which shows the steps: the
        // notification only mentions the upload.
        const closeNotification = this.notification.add(
            this.fileInput.el.files.length === 1
                ? _t("Uploading the receipt...")
                : _t("Reading the receipts..."),
            { type: "info", sticky: true }
        );
        // `createdExpenseIds` grows over the whole life of the controller:
        // only the ids created by this upload are kept.
        const alreadyCreated = this.createdExpenseIds.length;
        this.expenseScanInFlight++;
        try {
            await this._onChangeFileInput([...this.fileInput.el.files]);
            const created = this.createdExpenseIds.slice(alreadyCreated);
            // Only upload in progress and a single receipt: open its form.
            const alone = this.expenseScanInFlight === 1;
            if (alone && created.length === 1) {
                // The form shows its own banner: success and points to check
                // need no notification. An error is still notified, since the
                // form opens anyway, empty.
                await this._expenseScanReport(created, { onlyErrors: true });
                await this.actionService.doAction({
                    type: "ir.actions.act_window",
                    name: _t("Receipt review"),
                    res_model: "hr.expense",
                    res_id: created[0],
                    views: [[false, "form"]],
                    view_mode: "form",
                    context: this.props.context,
                });
                return;
            }
            await this._expenseScanReport(created);
            if (alone) {
                await this._expenseScanOpenList();
            }
        } finally {
            closeNotification();
            this.expenseScanInFlight--;
            // Kept up to date for the rest of Odoo, without going below zero.
            this.uploadsProcessing = Math.max(0, this.uploadsProcessing - 1);
        }
    },

    /**
     * Notify the result of the scan, including a failure.
     *
     * Without this notification, an unreadable receipt gives an empty form:
     * the error only shows in the form's banner, invisible if the user stays
     * on the list.
     */
    async _expenseScanReport(expenseIds, { onlyErrors = false } = {}) {
        if (!expenseIds.length) {
            return;
        }
        let records;
        try {
            records = await this.orm.read("hr.expense", expenseIds, [
                "scan_state",
                "scan_message",
                "scan_todo",
            ]);
        } catch {
            return; // the report must not make the upload fail
        }

        const failed = records.filter((record) => record.scan_state === "error");
        if (failed.length) {
            this.notification.add(
                _t("The receipt could not be scanned: %s", failed[0].scan_message || ""),
                { type: "danger", sticky: true }
            );
            return;
        }
        if (onlyErrors) {
            return;
        }
        const toCheck = records.map((record) => record.scan_todo).filter(Boolean);
        if (records.length > 1) {
            // The points of each receipt, put end to end, would repeat the
            // same labels without saying which receipt they concern.
            this.notification.add(
                toCheck.length
                    ? _t("%(count)s receipts scanned, %(check)s to check.", {
                          count: records.length,
                          check: toCheck.length,
                      })
                    : _t("%(count)s receipts scanned.", { count: records.length }),
                { type: toCheck.length ? "warning" : "success" }
            );
        } else if (toCheck.length) {
            this.notification.add(_t("Receipt scanned. To check: %s", toCheck[0]), {
                type: "warning",
            });
        } else {
            this.notification.add(_t("Receipt scanned."), { type: "success" });
        }
    },

    /** Several receipts at once: stay on the list, as Odoo does. */
    async _expenseScanOpenList() {
        const actionName = _t("Generate Expenses");
        const currentAction = this.actionService.currentController.action;
        let domain = [["id", "in", this.createdExpenseIds]];
        const options = {};
        if (currentAction.name === actionName) {
            domain = Domain.or([domain, currentAction.domain]).toList();
            options.stackPosition = "replaceCurrentAction";
        }
        await this.actionService.doAction(
            {
                name: actionName,
                res_model: "hr.expense",
                type: "ir.actions.act_window",
                views: [
                    [false, this.env.config.viewType],
                    [false, "form"],
                ],
                domain: domain,
                context: this.props.context,
            },
            options
        );
    },
});

patch(ExpenseListController.prototype, expenseScanUpload());
patch(ExpenseKanbanController.prototype, expenseScanUpload());
