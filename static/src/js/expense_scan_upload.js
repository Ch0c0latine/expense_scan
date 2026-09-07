/**
 * Après le dépôt d'un ticket, Odoo renvoie systématiquement vers la liste
 * « Generate Expenses ». Quand on n'a photographié qu'un seul ticket — le
 * cas normal depuis un téléphone — on ouvre plutôt directement sa fiche,
 * qui est l'écran de vérification : le ticket d'un côté, les champs
 * pré-remplis de l'autre.
 *
 * On signale aussi l'attente : la lecture du ticket prend une à deux
 * secondes côté serveur, pendant lesquelles l'interface ne montre rien.
 */
import { _t } from "@web/core/l10n/translation";
import { Domain } from "@web/core/domain";
import { patch } from "@web/core/utils/patch";

import { ExpenseListController } from "@hr_expense/views/list";
import { ExpenseKanbanController } from "@hr_expense/views/kanban";

const ExpenseScanUpload = {
    /**
     * Reprend la logique du mixin d'Odoo (hr_expense/mixins/document_upload)
     * en changeant uniquement la destination finale. On ne peut pas
     * déléguer à `super` : il déclenche lui-même la navigation, et la
     * corriger après coup ferait clignoter deux écrans.
     *
     * @override
     */
    async onChangeFileInput() {
        const closeNotification = this.notification.add(
            _t("Lecture du ticket en cours…"),
            { type: "info", sticky: true }
        );
        // `createdExpenseIds` s'accumule sur toute la vie du contrôleur :
        // on ne regarde que ce que cet envoi-ci a produit.
        const alreadyCreated = this.createdExpenseIds.length;
        try {
            await this._onChangeFileInput([...this.fileInput.el.files]);
            const created = this.createdExpenseIds.slice(alreadyCreated);
            if (this.uploadsProcessing === 1) {
                if (created.length === 1) {
                    await this.actionService.doAction({
                        type: "ir.actions.act_window",
                        name: _t("Vérification du ticket"),
                        res_model: "hr.expense",
                        res_id: created[0],
                        views: [[false, "form"]],
                        view_mode: "form",
                        context: this.props.context,
                    });
                    return;
                }
                await this._expenseScanOpenList();
            }
        } finally {
            closeNotification();
            this.uploadsProcessing--;
        }
    },

    /** Plusieurs tickets d'un coup : on garde la liste, comme Odoo. */
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
};

patch(ExpenseListController.prototype, ExpenseScanUpload);
patch(ExpenseKanbanController.prototype, ExpenseScanUpload);
