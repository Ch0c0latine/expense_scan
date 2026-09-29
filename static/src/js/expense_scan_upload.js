// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Après le dépôt d'un ticket, Odoo ouvre la liste « Generate Expenses ».
 * Pour un seul ticket (cas courant depuis un téléphone), ce correctif ouvre
 * directement la fiche, qui sert d'écran de vérification : ticket d'un côté,
 * champs pré-remplis de l'autre.
 *
 * Une notification signale l'attente : la lecture du ticket prend une à deux
 * secondes côté serveur, sans autre indication dans l'interface.
 */
import { _t } from "@web/core/l10n/translation";
import { Domain } from "@web/core/domain";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

import { ReceiptSourceDialog, configureReceiptInput } from "@expense_scan/js/receipt_source_dialog";

import { ExpenseListController } from "@hr_expense/views/list";
import { ExpenseKanbanController } from "@hr_expense/views/kanban";

/**
 * Fabrique un nouveau correctif à chaque appel (pas d'objet partagé).
 *
 * `patch` redéfinit le prototype de l'objet passé pour que `super` y
 * résolve la méthode d'origine. Le même objet appliqué à deux contrôleurs
 * ferait pointer le `super` du premier vers la chaîne du second.
 */
const expenseScanUpload = () => ({
    setup() {
        super.setup();
        this.expenseScanDialog = useService("dialog");
        // Envois réellement en cours. Le compteur d'Odoo, `uploadsProcessing`,
        // s'incrémente à chaque ouverture du sélecteur et ne redescend pas si
        // le sélecteur est fermé sans choix : après une annulation, l'envoi
        // suivant serait compté comme simultané à un autre et resterait sur
        // la liste. Ce compteur n'est donc pas utilisé ici.
        this.expenseScanInFlight = 0;
    },

    /**
     * Sur téléphone, demande la source (appareil photo, galerie ou fichiers) avant
     * d'ouvrir le sélecteur ; sur ordinateur, ouvre directement le sélecteur.
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
                // Doit s'exécuter dans le geste de l'utilisateur, sinon le
                // navigateur refuse d'ouvrir le sélecteur.
                this.fileInput.el.click();
            },
        });
    },

    /**
     * Un seul ticket : la dépense est créée sans attendre l'analyse, que la
     * fiche lance en affichant sa progression (widget expense_scan_progress).
     * Plusieurs tickets : analyse à la création, comportement d'origine ;
     * la liste ne permet pas de suivre la progression de chacun.
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
     * Reprend la logique du mixin d'Odoo (hr_expense/mixins/document_upload),
     * avec une autre destination finale. `super` n'est pas appelé : il
     * déclenche la navigation, et la corriger ensuite afficherait deux écrans
     * successifs.
     *
     * @override
     */
    async onChangeFileInput() {
        // Un seul ticket est analysé dans sa fiche, qui affiche les étapes :
        // la notification ne mentionne que l'envoi.
        const closeNotification = this.notification.add(
            this.fileInput.el.files.length === 1
                ? _t("Envoi du justificatif…")
                : _t("Lecture des tickets en cours…"),
            { type: "info", sticky: true }
        );
        // `createdExpenseIds` s'accumule pendant toute la vie du contrôleur :
        // seuls les ids créés par cet envoi sont retenus.
        const alreadyCreated = this.createdExpenseIds.length;
        this.expenseScanInFlight++;
        try {
            await this._onChangeFileInput([...this.fileInput.el.files]);
            const created = this.createdExpenseIds.slice(alreadyCreated);
            // Seul envoi en cours et un seul ticket : ouvre sa fiche.
            const alone = this.expenseScanInFlight === 1;
            if (alone && created.length === 1) {
                // La fiche affiche son propre bandeau : succès et points à
                // vérifier n'ont pas besoin de notification. Une erreur reste
                // notifiée, car la fiche s'ouvre quand même, vide.
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
            // Maintenu pour le reste d'Odoo, sans passer sous zéro.
            this.uploadsProcessing = Math.max(0, this.uploadsProcessing - 1);
        }
    },

    /**
     * Notifie le résultat de l'analyse, y compris un échec.
     *
     * Sans cette notification, un ticket illisible donne une fiche vide :
     * l'erreur n'apparaît que dans le bandeau du formulaire, invisible si
     * l'utilisateur reste sur la liste.
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
            return; // le compte rendu ne doit pas faire échouer l'envoi
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

    /** Plusieurs tickets à la fois : garde la liste, comme Odoo. */
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
