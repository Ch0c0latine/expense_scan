# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from . import models


def post_init_hook(env):
    """Propose des mots du ticket aux catégories existantes."""
    env['product.template']._expense_scan_seed_keywords()
