# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""The rental car category check no longer exists.

The delivered good practice rule goes. A rule written by the company (a
customer's requirement) is kept as words to watch on the rental contract.
"""

#: Words that, on a rental contract, point to a higher category.
RENTAL_WORDS = "premium\nprestige\nluxe\nluxury\nSUV\nfull size"


def migrate(cr, version):
    cr.execute("""
        DELETE FROM expense_scan_policy_rule
         WHERE id IN (SELECT res_id FROM ir_model_data
                       WHERE module = 'expense_scan'
                         AND name = 'policy_good_practice_rental')
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'expense_scan' AND name = 'policy_good_practice_rental'
    """)
    cr.execute("""
        UPDATE expense_scan_policy_rule
           SET rule_type = 'forbidden_words', words = %s
         WHERE rule_type = 'rental_class'
    """, [RENTAL_WORDS])
