// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Choix de la source du justificatif : appareil photo, galerie ou fichiers.
 *
 * Sur téléphone, un champ fichier unique ne propose pas ces trois choix
 * partout : l'application Odoo ouvre la galerie sans l'appareil photo, et
 * le filtre « image » masque les PDF. Le champ est réglé selon la source
 * choisie, puis ouvert dans le même geste : le navigateur refuse d'ouvrir
 * un sélecteur hors d'une action de l'utilisateur.
 */
import { _t } from "@web/core/l10n/translation";
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Réglage du champ fichier pour chaque source. */
export const RECEIPT_SOURCES = {
    // `capture` ouvre directement l'appareil photo arrière.
    camera: { accept: "image/*", capture: "environment" },
    gallery: { accept: "image/*" },
    // Avec un type non-image, Android ouvre son sélecteur de fichiers, qui
    // donne accès aux PDF téléchargés.
    files: { accept: "image/*,application/pdf" },
};

/**
 * Règle le champ fichier pour la source choisie.
 *
 * @param {HTMLInputElement} input
 * @param {keyof RECEIPT_SOURCES} source
 */
export function configureReceiptInput(input, source) {
    const settings = RECEIPT_SOURCES[source];
    input.accept = settings.accept;
    if (settings.capture) {
        input.setAttribute("capture", settings.capture);
    } else {
        input.removeAttribute("capture");
    }
}

export class ReceiptSourceDialog extends Component {
    static template = "expense_scan.ReceiptSourceDialog";
    static components = { Dialog };
    static props = {
        choose: Function,
        close: Function,
    };

    get title() {
        return _t("Ajouter un justificatif");
    }

    /** Ouvre le sélecteur avant de fermer la boîte, pendant l'action de l'utilisateur. */
    pick(source) {
        this.props.choose(source);
        this.props.close();
    }
}
