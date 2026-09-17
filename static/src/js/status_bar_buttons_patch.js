// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Sur petit écran, Odoo ne garde qu'un seul bouton d'en-tête visible et
 * relègue tous les autres derrière un menu « … ». Sur une note de frais
 * scannée au téléphone, c'est précisément là que se trouvent les gestes
 * qu'on enchaîne — terminer, scanner le suivant — et les cacher ajoute un
 * appui à chacun d'eux.
 *
 * On en laisse donc quatre pour les seules dépenses : Terminé, Scanner un
 * autre, Soumettre et Supprimer tiennent sur une ligne, les deux
 * secondaires n'y gardant que leur icône. Au-delà — les boutons d'une
 * dépense soumise ou approuvée — le menu reprend ses droits.
 *
 * Le reste d'Odoo n'est pas touché — hors de la dépense, le getter rend la
 * valeur d'origine.
 */
import { patch } from "@web/core/utils/patch";
import { StatusBarButtons } from "@web/views/form/status_bar_buttons/status_bar_buttons";

patch(StatusBarButtons.prototype, {
    get expenseScanMaxSlots() {
        // Chaînage optionnel de bout en bout : ce composant sert aussi
        // hors d'une vue formulaire, où `env.model` n'existe pas. Sans
        // modèle identifiable, on rend le comportement d'Odoo.
        return this.env.model?.root?.resModel === "hr.expense" ? 4 : 1;
    },
});
