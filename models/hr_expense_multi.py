# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Receipts in several pieces, and receipts received by email.

A receipt often comes in two parts: the card slip and the till receipt, or
simply a torn ticket. Photographed separately and attached to the same
message, they may describe one expense or several unrelated ones.

Each piece is read and compared: one expense when they match, as many
expenses as distinct receipts otherwise. By email, the scan starts on its
own, without any action from the user.
"""
import logging
import re

from odoo import _, api, fields, models
from .odoo_compat import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

#: Length beyond which an email subject is cut: enough for "Ibis hotel Lyon
#: Part-Dieu, Enedis trip 12 to 14", short enough for one list line.
MAIL_SUBJECT_MAX = 100

#: Reply and forward prefixes, in any language and mail client, possibly
#: repeated: "TR: RE: Fwd:".
MAIL_SUBJECT_PREFIX = re.compile(
    r'^(?:\s*(?:re|tr|fw|fwd|réf|ref|aw|wg|sv|vs|rv|enc)\s*(?:\[\d+\])?\s*:)+',
    re.IGNORECASE)

#: Two totals whose gap exceeds both thresholds are two expenses. The
#: absolute threshold (in currency units) protects small amounts, where
#: rounding weighs more; the relative one protects large amounts, where a
#: few units of difference prove nothing.
DIFFERENT_TOTAL_ABSOLUTE = 0.05
DIFFERENT_TOTAL_RATIO = 0.02


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_from_mail = fields.Boolean(
        string="Received by email",
        readonly=True,
        copy=False,
        help="Marks the expenses created by the mail gateway. They arrive "
             "with all their attachments at once, whereas the scan button "
             "creates one per photo.",
    )

    expense_scan_keep_name = fields.Boolean(
        string="Fixed description",
        readonly=True,
        copy=False,
        help="The description comes from the email subject: the receipt scan "
             "does not replace it with the merchant name.",
    )

    # ------------------------------------------------------------------
    # Receiving by email
    # ------------------------------------------------------------------

    @staticmethod
    def _expense_scan_mail_subject(subject):
        """An email subject, ready to be used as a description.

        Without reply or forward prefixes, on a single line, and cut at a
        word boundary when too long. Empty when the subject is nothing but
        punctuation.
        """
        text = MAIL_SUBJECT_PREFIX.sub('', subject or '')
        text = ' '.join(text.split()).strip(' -–—:;,.|')
        if not re.search(r'\w', text):
            return ''
        if len(text) > MAIL_SUBJECT_MAX:
            cut = text[:MAIL_SUBJECT_MAX - 1]
            # Cut at a word boundary, unless the only word is very long.
            if ' ' in cut[MAIL_SUBJECT_MAX // 2:]:
                cut = cut.rsplit(' ', 1)[0]
            text = cut.rstrip(' -–—:;,.|') + '…'
        return text

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """Name the expense after the subject and mark it for scanning.

        The subject is cleaned before Odoo looks for a category and an
        amount in it, so that a "Fwd:" does not hide a leading reference.

        The scan starts on the message, once the attachments are there.
        """
        subject = self._expense_scan_mail_subject(msg_dict.get('subject'))
        msg_dict = dict(msg_dict, subject=subject)
        expense = super().message_new(msg_dict, custom_values=custom_values)
        if expense:
            values = {'expense_scan_from_mail': True}
            # What Odoo left of the subject after removing the category and
            # the amount it recognised.
            description = self._expense_scan_mail_subject(expense.name) if subject else ''
            if description:
                values.update(name=description, expense_scan_keep_name=True)
            # As with the scan button: the company's default category,
            # replaced by the scan when the receipt reveals one.
            default = expense.company_id._expense_scan_default_product()
            if not expense.product_id and default:
                values['product_id'] = default.id
            expense.write(values)
        return expense

    def _message_post_after_hook(self, message, *args, **kwargs):
        """Scan the receipts of an expense received by email."""
        result = super()._message_post_after_hook(message, *args, **kwargs)
        for expense in self:
            if not expense.expense_scan_from_mail or expense.scan_state != 'none':
                continue
            if not expense.company_id.expense_scan_enabled:
                continue
            # The flag is used up by the first scan: otherwise any message
            # posted later on the expense would start another scan.
            expense.expense_scan_from_mail = False
            expense._expense_scan_run_pieces()
        return result

    # ------------------------------------------------------------------
    # Reading several receipts
    # ------------------------------------------------------------------

    def _expense_scan_image_attachments(self):
        """The readable attachments: the main receipt, then the others in
        the order they arrived.

        The first group of pieces stays on the expense: it must contain the
        main receipt. ``attachment_ids`` lists the attachments from newest
        to oldest; a second receipt attached later took its place.
        """
        self.ensure_one()
        main = self.message_main_attachment_id
        attachments = self.attachment_ids.filtered(
            lambda attachment: self._expense_scan_readable(attachment))
        return attachments.sorted(lambda attachment: (attachment != main, attachment.id))

    def _expense_scan_run_pieces(self, force=False, from_original=True):
        """Read each receipt, then group those that make a single one.

        The first group stays on this expense; each other group moves to a
        new expense. Adding up two different receipts would make the
        accounts wrong; an extra expense is easy to delete.

        Returns the new expenses.
        """
        self.ensure_one()
        attachments = self._expense_scan_image_attachments()
        if len(attachments) < 2:
            # A single receipt: nothing to compare, the usual scan.
            self._expense_scan_run(force=force, from_original=from_original)
            return self.browse()

        pieces = []
        for attachment in attachments:
            try:
                with self.env.cr.savepoint():
                    pieces.append((attachment, self._expense_scan_process(attachment)))
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                raise  # retried by Odoo, see _expense_scan_run
            except Exception as error:  # noqa: BLE001
                _logger.exception(
                    "Unreadable receipt (expense %s, attachment %s)",
                    self.id, attachment.id)
                self.scan_message = str(error)[:250]

        if not pieces:
            self.write({
                'scan_state': 'error',
                'scan_message': self.scan_message or _("No readable receipt."),
            })
            return self.browse()

        groups = self._expense_scan_group_pieces(pieces)
        first, others = groups[0], groups[1:]
        # Expense already scanned: the reading of its main receipt has been
        # reviewed by the employee. A receipt added later completes it
        # without contradicting it.
        self._expense_scan_apply_group(
            first, main_first=self.scan_state in ('done', 'partial'))
        return self.browse().union(*[self._expense_scan_split_off(group) for group in others])

    def _expense_scan_group_pieces(self, pieces):
        """Group the pieces that describe the same receipt.

        Each piece joins the first group it matches, or starts a new one.
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
        """Tell whether these two readings describe the same purchase.

        Cautious split: only a **clear** difference separates them. An
        unreadable total or a missing date keeps them together, since a
        card slip only prints the amount.
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

    def _expense_scan_merge_pieces(self, results, main_first=False):
        """Keep the most complete reading, completed by the others.

        The pieces of one receipt complete each other: the card slip gives
        the time and payment method, the till receipt the merchant and the
        VAT. The richest reading is kept, or the first one with
        ``main_first``; the others only fill its empty fields, without
        contradicting it.
        """
        best = results[0] if main_first else max(results, key=self._expense_scan_completeness)
        for other in results:
            if other is best:
                continue
            # The text of the other pieces is added to it: the merchant or
            # the words naming the category may be readable on one piece only.
            best.lines = list(best.lines) + list(other.lines)
            for name, field in other.fields.items():
                known = best.fields.get(name)
                if field.value is not None and (known is None or known.value is None):
                    best.fields[name] = field
        return best

    @staticmethod
    def _expense_scan_completeness(result):
        """How rich a reading is: its filled fields, weighted by confidence."""
        return sum(field.confidence for field in result.fields.values()
                   if field.value is not None)

    def _expense_scan_apply_group(self, group, main_first=False):
        """Write a group of pieces to this expense."""
        self.ensure_one()
        attachment, _first = group[0]
        merged = self._expense_scan_merge_pieces(
            [result for _piece, result in group], main_first=main_first)
        try:
            with self.env.cr.savepoint():
                self._expense_scan_apply(merged, attachment)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise  # retried by Odoo, see _expense_scan_run
        except Exception as error:  # noqa: BLE001
            _logger.exception("Could not write the scan result (expense %s)", self.id)
            self.write({
                'scan_state': 'error',
                'scan_message': str(error)[:250],
                'scan_todo': False,
                'expense_scan_todo_codes': False,
                'expense_scan_hints': False,
            })

    def _expense_scan_split_off(self, group):
        """Move a group of pieces to a new expense.

        The attachments move with the group: otherwise the first expense
        would carry the receipt of a purchase it does not describe.

        The description and category of the first expense do not describe
        this purchase: the new one gets a temporary description and the
        default category, which the scan replaces. Only an email subject,
        shared by all pieces, is kept.
        """
        self.ensure_one()
        product = self.company_id._expense_scan_default_product() or self.product_id
        if self.expense_scan_keep_name:
            name = self.name
        else:
            name = self._get_untitled_expense_name(
                format_date(self.env, fields.Date.context_today(self)))
        expense = self.create({
            'name': name,
            'employee_id': self.employee_id.id,
            'company_id': self.company_id.id,
            'product_id': product.id,
            # Temporary category: the scan may replace it.
            'expense_scan_guessed_product_id': product.id,
            'expense_scan_from_mail': False,
            'expense_scan_keep_name': self.expense_scan_keep_name,
        })
        attachments = self.env['ir.attachment'].union(
            *[attachment for attachment, _result in group])
        attachments.sudo().write({'res_id': expense.id})
        expense._expense_scan_apply_group(group)

        # Both sides say it: the user who scanned the first expense sees a
        # receipt vanish from it.
        self.message_post(body=_(
            "A receipt with a different total or date was moved to its own expense: %s",
            expense._get_html_link(title=expense.name)))
        expense.message_post(body=_(
            "Receipt moved here from %s: its total or date differ from the "
            "receipt of that expense.", self._get_html_link(title=self.name)))
        return expense
