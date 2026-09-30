# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Translation files: template export and consistency of the translations.

The template is written to ``/tmp/expense_scan_i18n/expense_scan.pot`` when
that folder exists, as Odoo's export would produce it; translators start
from it.
"""
import ast
import glob
import io
import os
import re

from odoo.tests import common, tagged
from odoo.tools.translate import trans_export

I18N_EXPORT_DIR = '/tmp/expense_scan_i18n'
#: Formatting placeholders: "%s", "%d", "%.2f", "%(name)s". A percent sign followed
#: by a space is text ("20 % of").
PLACEHOLDER_RE = re.compile(r"%(?:\([a-z_]+\))?(?:\.\d+)?[sdf]")


def po_entries(path):
    """``[(msgid, msgstr)]`` of a .po file, header left out."""
    entries = []
    for block in open(path, encoding='utf-8').read().split('\n\n'):
        parts = {'msgid': [], 'msgstr': []}
        current = None
        for line in block.split('\n'):
            if line.startswith(('msgid ', 'msgstr ')):
                current, _sep, line = line.partition(' ')
            if current and line.startswith('"'):
                parts[current].append(ast.literal_eval(line))
        msgid, msgstr = ''.join(parts['msgid']), ''.join(parts['msgstr'])
        if msgid:
            entries.append((msgid, msgstr))
    return entries


@tagged('post_install', '-at_install')
class TestTranslations(common.TransactionCase):

    def test_export_template(self):
        if not os.path.isdir(I18N_EXPORT_DIR):
            self.skipTest("no export folder")
        buffer = io.BytesIO()
        trans_export(False, ['expense_scan'], buffer, 'po', self.env)
        with open(os.path.join(I18N_EXPORT_DIR, 'expense_scan.pot'), 'wb') as handle:
            handle.write(buffer.getvalue())

    def test_translations_keep_the_placeholders(self):
        """A translation must carry the same placeholders as its source text."""
        folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'i18n')
        problems = []
        for path in sorted(glob.glob(os.path.join(folder, '*.po'))):
            for msgid, msgstr in po_entries(path):
                if msgstr and sorted(PLACEHOLDER_RE.findall(msgid)) != sorted(
                        PLACEHOLDER_RE.findall(msgstr)):
                    problems.append("%s: %s" % (os.path.basename(path), msgid[:60]))
        self.assertFalse(problems, "\n".join(problems))

    def test_numbers_follow_the_language(self):
        """"3,03" in French, "3.03" in English, and a rate keeps its own decimals."""
        self.env['res.lang']._activate_lang('fr_FR')
        self.env['res.lang']._activate_lang('en_US')
        expense = self.env['hr.expense']
        french, english = expense.with_context(lang='fr_FR'), expense.with_context(lang='en_US')
        self.assertEqual(french._expense_scan_number(3.03), "3,03")
        self.assertEqual(english._expense_scan_number(3.03), "3.03")
        self.assertEqual(french._expense_scan_rate_text(5.5), "5,5")
        self.assertEqual(french._expense_scan_rate_text(20.0), "20")
        self.assertEqual(english._expense_scan_rate_text(8.875), "8.875")
