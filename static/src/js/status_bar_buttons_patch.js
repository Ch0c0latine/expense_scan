/**
 * Sur petit écran, Odoo ne garde qu'un seul bouton d'en-tête visible et
 * relègue tous les autres derrière un menu « … ». Sur une note de frais
 * scannée au téléphone, c'est précisément là que se trouvent les gestes
 * qu'on enchaîne — terminer, scanner le suivant — et les cacher ajoute un
 * appui à chacun d'eux.
 *
 * On en laisse donc trois pour les seules dépenses. Le quatrième et les
 * suivants restent dans le menu, ce qui convient : « Supprimer » ferme la
 * marche, et l'y trouver l'éloigne du pouce.
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
        return this.env.model?.root?.resModel === "hr.expense" ? 3 : 1;
    },
});
