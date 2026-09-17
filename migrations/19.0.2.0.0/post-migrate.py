# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Exemples livrés : classeur du modèle de base, contrôle des règles.

À l'installation, le fichier de données s'en charge ; à la mise à jour, ses
appels ne sont pas rejoués, d'où ce script.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['expense.scan.export.template']._expense_scan_fill_blank_files()
    env['expense.scan.policy']._expense_scan_recheck_all()
