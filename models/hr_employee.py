# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Mileage rate per employee.

Official mileage scales often depend on the vehicle and the yearly distance:
a single rate on the "Mileage" category does not fit everyone.
"""
from odoo import fields, models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # `groups` is mandatory: Odoo requires it on every field missing from
    # the public employee profile. Without it, the field is prefetched for
    # every user and reading an employee as a non-HR user fails (opening an
    # expense is enough).
    #
    # No storage precision: "Product Price" has two decimals on a standard
    # database and would round 0.636 to 0.64 on save. `min_display_digits`
    # keeps the full number and shows at least three decimals.
    expense_mileage_rate = fields.Float(
        string="Mileage rate",
        min_display_digits=3,
        groups="hr.group_hr_user",
        help="Price per kilometre applied to this employee's distance "
             "expenses. When empty, the category cost applies. No decimal is "
             "lost: official scales often have three, an in-house rate may "
             "have more.",
    )
