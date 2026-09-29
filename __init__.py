# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from . import models


def post_init_hook(env):
    """Ajoute mots du ticket et icônes aux catégories de dépenses existantes."""
    env['product.template']._expense_scan_seed_keywords()
    env['product.template']._expense_scan_seed_icons()
