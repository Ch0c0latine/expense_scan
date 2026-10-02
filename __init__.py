# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from . import models
from .models.renamed_translations import apply_renamed_translations


def post_init_hook(env):
    """Add receipt words and icons to the existing expense categories."""
    env['product.template']._expense_scan_seed_keywords()
    env['product.template']._expense_scan_seed_icons()
    apply_renamed_translations(env)
