// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * The dropdown of a relational field shows seven results; the next ones go
 * through "Search More". Expense categories often exceed that number.
 *
 * The component accepts a higher limit (`searchLimit`), which Odoo does not
 * pass from the view. It is set here for the only field whose context
 * carries `expense_scan_category_order`.
 */
import { patch } from "@web/core/utils/patch";
import { Many2One } from "@web/views/fields/many2one/many2one";

/** Number of categories shown in the dropdown. */
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
