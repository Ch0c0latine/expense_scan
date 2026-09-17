# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Les règles et modèles d'export livrés autrefois restent, sans le module.

Ils étaient propres à une installation : le module n'en livre plus, hors
données de démonstration. Les enregistrements déjà créés sont conservés
tels quels, détachés du module pour que sa mise à jour ne les supprime pas.
"""


def migrate(cr, version):
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'expense_scan'
           AND model IN ('expense.scan.policy', 'expense.scan.policy.rule',
                         'expense.scan.export.template', 'expense.scan.export.column')
           AND name NOT LIKE 'demo%'
    """)
