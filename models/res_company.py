# -*- coding: utf-8 -*-
import logging
import os

from odoo import fields, models
from odoo.tools import config

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    expense_scan_enabled = fields.Boolean(
        string="Analyser les tickets à l'import",
        default=True,
        help="Lance l'OCR automatiquement sur chaque justificatif déposé "
             "depuis la liste des notes de frais.",
    )
    expense_scan_engine = fields.Selection(
        selection=[
            ('auto', "Automatique (le meilleur disponible)"),
            ('rapidocr', "RapidOCR / PP-OCR (recommandé)"),
            ('tesseract', "Tesseract"),
        ],
        string="Moteur OCR",
        default='auto',
    )
    expense_scan_threads = fields.Integer(
        string="Threads par worker",
        default=4,
        help="Nombre de threads alloués au calcul OCR dans chaque worker Odoo. "
             "0 laisse le moteur décider, ce qui sature le processeur quand "
             "plusieurs scans arrivent en même temps.",
    )
    expense_scan_tesseract_lang = fields.Char(
        string="Langue Tesseract",
        default='fra',
    )
    expense_scan_autocrop = fields.Boolean(
        string="Recadrer le ticket",
        default=True,
        help="Détecte les bords du ticket dans la photo et corrige la "
             "perspective avant lecture.",
    )
    expense_scan_deskew = fields.Boolean(
        string="Redresser le ticket",
        default=True,
        help="Corrige l'inclinaison résiduelle des lignes de texte.",
    )
    expense_scan_auto_rotate = fields.Boolean(
        string="Corriger les quarts de tour",
        default=True,
        help="Relance une lecture après rotation quand le texte reconnu "
             "semble écrit verticalement. Rallonge le scan concerné.",
    )
    expense_scan_keep_original = fields.Boolean(
        string="Conserver la photo d'origine",
        default=True,
        help="La photo non retouchée reste attachée à la dépense comme "
             "justificatif ; l'image recadrée sert à l'affichage.",
    )
    expense_scan_max_age_days = fields.Integer(
        string="Ancienneté maximale (jours)",
        default=730,
        help="Une date lue au-delà de cette ancienneté est écartée : c'est "
             "presque toujours une date de garantie ou de carte de fidélité.",
    )
    expense_scan_apply_tax = fields.Boolean(
        string="Reporter la TVA lue sur le ticket",
        default=False,
        help="Renseigne le champ « TVA du ticket » avec le montant lu sur le "
             "justificatif. Ce montant prime alors sur celui que produirait "
             "le taux, jusque dans l'écriture comptable — c'est ce qui permet "
             "à un ticket mêlant plusieurs taux de porter sa TVA exacte. Le "
             "taux, lui, reste celui de la catégorie de dépense.",
    )
    expense_scan_reinvoice = fields.Boolean(
        # Le libellé nomme la refacturation, faute de quoi on cherche en vain
        # ce mot-là dans les paramètres : c'est bien ce qu'on vient y activer.
        string="Refacturer les frais sur une mission",
        default=False,
        help="Ajoute un champ « À refacturer » sur les notes de frais et y "
             "propose la mission du salarié en cours à la date du ticket. Le "
             "frais est alors imputé sur l'analytique de la mission et porté "
             "sur sa commande à refacturer, pour ressortir sur la prochaine "
             "facture du projet.\n\n"
             "Désactivé par défaut : toutes les organisations ne travaillent "
             "pas par mission.",
    )
    expense_scan_set_vendor = fields.Boolean(
        string="Rechercher le fournisseur",
        default=False,
        help="Associe le contact dont le nom correspond à l'enseigne lue, "
             "uniquement en cas de correspondance unique.",
    )
    expense_scan_wide_split = fields.Boolean(
        string="Vue scindée dès 992 px",
        default=True,
        help="Odoo n'affiche le justificatif à côté du formulaire qu'à "
             "partir de 1400 px de large. Cette option abaisse le seuil aux "
             "écrans d'ordinateur portable et aux tablettes en paysage.",
    )

    expense_scan_model_dir = fields.Char(
        string="Dossier des modèles OCR",
        help="Où sont téléchargés les modèles de reconnaissance. Laissez vide "
             "pour utiliser un sous-dossier du répertoire de données d'Odoo, "
             "qui est toujours inscriptible par le service.",
    )

    def _expense_scan_model_dir(self):
        """Dossier de modèles, garanti inscriptible.

        RapidOCR télécharge ses modèles à côté de son propre code, dans
        site-packages : un chemin que l'utilisateur système « odoo » n'a
        généralement pas le droit d'écrire. On le renvoie donc par défaut
        dans le répertoire de données d'Odoo.
        """
        self.ensure_one()
        path = self.expense_scan_model_dir or os.path.join(
            config['data_dir'], 'expense_scan_models')
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            _logger.warning("Dossier de modèles OCR inaccessible : %s", path)
            return ''
        return path

    def _expense_scan_engine_options(self):
        """Options passées au moteur, dérivées de la configuration société."""
        self.ensure_one()
        return {
            'threads': self.expense_scan_threads or 0,
            'lang': self.expense_scan_tesseract_lang or 'fra',
            'model_dir': self._expense_scan_model_dir(),
        }
