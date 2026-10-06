# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Activity of French establishments, from the INSEE Sirene database.

Many French receipts print the merchant's SIRET number, rarely its activity
code (APE/NAF). The Sirene database gives the main activity of every
establishment. Only part of it is kept: active establishments whose activity
matches an expense family (hotels, restaurants, fuel stations, car parks,
etc.), a few hundred thousand rows instead of tens of millions.

The database is published monthly on data.gouv.fr under an open licence.
The import is run by hand or by a system scheduled task, outside Odoo: it
takes several minutes, more than an Odoo worker allows. A scan only looks
the number up in the table, without network access.
"""
import csv
import io
import logging
import os
import zipfile
from collections import Counter

from odoo import api, fields, models, tools

from ..ocr import lexicon

_logger = logging.getLogger(__name__)

#: "StockEtablissement" file (zipped CSV, about 1.2 GB).
SIRENE_URL = ('https://www.data.gouv.fr/api/1/datasets/r/'
              '88fbb6b4-0320-443e-b739-b4376a012c32')
#: Number of rows sent to PostgreSQL at once.
COPY_BATCH = 100000


class ExpenseScanSirene(models.Model):
    _name = 'expense.scan.sirene'
    _description = "Establishment activity (Sirene database)"
    _log_access = False
    _rec_name = 'siret'

    siret = fields.Char(string="SIRET", size=14, required=True, index=True)
    naf = fields.Char(string="Activity (NAF)", size=5, required=True)

    @api.model
    def _expense_scan_activity(self, number):
        """NAF code of a SIRET, or of the most common establishment of a SIREN."""
        if not number or not number.isdigit():
            return False
        records = self.sudo()
        if len(number) == 14:
            found = records.search([('siret', '=', number)], limit=1)
            if found:
                return found.naf
        siren = number[:9]
        if len(siren) != 9:
            return False
        # The SIRET numbers of a company share its first nine digits: a range
        # is enough, and the index walks it directly.
        groups = records._read_group(
            [('siret', '>=', siren + '00000'), ('siret', '<=', siren + '99999')],
            groupby=['naf'], aggregates=['__count'])
        if not groups:
            return False
        counts = Counter({naf: count for naf, count in groups})
        return counts.most_common(1)[0][0]

    @api.model
    def _expense_scan_default_path(self):
        return os.path.join(tools.config['data_dir'], 'expense_scan',
                            'StockEtablissement_utf8.zip')

    @api.model
    def _expense_scan_import(self, path=None):
        """Replace the table with the content of the downloaded Sirene file.

        The archive is streamed, never unzipped on disk. The table is only
        emptied once the whole file has been read: a truncated file leaves
        the previous table in place.
        """
        path = path or self._expense_scan_default_path()
        cr = self.env.cr
        cr.execute("""
            CREATE TEMP TABLE expense_scan_sirene_load (siret varchar(14), naf varchar(5))
            ON COMMIT DROP
        """)
        kept = read = 0
        buffer = io.StringIO()

        def flush():
            buffer.seek(0)
            cr._obj.copy_from(buffer, 'expense_scan_sirene_load', columns=('siret', 'naf'))
            buffer.seek(0)
            buffer.truncate()

        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith('.csv'))
            with archive.open(name) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding='utf-8', newline=''))
                header = next(reader)
                column = {title: position for position, title in enumerate(header)}
                siret_at = column['siret']
                state_at = column['etatAdministratifEtablissement']
                code_at = column['activitePrincipaleEtablissement']
                kind_at = column.get('nomenclatureActivitePrincipaleEtablissement')
                # NAF 2025 is gradually replacing rev. 2: its column is read
                # when present, if the class is in the family table.
                naf25_at = column.get('activitePrincipaleNAF25Etablissement')
                for row in reader:
                    read += 1
                    if row[state_at] != 'A':
                        continue
                    code = ''
                    if kind_at is None or row[kind_at] == 'NAFRev2':
                        code = row[code_at]
                    if naf25_at is not None and not code:
                        code = row[naf25_at]
                    digits = code.replace('.', '')
                    if digits[:4] not in lexicon.NAF_FAMILIES:
                        continue
                    buffer.write('%s\t%s\n' % (row[siret_at], digits[:5]))
                    kept += 1
                    if kept % COPY_BATCH == 0:
                        flush()
                flush()

        cr.execute("DELETE FROM expense_scan_sirene")
        cr.execute("""
            INSERT INTO expense_scan_sirene (siret, naf)
            SELECT siret, naf FROM expense_scan_sirene_load
        """)
        # The table was changed outside the ORM: the cache is invalidated.
        self.env.invalidate_all()
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_str('expense_scan.sirene_rows', kept)
        parameters.set_str('expense_scan.sirene_imported', fields.Datetime.to_string(fields.Datetime.now()))
        _logger.info("Sirene database: %s establishments kept out of %s read", kept, read)
        return kept
