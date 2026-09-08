# -*- coding: utf-8 -*-
import logging
import os
import time
from datetime import datetime, time as dtime

from pytz import timezone, utc

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from ..ocr import engines, parser, preprocess

_logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_PREFIX = 'image/'
PDF_MIMETYPE = 'application/pdf'


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    scan_state = fields.Selection(
        selection=[
            ('none', "Non analysé"),
            ('done', "Analysé"),
            ('partial', "À vérifier"),
            ('error', "Échec de l'analyse"),
        ],
        string="Analyse du ticket",
        default='none',
        readonly=True,
        copy=False,
    )
    scan_message = fields.Char(string="Détail de l'analyse", readonly=True, copy=False)
    scan_todo = fields.Char(string="Champs à vérifier", readonly=True, copy=False)
    scan_engine = fields.Char(string="Moteur", readonly=True, copy=False)
    scan_duration = fields.Float(string="Durée (s)", readonly=True, copy=False)
    scan_score = fields.Float(string="Confiance de lecture", readonly=True, copy=False,
                              help="Score moyen de reconnaissance des caractères, en pourcentage.")
    scan_detected_tax = fields.Char(string="TVA lue sur le ticket", readonly=True, copy=False)
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
    scan_cropped_attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string="Ticket recadré",
        readonly=True,
        copy=False,
        ondelete='set null',
    )

    # ------------------------------------------------------------------
    # Point d'entrée : dépôt d'un justificatif depuis la liste des frais
    # ------------------------------------------------------------------

    @api.model
    def create_expense_from_attachments(self, attachment_ids=None, view_type='list'):
        """Analyse chaque justificatif juste après la création de la dépense.

        Odoo crée déjà une dépense vide par pièce jointe : on se greffe
        derrière pour la remplir, plutôt que de réécrire ce comportement.
        """
        expense_ids = super().create_expense_from_attachments(
            attachment_ids=attachment_ids, view_type=view_type)
        if self.env.company.expense_scan_enabled:
            self.browse(expense_ids)._expense_scan_run()
        return expense_ids

    def action_expense_scan_rescan(self):
        """Relance l'analyse, en repartant de la photo d'origine."""
        self._expense_scan_run(force=True)
        return True

    # ------------------------------------------------------------------
    # Chaîne de traitement
    # ------------------------------------------------------------------

    def _expense_scan_run(self, force=False):
        """Analyse les dépenses du recordset, sans jamais interrompre l'import.

        Un ticket illisible ou une dépendance manquante ne doit pas faire
        échouer l'envoi de la photo : l'erreur est enregistrée sur la
        dépense et l'utilisateur saisit à la main.
        """
        for expense in self:
            attachment = expense._expense_scan_source_attachment()
            if not attachment:
                continue
            if not force and expense.scan_state in ('done', 'partial'):
                continue
            try:
                result = expense._expense_scan_process(attachment)
            except Exception as error:  # noqa: BLE001
                _logger.exception("Analyse du ticket impossible (dépense %s)", expense.id)
                expense.write({
                    'scan_state': 'error',
                    'scan_message': str(error)[:250],
                    'scan_todo': False,
                })
                continue
            expense._expense_scan_apply(result, attachment)

    def _expense_scan_source_attachment(self):
        """La pièce jointe à lire : la photo d'origine si on la conserve."""
        self.ensure_one()
        attachment = self.scan_original_attachment_id or self.message_main_attachment_id
        if not attachment:
            attachment = self.attachment_ids[:1]
        if not attachment:
            return self.env['ir.attachment']
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        if mimetype.startswith(SUPPORTED_IMAGE_PREFIX) or mimetype == PDF_MIMETYPE \
                or name.endswith('.pdf'):
            return attachment
        return self.env['ir.attachment']

    def _expense_scan_image_bytes(self, attachment):
        """Octets d'image exploitables, en convertissant le PDF si besoin."""
        self.ensure_one()
        data = attachment.raw
        if not data:
            raise UserError(_("Le justificatif est vide."))
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        if mimetype == PDF_MIMETYPE or name.endswith('.pdf'):
            converted = preprocess.pdf_first_page_to_image_bytes(data)
            if not converted:
                raise UserError(_(
                    "Conversion du PDF impossible. Installez « pdf2image » et "
                    "le paquet système « poppler-utils » pour scanner des PDF."))
            return converted
        return data

    def _expense_scan_process(self, attachment):
        """Pré-traite l'image, la lit, en extrait les champs."""
        self.ensure_one()
        ok, message = preprocess.dependencies_status()
        if not ok:
            raise UserError(message)

        company = self.company_id or self.env.company
        data = self._expense_scan_image_bytes(attachment)

        image, info = preprocess.prepare(
            data,
            autocrop=company.expense_scan_autocrop,
            deskew=company.expense_scan_deskew,
        )

        engine = engines.resolve_engine(
            company.expense_scan_engine, **company._expense_scan_engine_options())

        started = time.time()
        words = engine.recognize(image)
        if company.expense_scan_auto_rotate and preprocess.looks_quarter_turned(words):
            image, words, info.rotated_quarters = self._expense_scan_fix_rotation(
                engine, image, words)
        duration = time.time() - started

        # Second passage de mise en forme, cette fois guidé par le texte
        # reconnu. La détection de contours d'avant-OCR échoue sur un ticket
        # clair posé sur un fond clair ; la position du texte, elle, est
        # désormais connue. On ne relance pas l'OCR pour autant : la lecture
        # est déjà faite, il s'agit d'obtenir l'image que l'utilisateur aura
        # sous les yeux pour vérifier les champs.
        image = self._expense_scan_tighten(image, words, info, company)

        result = parser.parse(
            words,
            max_age_days=company.expense_scan_max_age_days or 730,
            default_currency=company.currency_id.name or 'EUR',
        )
        result.engine = getattr(engine, 'description', engine.label)
        result.duration = duration
        result.preprocess = info
        if info.changed or info.rotated_quarters:
            result.image_bytes = preprocess.encode_jpeg(image)
        return result

    def _expense_scan_fix_rotation(self, engine, image, words):
        """Cherche le quart de tour qui remet le texte à l'horizontale.

        On ne compte surtout pas les boîtes détectées : bien orienté, le
        détecteur fusionne chaque ligne du ticket en une seule boîte, alors
        que couché il en produit une nuée de petites. Compter les boîtes
        désigne donc systématiquement la mauvaise orientation. On mesure à
        la place la quantité de texte reconnu avec confiance dans des boîtes
        horizontales : elle s'effondre dès que l'image est de travers.
        """
        best = (image, words, 0, self._expense_scan_quality(words))
        for quarters in (1, 3):
            rotated = preprocess.rotate_quarters(image, quarters)
            candidate = engine.recognize(rotated)
            quality = self._expense_scan_quality(candidate)
            if quality > best[3]:
                best = (rotated, candidate, quarters, quality)
        return best[0], best[1], best[2]

    def _expense_scan_tighten(self, image, words, info, company):
        """Redresse puis recadre, une fois l'OCR passé.

        Cet ordre est important : la rotation agrandit le cadre pour ne rien
        rogner, donc recadrer avant elle laisse forcément de la marge. On
        redresse d'abord, en faisant suivre les boîtes de mots, et on
        recadre sur leur nouvelle position.
        """
        if company.expense_scan_deskew:
            # L'inclinaison se mesure sur les lignes de texte reconnues, et
            # non sur l'image : les bords du papier sont des droites bien
            # plus marquées que l'impression, et rarement parallèles à elle.
            angle = preprocess.skew_angle_from_lines(parser.build_lines(words))
            if preprocess.MIN_DESKEW_ANGLE < abs(angle) <= preprocess.MAX_DESKEW_ANGLE:
                matrix, _size = preprocess.rotation_matrix(
                    (image.shape[1], image.shape[0]), angle)
                image = preprocess.rotate(image, angle)
                words = preprocess.rotate_words(words, matrix)
                info.deskew_angle = angle
                info.changed = True

        if company.expense_scan_autocrop:
            image, tightened = preprocess.crop_to_text(image, words)
            if tightened:
                info.cropped = True
                info.changed = True

        info.final_size = (image.shape[1], image.shape[0])
        return image

    @staticmethod
    def _expense_scan_quality(words):
        """Quantité de texte reconnu avec confiance, à l'horizontale."""
        return sum(len(word.text) * word.score
                   for word in words if word.width >= word.height)

    # ------------------------------------------------------------------
    # Report du résultat sur la dépense
    # ------------------------------------------------------------------

    def _expense_scan_apply(self, result, attachment):
        """Écrit les champs lus et prépare le message de vérification."""
        self.ensure_one()
        company = self.company_id or self.env.company
        values = self._expense_scan_field_values(result, company)

        todo = parser.fields_to_check(result)
        values.update({
            'scan_state': 'partial' if todo else 'done',
            'scan_engine': result.engine,
            'scan_duration': result.duration,
            'scan_score': round(result.mean_score * 100.0, 1),
            'scan_raw_text': result.raw_text,
            'scan_todo': ", ".join(todo) if todo else False,
            'scan_message': self._expense_scan_summary(result),
            'scan_detected_tax': self._expense_scan_tax_label(result),
        })
        values.update(self._expense_scan_store_image(result, attachment, company))
        self.write(values)

    def _expense_scan_field_values(self, result, company):
        """Traduit le résultat du parseur en valeurs de champs Odoo."""
        values = {}

        scan_date = result.value('date')
        if scan_date:
            values['date'] = scan_date

        scan_time = result.value('time')
        if scan_time:
            values['scan_time'] = scan_time.strftime('%H:%M')
        if scan_date:
            values['scan_datetime'] = self._expense_scan_moment(scan_date, scan_time)

        merchant = result.value('merchant')
        if merchant:
            values['name'] = merchant
        elif scan_date:
            # Sans enseigne lisible, le titre par défaut d'Odoo porte la date
            # de saisie, qui n'apprend rien. Celle de la dépense, si, et elle
            # aide à retrouver la ligne dans une liste.
            values['name'] = _("Ticket du %s", format_date(self.env, scan_date))

        currency = self._expense_scan_currency(result, company)
        if currency:
            values['currency_id'] = currency.id

        total = result.value('total')
        if total:
            # Champ calculé mais réinscriptible : c'est le point d'entrée
            # prévu par Odoo pour un montant saisi tel quel, TTC.
            values['total_amount_currency'] = total

        if company.expense_scan_apply_tax:
            tax = self._expense_scan_tax(result, company)
            if tax:
                values['tax_ids'] = [(6, 0, tax.ids)]

        if company.expense_scan_set_vendor:
            vendor = self._expense_scan_vendor(result, company)
            if vendor:
                values['vendor_id'] = vendor.id

        return values

    def _expense_scan_moment(self, scan_date, scan_time):
        """Horodatage du ticket, converti en UTC comme le stocke Odoo.

        L'heure imprimée sur un ticket est une heure locale ; la stocker
        telle quelle décalerait l'affichage de deux heures en été.
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

    def _expense_scan_currency(self, result, company):
        """Devise correspondant au code lu, si elle est active dans Odoo."""
        code = result.value('currency')
        if not code or result.confidence('currency') < 0.5:
            return self.env['res.currency']
        if company.currency_id.name == code:
            return self.env['res.currency']  # déjà la devise par défaut
        return self.env['res.currency'].search([('name', '=', code)], limit=1)

    def _expense_scan_tax(self, result, company):
        """Taxe d'achat TTC correspondant au taux lu, si elle est unique."""
        rate = result.value('tax_rate')
        if rate is None:
            return self.env['account.tax']
        # Pas de filtre sur `price_include` : le plan comptable français
        # définit ses taxes d'achat hors taxe, et Odoo traite de toute façon
        # le total d'une note de frais comme TTC (`special_mode` du moteur
        # de taxes). Filtrer là-dessus ne retenait jamais aucune taxe.
        taxes = self.env['account.tax'].search([
            ('company_id', '=', company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', rate),
        ], limit=2)
        # Une correspondance ambiguë est pire qu'aucune : elle passerait
        # inaperçue à la relecture. Sur un plan comptable français complet,
        # une douzaine de taxes cohabitent au même taux (biens, services,
        # intracommunautaire...) : c'est alors la catégorie de dépense qui
        # tranche, et ce choix-là appartient au comptable.
        return taxes if len(taxes) == 1 else self.env['account.tax']

    def _expense_scan_vendor(self, result, company):
        """Contact fournisseur dont le nom correspond à l'enseigne lue."""
        merchant = result.value('merchant')
        if not merchant or result.confidence('merchant') < parser.LOW_CONFIDENCE:
            return self.env['res.partner']
        partners = self.env['res.partner'].search([
            ('name', '=ilike', merchant),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], limit=2)
        return partners if len(partners) == 1 else self.env['res.partner']

    def _expense_scan_store_image(self, result, attachment, company):
        """Attache l'image recadrée et la met en aperçu principal."""
        self.ensure_one()
        if not result.image_bytes:
            return {}

        if not company.expense_scan_keep_original:
            attachment.write({
                'raw': result.image_bytes,
                'mimetype': 'image/jpeg',
            })
            return {}

        # Une relance d'analyse produit une nouvelle image : on remplace la
        # précédente au lieu d'empiler les pièces jointes sur la dépense.
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
        # ticket redressé que l'utilisateur doit avoir sous les yeux pour
        # relire les champs, pas la photo de travers.
        self.sudo()._message_set_main_attachment_id(cropped, force=True)
        return {
            'scan_original_attachment_id': attachment.id,
            'scan_cropped_attachment_id': cropped.id,
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
            if steps:
                parts.append(", ".join(steps))
        return " · ".join(part for part in parts if part)[:250]

    def _expense_scan_tax_label(self, result):
        """Résumé de la TVA lue, à titre indicatif."""
        rate = result.value('tax_rate')
        amount = result.value('tax_amount')
        if rate is None and amount is None:
            return False
        if rate is not None and amount is not None:
            return _("%(rate)s %% — %(amount).2f", rate=rate, amount=amount)
        if rate is not None:
            return _("%s %%", rate)
        return _("%.2f", amount)
