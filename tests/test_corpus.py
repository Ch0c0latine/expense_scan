# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Passe un dossier de justificatifs réels dans la chaîne complète.

Ne fait rien si ``/tmp/expense_scan_corpus`` n'existe pas : les justificatifs
sont des données personnelles, jamais versionnées. Chaque fichier donne une
ligne « CORPUS| » dans le journal, sans le texte du justificatif.

Si ``/tmp/expense_scan_snapshot`` existe aussi, chaque justificatif y laisse
un instantané de ses mots lus (voir ``tools/bench.py``), ce qui permet de
rejouer l'analyse en quelques secondes sans repasser par l'OCR. Le texte
lu est une donnée personnelle : il est écrit dans ce dossier et jamais dans
le journal, que d'autres personnes lisent.
"""
import gc
import hashlib
import json
import logging
import os
import time
from datetime import date
from unittest import mock

from odoo.tests import common, tagged

from ..ocr import parser

_logger = logging.getLogger(__name__)
CORPUS_DIR = '/tmp/expense_scan_corpus'
SNAPSHOT_DIR = '/tmp/expense_scan_snapshot'
#: Limite de mémoire au-delà de laquelle le test s'arrête (la machine de
#: test héberge aussi la production).
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


def _dump(path, data):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))


def _code(product):
    return product.default_code or product.display_name


@tagged('post_install', '-at_install')
class TestCorpus(common.TransactionCase):

    def _category_meta(self, company):
        """Données dont le banc a besoin pour deviner une catégorie sans Odoo.

        Les catégories désignées par des mots et la catégorie de chaque
        famille de frais. Sans elles, l'instantané ne permettrait pas de
        déterminer la catégorie.
        """
        Expense = self.env['hr.expense']
        Product = self.env['product.product'].sudo()
        families = {}
        for template, family in self.env['product.template'].sudo() \
                ._expense_scan_family_templates().items():
            product = template.product_variant_id
            if Expense._expense_scan_guessable(product, company):
                families[family] = _code(product)
        return {
            'keyword_categories': {
                _code(Product.browse(pid)): list(words)
                for pid, words in Expense._expense_scan_keyword_categories(company).items()},
            'family_keys': families,
        }

    def test_real_receipts(self):
        if not os.path.isdir(CORPUS_DIR):
            self.skipTest("pas de corpus")
        snapshot = os.path.isdir(SNAPSHOT_DIR)
        # Un salarié ordinaire, sans droit de gestion : la chaîne doit passer
        # sous ses règles d'accès, comme depuis le bouton « Téléverser ».
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Corpus", 'login': 'expense_scan_corpus',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        employee = self.env['hr.employee'].create({'name': "Corpus", 'user_id': user.id})
        env = self.env(user=user)
        # Le corpus remonte à plusieurs années : la limite d'ancienneté ne
        # doit pas faire écarter la date d'un ticket de 2023.
        self.env.company.expense_scan_max_age_days = 3650
        if snapshot:
            module = self.env['ir.module.module'].sudo().search([('name', '=', 'expense_scan')])
            _dump(os.path.join(SNAPSHOT_DIR, '_meta.json'), dict(
                self._category_meta(self.env.company),
                created=date.today().isoformat(), version=module.installed_version))
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
            captured = {}
            original = parser.parse

            def capturing(words, *args, _original=original, **kwargs):
                captured['words'], captured['kwargs'] = list(words), kwargs
                captured['result'] = _original(words, *args, **kwargs)
                return captured['result']

            try:
                with mock.patch.object(parser, 'parse', capturing):
                    ids = env['hr.expense'].with_context(
                        default_employee_id=employee.id).create_expense_from_attachments(
                            [attachment.id], 'list')
                expense = env['hr.expense'].browse(ids[:1])
                elapsed = time.time() - started
                line = (
                    "CORPUS|%s|%.1fs|%s|%s|total=%s|tva=%s/%s|marchand=%s|date=%s|todo=%s|%s" % (
                        name, elapsed, expense.scan_state,
                        expense.product_id.display_name, expense.total_amount_currency,
                        expense.scan_tax_amount, expense.tax_ids.mapped('amount'),
                        expense.expense_scan_merchant, expense.date,
                        expense.scan_todo, expense.scan_message))
                if snapshot and captured:
                    self._write_snapshot(name, captured, expense)
                if name.startswith('OP-'):
                    # Tickets d'Open Prices : des achats sans lien entre eux.
                    # Gardées, ces dépenses du même salarié, souvent datées du
                    # jour faute de date lue, feraient recalculer à chaque
                    # ticket les règles de dépenses de toutes les autres :
                    # une durée qui croît avec le corpus.
                    expense.sudo().unlink()
            except Exception as error:  # noqa: BLE001
                _logger.warning("CORPUS|%s|ERREUR|%s", name, error, exc_info=True)
                continue
            # Un cache conservé sur tout le corpus, dans une seule
            # transaction, ne correspond pas à un worker : le cache est vidé
            # pour que la mémoire mesurée ne concerne que la chaîne.
            self.env.flush_all()
            self.env.invalidate_all()
            gc.collect()
            rss = _rss_mb()
            _logger.info("%s|rss=%dMo", line, rss)
            if rss > RSS_LIMIT_MB:
                # Le serveur de développement héberge aussi la production.
                _logger.warning("CORPUS|arrêt : %d Mo de mémoire après %s", rss, name)
                break

    def _write_snapshot(self, name, captured, expense):
        """Les mots lus, tels que l'analyseur les a reçus, et ce qu'Odoo en a tiré."""
        kwargs = captured['kwargs']
        number = captured['result'].value('company_number')
        naf = self.env['expense.scan.sirene']._expense_scan_activity(number) if number else False
        _dump(os.path.join(SNAPSHOT_DIR, name + '.json'), {
            'name': name,
            'today': date.today().isoformat(),
            'max_age_days': kwargs.get('max_age_days'),
            'default_currency': kwargs.get('default_currency'),
            'buyers': list(kwargs.get('buyers') or ()),
            'naf': naf or None,
            'words': [[w.text, round(w.score, 4), round(w.left, 2), round(w.top, 2),
                       round(w.right, 2), round(w.bottom, 2), round(w.angle, 2)]
                      for w in captured['words']],
            # Valeurs écrites par Odoo, pour vérifier que le rejeu donne le
            # même résultat.
            'odoo': {
                'total': expense.total_amount_currency,
                'tax': expense.scan_tax_amount,
                'date': expense.date and expense.date.isoformat(),
                'merchant': expense.expense_scan_merchant or None,
                'category': _code(expense.product_id),
            },
        })
