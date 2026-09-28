// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * D'où vient le justificatif ? Appareil photo, galerie ou fichiers.
 *
 * Sur téléphone, un seul champ fichier ne sait pas offrir ces trois choix
 * partout : l'application Odoo ouvre la galerie sans proposer l'appareil
 * photo, et un filtre « image » cache les PDF reçus par mail. On demande
 * donc d'abord, puis on règle le champ en conséquence — dans le même
 * geste, faute de quoi le navigateur refuserait d'ouvrir le sélecteur.
 */
import { _t } from "@web/core/l10n/translation";
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";

/** Réglage du champ fichier pour chaque source. */
export const RECEIPT_SOURCES = {
    // `capture` ouvre directement l'appareil photo arrière.
    camera: { accept: "image/*", capture: "environment" },
    gallery: { accept: "image/*" },
    // Un type non-image fait passer Android sur son sélecteur de fichiers :
    // c'est là que se trouvent les PDF téléchargés.
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

    /** Le sélecteur s'ouvre avant la fermeture : le geste compte encore. */
    pick(source) {
        this.props.choose(source);
        this.props.close();
    }
}
