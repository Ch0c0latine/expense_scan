# -*- coding: utf-8 -*-
"""Adresses e-mail supplémentaires d'un employé.

Odoo reconnaît l'expéditeur d'un ticket envoyé par courriel en comparant
son adresse à deux champs : l'e-mail professionnel de la fiche employé et
celui de l'utilisateur lié. C'est peu pour un usage mobile, où le même
salarié transfère aussi bien depuis son téléphone personnel que depuis sa
boîte professionnelle — et un expéditeur non reconnu produit une dépense
sans employé, qu'il faut rattacher à la main.
"""
import re

from odoo import fields, models
from odoo.tools import email_normalize

#: Séparateurs acceptés dans la liste : virgule, point-virgule, retour à la
#: ligne, espace. On ne demande pas à l'utilisateur de deviner lequel.
EMAIL_SEPARATORS = re.compile(r"[,;\s]+")


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    expense_scan_emails = fields.Text(
        string="Adresses e-mail supplémentaires",
        help="Autres adresses depuis lesquelles cet employé peut envoyer ses "
             "justificatifs à l'adresse de dépenses — adresse personnelle, "
             "téléphone, ancienne adresse professionnelle.\n\n"
             "Une par ligne, ou séparées par des virgules. L'e-mail "
             "professionnel et celui du compte utilisateur sont déjà "
             "reconnus, inutile de les répéter ici.",
    )

    def _expense_scan_email_set(self):
        """Adresses supplémentaires de cet employé, normalisées."""
        self.ensure_one()
        addresses = set()
        for chunk in EMAIL_SEPARATORS.split(self.expense_scan_emails or ""):
            chunk = chunk.strip()
            if not chunk:
                continue
            addresses.add(email_normalize(chunk) or chunk.lower())
        return addresses
