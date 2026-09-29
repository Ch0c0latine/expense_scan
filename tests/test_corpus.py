# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Run a folder of real receipts through the whole chain.

Does nothing if ``/tmp/expense_scan_corpus`` does not exist: the receipts are
personal data, never versioned. Each file gives a "CORPUS|" line in the log,
without the text of the receipt.

If ``/tmp/expense_scan_snapshot`` exists too, each receipt leaves a snapshot
of its words read there (see ``tools/bench.py``), which lets the parsing be
replayed in a few seconds without going through the OCR again. The text read
is personal data: it is written to that folder and never to the log, which
other people read.
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
#: Memory limit beyond which the test stops (the test machine may run other
#: services).
RSS_LIMIT_MB = 3000


def _rss_mb():
    """Resident memory of the process, in MB (0 outside Linux)."""
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
        """Data the benchmark needs to guess a category without Odoo.

        The categories designated by words and the category of each expense
        family. Without them, the snapshot could not determine the category.
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
            self.skipTest("no corpus")
        snapshot = os.path.isdir(SNAPSHOT_DIR)
        # An ordinary employee, without management rights: the chain must go
        # through under their access rules, as from the "Upload" button.
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': "Corpus", 'login': 'expense_scan_corpus',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        employee = self.env['hr.employee'].create({'name': "Corpus", 'user_id': user.id})
        env = self.env(user=user)
        # The corpus goes back several years: the age limit must not rule out
        # the date of a receipt from 2023.
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
                    "CORPUS|%s|%.1fs|%s|%s|total=%s|tax=%s/%s|merchant=%s|date=%s|todo=%s|%s" % (
                        name, elapsed, expense.scan_state,
                        expense.product_id.display_name, expense.total_amount_currency,
                        expense.scan_tax_amount, expense.tax_ids.mapped('amount'),
                        expense.expense_scan_merchant, expense.date,
                        expense.scan_todo, expense.scan_message))
                if snapshot and captured:
                    self._write_snapshot(name, captured, expense)
                if name.startswith('OP-'):
                    # Open Prices receipts: unrelated purchases. If kept,
                    # these expenses of the same employee, often dated today
                    # for want of a date read, would make every receipt
                    # recompute the expense rules of all the others: a
                    # duration that grows with the corpus.
                    expense.sudo().unlink()
            except Exception as error:  # noqa: BLE001
                _logger.warning("CORPUS|%s|ERROR|%s", name, error, exc_info=True)
                continue
            # A cache kept over the whole corpus, in a single transaction,
            # does not match a worker: the cache is cleared so that the
            # memory measured only concerns the chain.
            self.env.flush_all()
            self.env.invalidate_all()
            gc.collect()
            rss = _rss_mb()
            _logger.info("%s|rss=%dMB", line, rss)
            if rss > RSS_LIMIT_MB:
                _logger.warning("CORPUS|stopped: %d MB of memory after %s", rss, name)
                break

    def _write_snapshot(self, name, captured, expense):
        """The words read, as the parser received them, and what Odoo made of them."""
        kwargs = captured['kwargs']
        number = captured['result'].value('company_number')
        naf = self.env['expense.scan.sirene']._expense_scan_activity(number) if number else False
        _dump(os.path.join(SNAPSHOT_DIR, name + '.json'), {
            'name': name,
            'today': date.today().isoformat(),
            'max_age_days': kwargs.get('max_age_days'),
            'default_currency': kwargs.get('default_currency'),
            'country': kwargs.get('country'),
            'buyers': list(kwargs.get('buyers') or ()),
            'naf': naf or None,
            'words': [[w.text, round(w.score, 4), round(w.left, 2), round(w.top, 2),
                       round(w.right, 2), round(w.bottom, 2), round(w.angle, 2)]
                      for w in captured['words']],
            # Values written by Odoo, to check that the replay gives the same
            # result.
            'odoo': {
                'total': expense.total_amount_currency,
                'tax': expense.scan_tax_amount,
                'date': expense.date and expense.date.isoformat(),
                'merchant': expense.expense_scan_merchant or None,
                'category': _code(expense.product_id),
            },
        })
