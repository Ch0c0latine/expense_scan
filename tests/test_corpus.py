# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Passe un dossier de justificatifs réels dans la chaîne complète.

Ne fait rien si ``/tmp/expense_scan_corpus`` n'existe pas : les justificatifs
sont des données personnelles, jamais versionnées. Chaque fichier donne une
ligne « CORPUS| » dans le journal, à relire pour repérer les incohérences.
"""
import base64
import hashlib
import logging
import os
import time

from odoo.tests import common, tagged

_logger = logging.getLogger(__name__)
CORPUS_DIR = '/tmp/expense_scan_corpus'


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
                _logger.info(
                    "CORPUS|%s|%.1fs|%s|%s|total=%s|tva=%s/%s|marchand=%s|date=%s|todo=%s|%s",
                    name, time.time() - started, expense.scan_state,
                    expense.product_id.display_name, expense.total_amount_currency,
                    expense.scan_tax_amount, expense.tax_ids.mapped('amount'),
                    expense.expense_scan_merchant, expense.date,
                    expense.scan_todo, expense.scan_message)
                text = (expense.scan_raw_text or "").replace(chr(10), " | ")
                _logger.info("CORPUSTXT|%s|%s", name, text)
            except Exception as error:  # noqa: BLE001
                _logger.warning("CORPUS|%s|ERREUR|%s", name, error, exc_info=True)
