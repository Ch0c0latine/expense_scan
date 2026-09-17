// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * La liste déroulante d'un champ relationnel s'arrête à sept résultats, le
 * reste passant par « Rechercher plus ». Pour les catégories de dépenses —
 * une dizaine en général — c'est toujours la même poignée qu'il fallait
 * aller chercher dans la recherche avancée.
 *
 * Le composant sait afficher davantage (`searchLimit`), mais Odoo ne relaie
 * pas cette option depuis la vue. On la pose donc ici, et seulement pour le
 * champ qui porte le drapeau `expense_scan_category_order` dans son
 * contexte : tous les autres champs relationnels gardent leur limite.
 */
import { patch } from "@web/core/utils/patch";
import { Many2One } from "@web/views/fields/many2one/many2one";

/** Au-delà, la liste deviendrait plus longue à parcourir qu'une recherche. */
const EXPENSE_CATEGORY_LIMIT = 20;

patch(Many2One.prototype, {
    get many2XAutocompleteProps() {
        const props = super.many2XAutocompleteProps;
        if (this.props.context?.expense_scan_category_order) {
            return { ...props, searchLimit: EXPENSE_CATEGORY_LIMIT };
        }
        return props;
    },
});
