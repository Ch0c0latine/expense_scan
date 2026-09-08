# -*- coding: utf-8 -*-
import logging
import os
import time
from datetime import datetime, time as dtime
from uuid import uuid4

from pytz import timezone, utc

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date
from odoo.tools.safe_eval import safe_eval

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
    scan_tax_amount = fields.Monetary(
        string="TVA du ticket",
        currency_field='currency_id',
        copy=False,
        help="Montant de TVA imprimé sur le justificatif. Renseigné, il fait "
             "foi : la TVA de la dépense et celle de l'écriture comptable "
             "prennent cette valeur, quel que soit le taux retenu. C'est ce "
             "qui permet à un ticket mêlant 5,5 %, 10 % et 20 % de produire "
             "exactement sa TVA. Laisser à zéro pour qu'Odoo la calcule "
             "depuis le taux, comme d'habitude.",
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
    scan_cropped_attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string="Ticket recadré",
        readonly=True,
        copy=False,
        ondelete='set null',
    )

    # ------------------------------------------------------------------
    # TVA du ticket : le montant prime sur le taux
    #
    # Odoo dérive la TVA du taux appliqué au total. Un ticket de caisse fait
    # l'inverse : il imprime un montant, souvent issu de plusieurs taux
    # mêlés, et c'est ce montant qui doit se retrouver en comptabilité. Le
    # taux reste sélectionné — il porte les tags fiscaux — mais il ne
    # commande plus le montant.
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

    def _expense_scan_max_rate(self):
        """Le plus haut taux retenu, ou ``None`` si aucune taxe en pourcentage.

        La distinction compte : « aucune taxe » et « taxe à 0 % » sont deux
        situations différentes. La première ne permet aucun contrôle, la
        seconde impose un plafond de zéro.
        """
        self.ensure_one()
        rates = self.tax_ids.filtered(
            lambda tax: tax.amount_type == 'percent').mapped('amount')
        return max(rates) if rates else None

    def _expense_scan_tax_ceiling(self):
        """TVA maximale possible : le plus haut taux appliqué au total TTC.

        Un ticket mêlant plusieurs taux porte forcément moins de TVA que si
        tout était au taux le plus élevé. Ce plafond attrape donc les
        erreurs de lecture sans jamais gêner une saisie légitime — et il
        vaut zéro sur une catégorie exonérée, ce qui y interdit toute TVA.
        """
        self.ensure_one()
        rate = self._expense_scan_max_rate()
        if rate is None:
            return None
        return self.total_amount_currency * rate / (100.0 + rate)

    @api.constrains('scan_tax_amount', 'total_amount_currency', 'tax_ids')
    def _check_scan_tax_amount(self):
        for expense in self:
            if not expense.scan_tax_amount:
                continue
            if expense.scan_tax_amount < 0:
                raise ValidationError(_("La TVA du ticket ne peut pas être négative."))
            ceiling = expense._expense_scan_tax_ceiling()
            if ceiling is None:
                # Aucune taxe retenue : pas de plafond à opposer. On laisse
                # saisir — le montant reste une information du justificatif —
                # et on s'y oppose à la validation comptable, là où l'absence
                # de taux devient réellement bloquante.
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

        Odoo bâtit l'écriture en appliquant le taux au total : le montant lu
        sur le justificatif n'y arriverait jamais. Plutôt que de retoucher
        la ligne de taxe après coup — fragile, et recalculée à la moindre
        écriture —, on scinde la base en deux : la part qui, au taux retenu,
        produit exactement le montant voulu, et le reste hors taxe.

        L'écriture reste équilibrée, la TVA déductible est celle du ticket,
        et les tags fiscaux sont posés par le moteur de taxes habituel.
        """
        vals_list = super()._prepare_receipts_vals()
        # `_prepare_receipts_vals` regroupe par employé et crée une ligne par
        # dépense, dans cet ordre : on refait le même groupement pour
        # retrouver quelle ligne appartient à quelle dépense.
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
        """Ajuste la ligne de base, et renvoie la ligne du reliquat s'il y en a."""
        self.ensure_one()
        if not self.scan_tax_amount:
            return None
        rate = self._expense_scan_max_rate()
        if not rate:
            # C'est ici que l'absence de taux devient bloquante : sans lui,
            # la TVA du ticket n'a aucun moyen d'entrer dans l'écriture, et
            # la comptabiliser sans le dire serait la perdre en silence.
            raise UserError(_(
                "La dépense « %(nom)s » porte une TVA de ticket de "
                "%(montant).2f, mais aucune taxe à un taux exploitable. "
                "Sélectionnez la taxe correspondante sur la dépense — ou sur "
                "sa catégorie, pour les suivantes — ou videz le champ "
                "« TVA du ticket ».",
                nom=self.name, montant=self.scan_tax_amount))

        line_vals = command[2]
        currency = self.company_currency_id
        # Le prix d'une ligne d'écriture portant un `expense_id` est traité
        # comme TTC par Odoo — c'est la convention des notes de frais, et
        # elle est explicite dans hr_expense/models/account_move_line.py.
        # On lui passe donc le TTC de la part taxée, pas sa base : lui
        # donner la base reviendrait à en retirer la TVA une seconde fois.
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
        """Refuse de comptabiliser une TVA de ticket qu'on ne sait pas porter.

        Le chemin « payé par la société » construit ses lignes d'écriture
        avec des soldes explicites, sans passer par la ventilation de base
        ci-dessus. Plutôt que d'y comptabiliser silencieusement la TVA du
        taux à la place de celle du ticket, on s'arrête net.
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

    def action_expense_scan_done(self):
        """Vérification terminée : on rend la main à la liste."""
        return self._expense_scan_expense_list()

    def action_expense_scan_again(self):
        """Retour à la liste, sélecteur de photo déjà ouvert.

        Enchaîner plusieurs tickets est le cas courant. Sans ce raccourci,
        il faudrait revenir à la liste puis viser le bouton Scan : deux
        gestes là où l'utilisateur n'en attend qu'un.
        """
        action = self._expense_scan_expense_list()
        # Le contexte d'une action lue en base est une **chaîne**, que le
        # client évalue lui-même : la fusionner demande de l'évaluer ici.
        raw_context = action.get('context') or {}
        if isinstance(raw_context, str):
            try:
                raw_context = safe_eval(raw_context, {'uid': self.env.uid})
            except Exception:  # noqa: BLE001 - contexte trop dynamique
                raw_context = {}
        # Un jeton, et non un simple drapeau : le contexte d'une action
        # survit dans le fil d'ariane, et un booléen rouvrait le sélecteur
        # de photo à chaque retour à la liste — y compris après une
        # suppression. Le client note le jeton comme consommé.
        action['context'] = dict(raw_context, expense_scan_start_upload=uuid4().hex)
        return action

    def action_expense_scan_drop(self):
        """Supprime la dépense scannée et revient à la liste."""
        action = self._expense_scan_expense_list()
        self.unlink()
        return action

    def _expense_scan_expense_list(self):
        """L'action « Mes frais », ou un équivalent si l'écran a changé.

        ``target: main`` remet le fil d'ariane à zéro. Sans lui, la fiche
        qu'on vient de quitter — ou de supprimer — y reste inscrite, avec
        un lien qui ne mène plus nulle part.
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
                # Le report des valeurs est dans le filet, lui aussi : une
                # contrainte du modèle ne doit pas faire échouer l'envoi de
                # la photo, sur laquelle l'utilisateur n'a aucune prise. Le
                # point de sauvegarde permet d'écrire l'erreur ensuite, sur
                # un curseur redevenu sain.
                with self.env.cr.savepoint():
                    result = expense._expense_scan_process(attachment)
                    expense._expense_scan_apply(result, attachment)
            except Exception as error:  # noqa: BLE001
                _logger.exception("Analyse du ticket impossible (dépense %s)", expense.id)
                expense.write({
                    'scan_state': 'error',
                    'scan_message': str(error)[:250],
                    'scan_todo': False,
                })

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
        # Redresser avant de lire, et non après : la reconstruction des
        # lignes suppose du texte horizontal. Sur un ticket posé en
        # diagonale, elle assemble des colonnes, et le parseur y cherche en
        # vain « le dernier montant de la ligne ».
        image = self._expense_scan_straighten(engine, image, info, company)
        words = engine.recognize(image)
        duration = time.time() - started

        # Dernier mot sur l'orientation, sur les boîtes de la lecture
        # définitive : plus nombreuses et mieux placées que celles de
        # l'essai, elles rattrapent un quart de tour mal choisi.
        if company.expense_scan_auto_rotate:
            words, image = self._expense_scan_reorient(words, image, info)

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

    #: Côté maximal de l'image d'essai servant à choisir l'orientation.
    ORIENTATION_PROBE_SIDE = 800

    def _expense_scan_straighten(self, engine, image, info, company):
        """Remet le ticket d'aplomb avant la lecture définitive.

        Une seule passe d'essai à basse résolution sert à tout : choisir le
        quart de tour, mesurer l'inclinaison résiduelle, et délimiter le
        ticket. Toute orientation se ramène ainsi à un quart de tour plus un
        résidu dans ±45°.

        Le quart de tour se lit sur la **forme des boîtes**, pas sur la
        qualité de la lecture. Le moteur redresse chaque boîte détectée
        avant de la reconnaître : il lit donc aussi bien dans les quatre
        sens, et comparer les scores — ce que faisait cette méthode, en
        relisant l'image quatre fois — ne tranchait rien du tout.
        """
        if not company.expense_scan_auto_rotate:
            return image

        probe = preprocess.limit_size(image, self.ORIENTATION_PROBE_SIDE)
        best_words = engine.recognize(probe)

        # L'essai et la photo suivent exactement les mêmes transformations :
        # les boîtes trouvées sur l'un restent donc transposables sur l'autre
        # par une simple homothétie.
        quarters = self._expense_scan_quarters(best_words, probe)
        if quarters:
            best_words = preprocess.rotate_words_quarters(
                best_words, quarters, probe.shape[1], probe.shape[0])
            probe = preprocess.rotate_quarters(probe, quarters)
            image = preprocess.rotate_quarters(image, quarters)
            info.rotated_quarters = quarters

        if company.expense_scan_deskew:
            angle = preprocess.skew_angle_from_words(best_words)
            if preprocess.MIN_DESKEW_ANGLE < abs(angle) <= preprocess.MAX_TEXT_DESKEW_ANGLE:
                matrix, _size = preprocess.rotation_matrix(
                    (probe.shape[1], probe.shape[0]), angle)
                best_words = preprocess.rotate_words(best_words, matrix, angle)
                probe = preprocess.rotate(probe, angle)
                image = preprocess.rotate(image, angle)
                info.deskew_angle = angle

        # Recadrer sur le ticket avant la lecture définitive. Le moteur
        # ramène de toute façon l'image à sa taille de travail : autant que
        # ce budget de résolution se dépense sur le ticket plutôt que sur la
        # table qui l'entoure. Marge large, car l'essai est basse
        # définition et peut avoir manqué une ligne en bord de ticket.
        if company.expense_scan_autocrop and probe.shape[1]:
            # Sur les mêmes boîtes retenues pour l'angle : un caractère du
            # décor étirerait le cadre bien au-delà du ticket.
            scaled = preprocess.scale_words(
                preprocess.text_inliers(best_words),
                image.shape[1] / float(probe.shape[1]))
            image, cropped = preprocess.crop_to_text(image, scaled, margin_ratio=0.08)
            info.cropped = info.cropped or cropped

        info.changed = info.changed or bool(
            info.rotated_quarters or info.deskew_angle or info.cropped)
        return image

    def _expense_scan_reorient(self, words, image, info):
        """Applique le quart de tour manquant, sans relire l'image."""
        quarters = self._expense_scan_quarters(words, image)
        if not quarters:
            return words, image
        height, width = image.shape[:2]
        info.rotated_quarters = (info.rotated_quarters + quarters) % 4
        info.changed = True
        return (preprocess.rotate_words_quarters(words, quarters, width, height),
                preprocess.rotate_quarters(image, quarters))

    def _expense_scan_quarters(self, words, image):
        """Quart de tour à appliquer, déduit des seules boîtes reconnues.

        Deux critères, dans cet ordre. La **direction des lignes** d'abord,
        celle que le détecteur donne à chaque boîte : elle sépare les quarts
        pairs des impairs, un ticket debout n'ayant pas la même que couché. Le
        **sens de lecture** ensuite, pour départager l'endroit de l'envers :
        un montant suit son libellé, l'enseigne est en haut, le règlement
        en bas.

        Rien de tout cela ne relit l'image : les boîtes se transposent, et
        leur texte est déjà juste puisque le moteur redresse chaque boîte
        avant de la lire. C'est aussi pourquoi la qualité de reconnaissance
        ne dit rien de l'orientation, et pourquoi cette méthode a remplacé
        quatre relectures qui ne tranchaient rien.
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

        for quarters, turned in candidates:
            if parser.reading_direction(parser.build_lines(turned)) > 0:
                return quarters
        # Aucun indice de sens — un ticket sans total lisible, par exemple.
        # On garde alors le quart le moins coûteux parmi ceux qui couchent
        # bien le texte, faute de mieux.
        return candidates[0][0] if candidates else 0

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
            # L'orientation des boîtes du détecteur d'abord ; la régression
            # sur les mots ne sert que de repli, pour un moteur comme
            # Tesseract qui ne rend que des rectangles droits.
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

    def _expense_scan_apply(self, result, attachment):
        """Écrit les champs lus et prépare le message de vérification."""
        self.ensure_one()
        company = self.company_id or self.env.company
        values = self._expense_scan_field_values(result, company)

        todo = parser.fields_to_check(result)
        if company.expense_scan_reinvoice and not values.get('project_id'):
            # Aucune mission ne couvre cette date, ou plusieurs : dans les
            # deux cas c'est au salarié de trancher — y compris pour dire
            # que le frais n'est pas refacturable.
            todo.append(_("À refacturer"))
        if company.expense_scan_apply_tax:
            if not result.value('tax_amount'):
                todo.append(_("TVA (aucune sur le justificatif)"))
            elif not values.get('scan_tax_amount'):
                # Lecture écartée parce qu'elle dépasse le plafond du taux :
                # c'est presque toujours un montant pris pour un autre.
                todo.append(_("TVA (lecture incompatible avec le taux)"))
            elif not self.tax_ids:
                # La TVA lue ne pourra pas être comptabilisée tant qu'aucune
                # taxe ne porte les tags fiscaux : autant le dire tout de
                # suite, plutôt qu'au moment de valider.
                todo.append(_("Taxe (aucune sur la catégorie)"))
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
            # Le taux lu sur le ticket prime sur celui de la catégorie —
            # un repas à 10 % ne doit pas être déclaré à 20 % parce que la
            # catégorie générique le prévoit. Encore faut-il qu'une seule
            # taxe corresponde à ce taux : sur un plan comptable chargé, le
            # choix entre biens et services appartient au comptable.
            tax = self._expense_scan_tax(result.value('tax_rate'), company)
            if tax:
                values['tax_ids'] = [Command.set(tax.ids)]
                effective_rate = tax.amount
            else:
                # Ticket à plusieurs taux, ou taux introuvable au plan
                # comptable : la dépense garde la taxe de sa catégorie, qui
                # portera l'écriture. Pour juger de la TVA lue, en revanche,
                # le plafond du ticket vaut mieux que celui de la catégorie —
                # un repas à 10 % + 20 % dépasse le plafond d'une catégorie
                # à 10 % sans être faux pour autant.
                effective_rate = (result.value('tax_rate_max')
                                  or self._expense_scan_max_rate())

            tax_amount = result.value('tax_amount')
            # Une TVA lue de travers ne doit jamais faire échouer tout le
            # scan sur ma propre contrainte : on la laisse alors de côté et
            # on la signale, plutôt que d'écrire une valeur inenregistrable.
            if tax_amount and self._expense_scan_tax_fits(
                    tax_amount, values.get('total_amount_currency'), effective_rate):
                values['scan_tax_amount'] = tax_amount
            else:
                # Aucune TVA exploitable — le justificatif n'en porte pas,
                # comme un ticket de carte bancaire, ou la lecture est
                # incohérente. Dans les deux cas, laisser la taxe de la
                # catégorie ferait apparaître une TVA déductible calculée
                # depuis un taux, que rien dans le justificatif ne fonde.
                # Pas de TVA lisible, donc pas de TVA : zéro.
                values['tax_ids'] = [Command.clear()]
                values['scan_tax_amount'] = 0.0

        if company.expense_scan_reinvoice:
            # La mission se cherche à la date du ticket, pas à celle de la
            # saisie : un frais scanné le lundi peut dater du vendredi, sur
            # une autre mission.
            values.update(self._expense_scan_project_values(
                self._expense_scan_find_project(scan_date)))

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

    def _expense_scan_tax(self, rate, company):
        """Taxe d'achat au taux lu, si une seule y correspond."""
        if rate is None:
            return self.env['account.tax']
        taxes = self.env['account.tax'].search([
            ('company_id', '=', company.id),
            ('type_tax_use', '=', 'purchase'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', rate),
        ], limit=2)
        # Une correspondance ambiguë est pire qu'aucune : elle passerait
        # inaperçue à la relecture. On garde alors celle de la catégorie.
        return taxes if len(taxes) == 1 else self.env['account.tax']

    def _expense_scan_tax_fits(self, amount, total=None, rate=None):
        """La TVA lue tient-elle sous le plafond du taux de la dépense ?

        Le total et le taux sont passés en argument : au moment où l'on
        décide, ni l'un ni l'autre n'est encore écrit sur la dépense, et un
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

        # La photo d'origine devient une pièce jointe « de champ » : Odoo
        # exclut d'office celles-ci de ses recherches, donc elle disparaît
        # de la liste des justificatifs et n'est plus recopiée sur l'écriture
        # comptable. Elle reste intégralement accessible par le champ
        # « Photo d'origine », qui la désigne par son identifiant.
        attachment.sudo().write({'res_field': 'scan_original_attachment_id'})
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
        """Résumé de la TVA lue, à titre indicatif.

        Un ticket à plusieurs taux n'en fournit aucun pour la dépense, mais
        il faut le dire : sans cette mention, la TVA du ticket paraîtrait
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
