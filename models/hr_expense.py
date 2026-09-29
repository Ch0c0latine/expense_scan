# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import base64
import json
import logging
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, time as dtime, timedelta

import psycopg2
from pytz import timezone, utc

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo import tools
from odoo.modules import module as odoo_module
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import email_normalize, format_date, formatLang

from ..ocr import engines, parser, preprocess
from .hr_expense_category import CONFIRMED_STATES

_logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_PREFIX = 'image/'
PDF_MIMETYPE = 'application/pdf'
#: Pages supplémentaires converties et lues sur un PDF dont la première ne
#: donne pas de total : une facture de plusieurs pages porte le sien en
#: pied de la dernière. Plafond, comme les autres limites d'entrée, pour
#: borner le temps de traitement.
PDF_MAX_PAGES_READ = 5
#: Écart, en jours, entre deux dépenses d'un même déplacement.
TRIP_DAYS = 3


class Stopwatch:
    """Chronométrage des étapes d'une analyse pour le journal."""

    def __init__(self, on_lap=None):
        self.started = self.last = time.time()
        self.laps = []
        # Appelé à la fin de chaque étape pour avancer la progression affichée.
        self.on_lap = on_lap

    def lap(self, name):
        now = time.time()
        self.laps.append((name, now - self.last))
        self.last = now
        if self.on_lap:
            self.on_lap(name)

    def sum(self, *names):
        return sum(duration for name, duration in self.laps if name in names)

    @property
    def total(self):
        return self.last - self.started

    def __str__(self):
        return " ".join("%s=%.2f" % lap for lap in self.laps) + " total=%.2f" % self.total


class HrExpense(models.Model):
    _inherit = 'hr.expense'
    # Plus récentes en haut, comme Odoo, mais les tickets d'une même
    # journée suivent l'heure imprimée plutôt que l'ordre de saisie. Sans
    # heure connue (saisie à la main), la dépense passe après ceux du jour.
    _order = 'date desc, scan_datetime desc nulls last, id desc'

    scan_state = fields.Selection(
        selection=[
            ('none', "Non analysé"),
            ('done', "Analysé"),
            ('partial', "À vérifier"),
            ('running', "Analyse en cours"),
            ('error', "Échec de l'analyse"),
        ],
        string="Analyse du ticket",
        default='none',
        readonly=True,
        copy=False,
        index=True,
    )
    scan_message = fields.Char(string="Détail de l'analyse", readonly=True, copy=False)
    scan_todo = fields.Char(string="Champs à vérifier", readonly=True, copy=False)
    #: Un code par point listé dans ``scan_todo``, dans le même ordre
    #: (« à refacturer,tax_incompatible »), pour savoir lesquels restent
    #: à traiter une fois la dépense modifiée. ``static`` marque les points
    #: qui ne peuvent pas être rouverts automatiquement (date, catégorie…) :
    #: ils restent à traiter, comme avant l'existence de ce champ.
    expense_scan_todo_codes = fields.Char(readonly=True, copy=False)
    #: Phrase d'aide de chaque point, en JSON : ``{code: phrase}``.
    expense_scan_hints = fields.Text(readonly=True, copy=False)
    #: Valeurs posées par l'analyse, en JSON, pour reconnaître une correction.
    expense_scan_read_values = fields.Text(readonly=True, copy=False)
    # Champs saisis à la main avant l'analyse (liste séparée par des
    # virgules) : une nouvelle analyse ne les remplace pas non plus.
    expense_scan_manual_fields = fields.Char(readonly=True, copy=False)
    expense_scan_hint_date = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_total = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_tax = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_category = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_hint_reinvoice = fields.Char(compute='_compute_expense_scan_field_hints')
    expense_scan_todo_unplaced = fields.Boolean(compute='_compute_expense_scan_field_hints')
    expense_scan_todo_pending = fields.Boolean(
        string="Points à vérifier encore ouverts",
        compute='_compute_expense_scan_todo_pending', store=True,
        help="Faux dès que chaque point listé dans « Champs à vérifier » "
             "est résolu — le bandeau de relecture s'appuie dessus pour "
             "disparaître sans attendre un nouveau scan.",
    )
    scan_engine = fields.Char(string="Moteur", readonly=True, copy=False)
    scan_duration = fields.Float(string="Durée (s)", readonly=True, copy=False)
    scan_score = fields.Float(string="Confiance de lecture", readonly=True, copy=False,
                              help="Score moyen de reconnaissance des caractères, en pourcentage.")
    scan_detected_tax = fields.Char(string="TVA lue sur le ticket", readonly=True, copy=False)
    scan_tax_amount = fields.Monetary(
        string="TVA du ticket",
        currency_field='currency_id',
        copy=False,
        help="Montant de TVA imprimé sur le justificatif. C'est lui qui fait "
             "foi : la TVA de la dépense et celle de l'écriture comptable "
             "prennent cette valeur, quel que soit le taux affiché à côté. "
             "C'est ce qui permet à un ticket mêlant 5,5 %, 10 % et 20 % de "
             "produire exactement sa TVA.\n\n"
             "Zéro veut dire zéro : aucune TVA déductible, ce qui est le cas "
             "d'un ticket de carte bancaire ou d'un justificatif où aucune "
             "TVA n'a pu être lue.\n\n"
             "Le taux ne sert qu'à deux choses : porter les tags fiscaux de "
             "l'écriture, et plafonner ce montant — une TVA supérieure au "
             "taux appliqué au total du ticket est forcément une erreur, et "
             "choisir un taux plus bas rabote le montant d'autant.",
    )
    scan_time = fields.Char(string="Heure du ticket", readonly=True, copy=False)
    scan_datetime = fields.Datetime(
        string="Horodatage du ticket",
        readonly=True,
        copy=False,
        index=True,
        help="Date et heure imprimées sur le justificatif. La date d'une "
             "dépense ne porte pas l'heure : ce champ permet d'ordonner "
             "plusieurs tickets d'une même journée.",
    )
    scan_raw_text = fields.Text(string="Texte reconnu", readonly=True, copy=False)
    scan_original_attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string="Photo d'origine",
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    expense_scan_own = fields.Boolean(
        string="Dépense personnelle",
        compute='_compute_expense_scan_own',
        help="Vrai quand la dépense appartient à l'utilisateur qui la "
             "consulte. Le formulaire s'en sert pour masquer le champ "
             "« Employé », qui n'a d'intérêt que lorsqu'un gestionnaire "
             "saisit pour quelqu'un d'autre.",
    )
    expense_scan_one_payment_method = fields.Boolean(
        string="Mode de paiement unique",
        compute='_compute_expense_scan_one_payment_method',
        help="Vrai quand la société n'a qu'un mode de paiement possible. Le "
             "formulaire masque alors le champ, qu'Odoo renseigne de "
             "lui-même : un choix à une seule option n'est pas un choix.",
    )
    scan_cropped_attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string="Ticket recadré",
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    #: Le justificatif affiché a été retouché à la main (outil « Retouche ») :
    #: l'analyse le lit tel quel, sans recadrage ni redressement automatiques.
    expense_scan_manual_retouch = fields.Boolean(readonly=True, copy=False)
    #: Réglages de la dernière retouche manuelle, en JSON : ``quarter`` (quarts
    #: de tour horaires), ``fine`` (degrés, sens horaire), ``crop`` (cadre
    #: ``[x0, y0, x1, y1]`` en fractions de l'image pivotée). L'éditeur les
    #: réapplique à l'image de départ à sa réouverture.
    expense_scan_retouch_params = fields.Text(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Bandeau de relecture : se ferme quand les champs sont corrigés
    #
    # Sans recalcul, le bandeau reste affiché après correction jusqu'au
    # scan suivant. expense_scan_todo_codes retient un code par point, à
    # côté du texte affiché ; le bandeau est recalculé à chaque changement
    # d'un champ surveillé, y compris avant l'enregistrement.
    # ------------------------------------------------------------------

    #: Un point est résolu quand son champ surveillé prend une valeur qui
    #: lève le doute signalé par le scan. Pour date, total, catégorie et
    #: devise, la valeur doit différer de celle posée par l'analyse (elle
    #: a été corrigée). Les points « static » (anciennes analyses) ne se
    #: résolvent jamais seuls.
    TODO_RESOLVED_WHEN = {
        'reinvoice': lambda expense: expense.reinvoice_mode != 'todo',
        'tax_category': lambda expense: bool(expense.tax_ids),
        # Foreign/none/incompatible : les trois se résolvent en saisissant
        # le montant de TVA du ticket (scan_tax_amount).
        'tax_amount': lambda expense: bool(expense.scan_tax_amount),
        'tax_total': lambda expense: bool(expense.total_amount_currency),
        'date': lambda expense: expense._expense_scan_changed('date'),
        'total': lambda expense: expense._expense_scan_changed('total_amount_currency'),
        'category': lambda expense: expense._expense_scan_changed('product_id'),
        'currency': lambda expense: expense._expense_scan_changed('currency_id'),
    }
    #: Champ concerné par chaque point.
    TODO_FIELDS = {
        'date': 'date',
        'total': 'total_amount_currency',
        'category': 'product_id',
        'currency': 'currency_id',
        'reinvoice': 'reinvoice_mode',
        'tax_amount': 'scan_tax_amount',
    }
    #: Champ sous lequel s'affiche l'indication de chaque point.
    HINT_PLACES = {
        'date': 'date',
        'total': 'total', 'currency': 'total',
        'tax_amount': 'tax', 'tax_total': 'tax', 'tax_category': 'tax',
        'category': 'category',
        'reinvoice': 'reinvoice',
    }
    #: Champs dont l'analyse retient la valeur posée, pour détecter une
    #: correction ultérieure.
    READ_VALUE_FIELDS = ('date', 'total_amount_currency', 'product_id', 'currency_id')

    @api.depends('expense_scan_todo_codes', 'expense_scan_read_values', 'reinvoice_mode',
                 'tax_ids', 'scan_tax_amount', 'total_amount_currency', 'date',
                 'product_id', 'currency_id')
    def _compute_expense_scan_todo_pending(self):
        for expense in self:
            expense.expense_scan_todo_pending = bool(expense._expense_scan_open_codes())

    def _expense_scan_open_codes(self):
        """Codes des points encore à vérifier, dans leur ordre."""
        self.ensure_one()
        codes = [code for code in (self.expense_scan_todo_codes or '').split(',') if code]
        return [code for code in codes
                if not self.TODO_RESOLVED_WHEN.get(code, lambda e: False)(self)]

    def _expense_scan_read_value(self, name):
        """Valeur que l'analyse avait posée sur ce champ, ``None`` sinon."""
        try:
            return json.loads(self.expense_scan_read_values or '{}').get(name)
        except ValueError:
            return None

    def _expense_scan_changed(self, name):
        """Indique si le champ a quitté la valeur posée par l'analyse."""
        read = self._expense_scan_read_value(name)
        if read is None:
            return False
        return read != self._expense_scan_comparable(name)

    def _expense_scan_comparable(self, name):
        """Valeur du champ sous une forme comparable à celle retenue."""
        value = self[name]
        if isinstance(value, models.BaseModel):
            return value.id or False
        if name == 'date':
            return fields.Date.to_string(value) if value else False
        if isinstance(value, float):
            return round(value, 2)
        return value

    @api.depends('expense_scan_hints', 'expense_scan_todo_codes', 'expense_scan_read_values',
                 'reinvoice_mode', 'tax_ids', 'scan_tax_amount', 'total_amount_currency',
                 'date', 'product_id', 'currency_id', 'state')
    def _compute_expense_scan_field_hints(self):
        """Une indication sous chaque champ à vérifier, tant qu'il l'est.

        Remplace le bandeau « vérifiez ces champs » : l'indication est
        affichée là où l'utilisateur corrige et disparaît avec la correction.
        Le formulaire recalcule ces champs à chaque modification, avant
        l'enregistrement.
        """
        for expense in self:
            texts = {}
            if expense.state == 'draft':
                try:
                    hints = json.loads(expense.expense_scan_hints or '{}')
                except ValueError:
                    hints = {}
                for code in expense._expense_scan_open_codes():
                    place = self.HINT_PLACES.get(code)
                    if place and hints.get(code):
                        texts.setdefault(place, []).append(hints[code])
            for place in ('date', 'total', 'tax', 'category', 'reinvoice'):
                expense['expense_scan_hint_%s' % place] = " ".join(texts.get(place, [])) or False
            # Le bandeau ne reste que pour les points sans champ attitré
            # (analyses antérieures aux indications par champ).
            expense.expense_scan_todo_unplaced = any(
                code not in self.HINT_PLACES for code in expense._expense_scan_open_codes())

    # ------------------------------------------------------------------
    # TVA du ticket : le montant prime sur le taux
    #
    # Odoo dérive la TVA du taux appliqué au total. Un ticket de caisse
    # imprime un montant, souvent issu de plusieurs taux mêlés, et c'est ce
    # montant qui doit arriver en comptabilité. Le taux reste sélectionné
    # pour les tags fiscaux, mais ne détermine plus le montant.
    # ------------------------------------------------------------------

    @api.depends('total_amount_currency', 'tax_ids', 'scan_tax_amount')
    def _compute_tax_amount_currency(self):
        super()._compute_tax_amount_currency()
        for expense in self:
            if expense.scan_tax_amount:
                expense.tax_amount_currency = expense.scan_tax_amount
                expense.untaxed_amount_currency = (
                    expense.total_amount_currency - expense.scan_tax_amount)

    @api.depends('total_amount', 'currency_rate', 'tax_ids', 'is_multiple_currency',
                 'scan_tax_amount')
    def _compute_tax_amount(self):
        super()._compute_tax_amount()
        for expense in self:
            if not expense.scan_tax_amount:
                continue
            rate = expense.currency_rate or 1.0
            company_tax = expense.company_currency_id.round(expense.scan_tax_amount / rate)
            expense.tax_amount = company_tax
            expense.untaxed_amount = expense.total_amount - company_tax

    @api.depends('employee_id')
    @api.depends_context('uid')
    def _compute_expense_scan_own(self):
        """Indique si la dépense est celle de l'utilisateur qui la consulte."""
        mine = self.env.user.employee_ids
        for expense in self:
            expense.expense_scan_own = expense.employee_id in mine

    @api.depends('selectable_payment_method_line_ids')
    def _compute_expense_scan_one_payment_method(self):
        """Indique s'il n'y a pas de mode de paiement à choisir (un seul possible).

        Le calcul est fait ici et non dans la vue : l'évaluateur d'expressions
        du client ne connaît pas ``len``.
        """
        for expense in self:
            expense.expense_scan_one_payment_method = (
                len(expense.selectable_payment_method_line_ids) < 2)

    def _expense_scan_max_rate(self):
        """Plus haut taux retenu, ou ``None`` si aucune taxe en pourcentage.

        « Aucune taxe » et « taxe à 0 % » sont deux cas distincts : le
        premier ne permet aucun contrôle, le second impose un plafond nul.
        """
        self.ensure_one()
        rates = self.tax_ids.filtered(
            lambda tax: tax.amount_type == 'percent').mapped('amount')
        return max(rates) if rates else None

    def _expense_scan_tax_ceiling(self):
        """TVA maximale possible : le plus haut taux appliqué au total TTC.

        Un ticket à plusieurs taux porte moins de TVA que si tout était au
        taux le plus élevé. Ce plafond détecte les erreurs de lecture sans
        bloquer une saisie légitime. Il vaut zéro sur une catégorie
        exonérée, ce qui y interdit toute TVA.
        """
        self.ensure_one()
        rate = self._expense_scan_max_rate()
        if rate is None:
            return None
        return self.total_amount_currency * rate / (100.0 + rate)

    @api.onchange('product_id')
    def _onchange_expense_scan_name(self):
        """Une description posée automatiquement suit la catégorie choisie.

        « Ticket du 12/09 » devient « Péage du 12/09 » quand la catégorie
        est Péage. Une description saisie par l'employé n'est pas modifiée.
        """
        for expense in self:
            if expense.date and expense.scan_state != 'none' \
                    and not expense.expense_scan_keep_name \
                    and expense._expense_scan_name_is_automatic():
                expense.name = expense._expense_scan_auto_name(expense.product_id, expense.date)

    @api.onchange('total_amount_currency')
    def _onchange_expense_scan_total(self):
        """Un total revu à la baisse plafonne la TVA qui ne tiendrait plus."""
        for expense in self:
            expense._expense_scan_clamp_tax()

    @api.onchange('tax_ids')
    def _onchange_expense_scan_taxes(self):
        """Choisir un taux plafonne la TVA, ou la remplit si elle est vide.

        Plafonnement : passer la dépense en « 0 % EX » laissait le montant lu
        intact, donc une TVA déductible que le taux retenu ne justifie plus.
        Il ne joue que dans ce sens : remonter le taux n'invente pas de TVA,
        seul le justificatif indique ce qui a été payé.

        Remplissage, uniquement si le champ est vide (ticket sans TVA
        lisible, saisie manuelle) : le total étant TTC, la TVA vaut
        total × taux / (100 + taux), et non total × taux.

        Un montant déjà présent n'est pas remplacé : il vient souvent du
        ticket et reste exact sur plusieurs taux, ce qu'un calcul depuis un
        seul taux fausserait.
        """
        for expense in self:
            if expense._expense_scan_clamp_tax():
                continue
            if expense.scan_tax_amount or not expense.currency_id:
                continue
            # Plusieurs taxes : Odoo sait les cumuler, un seul taux ne le
            # peut pas. Le calcul d'Odoo s'applique alors.
            percent = expense.tax_ids.filtered(lambda tax: tax.amount_type == 'percent')
            if len(percent) != 1 or len(expense.tax_ids) != 1:
                continue
            ceiling = expense._expense_scan_tax_ceiling()
            if ceiling:
                expense.scan_tax_amount = expense.currency_id.round(ceiling)

    def _expense_scan_clamp_tax(self):
        """Ramène la TVA du ticket au plafond du taux si elle le dépasse.

        Renvoie ``True`` quand le montant a été modifié.
        """
        self.ensure_one()
        if not self.scan_tax_amount or not self.currency_id:
            return False
        ceiling = self._expense_scan_tax_ceiling()
        if ceiling is None:
            # Aucune taxe en pourcentage : pas de plafond à appliquer. Le
            # blocage a lieu à la comptabilisation, où l'absence de taux
            # empêche de porter la TVA dans l'écriture.
            return False
        if self.currency_id.compare_amounts(self.scan_tax_amount, ceiling) > 0:
            self.scan_tax_amount = self.currency_id.round(ceiling)
            return True
        return False

    @api.constrains('tax_ids', 'scan_state')
    def _check_expense_scan_single_tax(self):
        """Une dépense scannée ne porte qu'une seule taxe.

        La ventilation qui porte la TVA du ticket dans l'écriture ne gère
        qu'un taux : avec deux taxes, les deux s'appliqueraient à la même
        base et le montant comptabilisé ne serait plus celui du justificatif.

        Un ticket à plusieurs taux se traite avec un seul taux retenu et le
        montant exact saisi à côté.
        """
        for expense in self:
            if expense.scan_state == 'none':
                continue
            if len(expense.tax_ids) > 1:
                raise ValidationError(_(
                    "Une dépense scannée ne peut porter qu'une seule taxe : "
                    "c'est le montant du champ « TVA du ticket » qui fait "
                    "foi, et le taux ne sert qu'à porter les tags fiscaux.\n\n"
                    "Pour un ticket mêlant plusieurs taux, gardez le plus "
                    "élevé et laissez le montant lu sur le justificatif."))

    @api.constrains('scan_tax_amount', 'total_amount_currency', 'tax_ids')
    def _check_scan_tax_amount(self):
        for expense in self:
            if not expense.scan_tax_amount:
                continue
            if expense.scan_tax_amount < 0:
                raise ValidationError(_("La TVA du ticket ne peut pas être négative."))
            ceiling = expense._expense_scan_tax_ceiling()
            if ceiling is None:
                # Aucune taxe retenue : pas de plafond à contrôler. La saisie
                # est acceptée (le montant reste une information du
                # justificatif) ; le blocage a lieu à la validation
                # comptable, où l'absence de taux empêche de porter la TVA.
                continue
            if expense.currency_id.compare_amounts(expense.scan_tax_amount, ceiling) > 0:
                raise ValidationError(_(
                    "TVA du ticket impossible : %(saisi).2f dépasse le maximum "
                    "de %(plafond).2f, qui correspond au taux de %(taux).2f %% "
                    "appliqué à la totalité des %(total).2f du ticket.\n\n"
                    "Choisissez une catégorie de dépense dont le taux couvre "
                    "cette TVA, ou videz le champ « TVA du ticket » si cette "
                    "dépense n'ouvre pas droit à déduction.",
                    saisi=expense.scan_tax_amount, plafond=ceiling,
                    taux=expense._expense_scan_max_rate(),
                    total=expense.total_amount_currency))

    def _prepare_receipts_vals(self):
        """Ventile la base pour que l'écriture porte la TVA du ticket.

        Odoo construit l'écriture en appliquant le taux au total : le montant
        lu sur le justificatif n'y figurerait pas. Retoucher la ligne de taxe
        après coup est fragile (elle est recalculée à chaque modification).
        La base est donc scindée en deux : la part qui produit exactement le
        montant voulu au taux retenu, et le reste hors taxe.

        L'écriture reste équilibrée, la TVA déductible est celle du ticket
        et les tags fiscaux sont posés par le moteur de taxes habituel.
        """
        vals_list = super()._prepare_receipts_vals()
        # _prepare_receipts_vals regroupe par employé et crée une ligne par
        # dépense, dans cet ordre : le même groupement est refait ici pour
        # associer chaque ligne à sa dépense.
        for vals, expenses in zip(vals_list, self.sudo().grouped('employee_id').values()):
            extra_lines = []
            for command, expense in zip(vals.get('line_ids') or [], expenses):
                remainder = expense._expense_scan_split_base(command)
                if remainder is not None:
                    extra_lines.append(remainder)
            if extra_lines:
                vals['line_ids'] = list(vals['line_ids']) + extra_lines
        return vals_list

    def _expense_scan_split_base(self, command):
        """Ajuste la ligne de base et renvoie la ligne du reliquat s'il y en a une."""
        self.ensure_one()
        if not self.scan_tax_amount:
            return None
        rate = self._expense_scan_max_rate()
        if not rate:
            # L'absence de taux est bloquante ici : sans taux, la TVA du
            # ticket ne peut pas entrer dans l'écriture, et comptabiliser
            # sans elle la perdrait sans avertissement.
            raise UserError(_(
                "La dépense « %(nom)s » porte une TVA de ticket de "
                "%(montant).2f, mais aucune taxe à un taux exploitable. "
                "Sélectionnez la taxe correspondante sur la dépense — ou sur "
                "sa catégorie, pour les suivantes — ou videz le champ "
                "« TVA du ticket ».",
                nom=self.name, montant=self.scan_tax_amount))

        line_vals = command[2]
        currency = self.company_currency_id
        # Odoo traite le prix d'une ligne d'écriture portant un expense_id
        # comme TTC (convention des notes de frais, explicite dans
        # hr_expense/models/account_move_line.py). Il faut donc lui passer
        # le TTC de la part taxée et non sa base, sinon la TVA serait
        # retirée une seconde fois.
        taxed_total = currency.round(self.tax_amount * (100.0 + rate) / rate)
        remainder = currency.round(self.total_amount - taxed_total)

        line_vals['quantity'] = 1
        line_vals['price_unit'] = taxed_total
        if currency.is_zero(remainder):
            return None

        untaxed = dict(line_vals)
        untaxed['quantity'] = 1
        untaxed['price_unit'] = remainder
        untaxed['tax_ids'] = [Command.set([])]
        untaxed['tax_tag_ids'] = [Command.set([])]
        untaxed['name'] = _("%s (hors taxe)", line_vals.get('name') or self.name)
        return Command.create(untaxed)

    def _prepare_payments_vals(self):
        """Refuse de comptabiliser une TVA de ticket qui ne peut pas être portée.

        Le chemin « payé par la société » construit ses lignes d'écriture
        avec des soldes explicites, sans passer par la ventilation de base
        ci-dessus. Il comptabiliserait la TVA du taux à la place de celle du
        ticket sans avertir.
        """
        if self.scan_tax_amount:
            raise UserError(_(
                "La TVA du ticket (%(montant).2f) ne peut pas encore être "
                "reportée sur une dépense payée par la société. Repassez la "
                "dépense en « Employé (à rembourser) », ou videz le champ "
                "« TVA du ticket » pour laisser Odoo la calculer depuis le "
                "taux.", montant=self.scan_tax_amount))
        return super()._prepare_payments_vals()

    # ------------------------------------------------------------------
    # Point d'entrée : dépôt d'un justificatif depuis la liste des frais
    # ------------------------------------------------------------------

    @api.model
    def create_expense_from_attachments(self, attachment_ids=None, view_type='list'):
        """Analyse chaque justificatif juste après la création de la dépense.

        Odoo crée déjà une dépense vide par pièce jointe : l'analyse la
        remplit, sans réécrire ce comportement.
        """
        expense_ids = super().create_expense_from_attachments(
            attachment_ids=attachment_ids, view_type=view_type)
        expenses = self.browse(expense_ids)
        company = self.env.company

        # Odoo choisit la catégorie par la référence interne « EXP_GEN »,
        # sinon la première par ordre alphabétique (« Cadeau » par exemple) :
        # renommer cette référence redirige tous les tickets. Le réglage de
        # la société ne dépend d'aucune référence.
        if company.expense_scan_product_id:
            expenses.product_id = company.expense_scan_product_id

        if company.expense_scan_enabled:
            if self.env.context.get('expense_scan_async') and len(expenses) == 1:
                # Un seul ticket, depuis l'interface : la fiche s'ouvre
                # aussitôt et lance l'analyse en affichant sa progression
                # (action_expense_scan_start).
                expenses.write({'scan_state': 'running'})
            else:
                expenses._expense_scan_run()
        return expense_ids

    # ------------------------------------------------------------------
    # Analyse lancée par la fiche, avec sa progression
    # ------------------------------------------------------------------

    #: Délai (minutes) au-delà duquel une analyse restée en attente est
    #: reprise par la tâche planifiée : l'application a pu se fermer avant
    #: de la lancer.
    PENDING_SCAN_MINUTES = 3
    #: Délai (minutes) au-delà duquel l'analyse est abandonnée. Un
    #: justificatif qui fait tuer son worker (mémoire, processeur) le ferait
    #: sinon tuer à chaque tentative : la fiche relance l'analyse à chaque
    #: ouverture, la tâche planifiée toutes les cinq minutes. Limite de
    #: durée plutôt que nombre d'essais : Odoo rejoue lui-même une requête
    #: en conflit, ce qui fausserait un compteur, et le compteur devrait
    #: être écrit hors de la transaction.
    EXPIRE_SCAN_MINUTES = 15

    def _expense_scan_expire(self):
        """Passe en erreur les analyses en cours depuis trop longtemps.

        Renvoie les dépenses concernées.
        """
        limit = fields.Datetime.subtract(fields.Datetime.now(), minutes=self.EXPIRE_SCAN_MINUTES)
        stale = self.filtered(lambda e: e.scan_state == 'running' and e.create_date < limit)
        stale.sudo().write({
            'scan_state': 'error',
            'scan_message': _("L'analyse de ce justificatif n'a pas abouti. "
                              "Saisissez la dépense à la main."),
            'scan_todo': False,
            'expense_scan_todo_codes': False,
            'expense_scan_hints': False,
        })
        return stale

    def action_expense_scan_start(self, specification=None):
        """Analyse le ticket que la fiche vient d'ouvrir, étape par étape.

        Chaque étape est annoncée à l'utilisateur avec les premières valeurs
        lues, pour que la fiche se remplisse pendant l'analyse. Renvoie les
        valeurs finales au format de ``web_read`` (``specification`` : les
        champs de la fiche) ; la fiche les applique sans écraser les
        modifications de l'utilisateur.

        Le verrou de ligne garantit une seule analyse à la fois. Il retient
        aussi l'enregistrement de la fiche jusqu'à la fin de l'analyse : les
        corrections de l'utilisateur passent donc après elle.
        """
        self.ensure_one()
        # Droits vérifiés d'abord : le verrou de ligne est posé en SQL, sans
        # contrôle d'accès de l'ORM.
        self.check_access('write')
        if self._expense_scan_expire():
            return {'started': False, 'values': self._expense_scan_web_values(specification)}
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM hr_expense WHERE id = %s FOR UPDATE NOWAIT", [self.id])
        except psycopg2.errors.LockNotAvailable:
            return {'started': False, 'busy': True}
        self.invalidate_recordset(['scan_state'])
        if self.scan_state != 'running':
            return {'started': False, 'values': self._expense_scan_web_values(specification)}
        self._expense_scan_run(force=True, progress=self._expense_scan_progress_sender())
        return {'started': True, 'values': self._expense_scan_web_values(specification)}

    def _expense_scan_web_values(self, specification):
        """Valeurs de la fiche, au format qu'elle attend."""
        if not specification:
            return {}
        return self.web_read(specification)[0]

    def _expense_scan_progress_sender(self):
        """Fonction qui annonce une étape de l'analyse à l'utilisateur.

        Elle utilise un curseur à part, validé aussitôt : une notification
        émise dans la transaction de l'analyse n'arriverait qu'à la fin,
        toutes étapes confondues.
        """
        registry, uid = self.env.registry, self.env.uid
        partner_id, expense_id = self.env.user.partner_id.id, self.id

        def send(step, values=None):
            if odoo_module.current_test:
                return  # un test ne valide rien hors de sa transaction
            try:
                with registry.cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    env['bus.bus']._sendone(
                        env['res.partner'].browse(partner_id), 'expense_scan/progress',
                        {'expense_id': expense_id, 'step': step, 'values': values or {}})
            except Exception:  # noqa: BLE001 (la progression n'est qu'un confort)
                _logger.debug("Progression de l'analyse non envoyée", exc_info=True)
        return send

    def _expense_scan_preview(self, result):
        """Premières valeurs lues, affichées avant la fin de l'analyse."""
        values = {}
        if result.value('date'):
            values['date'] = fields.Date.to_string(result.value('date'))
        if result.value('total'):
            values['total_amount_currency'] = result.value('total')
        if result.value('merchant'):
            values['expense_scan_merchant'] = result.value('merchant')
        return values

    @api.model
    def _cron_expense_scan_pending(self):
        """Reprend les analyses restées en attente (fiche fermée avant la fin)."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), minutes=self.PENDING_SCAN_MINUTES)
        running = self.search([('scan_state', '=', 'running')])
        running -= running._expense_scan_expire()
        pending = running.filtered(lambda e: e.write_date < limit)
        for expense in pending:
            expense._expense_scan_run(force=True)
            if not odoo_module.current_test:
                self.env.cr.commit()  # une analyse faite ne se perd pas avec la suivante

    @api.model
    def _cron_expense_scan_purge_texts(self, batch=1000):
        """Efface le texte lu des dépenses soumises depuis assez longtemps.

        Le texte d'un justificatif contient des données personnelles (noms,
        adresses, fin de numéro de carte) inutiles une fois la dépense
        soumise : il ne sert qu'à l'analyse et au rattachement des
        déplacements voisins. La durée est un réglage de la société.
        """
        for company in self.env['res.company'].search([]):
            days = company.expense_scan_text_retention_days
            if days <= 0:
                continue
            limit = fields.Date.subtract(fields.Date.context_today(self), days=days)
            old = self.sudo().search([
                ('company_id', '=', company.id),
                ('scan_raw_text', '!=', False),
                ('state', 'in', CONFIRMED_STATES),
                ('date', '<', limit),
            ], limit=batch)
            old.with_context(tracking_disable=True).write({'scan_raw_text': False})
            if not odoo_module.current_test:
                self.env.cr.commit()

    def _get_employee_from_email(self, email_address):
        """Reconnaît aussi l'expéditeur à son adresse privée.

        Odoo ne regarde que l'e-mail professionnel et celui du compte
        utilisateur. Un salarié qui transfère un justificatif depuis son
        téléphone personnel n'est pas reconnu : Odoo crée une dépense sans
        employé, à rattraper à la main, sans avertissement.

        L'adresse privée de la fiche employé couvre ce cas et existe déjà.
        Un champ supplémentaire sur ``hr.employee`` serait inconnu du profil
        public des employés, qui refuserait alors toute lecture aux
        utilisateurs non-RH.

        Lecture en droits élevés : l'adresse privée est réservée au groupe
        RH, et l'appelant est la passerelle de messagerie.
        """
        employee = super()._get_employee_from_email(email_address)
        if employee:
            return employee

        normalized = email_normalize(email_address)
        if not normalized:
            return employee

        Employee = self.env['hr.employee'].sudo()
        if 'private_email' not in Employee._fields:
            return employee

        # Le « ilike » ne fait que restreindre la recherche ; la comparaison
        # décisive porte sur l'adresse entière, normalisée des deux côtés
        # (un fragment n'identifie personne).
        for candidate in Employee.search([('private_email', 'ilike', normalized)]):
            if email_normalize(candidate.private_email) == normalized:
                return candidate
        return employee

    def _alias_get_error(self, message, message_dict, alias):
        """Laisse passer l'adresse privée jusqu'à l'alias « employés ».

        Le filtre du module RH, appliqué avant toute création, ne connaît
        que l'e-mail professionnel et celui du compte : un envoi depuis
        l'adresse privée était refusé avant d'atteindre la reconnaissance
        ci-dessus.
        """
        error = super()._alias_get_error(message, message_dict, alias)
        if error and alias.alias_contact == 'employees':
            email_from = tools.mail.decode_message_header(message, 'From')
            if self._get_employee_from_email(email_normalize(email_from, strict=False)):
                return False
        return error

    # ------------------------------------------------------------------
    # Retouche manuelle
    # ------------------------------------------------------------------

    def _expense_scan_retouch_source(self):
        """Pièce de départ de la retouche : la photo d'origine, sinon la pièce affichée."""
        self.ensure_one()
        source = self.scan_original_attachment_id or self.message_main_attachment_id
        if not source:
            raise UserError(_("Aucun justificatif à retoucher."))
        return source

    def _expense_scan_retouch_image(self):
        """Image de départ de la retouche : ``(octets, type MIME)``.

        Un PDF est rendu en image (première page), comme pour l'analyse.
        """
        source = self._expense_scan_retouch_source()
        if not self._expense_scan_is_pdf(source):
            return source.raw, source.mimetype or 'image/jpeg'
        data = preprocess.pdf_first_page_to_image_bytes(source.raw)
        if not data:
            raise UserError(_(
                "Conversion du PDF impossible. Installez « pdf2image » et "
                "le paquet système « poppler-utils » pour retoucher des PDF."))
        return data, 'image/png'

    def expense_scan_retouch_data(self):
        """Image de départ et réglages de la dernière retouche, pour l'éditeur.

        Une image est servie par son adresse ; un PDF, rendu à la volée, est
        renvoyé en data URL.
        """
        self.ensure_one()
        self.check_access('read')
        source = self._expense_scan_retouch_source()
        if self._expense_scan_is_pdf(source):
            data, mimetype = self._expense_scan_retouch_image()
            url = 'data:%s;base64,%s' % (mimetype, base64.b64encode(data).decode())
        else:
            url = '/web/image/%d' % source.id
        try:
            params = json.loads(self.expense_scan_retouch_params or 'null')
        except ValueError:
            params = None
        return {'url': url, 'params': params}

    def expense_scan_auto_retouch_params(self):
        """Réglages que proposerait la retouche automatique, pour l'éditeur.

        Calculés sur l'image de départ de l'éditeur, d'après les boîtes de
        l'OCR : quart de tour et redressement comme dans l'analyse, cadre
        autour du texte comme ``preprocess.crop_to_text``. La correction de
        perspective de l'analyse n'a pas d'équivalent dans l'éditeur
        (rotation et rectangle) : le cadre proposé en est une approximation.
        """
        self.ensure_one()
        self.check_access('read')
        ok, message = preprocess.dependencies_status()
        if not ok:
            raise UserError(message)
        company = self.company_id or self.env.company
        data, _mimetype = self._expense_scan_retouch_image()
        image = preprocess.load_image(data)
        engine = engines.resolve_engine(
            company.expense_scan_engine, **company._expense_scan_engine_options())
        words = [word for word in engine.recognize(image) if word.text.strip()]
        height, width = image.shape[:2]
        quarters = 0
        if words:
            quarters = self._expense_scan_quarters(
                words, image,
                decide_180=not self._expense_scan_is_pdf(self._expense_scan_retouch_source()))
        turned = preprocess.rotate_words_quarters(words, quarters, width, height)
        # Même convention que l'analyse : preprocess.rotate tourne dans le
        # sens antihoraire, l'éditeur dans le sens horaire.
        skew = preprocess.skew_angle_from_words(turned)
        if not preprocess.MIN_DESKEW_ANGLE < abs(skew) <= preprocess.MAX_TEXT_DESKEW_ANGLE:
            skew = 0.0
        fine = max(-45.0, min(45.0, round(-skew * 2) / 2))
        return {
            'quarter': quarters,
            'fine': fine,
            'crop': self._expense_scan_text_frame(words, width, height, quarters * 90 + fine),
        }

    @staticmethod
    def _expense_scan_text_frame(words, width, height, angle, margin_ratio=0.035, min_score=0.5):
        """Cadre du texte dans l'image pivotée de ``angle`` degrés (sens horaire).

        L'image pivotée est placée comme dans l'éditeur : rotation autour du
        centre, dans le rectangle qui la contient entière. Renvoie le cadre
        en fractions de ce rectangle ; l'image entière faute de texte.
        """
        kept = preprocess.text_inliers([word for word in words if word.score >= min_score])
        if len(kept) < 3:
            return [0.0, 0.0, 1.0, 1.0]
        rad = math.radians(angle)
        cos, sin = math.cos(rad), math.sin(rad)
        bound_w = width * abs(cos) + height * abs(sin)
        bound_h = width * abs(sin) + height * abs(cos)
        xs, ys = [], []
        for word in kept:
            for x, y in ((word.left, word.top), (word.right, word.top),
                         (word.right, word.bottom), (word.left, word.bottom)):
                dx, dy = x - width / 2.0, y - height / 2.0
                xs.append(dx * cos - dy * sin + bound_w / 2.0)
                ys.append(dx * sin + dy * cos + bound_h / 2.0)
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        margin_x = (right - left) * margin_ratio + 8
        margin_y = (bottom - top) * margin_ratio + 8
        return [
            max(left - margin_x, 0.0) / bound_w,
            max(top - margin_y, 0.0) / bound_h,
            min(right + margin_x, bound_w) / bound_w,
            min(bottom + margin_y, bound_h) / bound_h,
        ]

    @staticmethod
    def _expense_scan_clean_retouch_params(params):
        """Réglages de retouche reçus du navigateur, vérifiés, en JSON ; False sinon."""
        try:
            crop = [min(max(float(value), 0.0), 1.0) for value in params['crop']]
            if len(crop) != 4 or crop[0] >= crop[2] or crop[1] >= crop[3]:
                return False
            return json.dumps({
                'quarter': int(params['quarter']) % 4,
                'fine': min(max(float(params['fine']), -45.0), 45.0),
                'crop': crop,
            })
        except (KeyError, TypeError, ValueError):
            return False

    def action_expense_scan_retouch(self, image_base64, params=None):
        """Remplace le justificatif affiché par une image retouchée à la main.

        La retouche (rotation, recadrage) est faite dans le navigateur, à
        partir de l'image de départ (``expense_scan_retouch_data``) ; le
        serveur reçoit le résultat en JPEG, et les réglages (``params``) qui
        seront proposés à la prochaine retouche.
        La photo d'origine n'est jamais modifiée : le résultat remplace
        l'image recadrée, ou devient une nouvelle pièce affichée s'il n'y en
        a pas encore (même disposition que ``_expense_scan_store_image``).

        Ne relance pas l'analyse : le bouton « Relancer l'analyse » le fait,
        et lit alors l'image retouchée sans la retoucher automatiquement.
        """
        self.ensure_one()
        self.check_access('write')
        main = self.message_main_attachment_id
        if not main:
            raise UserError(_("Aucun justificatif à retoucher."))
        try:
            raw = base64.b64decode(image_base64)
        except (ValueError, TypeError) as error:
            raise UserError(_("Image retouchée illisible.")) from error
        if not raw:
            raise UserError(_("Image retouchée illisible."))
        if len(raw) > preprocess.MAX_FILE_BYTES:
            raise UserError(_(
                "L'image retouchée est trop volumineuse (%(size)d Mo, plafond %(max)d Mo).",
                size=len(raw) // (1024 * 1024),
                max=preprocess.MAX_FILE_BYTES // (1024 * 1024)))
        original = self.scan_original_attachment_id or main
        values = {
            'expense_scan_manual_retouch': True,
            'expense_scan_retouch_params': self._expense_scan_clean_retouch_params(params),
        }
        if main != original:
            main.write({'raw': raw, 'mimetype': 'image/jpeg'})
        else:
            stem = os.path.splitext(original.name or 'ticket')[0]
            retouched = self.env['ir.attachment'].create({
                'name': "%s (retouché).jpg" % stem,
                'raw': raw,
                'mimetype': 'image/jpeg',
                'res_model': 'hr.expense',
                'res_id': self.id,
            })
            self.sudo()._message_set_main_attachment_id(retouched, force=True)
            # Comme pour l'image recadrée par l'analyse : l'original devient
            # une pièce jointe de champ, exclue de la liste des justificatifs
            # et de l'écriture comptable, accessible par « Photo d'origine ».
            original.sudo().write({'res_field': 'scan_original_attachment_id'})
            values.update({
                'scan_original_attachment_id': original.id,
                'scan_cropped_attachment_id': retouched.id,
            })
        self.write(values)
        return True

    # ------------------------------------------------------------------
    # Justificatif joint à une dépense neuve
    # ------------------------------------------------------------------

    @api.model
    def expense_scan_receipt_defaults(self):
        """Valeurs qui permettent d'enregistrer une dépense neuve avant d'y joindre un justificatif.

        Le volet de discussion enregistre la fiche avant l'envoi d'un
        fichier ; la description et la catégorie sont obligatoires. Le
        formulaire ne pose ces valeurs que sur les champs encore vides.
        """
        company = self.env.company
        Product = self.env['product.product']
        product = (company.expense_scan_product_id
                   or Product.search([('default_code', '=', 'EXP_GEN'),
                                      ('can_be_expensed', '=', True)], limit=1)
                   or Product.search([('can_be_expensed', '=', True)], limit=1))
        today = format_date(self.env, fields.Date.context_today(self))
        return {
            'name': self._get_untitled_expense_name(today),
            'product_id': {'id': product.id, 'display_name': product.display_name}
            if product else False,
        }

    #: Champs que l'analyse peut remplir et que l'utilisateur a pu saisir
    #: sur la fiche avant d'y joindre le justificatif.
    KEEPABLE_FIELDS = ('date', 'total_amount_currency', 'product_id', 'currency_id',
                       'tax_ids', 'scan_tax_amount', 'vendor_id')

    def expense_scan_analyze_new_receipt(self, changed=None):
        """Analyse le justificatif joint à une dépense enregistrée pour lui.

        Seulement si la dépense n'a jamais été analysée. ``changed`` liste
        les champs modifiés à la main sur la fiche neuve : l'analyse ne les
        remplit pas. La description provisoire et la catégorie par défaut,
        posées pour permettre l'enregistrement, restent à remplir.
        """
        self.ensure_one()
        self.check_access('write')
        if self.scan_state != 'none' \
                or not (self.company_id or self.env.company).expense_scan_enabled:
            return True
        changed = set(changed or ())
        keep = changed & set(self.KEEPABLE_FIELDS)
        if changed & set(self.REINVOICE_FIELDS):
            keep |= set(self.REINVOICE_FIELDS)
        self.with_context(expense_scan_new_receipt=True,
                          expense_scan_keep_fields=sorted(keep))._expense_scan_run()
        return True

    def _expense_scan_kept_fields(self):
        """Champs saisis ou corrigés à la main, que l'analyse ne remplit pas.

        Ceux saisis sur la fiche neuve avant l'envoi du justificatif, et,
        lors d'une nouvelle analyse, ceux saisis ainsi la première fois ou
        corrigés depuis l'analyse précédente.
        """
        keep = set(self.env.context.get('expense_scan_keep_fields') or ())
        keep |= set(filter(None, (self.expense_scan_manual_fields or '').split(',')))
        keep |= {name for name in self.READ_VALUE_FIELDS if self._expense_scan_changed(name)}
        return keep & set(self.KEEPABLE_FIELDS + self.REINVOICE_FIELDS)

    def action_expense_scan_rescan(self):
        """Relance l'analyse sur le ou les justificatifs courants.

        L'analyse ne repart pas de la photo d'origine : l'utilisateur a le
        plus souvent retouché ou remplacé le ticket recadré, et repartir de
        l'original perdrait sa correction.

        S'il y a plusieurs pièces, elles passent par la comparaison des
        morceaux : ajouter une seconde photo puis relancer indique que les
        deux vont ensemble.
        """
        for expense in self:
            expense._expense_scan_run_pieces(force=True, from_original=False)
        return True

    def action_expense_scan_done(self):
        """Marque la vérification comme terminée et revient à la liste.

        Terminer vaut relecture : les indications encore affichées sous les
        champs (par exemple une date jugée peu lisible mais exacte) sont
        effacées. Le libellé des points reste, pour mémoire.
        """
        self.filtered(lambda e: e.state == 'draft' and e.expense_scan_todo_codes).write({
            'expense_scan_todo_codes': False,
            'expense_scan_hints': False,
        })
        return self._expense_scan_expense_list()

    def action_expense_scan_drop(self):
        """Supprime la dépense scannée et revient à la liste."""
        action = self._expense_scan_expense_list()
        self.unlink()
        return action

    def _expense_scan_expense_list(self):
        """Action « Mes frais », ou un équivalent si l'écran a changé.

        ``target: main`` réinitialise le fil d'ariane. Sans lui, la fiche
        quittée ou supprimée y reste, avec un lien qui ne mène nulle part.
        """
        action = self.env.ref('hr_expense.hr_expense_actions_my_all',
                              raise_if_not_found=False)
        if action:
            action = action.sudo().read()[0]
        else:
            action = {
                'type': 'ir.actions.act_window',
                'name': _("Mes frais"),
                'res_model': 'hr.expense',
                'view_mode': 'kanban,list,form',
            }
        action['target'] = 'main'
        return action

    # ------------------------------------------------------------------
    # Chaîne de traitement
    # ------------------------------------------------------------------

    def _expense_scan_run(self, force=False, from_original=True, progress=None):
        """Analyse les dépenses sans interrompre l'import.

        Un ticket illisible ou une dépendance manquante ne fait pas échouer
        l'envoi de la photo : l'erreur est enregistrée sur la dépense et
        l'utilisateur saisit les données à la main.
        """
        for expense in self:
            attachment = expense._expense_scan_source_attachment(from_original)
            if not attachment:
                continue
            if not force and expense.scan_state in ('done', 'partial'):
                continue
            try:
                # Le report des valeurs est protégé lui aussi : une contrainte
                # du modèle ne doit pas faire échouer l'envoi de la photo,
                # sur laquelle l'utilisateur n'a aucune prise. Le point de
                # sauvegarde permet d'écrire l'erreur ensuite sur un curseur
                # sain.
                with self.env.cr.savepoint():
                    result = expense._expense_scan_process(attachment, progress=progress)
                    if progress:
                        progress('valeurs', expense._expense_scan_preview(result))
                    expense._expense_scan_apply(result, attachment)
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                # Une autre requête a écrit la même ligne pendant l'analyse
                # (la fiche qui s'ouvre pose par exemple un jeton d'accès sur
                # la photo). Ce n'est pas un échec de l'analyse : Odoo rejoue
                # toute la requête, analyse comprise.
                raise
            except Exception as error:  # noqa: BLE001
                _logger.exception("Analyse du ticket impossible (dépense %s)", expense.id)
                expense.write({
                    'scan_state': 'error',
                    'scan_message': str(error)[:250],
                    'scan_todo': False,
                    'expense_scan_todo_codes': False,
                    'expense_scan_hints': False,
                })

    def _expense_scan_source_attachment(self, from_original=True):
        """Pièce jointe à lire.

        Premier passage : la photo d'origine, qui a toute la résolution ; le
        module la redresse. Relance : le justificatif courant, que
        l'utilisateur a pu retoucher ou remplacer ; relire l'original
        effacerait sa correction.
        """
        self.ensure_one()
        attachment = self.message_main_attachment_id
        if from_original:
            attachment = self.scan_original_attachment_id or attachment
        if not attachment:
            attachment = self.attachment_ids[:1]
        if not attachment or not self._expense_scan_readable(attachment):
            return self.env['ir.attachment']
        return attachment

    @staticmethod
    def _expense_scan_readable(attachment):
        """Indique si la pièce jointe peut être lue par le moteur."""
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        return bool(mimetype.startswith(SUPPORTED_IMAGE_PREFIX)
                    or mimetype == PDF_MIMETYPE or name.endswith('.pdf'))

    @staticmethod
    def _expense_scan_is_pdf(attachment):
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        return mimetype == PDF_MIMETYPE or name.endswith('.pdf')

    def _expense_scan_image_bytes(self, attachment):
        """Octets d'image exploitables, en convertissant le PDF si besoin."""
        self.ensure_one()
        if attachment.file_size and attachment.file_size > preprocess.MAX_FILE_BYTES:
            raise UserError(_(
                "Le justificatif est trop volumineux (%(size)d Mo, plafond %(max)d Mo).",
                size=attachment.file_size // (1024 * 1024),
                max=preprocess.MAX_FILE_BYTES // (1024 * 1024)))
        data = attachment.raw
        if not data:
            raise UserError(_("Le justificatif est vide."))
        if self._expense_scan_is_pdf(attachment):
            converted = preprocess.pdf_first_page_to_image_bytes(data)
            if not converted:
                raise UserError(_(
                    "Conversion du PDF impossible. Installez « pdf2image » et "
                    "le paquet système « poppler-utils » pour scanner des PDF."))
            return converted
        return data

    def _expense_scan_process(self, attachment, progress=None):
        """Pré-traite l'image, la lit, puis en extrait les champs.

        ``progress`` : appelé à la fin de chaque étape avec son nom (voir
        _expense_scan_progress_sender).
        """
        self.ensure_one()
        ok, message = preprocess.dependencies_status()
        if not ok:
            raise UserError(message)

        company = self.company_id or self.env.company
        timer = Stopwatch(on_lap=progress)
        data = self._expense_scan_image_bytes(attachment)
        timer.lap('fichier')

        # Image retouchée à la main : lue telle quelle, sans recadrage,
        # redressement ni rotation automatiques, et sans être remplacée.
        manual = (self.expense_scan_manual_retouch
                  and attachment != self.scan_original_attachment_id)
        image, info = preprocess.prepare(
            data,
            autocrop=company.expense_scan_autocrop and not manual,
            deskew=company.expense_scan_deskew and not manual,
        )
        timer.lap('préparation')

        engine = engines.resolve_engine(
            company.expense_scan_engine, **company._expense_scan_engine_options())

        # Une lecture, puis redressement d'après les boîtes : le moteur
        # redresse chaque boîte avant de la reconnaître (il lit une ligne
        # dans n'importe quel sens) et les boîtes se transposent sans
        # relecture. Une seconde lecture n'a lieu que si elle apporte
        # quelque chose (voir _expense_scan_straighten).
        words = engine.recognize(image)
        timer.lap('lecture')
        reread = False
        if not manual:
            image, words, reread = self._expense_scan_straighten(
                engine, image, words, info, company)
        if reread:
            words = engine.recognize(image)
            timer.lap('relecture')
        else:
            timer.lap('redressement')
        duration = timer.sum('lecture', 'relecture')

        # Décision finale sur l'orientation, d'après les boîtes de la lecture
        # définitive : plus nombreuses et mieux placées que celles de l'essai,
        # elles corrigent un quart de tour mal choisi.
        #
        # Exception : le sens haut/bas (180°) d'un PDF. Contrairement à une
        # photo, dont le téléphone n'indique pas toujours le sens, une page
        # PDF est rendue telle que le document la déclare (vérifié). Décider
        # quand même, sur un texte dense où l'OCR varie d'une lecture à
        # l'autre, retournerait parfois une page qui n'en a pas besoin.
        if company.expense_scan_auto_rotate and not manual:
            words, image = self._expense_scan_reorient(
                words, image, info, decide_180=not self._expense_scan_is_pdf(attachment))

        # Second passage de mise en forme, guidé par le texte reconnu. La
        # détection de contours avant OCR échoue sur un ticket clair posé sur
        # un fond clair ; la position du texte est maintenant connue.
        # L'OCR n'est pas relancé : la lecture est faite, il s'agit de
        # produire l'image que l'utilisateur verra pour vérifier les champs.
        if not manual:
            image = self._expense_scan_tighten(image, words, info, company)
        timer.lap('recadrage')

        parse_kwargs = dict(
            max_age_days=company.expense_scan_max_age_days or 730,
            default_currency=company.currency_id.name or 'EUR',
            buyers=[name for name in (self.employee_id.name, company.name) if name],
        )
        result = parser.parse(words, **parse_kwargs)
        if self._expense_scan_is_pdf(attachment) and parser.fields_to_check(result, names=('total',)):
            # Repli : la plupart des PDF donnent leur total dès la première
            # page, seule lue jusqu'ici. Une facture de plusieurs pages le
            # porte parfois en pied de la dernière.
            words = self._expense_scan_extra_pdf_pages(attachment, engine, words, timer)
            result = parser.parse(words, **parse_kwargs)
        result.engine = getattr(engine, 'description', engine.label)
        result.duration = duration
        result.preprocess = info
        if (info.changed or info.rotated_quarters) and not manual:
            result.image_bytes = preprocess.encode_jpeg(image)
        timer.lap('analyse')
        result.timer = timer
        return result

    def _expense_scan_extra_pdf_pages(self, attachment, engine, words, timer):
        """Complète les mots lus avec ceux des pages suivantes d'un PDF.

        Appelée quand la première page ne donne pas de total : une facture
        de plusieurs pages porte souvent le sien en pied de la dernière. Les
        pages ajoutées ne reçoivent pas les traitements réservés à l'image
        affichée (redressement, recadrage) : ce ne sont pas des photos à
        main levée, et elles ne servent qu'à l'analyse, pas à l'aperçu.
        """
        self.ensure_one()
        data = attachment.raw
        count = preprocess.pdf_page_count(data)
        if count <= 1:
            return words
        # Grand décalage vertical par page : les mots de deux pages ne
        # doivent pas se mêler à une ligne de l'autre.
        step = max((word.bottom for word in words), default=0.0) + 1000.0
        combined = list(words)
        for page in range(2, min(count, PDF_MAX_PAGES_READ) + 1):
            page_bytes = preprocess.pdf_page_to_image_bytes(data, page)
            if not page_bytes:
                continue
            try:
                page_image = preprocess.load_image(page_bytes)
            except Exception:  # noqa: BLE001 (une page illisible n'arrête pas les autres)
                _logger.warning("Page %s du PDF illisible", page, exc_info=True)
                continue
            page_words = engine.recognize(page_image)
            combined.extend(preprocess.shift_words(page_words, step * (page - 1)))
        timer.lap('pages suivantes')
        return combined

    #: Hauteur médiane des mots (pixels de travail du moteur) en dessous de
    #: laquelle la lecture se dégrade : ticket photographié de loin, page A4
    #: entière à texte dense.
    SMALL_TEXT_PX = 20
    #: Agrandissement minimal du texte qui justifie de relire le ticket
    #: recadré : en dessous, la seconde lecture coûte autant que la
    #: première pour un gain négligeable.
    REREAD_MIN_GAIN = 1.3
    #: Inclinaison (degrés) au-delà de laquelle l'image redressée est
    #: relue : les boîtes de la première lecture sont droites et, sur un
    #: texte penché, elles se chevauchent d'une ligne à l'autre, ce qui
    #: brouille leur regroupement.
    REREAD_ANGLE = 4.0

    def _expense_scan_straighten(self, engine, image, words, info, company):
        """Redresse le ticket d'après les boîtes de la première lecture.

        Toute orientation se ramène à un quart de tour plus un résidu dans
        ±45°. Le quart de tour se déduit de la **forme des boîtes** et non de
        la qualité de lecture : le moteur redresse chaque boîte avant de la
        reconnaître, donc il lit dans les quatre sens. L'image et les boîtes
        tournent ensemble, sans relecture.

        Le sens haut/bas (180°) n'est pas tranché ici (voir
        _expense_scan_quarters) : il l'est une seule fois, ensuite.

        Renvoie ``(image, mots, relire)``. La relecture n'a lieu que dans
        deux cas : un ticket nettement penché, ou un texte trop petit dans la
        photo entière que le recadrage agrandirait. Le moteur ramenant
        l'image à sa taille de travail, ce budget est mieux employé sur le
        ticket que sur la table. Tout le reste se lit correctement du
        premier coup : l'ancienne lecture d'essai doublait le temps
        d'analyse pour rien.
        """
        if not company.expense_scan_auto_rotate:
            return image, words, False

        quarters = self._expense_scan_quarters(words, image, decide_180=False)
        if quarters:
            words = preprocess.rotate_words_quarters(
                words, quarters, image.shape[1], image.shape[0])
            image = preprocess.rotate_quarters(image, quarters)
            info.rotated_quarters = quarters

        angle = 0.0
        if company.expense_scan_deskew:
            angle = preprocess.skew_angle_from_words(words)
            if preprocess.MIN_DESKEW_ANGLE < abs(angle) <= preprocess.MAX_TEXT_DESKEW_ANGLE:
                matrix, _size = preprocess.rotation_matrix(
                    (image.shape[1], image.shape[0]), angle)
                words = preprocess.rotate_words(words, matrix, angle)
                image = preprocess.rotate(image, angle)
                info.deskew_angle = angle
            else:
                angle = 0.0

        # Un ticket tourné est toujours relu : le moteur lit mal une ligne
        # verticale (« aiement P » pour « Paiement »), même quand il en
        # trouve bien la position. Les boîtes suffisent à choisir le quart
        # de tour, pas à lire le texte.
        reread = bool(quarters) or abs(angle) > self.REREAD_ANGLE
        if company.expense_scan_autocrop:
            # Sur les boîtes retenues pour l'angle : un caractère du décor
            # étirerait le cadre bien au-delà du ticket.
            inliers = preprocess.text_inliers(words)
            small = self._expense_scan_text_px(engine, image, words) < self.SMALL_TEXT_PX
            if reread or (small and self._expense_scan_crop_gain(
                    engine, image, inliers) >= self.REREAD_MIN_GAIN):
                # Marge large : un mot manqué en bord de ticket reviendra
                # à la relecture.
                image, cropped = preprocess.crop_to_text(image, inliers, margin_ratio=0.08)
                info.cropped = info.cropped or cropped
                reread = True

        info.changed = info.changed or bool(
            info.rotated_quarters or info.deskew_angle or info.cropped)
        info.reread = reread
        return image, words, reread

    @staticmethod
    def _expense_scan_working_scale(engine, width, height):
        """Facteur de réduction que le moteur applique à cette image."""
        side = engine.working_side
        longest = max(width, height)
        return min(1.0, side / float(longest)) if side and longest else 1.0

    def _expense_scan_text_px(self, engine, image, words):
        """Hauteur médiane des mots, telle que le moteur les a vus."""
        heights = sorted(word.bottom - word.top for word in words if word.text.strip())
        if not heights:
            return 0.0
        scale = self._expense_scan_working_scale(engine, image.shape[1], image.shape[0])
        return heights[len(heights) // 2] * scale

    def _expense_scan_crop_gain(self, engine, image, words):
        """Agrandissement du texte qu'apporterait une relecture recadrée."""
        boxes = [word for word in words if word.text.strip()]
        if not boxes:
            return 1.0
        width = max(word.right for word in boxes) - min(word.left for word in boxes)
        height = max(word.bottom for word in boxes) - min(word.top for word in boxes)
        before = self._expense_scan_working_scale(engine, image.shape[1], image.shape[0])
        after = self._expense_scan_working_scale(engine, width * 1.16, height * 1.16)
        return after / before if before else 1.0

    def _expense_scan_reorient(self, words, image, info, decide_180=True):
        """Applique le quart de tour manquant, sans relire l'image.

        Seul endroit qui décide du sens haut/bas (``decide_180``) : sur les
        boîtes de la lecture définitive, bien plus nombreuses et mieux
        placées que celles de l'essai. Une seule décision, non recroisée
        avec une décision antérieure (voir _expense_scan_quarters).
        """
        quarters = self._expense_scan_quarters(words, image, decide_180=decide_180)
        if not quarters:
            return words, image
        height, width = image.shape[:2]
        info.rotated_quarters = (info.rotated_quarters + quarters) % 4
        info.changed = True
        return (preprocess.rotate_words_quarters(words, quarters, width, height),
                preprocess.rotate_quarters(image, quarters))

    def _expense_scan_quarters(self, words, image, decide_180=True):
        """Quart de tour à appliquer, déduit des seules formes de boîtes.

        Deux critères, dans cet ordre. **Direction des lignes** d'abord,
        fournie par le détecteur pour chaque boîte : elle sépare les quarts
        pairs des impairs, un ticket debout n'ayant pas la même direction
        qu'un ticket couché. **Sens de lecture** ensuite, pour départager
        l'endroit de l'envers : un montant suit son libellé, l'enseigne est
        en haut, le règlement en bas.

        L'image n'est pas relue : les boîtes se transposent et leur texte
        est déjà juste, le moteur redressant chaque boîte avant de la lire.
        La qualité de reconnaissance n'indique rien sur l'orientation ; cette
        méthode a remplacé quatre relectures qui ne tranchaient pas.

        ``decide_180=False`` : ne corrige que le premier critère (debout
        devient couché) et laisse le second à un appel ultérieur, sur de
        meilleures boîtes. Basculer le sens haut/bas n'apporte rien avant la
        lecture définitive (une ligne couchée se lit aussi bien dans les deux
        sens). L'ignorer ici évite qu'une décision prise sur l'essai (basse
        résolution, où une page dense se relit mal) soit « confirmée » par
        une seconde décision sur les mêmes indices, aussi trompeurs une fois
        la page tournée par la première.
        """
        if not words:
            return 0
        height, width = image.shape[:2]
        candidates = []
        for quarters in range(4):
            turned = preprocess.rotate_words_quarters(words, quarters, width, height)
            if preprocess.horizontal_text_score(turned) <= 0:
                continue  # lignes debout : ce n'est pas ce quart-là
            candidates.append((quarters, turned))

        if not decide_180 and any(quarters == 0 for quarters, _turned in candidates):
            # Déjà couché dans son orientation d'origine : rien à faire, le
            # sens haut/bas sera décidé une seule fois, plus tard.
            return 0

        # Sens de lecture de chaque candidat couché, définitif :
        # -1 (envers), 0 (aucun avis), 1 (endroit).
        verdicts = [(quarters, parser.reading_direction(parser.build_lines(turned)))
                    for quarters, turned in candidates]
        chosen = self._expense_scan_pick_quarter(verdicts)
        # Une ligne par scan : suffit à rejouer une décision d'orientation
        # contestée sans relancer le scan en debug.
        _logger.info("expense_scan : orientation (decide_180=%s) candidats=%s retenu=%s",
                     decide_180, verdicts, chosen)
        return chosen

    @api.model
    def _expense_scan_pick_quarter(self, verdicts):
        """Choisit le quart d'après le sens de lecture de chaque candidat.

        ``verdicts`` : ``[(quart, -1|0|1), ...]``, dans l'ordre d'essai
        (0 d'abord). Un avis franc (+1) l'emporte aussitôt. Sinon, le premier
        quart qui n'inspire pas de doute franc (-1) est retenu. Le premier de
        la liste n'est pas pris sans examiner son propre avis : un ticket
        dont le sens n'est lisible qu'à moitié (un péage, par exemple) ne
        doit pas être retourné sur un solde à peine négatif quand
        l'orientation d'origine ne suscite aucun doute.
        """
        for quarters, verdict in verdicts:
            if verdict > 0:
                return quarters
        neutral_or_better = [quarters for quarters, verdict in verdicts if verdict >= 0]
        if neutral_or_better:
            return neutral_or_better[0]
        return verdicts[0][0] if verdicts else 0

    def _expense_scan_tighten(self, image, words, info, company):
        """Redresse puis recadre, une fois l'OCR passé.

        L'ordre compte : la rotation agrandit le cadre pour ne rien rogner,
        donc recadrer avant elle laisse de la marge. Le redressement vient
        d'abord (les boîtes de mots suivent), puis le recadrage sur leur
        nouvelle position.
        """
        if company.expense_scan_deskew:
            # L'inclinaison est mesurée sur les lignes de texte reconnues et
            # non sur l'image : les bords du papier sont des droites plus
            # marquées que l'impression, et rarement parallèles à elle.
            # Angle des boîtes du détecteur d'abord ; la régression sur les
            # mots sert de repli pour un moteur comme Tesseract, qui ne
            # renvoie que des rectangles droits.
            angle = preprocess.skew_angle_from_words(words) \
                or preprocess.skew_angle_from_lines(parser.build_lines(words))
            if preprocess.MIN_DESKEW_ANGLE < abs(angle) <= preprocess.MAX_DESKEW_ANGLE:
                matrix, _size = preprocess.rotation_matrix(
                    (image.shape[1], image.shape[0]), angle)
                image = preprocess.rotate(image, angle)
                words = preprocess.rotate_words(words, matrix, angle)
                info.deskew_angle += angle
                info.changed = True

        if company.expense_scan_autocrop:
            image, tightened = preprocess.crop_to_text(
                image, preprocess.text_inliers(words))
            if tightened:
                info.cropped = True
                info.changed = True

        info.final_size = (image.shape[1], image.shape[0])
        return image

    # ------------------------------------------------------------------
    # Report du résultat sur la dépense
    # ------------------------------------------------------------------

    #: Champs déduits de la mission, réservés aux groupes Ventes et
    #: Analytique. Écrits séparément, en droits élevés.
    REINVOICE_FIELDS = ('project_id', 'reinvoice_mode',
                        'analytic_distribution', 'sale_order_id', 'expense_scan_task_id')

    def _expense_scan_apply(self, result, attachment):
        """Écrit les champs lus et prépare le message de vérification."""
        self.ensure_one()
        company = self.company_id or self.env.company
        foreign = self._expense_scan_foreign_tax(result, company)
        values = self._expense_scan_field_values(result, company, foreign=foreign)
        if result.timer:
            result.timer.lap('catégorie')

        # (texte, code) par point à vérifier. Le code, gardé à part du texte
        # affiché, indique si le point est encore ouvert (voir
        # TODO_RESOLVED_WHEN et _compute_expense_scan_todo_pending).
        # « static » marque les points qui ne peuvent pas être rouverts
        # automatiquement : ils restent affichés jusqu'à une relance de
        # l'analyse, comme avant ce mécanisme.
        #
        # Chaque point porte aussi sa phrase d'aide, affichée sous le champ
        # concerné (voir _compute_expense_scan_field_hints) : (libellé court,
        # code, phrase).
        items = []
        for name, found, missing in (
                ('date', _("Date peu lisible sur le ticket : vérifiez-la."),
                 _("Date introuvable sur le ticket : saisissez-la.")),
                ('total', _("Montant peu lisible sur le ticket : vérifiez-le."),
                 _("Montant introuvable sur le ticket : saisissez-le."))):
            for label in parser.fields_to_check(result, names=(name,)):
                items.append((label, name, missing if result.value(name) is None else found))
        if not values.get('expense_scan_guessed_product_id') \
                and self._expense_scan_category_is_free(company):
            # Rien de sûr sur le ticket : la catégorie par défaut n'est
            # qu'un point de départ.
            items.append((_("Catégorie"), 'category',
                          _("Catégorie non reconnue sur le ticket : choisissez-la.")))
        code = result.value('currency')
        if code and code != company.currency_id.name and not values.get('currency_id') \
                and result.confidence('currency') >= 0.5:
            # Ticket en zlotys, devise inactive : le montant serait compté
            # en euros sans avertissement.
            items.append((_("Devise (%s à activer dans Odoo)", code), 'currency',
                          _("Ticket en %s : cette devise est à activer dans Odoo, "
                            "sans quoi le montant compte en euros.", code)))
        if company.expense_scan_reinvoice and not values.get('project_id'):
            # Aucune mission ne couvre cette date, ou plusieurs : le salarié
            # tranche (y compris pour indiquer que le frais n'est pas
            # refacturable). Résolu dès qu'il choisit.
            items.append((_("À refacturer"), 'reinvoice',
                          _("Aucune mission trouvée pour cette date : "
                            "indiquez si ce frais est à refacturer.")))
        check_category_tax = False
        target = self._expense_scan_target_product(values)
        if company.expense_scan_apply_tax and not self._expense_scan_product_no_vat(target):
            if foreign:
                items.append((_("TVA étrangère (%s), non déduite", foreign), 'tax_amount',
                              _("TVA étrangère (%s) : elle ne se déduit pas en France, "
                                "laissez-la à zéro.", foreign)))
            elif not result.value('tax_amount'):
                items.append((_("TVA (aucune sur le justificatif)"), 'tax_amount',
                              _("Aucune TVA lue sur le ticket : saisissez-la si elle y figure.")))
            elif not (values.get('total_amount_currency') or self.total_amount_currency):
                items.append((_("TVA (%.2f lue, à reporter avec le total)",
                              result.value('tax_amount')), 'tax_total',
                              _("TVA de %.2f lue : saisissez le total pour la reporter.",
                                result.value('tax_amount'))))
            elif not values.get('scan_tax_amount'):
                # Lecture écartée car elle dépasse le plafond du taux : en
                # général un autre montant lu à la place de la TVA.
                items.append((_("TVA (lecture incompatible avec le taux)"), 'tax_amount',
                              _("La TVA lue ne colle pas avec le taux : "
                                "saisissez le montant imprimé sur le ticket.")))
            else:
                # La TVA lue n'est comptabilisable qu'avec une taxe portant
                # des tags fiscaux : vérifié après écriture, la catégorie
                # reconnue pouvant en apporter une.
                check_category_tax = True
        # Un champ saisi à la main avant l'analyse n'est à vérifier que s'il
        # diffère du justificatif.
        keep = self._expense_scan_kept_fields()
        items = self._expense_scan_kept_differences(result, keep) + [
            item for item in items if self.TODO_FIELDS.get(item[1]) not in keep]
        values.update({
            'scan_state': 'partial' if items else 'done',
            'scan_engine': result.engine,
            'scan_duration': result.duration,
            'scan_score': round(result.mean_score * 100.0, 1),
            'scan_raw_text': result.raw_text,
            **self._expense_scan_todo_values(items),
            'scan_message': self._expense_scan_summary(result),
            'scan_detected_tax': self._expense_scan_tax_label(result),
        })
        values.update(self._expense_scan_store_image(result, attachment, company))
        if result.timer:
            result.timer.lap('image')

        # Les champs de mission sont écrits en droits élevés, comme pour leur
        # lecture : ils relèvent des groupes Ventes et Analytique, dont le
        # salarié qui photographie son ticket ne fait pas partie. Le module
        # les déduit (l'utilisateur ne les choisit pas) et ils sont posés sur
        # sa propre dépense, en brouillon.
        reinvoice_values = {name: values.pop(name)
                            for name in self.REINVOICE_FIELDS if name in values}
        self.write(values)
        if reinvoice_values:
            self.sudo().write(reinvoice_values)
        if result.timer:
            result.timer.lap('enregistrement')
        after = {}
        if check_category_tax and not self.tax_ids:
            # Résolu dès qu'une taxe est posée, à la main ou par un
            # changement de catégorie qui en apporte une.
            items.append((_("Taxe (aucune sur la catégorie)"), 'tax_category',
                          _("Cette catégorie n'a pas de taxe : choisissez le taux "
                            "pour que la TVA lue soit déduite.")))
            after.update({'scan_state': 'partial', **self._expense_scan_todo_values(items)})
        # Valeurs posées par l'analyse, pour reconnaître ensuite une correction.
        after['expense_scan_read_values'] = json.dumps({
            name: self._expense_scan_comparable(name) for name in self.READ_VALUE_FIELDS})
        after['expense_scan_manual_fields'] = ','.join(sorted(keep)) or False
        self.write(after)
        if result.timer:
            result.timer.lap('écriture')
            _logger.info("expense_scan : durées dépense=%s %s", self.id, result.timer)

    def _expense_scan_kept_differences(self, result, keep):
        """Points à vérifier : date ou montant saisis qui diffèrent du justificatif."""
        items = []
        date = result.value('date')
        if 'date' in keep and date and date != self.date:
            shown = format_date(self.env, date)
            items.append((_("Date (%s sur le justificatif)", shown), 'date',
                          _("Le justificatif est daté du %s : vérifiez la date saisie.", shown)))
        total = result.value('total')
        if 'total_amount_currency' in keep and total \
                and self.currency_id.compare_amounts(total, self.total_amount_currency):
            shown = formatLang(self.env, total, currency_obj=self.currency_id)
            items.append((_("Montant (%s sur le justificatif)", shown), 'total',
                          _("Le justificatif indique %s : vérifiez le montant saisi.", shown)))
        return items

    @staticmethod
    def _expense_scan_todo_values(items):
        """Champs décrivant les points à vérifier : libellés, codes, phrases."""
        return {
            'scan_todo': ", ".join(text for text, _code, _hint in items) or False,
            'expense_scan_todo_codes': ",".join(code for _text, code, _hint in items) or False,
            'expense_scan_hints': json.dumps({code: hint for _text, code, hint in items})
            if items else False,
        }

    def _expense_scan_foreign_tax(self, result, company):
        """Nom de la taxe si le ticket vient de l'étranger, sinon ``False``.

        La TVA payée hors de France ne se déduit pas sur la déclaration
        française : elle se récupère, le cas échéant, auprès du pays
        concerné. La reporter en TVA déductible fausserait la comptabilité.

        Trois indices : une taxe qui ne s'appelle pas TVA (IVA, MwSt,
        PTU…), une devise étrangère, ou un taux qu'aucune taxe de la société
        ne connaît (par exemple la TVA belge à 21 %, imprimée « TVA »).
        """
        label = result.value('tax_label')
        if not (label or result.value('tax_amount') or result.value('tax_rate_max')):
            return False
        if label and label not in ('TVA', 'VAT'):  # « VAT » : le mot anglais, pas un pays
            return label
        code = result.value('currency')
        if code and company.currency_id and code != company.currency_id.name \
                and result.confidence('currency') >= 0.5:
            return label or _("TVA")
        rate = result.value('tax_rate_max')
        if rate is not None:
            known = self.env['account.tax'].search([
                ('company_id', '=', company.id),
                ('type_tax_use', '=', 'purchase'),
                ('amount_type', '=', 'percent'),
            ]).mapped('amount')
            if known and not any(abs(amount - rate) < 0.01 for amount in known):
                return _("%(label)s %(rate)s %%", label=label or _("TVA"), rate=rate)
        return False

    def _expense_scan_name_is_automatic(self):
        """Indique si la description est encore celle posée par Odoo ou l'analyse.

        Les salariés y inscrivent la mission (« FAI chez Bidule »), qu'une
        relance d'analyse ne doit pas écraser.
        """
        self.ensure_one()
        name = (self.name or '').strip()
        if not name:
            return True
        untitled = self._get_untitled_expense_name('').strip()
        if name.startswith(untitled) \
                or name == (self.product_id.display_name or '') \
                or name == (self.expense_scan_merchant or ''):
            return True
        # « Ticket du 12/09/2026 », « Péage du 12/09/2026 » : le nom d'une
        # catégorie quelconque (elle a pu changer depuis), suivi d'une date.
        pattern = re.escape(self._expense_scan_date_name('XCATEGORYX', 'XDATEX'))
        pattern = pattern.replace('XCATEGORYX', r'(?P<label>.+?)').replace('XDATEX', r'.*\d.*')
        match = re.fullmatch(pattern, name)
        if not match:
            return False
        label = match.group('label').strip()
        if label == _("Ticket"):
            return True
        return bool(self.env['product.product'].sudo().with_context(active_test=False).search_count([
            ('can_be_expensed', '=', True), ('name', '=ilike', label)], limit=1))

    def _expense_scan_home_places(self):
        """Lieux sans lien avec un déplacement : la société, le domicile.

        « GREEN ENGINE … 31790 ST JORY » figure au pied de chaque facture
        adressée à la société : son code postal relierait toutes les
        dépenses.
        """
        self.ensure_one()
        employee = self.employee_id.sudo()
        partners = [self.company_id.partner_id]
        zips = {self.company_id.zip}
        cities = {self.company_id.city}
        if 'private_zip' in employee._fields:
            zips.add(employee.private_zip)
            cities.add(employee.private_city)
        zips.update(partner.zip for partner in partners)
        cities.update(partner.city for partner in partners)
        home_cities = set()
        for city in filter(None, cities):
            home_cities.update(parser.normalize(city).replace("-", " ").split())
        return ({"cp:%s" % zip_code.strip() for zip_code in zips if zip_code},
                {city for city in home_cities if len(city) >= 4})

    def _expense_scan_trip_reason(self, scan_date, lines):
        """Raison d'un déplacement, déjà écrite sur une dépense voisine.

        Toutes les dépenses d'un même déplacement partagent sa raison
        (« Déplacement commercial Mécaprec »). Sont du même déplacement, à
        quelques jours près :

        * une dépense du même jour ;
        * une dépense du même lieu : même code postal, même gare de péage
          (aller et retour), ou ville de l'une citée par l'autre (billet
          « Lille à Bordeaux », hôtel de Bordeaux, péage « Sortie Bordeaux ») ;
        * une journée encadrée par deux dépenses de même raison.

        Chaque dépense qui reprend la raison sert à son tour de relais : un
        long déplacement se couvre de proche en proche. La ville de la
        société et celle du domicile ne relient rien : elles figurent sur
        tous les justificatifs.
        """
        self.ensure_one()
        if not (scan_date and self.employee_id):
            return False
        neighbours = self.sudo().search([
            ('id', '!=', self._origin.id or 0),
            ('employee_id', '=', self.employee_id.id),
            ('date', '>=', scan_date - timedelta(days=TRIP_DAYS)),
            ('date', '<=', scan_date + timedelta(days=TRIP_DAYS)),
            ('approval_state', '!=', 'refused'),
        ])
        neighbours = neighbours.filtered(lambda e: not e._expense_scan_name_is_automatic())
        if not neighbours:
            return False

        def most_common(expenses):
            # Raison la plus fréquente ; à égalité, la plus proche en date.
            counts = Counter(expenses.mapped('name'))
            return max(expenses, key=lambda e: (
                counts[e.name], -abs((e.date - scan_date).days), e.id)).name

        same_day = neighbours.filtered(lambda e: e.date == scan_date)
        if same_day:
            return most_common(same_day)

        home_places, home_cities = self._expense_scan_home_places()
        places, cities = parser.trip_places(lines)
        places -= home_places
        cities -= home_cities

        def same_place(expense):
            other_lines = (expense.scan_raw_text or '').splitlines()
            other_places, other_cities = parser.trip_places(other_lines)
            return bool(
                places & (other_places - home_places)
                or any(parser.cites_city(city, other_lines) for city in cities)
                or any(parser.cites_city(city, lines) for city in other_cities - home_cities))

        linked = neighbours.filtered(same_place)
        if linked:
            return most_common(linked)

        before = set(neighbours.filtered(lambda e: e.date < scan_date).mapped('name'))
        after = set(neighbours.filtered(lambda e: e.date > scan_date).mapped('name'))
        between = neighbours.filtered(lambda e: e.name in before & after)
        return most_common(between) if between else False

    def _expense_scan_date_name(self, label, date_text):
        """« Péage du 12/09/2026 » : la catégorie, puis la date du ticket."""
        return _("%(category)s du %(date)s", category=label, date=date_text)

    def _expense_scan_auto_name(self, product, scan_date):
        """Description posée automatiquement : la catégorie reconnue, sinon « Ticket »."""
        company_default = self.company_id.expense_scan_product_id
        label = product.name if product and product != company_default else _("Ticket")
        return self._expense_scan_date_name(label, format_date(self.env, scan_date))

    def _expense_scan_field_values(self, result, company, foreign=None):
        """Traduit le résultat du parseur en valeurs de champs Odoo."""
        values = {}
        keep = self._expense_scan_kept_fields()
        if foreign is None:
            foreign = self._expense_scan_foreign_tax(result, company)

        # Catégorie d'abord : sa taxe sert de repli au contrôle de la TVA.
        values.update(self._expense_scan_category_values(result, company))
        guessed = self.env['product.product'].browse(
            values.get('expense_scan_guessed_product_id') or [])

        scan_date = result.value('date')
        if scan_date and 'date' not in keep:
            values['date'] = scan_date
        # Date de la dépense : celle saisie à la main prime sur celle du
        # justificatif pour la description et la recherche de mission.
        expense_date = self.date if 'date' in keep and self.date else scan_date

        scan_time = result.value('time')
        if scan_time:
            values['scan_time'] = scan_time.strftime('%H:%M')
        if scan_date:
            values['scan_datetime'] = self._expense_scan_moment(scan_date, scan_time)

        # La description appartient au salarié (la mission, le plus souvent).
        # L'enseigne a son propre champ ; la description ne reçoit au plus que
        # la date du ticket, tant que personne n'y a écrit. Dès que la
        # catégorie n'est plus la catégorie générique de la société, elle
        # nomme la dépense : « Péage du 12/09/2026 ».
        if expense_date and not self.expense_scan_keep_name \
                and self._expense_scan_name_is_automatic():
            # La raison d'un même déplacement, déjà écrite ailleurs, prime sur
            # la description automatique.
            values['name'] = self._expense_scan_trip_reason(
                expense_date, [line.text for line in result.lines]) \
                or self._expense_scan_auto_name(self._expense_scan_target_product(values),
                                                expense_date)

        currency = self._expense_scan_currency(result, company)
        if currency and 'currency_id' not in keep:
            values['currency_id'] = currency.id

        total = result.value('total')
        if total and 'total_amount_currency' not in keep:
            # Champ calculé mais modifiable : point d'entrée prévu par Odoo
            # pour un montant saisi tel quel, TTC.
            values['total_amount_currency'] = total

        no_vat = self._expense_scan_product_no_vat(self._expense_scan_target_product(values))
        if company.expense_scan_apply_tax and (foreign or no_vat):
            # TVA étrangère, ou catégorie sans TVA récupérable (hôtel,
            # transport de personnes) : aucune taxe, aucun montant déductible.
            values['tax_ids'] = [Command.clear()]
            values['scan_tax_amount'] = 0.0
        elif company.expense_scan_apply_tax:
            # Le taux lu sur le ticket prime sur celui de la catégorie : un
            # repas à 10 % ne doit pas être déclaré à 20 % parce que la
            # catégorie générique le prévoit. Condition : une seule taxe
            # correspond à ce taux ; sur un plan comptable chargé, le choix
            # entre biens et services revient au comptable.
            tax = self._expense_scan_tax(result.value('tax_rate'), company)
            if not tax and result.value('tax_rate_max'):
                # Plusieurs taux sur le même ticket (repas à 5,5 % et 10 %) :
                # aucun ne vaut pour la dépense entière, mais une taxe doit
                # être posée. Le plus élevé est retenu (position prudente pour
                # la TVA déductible ; le comptable peut corriger).
                tax = self._expense_scan_tax(result.value('tax_rate_max'), company)
            if tax:
                values['tax_ids'] = [Command.set(tax.ids)]
                effective_rate = tax.amount
            else:
                # Taux introuvable au plan comptable : la dépense garde la
                # taxe de sa catégorie, qui porte l'écriture. Pour juger la
                # TVA lue, le plafond du ticket vaut mieux que celui de la
                # catégorie : un repas à 10 % + 20 % dépasse le plafond d'une
                # catégorie à 10 % sans être erroné.
                category_rates = (guessed.supplier_taxes_id.filtered(
                    lambda t: t.company_id == company and t.amount_type == 'percent'
                ).mapped('amount') if guessed else None)
                effective_rate = (result.value('tax_rate_max')
                                  or (max(category_rates) if category_rates else None)
                                  or self._expense_scan_max_rate())

            tax_amount = result.value('tax_amount')
            total_known = values.get('total_amount_currency') or self.total_amount_currency
            # Une TVA mal lue ne doit pas faire échouer tout le scan sur la
            # contrainte du module : elle est écartée et signalée plutôt
            # qu'écrite sous une valeur inenregistrable.
            if tax_amount and not total_known:
                # Total illisible : aucun plafond ne permet de juger la TVA, et
                # la comparer à un total nul la déclarerait « incompatible ».
                # Rien n'est modifié ; le salarié saisit les deux.
                pass
            elif tax_amount and self._expense_scan_tax_fits(
                    tax_amount, values.get('total_amount_currency'), effective_rate):
                values['scan_tax_amount'] = tax_amount
            else:
                # Aucune TVA exploitable : le justificatif n'en porte pas
                # (ticket de carte bancaire) ou la lecture est incohérente.
                # Dans les deux cas, garder la taxe de la catégorie ferait
                # apparaître une TVA déductible calculée depuis un taux que
                # rien dans le justificatif ne fonde. Pas de TVA lisible :
                # zéro.
                values['tax_ids'] = [Command.clear()]
                values['scan_tax_amount'] = 0.0

        for name in ('tax_ids', 'scan_tax_amount'):
            if name in keep:
                values.pop(name, None)

        if company.expense_scan_reinvoice and not keep & set(self.REINVOICE_FIELDS):
            # La mission est cherchée à la date du ticket et non à celle de
            # la saisie : un frais scanné le lundi peut dater du vendredi, sur
            # une autre mission.
            # « Non » est une décision du salarié : la mission retrouvée ne
            # sert alors qu'au suivi du budget, sans refacturation.
            values.update(self._expense_scan_project_values(
                self._expense_scan_find_project(expense_date),
                reinvoice=self.reinvoice_mode != 'none'))

        if company.expense_scan_set_vendor and 'vendor_id' not in keep:
            # L'enseigne reconnue par l'historique est mieux orthographiée
            # que la lecture brute.
            known = values.get('expense_scan_merchant') \
                if values.get('expense_scan_merchant') != result.value('merchant') else None
            vendor = self._expense_scan_vendor(result, company, merchant=known)
            if vendor:
                values['vendor_id'] = vendor.id

        return values

    def _expense_scan_moment(self, scan_date, scan_time):
        """Horodatage du ticket, converti en UTC comme le stocke Odoo.

        L'heure imprimée est une heure locale : la stocker telle quelle
        décalerait l'affichage de deux heures en été.
        """
        moment = datetime.combine(scan_date, scan_time or dtime(0, 0))
        zone = self.env.user.tz or self.env.context.get('tz')
        if not zone:
            return moment
        try:
            return timezone(zone).localize(moment).astimezone(utc).replace(tzinfo=None)
        except Exception:  # noqa: BLE001 - fuseau inconnu côté serveur
            _logger.warning("Fuseau horaire inutilisable : %s", zone)
            return moment

    def _expense_scan_tax(self, rate, company):
        """Taxe d'achat au taux lu, si une seule correspond."""
        if rate is None:
            return self.env['account.tax']
        taxes = self.env['account.tax'].search([
            ('company_id', '=', company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', rate),
        ], limit=2)
        # Une correspondance ambiguë est pire qu'aucune : elle passerait
        # inaperçue. La taxe de la catégorie est conservée.
        return taxes if len(taxes) == 1 else self.env['account.tax']

    def _expense_scan_tax_fits(self, amount, total=None, rate=None):
        """Indique si la TVA lue tient sous le plafond du taux de la dépense.

        Le total et le taux sont passés en argument : au moment de la
        décision, ni l'un ni l'autre n'est encore écrit sur la dépense, et un
        plafond calculé sur les anciennes valeurs serait faux.
        """
        self.ensure_one()
        if rate is None:
            rate = self._expense_scan_max_rate()
        if rate is None:
            return True  # aucun taux : on bloquera à la validation, pas ici
        total = self.total_amount_currency if total is None else total
        ceiling = total * rate / (100.0 + rate)
        return self.currency_id.compare_amounts(amount, ceiling) <= 0

    def _expense_scan_currency(self, result, company):
        """Devise correspondant au code lu, si elle est active dans Odoo."""
        code = result.value('currency')
        if not code or result.confidence('currency') < 0.5:
            return self.env['res.currency']
        if company.currency_id.name == code:
            return self.env['res.currency']  # déjà la devise par défaut
        return self.env['res.currency'].search([('name', '=', code)], limit=1)

    def _expense_scan_vendor(self, result, company, merchant=None):
        """Contact fournisseur dont le nom correspond à l'enseigne lue."""
        if not merchant:
            merchant = result.value('merchant')
            if not merchant or result.confidence('merchant') < parser.LOW_CONFIDENCE:
                return self.env['res.partner']
        partners = self.env['res.partner'].search([
            ('name', '=ilike', merchant),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], limit=2)
        return partners if len(partners) == 1 else self.env['res.partner']

    def _expense_scan_store_image(self, result, attachment, company):
        """Attache l'image recadrée et la définit comme aperçu principal."""
        self.ensure_one()
        if not result.image_bytes:
            return {}

        if not company.expense_scan_keep_original:
            attachment.write({
                'raw': result.image_bytes,
                'mimetype': 'image/jpeg',
            })
            return {}

        # Relance sur le justificatif courant : l'image lue est déjà celle
        # que le formulaire affiche. Elle est corrigée sur place : en créer
        # une autre en ferait la « photo d'origine » à la place de la vraie,
        # qui serait définitivement perdue.
        if attachment == self.scan_cropped_attachment_id:
            attachment.write({'raw': result.image_bytes, 'mimetype': 'image/jpeg'})
            return {}

        # Une relance produit une nouvelle image : elle remplace la
        # précédente au lieu d'ajouter des pièces jointes à la dépense.
        previous = self.scan_cropped_attachment_id
        if previous and previous != attachment:
            previous.unlink()

        stem = os.path.splitext(attachment.name or 'ticket')[0]
        cropped = self.env['ir.attachment'].create({
            'name': "%s (recadré).jpg" % stem,
            'raw': result.image_bytes,
            'mimetype': 'image/jpeg',
            'res_model': 'hr.expense',
            'res_id': self.id,
        })
        # L'aperçu du formulaire suit la pièce jointe principale : c'est le
        # ticket redressé que l'utilisateur doit voir pour relire les champs,
        # pas la photo de travers.
        self.sudo()._message_set_main_attachment_id(cropped, force=True)

        # La photo d'origine devient une pièce jointe « de champ » : Odoo
        # les exclut de ses recherches, donc elle disparaît de la liste des
        # justificatifs et n'est plus recopiée sur l'écriture comptable. Elle
        # reste accessible en entier par le champ « Photo d'origine », qui la
        # désigne par son identifiant.
        attachment.sudo().write({'res_field': 'scan_original_attachment_id'})
        return {
            'scan_original_attachment_id': attachment.id,
            'scan_cropped_attachment_id': cropped.id,
            'expense_scan_manual_retouch': False,
        }

    # ------------------------------------------------------------------
    # Libellés
    # ------------------------------------------------------------------

    def _expense_scan_summary(self, result):
        """Ligne d'information affichée sous le formulaire."""
        parts = [result.engine or ""]
        parts.append(_("%.1f s", result.duration))
        parts.append(_("confiance %d %%", round(result.mean_score * 100)))
        info = result.preprocess
        if info:
            steps = []
            if info.cropped:
                steps.append(_("recadré"))
            if info.deskew_angle:
                steps.append(_("redressé de %.1f°", info.deskew_angle))
            if info.rotated_quarters:
                steps.append(_("pivoté de %d°", info.rotated_quarters * 90))
            if info.reread:
                steps.append(_("relu"))
            if steps:
                parts.append(", ".join(steps))
        return " · ".join(part for part in parts if part)[:250]

    def _expense_scan_tax_label(self, result):
        """Résumé de la TVA lue, à titre indicatif.

        Un ticket à plusieurs taux n'en fournit aucun pour la dépense, mais
        la mention est nécessaire : sans elle, la TVA du ticket paraîtrait
        incohérente avec le taux affiché sur la fiche.
        """
        rate = result.value('tax_rate')
        amount = result.value('tax_amount')
        if rate is None and result.value('tax_rate_max') is not None:
            rate_text = _("plusieurs taux, jusqu'à %s %%", result.value('tax_rate_max'))
        elif rate is not None:
            rate_text = _("%s %%", rate)
        else:
            rate_text = False
        if not rate_text and amount is None:
            return False
        if rate_text and amount is not None:
            return _("%(rate)s — %(amount).2f", rate=rate_text, amount=amount)
        if rate_text:
            return rate_text
        return _("%.2f", amount)
