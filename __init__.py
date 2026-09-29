# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from . import models


def post_init_hook(env):
    """Add receipt words and icons to the existing expense categories."""
    env['product.template']._expense_scan_seed_keywords()
    env['product.template']._expense_scan_seed_icons()
