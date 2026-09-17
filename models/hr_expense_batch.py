# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Actions groupées sur la liste des dépenses.

Odoo propose de soumettre et d'approuver plusieurs dépenses à la fois, pas
de les remettre en brouillon. L'action ajoutée ici applique la remise à
zéro d'Odoo, avec ses propres contrôles, à chaque dépense qui s'y prête, et
dit lesquelles elle a dû laisser de côté plutôt que de tout refuser pour
une seule.
"""
from odoo import _, models


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    def action_expense_scan_reset_batch(self):
        """Remet en brouillon les dépenses sélectionnées qui peuvent l'être."""
        to_reset = self.filtered(lambda expense: expense.state != 'draft')
        # Une écriture comptable validée doit d'abord être annulée en
        # comptabilité : Odoo refuse alors la remise en brouillon.
        posted = to_reset.filtered(lambda expense: any(
            state not in (False, 'draft')
            for state in expense.sudo().account_move_id.mapped('state')))
        forbidden = (to_reset - posted).filtered(lambda expense: not expense.can_reset)
        allowed = to_reset - posted - forbidden
        if allowed:
            allowed.action_reset()

        lines = [_("%s dépense(s) remise(s) en brouillon.", len(allowed))]
        if posted:
            lines.append(_(
                "%(count)s laissée(s) de côté : leur écriture comptable est validée "
                "(%(names)s). Annulez-la d'abord depuis la comptabilité.",
                count=len(posted), names=", ".join(posted.mapped('name'))))
        if forbidden:
            lines.append(_(
                "%(count)s laissée(s) de côté faute de droits (%(names)s).",
                count=len(forbidden), names=", ".join(forbidden.mapped('name'))))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Remise en brouillon"),
                'message': "\n".join(lines),
                'type': 'warning' if (posted or forbidden) else 'success',
                'sticky': bool(posted or forbidden),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }
