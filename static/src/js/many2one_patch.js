// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * La liste déroulante d'un champ relationnel affiche sept résultats ; les
 * suivants passent par « Rechercher plus ». Les catégories de dépenses
 * dépassent souvent ce nombre.
 *
 * Le composant accepte une limite plus haute (`searchLimit`), qu'Odoo ne
 * transmet pas depuis la vue. Elle est posée ici pour le seul champ dont le
 * contexte porte `expense_scan_category_order`.
 */
import { patch } from "@web/core/utils/patch";
import { Many2One } from "@web/views/fields/many2one/many2one";

/** Nombre de catégories affichées dans la liste déroulante. */
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
