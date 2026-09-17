# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tarif kilométrique propre à chaque salarié.

Le barème fiscal dépend de la puissance du véhicule et de la distance
annuelle : un tarif unique porté par la catégorie « Kilométrage » ne
convient à personne en particulier.
"""
from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # `groups` est indispensable, et c'est ce qui manquait à un champ
    # précédent : Odoo exige que tout champ absent du profil public des
    # employés en soit pourvu. Sans lui, le champ est préchargé pour tout
    # utilisateur, et la moindre lecture d'un employé par un non-RH échoue —
    # ouvrir une dépense suffisait à le déclencher.
    #
    # Aucune précision imposée au stockage : « Product Price » vaut deux
    # décimales sur une base standard, et arrondissait 0,636 à 0,64 dès
    # l'enregistrement. `min_display_digits` seul garde le nombre entier et
    # en affiche au moins trois décimales — davantage s'il en porte.
    expense_mileage_rate = fields.Float(
        string="Tarif kilométrique",
        min_display_digits=3,
        groups="hr.group_hr_user",
        help="Prix au kilomètre appliqué aux frais de distance de ce "
             "salarié. Laissé vide, c'est le coût de la catégorie qui "
             "s'applique. Aucune décimale n'est perdue : le barème fiscal "
             "en compte trois, un tarif maison peut en compter davantage.",
    )
