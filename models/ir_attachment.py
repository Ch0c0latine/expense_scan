# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Suppression du justificatif affiché d'une dépense analysée."""
from odoo import models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    def unlink(self):
        """Supprime aussi la photo d'origine d'un justificatif supprimé.

        Après l'analyse, le justificatif affiché est l'image recadrée ou
        retouchée ; la photo d'origine est une pièce jointe de champ, que
        l'utilisateur ne voit pas. Quand il supprime le justificatif (le
        mauvais ticket, par exemple), l'original ne doit pas lui survivre :
        la retouche le rouvrirait à la place du justificatif joint ensuite.
        Les réglages de retouche, propres à cette image, sont effacés.

        ``expense_scan_keep_original`` : l'analyse remplace l'image recadrée
        par une nouvelle, tirée du même original.
        """
        expenses = self.env['hr.expense']
        if self.ids and not self.env.context.get('expense_scan_keep_original'):
            expenses = expenses.sudo().search(
                [('scan_cropped_attachment_id', 'in', self.ids)])
        originals = expenses.scan_original_attachment_id - self
        result = super().unlink()
        if expenses:
            expenses.exists().write({
                'scan_original_attachment_id': False,
                'expense_scan_manual_retouch': False,
                'expense_scan_retouch_params': False,
            })
            originals.exists().unlink()
        return result
