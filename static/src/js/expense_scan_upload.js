// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
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
import { useService } from "@web/core/utils/hooks";

import { ReceiptSourceDialog, configureReceiptInput } from "@expense_scan/js/receipt_source_dialog";

import { ExpenseListController } from "@hr_expense/views/list";
import { ExpenseKanbanController } from "@hr_expense/views/kanban";

/**
 * Fabrique un correctif neuf à chaque appel — et non un objet partagé.
 *
 * `patch` redéfinit le prototype de l'objet qu'on lui passe pour que
 * `super` y résolve la méthode d'origine. Réutiliser le même objet sur
 * deux contrôleurs ferait donc pointer le `super` du premier vers la
 * chaîne du second : la liste hériterait du kanban, et son `setup`
 * n'y trouverait pas les mêmes informations de vue.
 */
const expenseScanUpload = () => ({
    setup() {
        super.setup();
        this.expenseScanDialog = useService("dialog");
        // Envois réellement en cours. Le compteur d'Odoo, `uploadsProcessing`,
        // monte à chaque ouverture du sélecteur mais ne redescend pas quand
        // on le referme sans rien choisir : après une annulation, l'envoi
        // suivant se croyait « un parmi d'autres » et restait sur la liste
        // au lieu d'ouvrir la fiche. On ne s'y fie donc plus.
        this.expenseScanInFlight = 0;
    },

    /**
     * Sur téléphone, on demande la source avant d'ouvrir le sélecteur ;
     * sur ordinateur, le sélecteur de fichiers s'ouvre directement.
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
                // Dans le geste de l'utilisateur sur le bouton du choix,
                // sans quoi le navigateur refuse d'ouvrir le sélecteur.
                this.fileInput.el.click();
            },
        });
    },

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
        this.expenseScanInFlight++;
        try {
            await this._onChangeFileInput([...this.fileInput.el.files]);
            const created = this.createdExpenseIds.slice(alreadyCreated);
            // Seul envoi en cours et un seul ticket : on ouvre sa fiche.
            const alone = this.expenseScanInFlight === 1;
            if (alone && created.length === 1) {
                // La fiche qui s'ouvre porte déjà son propre bandeau — un
                // succès ou un point à vérifier n'a rien à ajouter. Une
                // erreur reste utile : elle ne dépend pas du scan_state
                // affiché sur la fiche vide qui s'ouvrira quand même.
                await this._expenseScanReport(created, { onlyErrors: true });
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
            await this._expenseScanReport(created);
            if (alone) {
                await this._expenseScanOpenList();
            }
        } finally {
            closeNotification();
            this.expenseScanInFlight--;
            // Tenu à jour pour le reste d'Odoo, sans jamais passer sous zéro.
            this.uploadsProcessing = Math.max(0, this.uploadsProcessing - 1);
        }
    },

    /**
     * Dit ce que l'analyse a donné, y compris quand elle a échoué.
     *
     * Sans ça, un ticket illisible se traduisait par une fiche vide sans
     * un mot d'explication : l'erreur n'était visible que dans le bandeau
     * du formulaire, qu'on ne voit pas si l'on est resté sur la liste.
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
            return; // un compte rendu ne doit jamais faire échouer l'envoi
        }

        const failed = records.filter((record) => record.scan_state === "error");
        if (failed.length) {
            this.notification.add(
                _t("Le ticket n'a pas pu être analysé : %s", failed[0].scan_message || ""),
                { type: "danger", sticky: true }
            );
            return;
        }
        if (onlyErrors) {
            return;
        }
        const toCheck = records.map((record) => record.scan_todo).filter(Boolean);
        if (toCheck.length) {
            this.notification.add(_t("Ticket analysé. À vérifier : %s", toCheck.join(" · ")), {
                type: "warning",
            });
        } else {
            this.notification.add(_t("Ticket analysé."), { type: "success" });
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
});

patch(ExpenseListController.prototype, expenseScanUpload());
patch(ExpenseKanbanController.prototype, expenseScanUpload());
