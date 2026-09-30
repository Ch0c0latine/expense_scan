# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, api, fields, models
from odoo.tools import formatLang

from ..ocr import engines, preprocess


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    expense_scan_enabled = fields.Boolean(
        related='company_id.expense_scan_enabled', readonly=False)
    expense_scan_engine = fields.Selection(
        related='company_id.expense_scan_engine', readonly=False)
    expense_scan_threads = fields.Integer(
        related='company_id.expense_scan_threads', readonly=False)
    expense_scan_tesseract_lang = fields.Char(
        related='company_id.expense_scan_tesseract_lang', readonly=False)
    expense_scan_model_dir = fields.Char(
        related='company_id.expense_scan_model_dir', readonly=False)
    expense_scan_autocrop = fields.Boolean(
        related='company_id.expense_scan_autocrop', readonly=False)
    expense_scan_deskew = fields.Boolean(
        related='company_id.expense_scan_deskew', readonly=False)
    expense_scan_auto_rotate = fields.Boolean(
        related='company_id.expense_scan_auto_rotate', readonly=False)
    expense_scan_keep_original = fields.Boolean(
        related='company_id.expense_scan_keep_original', readonly=False)
    expense_scan_max_age_days = fields.Integer(
        related='company_id.expense_scan_max_age_days', readonly=False)
    expense_scan_text_retention_days = fields.Integer(
        related='company_id.expense_scan_text_retention_days', readonly=False)
    expense_scan_apply_tax = fields.Boolean(
        related='company_id.expense_scan_apply_tax', readonly=False)
    expense_scan_reinvoice = fields.Boolean(
        related='company_id.expense_scan_reinvoice', readonly=False)
    expense_scan_set_vendor = fields.Boolean(
        related='company_id.expense_scan_set_vendor', readonly=False)
    expense_scan_product_id = fields.Many2one(
        related='company_id.expense_scan_product_id', readonly=False)
    expense_scan_wide_split = fields.Boolean(
        related='company_id.expense_scan_wide_split', readonly=False)

    expense_scan_status = fields.Text(
        string="Engine status",
        compute='_compute_expense_scan_status',
    )

    @api.depends('expense_scan_engine')
    def _compute_expense_scan_status(self):
        """Readable diagnosis: what is installed, what is missing."""
        ok, message = preprocess.dependencies_status()
        lines = [_("Image processing: %s", "OK - " + message if ok else message)]
        for status in engines.engines_status():
            lines.append("%s: %s" % (
                status['label'],
                _("available (%s)", status['message']) if status['available']
                else _("unavailable - %s", status['message']),
            ))
        text = "\n".join(lines)
        for record in self:
            record.expense_scan_status = text

    def action_expense_scan_self_test(self):
        """Load the models and read a test image.

        Also works as a warm-up: the first download of the models happens
        here, before the user's first photo.
        """
        self.ensure_one()
        company = self.company_id
        try:
            report = engines.self_test(
                company.expense_scan_engine, **company._expense_scan_engine_options())
        except Exception as error:  # noqa: BLE001
            return self._expense_scan_notification(
                _("OCR engine test"), str(error), 'danger')
        return self._expense_scan_notification(
            _("OCR engine test"),
            _("%(engine)s - loaded and tested in %(duration)s s.\nText read: \"%(text)s\"",
              engine=report['engine'], duration=formatLang(self.env, report['duration'], digits=1),
              text=report['text']),
            'success' if report['word_count'] else 'warning',
        )

    def _expense_scan_notification(self, title, message, kind):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': kind,
                'sticky': True,
            },
        }
