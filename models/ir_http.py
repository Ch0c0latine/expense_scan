# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """Expose le réglage de vue scindée au client web.

        Le correctif JavaScript qui abaisse le seuil d'affichage du
        justificatif s'exécute à chaque rendu de formulaire : il lui faut
        cette information sans aller la chercher par une requête.
        """
        result = super().session_info()
        result['expense_scan_wide_split'] = self.env.company.expense_scan_wide_split
        # La liste des dépenses remplace « Imprimer » par « Fiche de frais » :
        # le client reconnaît l'action à son identifiant.
        sheet = self.env.ref('expense_scan.expense_scan_sheet_wizard_action',
                             raise_if_not_found=False)
        result['expense_scan_sheet_action_id'] = sheet.id if sheet else False
        return result
