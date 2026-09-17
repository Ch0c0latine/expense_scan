# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Activité des établissements français, d'après la base Sirene de l'INSEE.

Beaucoup de tickets impriment le SIRET du commerçant, rarement son code
APE. La base Sirene fait le lien : à chaque établissement, son activité
principale. On n'en garde qu'une petite partie — les établissements actifs
dont l'activité correspond à une famille de frais (hôtels, restaurants,
stations-service, parkings…) —, soit quelques centaines de milliers de
lignes au lieu de plusieurs dizaines de millions.

La base est publiée chaque mois sur data.gouv.fr, sous licence ouverte.
Son import se lance à la main ou par une tâche planifiée du système, hors
d'Odoo : il dure plusieurs minutes, bien au-delà de ce qu'un processus
Odoo tolère. Le scan, lui, ne fait qu'une recherche en base, sans réseau.
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

#: Fichier « StockEtablissement » (CSV compressé, environ 1,2 Go).
SIRENE_URL = ('https://www.data.gouv.fr/api/1/datasets/r/'
              '88fbb6b4-0320-443e-b739-b4376a012c32')
#: Nombre de lignes envoyées à PostgreSQL d'un coup.
COPY_BATCH = 100000


class ExpenseScanSirene(models.Model):
    _name = 'expense.scan.sirene'
    _description = "Activité d'un établissement (base Sirene)"
    _log_access = False
    _rec_name = 'siret'

    siret = fields.Char(string="SIRET", size=14, required=True, index=True)
    naf = fields.Char(string="Activité (NAF)", size=5, required=True)

    @api.model
    def _expense_scan_activity(self, number):
        """Code NAF d'un SIRET, ou de l'établissement le plus courant d'un SIREN."""
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
        # Les SIRET d'une même entreprise partagent ses neuf premiers
        # chiffres : une plage suffit, et l'index la parcourt directement.
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
        """Remplace la table par le contenu du fichier Sirene téléchargé.

        Lecture en flux : l'archive n'est jamais décompressée sur le disque,
        et la table n'est vidée qu'une fois le fichier entièrement lu — un
        fichier tronqué laisse l'ancienne table en place.
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
                # La NAF 2025 remplace peu à peu la rév. 2 : sa colonne est
                # lue quand elle existe, les classes qui nous concernent
                # figurant dans la table des familles.
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
        # La table a changé sous l'ORM : son cache ne vaut plus rien.
        self.env.invalidate_all()
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_param('expense_scan.sirene_rows', str(kept))
        parameters.set_param('expense_scan.sirene_imported', fields.Datetime.to_string(fields.Datetime.now()))
        _logger.info("Base Sirene : %s établissements gardés sur %s lus", kept, read)
        return kept
