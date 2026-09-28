# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Passe un dossier de justificatifs réels dans la chaîne complète.

Ne fait rien si ``/tmp/expense_scan_corpus`` n'existe pas : les justificatifs
sont des données personnelles, jamais versionnées. Chaque fichier donne une
ligne « CORPUS| » dans le journal, à relire pour repérer les incohérences.
"""
import base64
import gc
import hashlib
import logging
import os
import time

from odoo.tests import common, tagged

_logger = logging.getLogger(__name__)
CORPUS_DIR = '/tmp/expense_scan_corpus'
#: Au-delà, on arrête : la machine de test héberge aussi la production.
RSS_LIMIT_MB = 3000


def _rss_mb():
    """Mémoire résidente du processus, en Mo (0 hors Linux)."""
    try:
        with open('/proc/self/status') as status:
            for line in status:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


@tagged('post_install', '-at_install')
class TestCorpus(common.TransactionCase):

    def test_real_receipts(self):
        if not os.path.isdir(CORPUS_DIR):
            self.skipTest("pas de corpus")
        # Un salarié ordinaire, sans droit de gestion : la chaîne doit passer
        # sous ses règles d'accès, comme depuis le bouton « Téléverser ».
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Corpus", 'login': 'expense_scan_corpus',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        employee = self.env['hr.employee'].create({'name': "Corpus", 'user_id': user.id})
        env = self.env(user=user)
        # Un corpus d'archives remonte à plusieurs années : la limite
        # d'ancienneté ne doit pas faire écarter la date d'un ticket de 2023.
        self.env.company.expense_scan_max_age_days = 3650
        seen = set()
        for name in sorted(os.listdir(CORPUS_DIR)):
            path = os.path.join(CORPUS_DIR, name)
            with open(path, 'rb') as handle:
                raw = handle.read()
            digest = hashlib.md5(raw).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            attachment = env['ir.attachment'].create({
                'name': name, 'raw': raw, 'res_model': 'hr.expense', 'res_id': 0})
            started = time.time()
            try:
                ids = env['hr.expense'].with_context(
                    default_employee_id=employee.id).create_expense_from_attachments(
                        [attachment.id], 'list')
                expense = env['hr.expense'].browse(ids[:1])
                elapsed = time.time() - started
                text = (expense.scan_raw_text or "").replace(chr(10), " | ")
                line = (
                    "CORPUS|%s|%.1fs|%s|%s|total=%s|tva=%s/%s|marchand=%s|date=%s|todo=%s|%s" % (
                        name, elapsed, expense.scan_state,
                        expense.product_id.display_name, expense.total_amount_currency,
                        expense.scan_tax_amount, expense.tax_ids.mapped('amount'),
                        expense.expense_scan_merchant, expense.date,
                        expense.scan_todo, expense.scan_message))
            except Exception as error:  # noqa: BLE001
                _logger.warning("CORPUS|%s|ERREUR|%s", name, error, exc_info=True)
                continue
            # Tout garder en cache d'un bout à l'autre du corpus, dans une
            # seule transaction, n'est pas ce que vit un worker : on repart
            # d'un cache vide, et la mémoire restante mesure la chaîne seule.
            self.env.flush_all()
            self.env.invalidate_all()
            gc.collect()
            rss = _rss_mb()
            _logger.info("%s|rss=%dMo", line, rss)
            _logger.info("CORPUSTXT|%s|%s", name, text)
            if rss > RSS_LIMIT_MB:
                # Le serveur de développement est celui de la production.
                _logger.warning("CORPUS|arrêt : %d Mo de mémoire après %s", rss, name)
                break
