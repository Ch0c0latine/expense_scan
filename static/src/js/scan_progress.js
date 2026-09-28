// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Progression de l'analyse, affichée sur la fiche qui vient de s'ouvrir.
 *
 * La fiche s'ouvre dès l'envoi du ticket, avant toute lecture : ce widget
 * lance alors l'analyse (action_expense_scan_start), en suit les étapes
 * annoncées par le serveur, et remplit les champs au fil de l'eau — date,
 * montant et enseigne dès qu'ils sont lus, le reste à la fin. On peut donc
 * écrire la description pendant ce temps : ce que l'utilisateur a modifié
 * n'est jamais remplacé, ni à l'écran ni en base.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { getFieldsSpec } from "@web/model/relational_model/utils";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

/** Étapes affichées, et les étapes du serveur qui les terminent. */
const STEPS = [
    { key: "prepare", label: _t("Préparation de l'image"), ends: ["préparation"] },
    { key: "read", label: _t("Lecture du texte"), ends: ["lecture"] },
    {
        key: "straighten",
        label: _t("Redressement et recadrage"),
        ends: ["relecture", "redressement", "recadrage"],
    },
    { key: "values", label: _t("Date, montant et TVA"), ends: ["analyse", "valeurs"] },
    { key: "category", label: _t("Catégorie et mission"), ends: ["catégorie"] },
    { key: "save", label: _t("Enregistrement"), ends: ["écriture"] },
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
                // Déjà lancée ailleurs (un autre onglet) : la fiche se
                // rechargera avec ses résultats.
                setTimeout(() => record.model.load(), 3000);
                return;
            }
            this.apply(result.values);
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
     * Valeurs du serveur, appliquées comme un rechargement : Odoo les pose
     * sous les modifications en cours de l'utilisateur, qui restent visibles
     * et seront seules enregistrées.
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
