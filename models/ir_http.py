# -*- coding: utf-8 -*-
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
        return result
