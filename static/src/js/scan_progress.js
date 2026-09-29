// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Progression de l'analyse, affichée sur la fiche ouverte après l'envoi.
 *
 * La fiche s'ouvre avant toute lecture. Le widget lance l'analyse
 * (action_expense_scan_start), suit les étapes annoncées par le serveur et
 * remplit les champs au fur et à mesure : date, montant et enseigne dès
 * qu'ils sont lus, le reste à la fin. Les champs modifiés par l'utilisateur
 * pendant l'analyse ne sont pas remplacés, ni à l'écran ni en base.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { getFieldsSpec } from "@web/model/relational_model/utils";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/** Étapes affichées, et les étapes du serveur qui les terminent. */
const STEPS = [
    { key: "prepare", label: _t("Préparation de l'image"), ends: ["prepare"] },
    { key: "read", label: _t("Lecture du texte"), ends: ["read"] },
    {
        key: "straighten",
        label: _t("Redressement et recadrage"),
        ends: ["reread", "straighten", "crop"],
    },
    { key: "values", label: _t("Date, montant et TVA"), ends: ["parse", "values"] },
    { key: "category", label: _t("Catégorie et mission"), ends: ["category"] },
    { key: "save", label: _t("Enregistrement"), ends: ["write"] },
];

export class ScanProgress extends Component {
    static template = "expense_scan.ScanProgress";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.bus = useService("bus_service");
        this.notification = useService("notification");
        this.state = useState({ active: false, done: 0 });
        this.onProgress = this.onProgress.bind(this);

        onMounted(() => {
            if (this.props.record.data.scan_state === "running") {
                this.start();
            }
        });
        onWillUnmount(() => this.bus.unsubscribe("expense_scan/progress", this.onProgress));
    }

    get steps() {
        return STEPS.map((step, index) => ({
            ...step,
            done: index < this.state.done,
            current: index === this.state.done,
        }));
    }

    async start() {
        const record = this.props.record;
        this.state.active = true;
        this.bus.subscribe("expense_scan/progress", this.onProgress);
        try {
            const specification = getFieldsSpec(
                record.activeFields, record.fields, record.evalContext);
            const result = await this.orm.call(
                "hr.expense", "action_expense_scan_start", [[record.resId]], { specification });
            if (result.busy) {
                // Analyse déjà lancée ailleurs (autre onglet) : rechargement
                // différé pour récupérer les résultats.
                setTimeout(() => record.model.load(), 3000);
                return;
            }
            if (await record.isDirty()) {
                // Saisies en cours : seuls les champs non modifiés sont mis à jour.
                this.apply(result.values);
            } else {
                // Aucune saisie : rechargement complet, qui met aussi à jour
                // le titre et le justificatif recadré dans le volet.
                await record.model.load();
            }
        } catch (error) {
            this.notification.add(_t("L'analyse du ticket a échoué : relancez-la depuis la fiche."), {
                type: "danger",
            });
            throw error;
        } finally {
            this.state.active = false;
            this.bus.unsubscribe("expense_scan/progress", this.onProgress);
        }
    }

    onProgress(payload) {
        if (payload.expense_id !== this.props.record.resId) {
            return;
        }
        const index = STEPS.findIndex((step) => step.ends.includes(payload.step));
        if (index >= 0) {
            this.state.done = Math.max(this.state.done, index + 1);
        }
        if (payload.values && Object.keys(payload.values).length) {
            this.apply(payload.values);
        }
    }

    /**
     * Applique les valeurs du serveur comme un rechargement : Odoo les place
     * sous les modifications en cours de l'utilisateur, qui restent affichées
     * et sont seules enregistrées.
     */
    apply(values) {
        const record = this.props.record;
        const known = Object.fromEntries(
            Object.entries(values || {}).filter(([name]) => name in record.activeFields));
        if (Object.keys(known).length) {
            record._applyValues(known);
        }
    }
}

registry.category("view_widgets").add("expense_scan_progress", {
    component: ScanProgress,
});
