# -*- coding: utf-8 -*-
"""Justificatifs en plusieurs morceaux, et tickets reçus par courriel.

Un reçu arrive souvent en deux parties : la bande de carte bancaire et le
ticket de caisse, ou simplement un ticket déchiré. Photographiés séparément
et joints au même message, ils décrivent une seule dépense — mais rien ne
garantit que ce soit le cas, et deux justificatifs sans rapport joints au
même courriel sont tout aussi courants.

On lit donc chaque pièce, on les compare, et on tranche : une dépense si
elles concordent, autant de dépenses que de justificatifs distincts sinon.
Sans rien demander à personne, car un envoi par courriel se fait justement
loin de l'interface.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: Deux totaux séparés de plus que cela désignent deux dépenses.
#: En valeur absolue d'abord, pour les petits montants où deux centimes
#: d'arrondi pèsent lourd en proportion ; en proportion ensuite, pour les
#: gros, où deux euros d'écart ne prouvent rien.
DIFFERENT_TOTAL_ABSOLUTE = 0.05
DIFFERENT_TOTAL_RATIO = 0.02


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_from_mail = fields.Boolean(
        string="Reçue par courriel",
        readonly=True,
        copy=False,
        help="Marque les dépenses créées par la passerelle de messagerie. "
             "Elles arrivent avec toutes leurs pièces jointes d'un coup, là "
             "où le bouton de scan en crée une par photo.",
    )

    # ------------------------------------------------------------------
    # Déclenchement sur le chemin du courriel
    # ------------------------------------------------------------------

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Marque la dépense : ses pièces jointes n'arrivent qu'après.

        L'analyse ne peut pas se lancer ici — la passerelle crée d'abord
        l'enregistrement, puis y poste le message et ses pièces jointes.
        On pose donc un repère, et c'est le message qui déclenchera la
        lecture une fois les photos réellement attachées.
        """
        expense = super().message_new(msg_dict, custom_values=custom_values)
        if expense:
            expense.expense_scan_from_mail = True
        return expense

    def _message_post_after_hook(self, message, msg_vals):
        """Analyse les justificatifs d'une dépense reçue par courriel."""
        result = super()._message_post_after_hook(message, msg_vals)
        for expense in self:
            if not expense.expense_scan_from_mail or expense.scan_state != 'none':
                continue
            if not expense.company_id.expense_scan_enabled:
                continue
            # Le repère ne sert qu'une fois : sans quoi le moindre message
            # posté ensuite sur la dépense relancerait toute l'analyse.
            expense.expense_scan_from_mail = False
            expense._expense_scan_run_pieces()
        return result

    # ------------------------------------------------------------------
    # Lecture de plusieurs justificatifs
    # ------------------------------------------------------------------

    def _expense_scan_image_attachments(self):
        """Les pièces jointes lisibles, dans l'ordre où elles sont arrivées."""
        self.ensure_one()
        return self.attachment_ids.filtered(
            lambda attachment: self._expense_scan_readable(attachment))

    def _expense_scan_run_pieces(self, force=False, from_original=True):
        """Lit chaque justificatif, puis regroupe ceux qui n'en font qu'un.

        Le premier groupe reste sur cette dépense ; chacun des suivants part
        sur une dépense à lui. C'est le seul choix défendable sans personne
        devant l'écran : additionner deux tickets sans rapport fausserait la
        comptabilité en silence, tandis qu'une dépense de trop se remarque
        et se supprime d'un bouton.
        """
        self.ensure_one()
        attachments = self._expense_scan_image_attachments()
        if len(attachments) < 2:
            # Un seul justificatif : rien à comparer, chemin ordinaire.
            return self._expense_scan_run(force=force, from_original=from_original)

        pieces = []
        for attachment in attachments:
            try:
                with self.env.cr.savepoint():
                    pieces.append((attachment, self._expense_scan_process(attachment)))
            except Exception as error:  # noqa: BLE001
                _logger.exception(
                    "Justificatif illisible (dépense %s, pièce jointe %s)",
                    self.id, attachment.id)
                self.scan_message = str(error)[:250]

        if not pieces:
            self.write({
                'scan_state': 'error',
                'scan_message': self.scan_message or "Aucun justificatif lisible.",
            })
            return None

        groups = self._expense_scan_group_pieces(pieces)
        first, others = groups[0], groups[1:]
        self._expense_scan_apply_group(first)
        for group in others:
            self._expense_scan_split_off(group)
        return None

    def _expense_scan_group_pieces(self, pieces):
        """Réunit les morceaux qui décrivent le même justificatif.

        Chaque pièce rejoint le premier groupe avec lequel elle concorde,
        et en ouvre un sinon.
        """
        groups = []
        for attachment, result in pieces:
            for group in groups:
                if self._expense_scan_same_receipt(group[0][1], result):
                    group.append((attachment, result))
                    break
            else:
                groups.append([(attachment, result)])
        return groups

    def _expense_scan_same_receipt(self, first, second):
        """Ces deux lectures décrivent-elles le même achat ?

        Le critère est volontairement prudent : on ne sépare que sur une
        **franche** différence. Un doute — un total illisible, une date
        absente d'un des deux morceaux — laisse les pièces ensemble, parce
        que c'est le cas le plus fréquent : une bande de carte bancaire
        n'imprime ni enseigne ni détail, seulement un montant.
        """
        total_first, total_second = first.value('total'), second.value('total')
        if total_first and total_second:
            gap = abs(total_first - total_second)
            if gap > DIFFERENT_TOTAL_ABSOLUTE \
                    and gap > DIFFERENT_TOTAL_RATIO * max(total_first, total_second):
                return False

        date_first, date_second = first.value('date'), second.value('date')
        if date_first and date_second and date_first != date_second:
            return False
        return True

    def _expense_scan_merge_pieces(self, results):
        """Retient la lecture la plus complète, complétée par les autres.

        Les morceaux d'un même reçu se complètent : la bande de carte donne
        l'heure et le moyen de paiement, le ticket de caisse l'enseigne et
        la TVA. On part donc du plus riche, et les autres ne comblent que
        ses vides — jamais ils ne contredisent ce qu'il affirme.
        """
        best = max(results, key=self._expense_scan_completeness)
        for other in results:
            if other is best:
                continue
            for name, field in other.fields.items():
                known = best.fields.get(name)
                if field.value is not None and (known is None or known.value is None):
                    best.fields[name] = field
        return best

    @staticmethod
    def _expense_scan_completeness(result):
        """Richesse d'une lecture : ses champs remplis, pondérés par leur sûreté."""
        return sum(field.confidence for field in result.fields.values()
                   if field.value is not None)

    def _expense_scan_apply_group(self, group):
        """Reporte un groupe de morceaux sur cette dépense."""
        self.ensure_one()
        attachment, _first = group[0]
        merged = self._expense_scan_merge_pieces([result for _piece, result in group])
        try:
            with self.env.cr.savepoint():
                self._expense_scan_apply(merged, attachment)
        except Exception as error:  # noqa: BLE001
            _logger.exception("Report impossible (dépense %s)", self.id)
            self.write({
                'scan_state': 'error',
                'scan_message': str(error)[:250],
                'scan_todo': False,
            })

    def _expense_scan_split_off(self, group):
        """Détache un groupe de morceaux sur une dépense à lui.

        Le justificatif suit la dépense : le laisser sur la première ferait
        porter à celle-ci la preuve d'un achat qu'elle ne décrit pas.
        """
        self.ensure_one()
        expense = self.create({
            'name': self.name,
            'employee_id': self.employee_id.id,
            'company_id': self.company_id.id,
            'product_id': self.product_id.id,
            'expense_scan_from_mail': False,
        })
        attachments = self.env['ir.attachment'].union(
            *[attachment for attachment, _result in group])
        attachments.sudo().write({'res_id': expense.id})
        expense._expense_scan_apply_group(group)

        self.message_post(body=_(
            "Ce message portait un justificatif sans rapport avec celui-ci : "
            "il a été détaché sur sa propre dépense."))
        expense.message_post(body=_(
            "Justificatif détaché d'un même courriel, dont le total ne "
            "correspondait pas."))
        return expense
