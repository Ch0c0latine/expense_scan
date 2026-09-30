# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import logging
import os

from odoo import fields, models
from odoo.tools import config

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    expense_scan_enabled = fields.Boolean(
        string="Scan receipts on upload",
        default=True,
        help="Runs OCR on every receipt uploaded from the expense list.",
    )
    expense_scan_engine = fields.Selection(
        selection=[
            ('auto', "Automatic (best available)"),
            ('rapidocr', "RapidOCR / PP-OCR (recommended)"),
            ('tesseract', "Tesseract"),
        ],
        string="OCR engine",
        default='auto',
    )
    expense_scan_threads = fields.Integer(
        string="Threads per worker",
        default=4,
        help="Number of threads each Odoo worker gives to OCR. 0 means 4. "
             "More threads do not read a receipt faster: they overload the "
             "CPU when several scans arrive at once and increase the memory "
             "reserved by each worker.",
    )
    expense_scan_tesseract_lang = fields.Char(
        string="Tesseract language",
        default='eng',
        help="Tesseract language codes, joined with \"+\" (\"eng+fra\"). Each "
             "language needs its tesseract-ocr package on the server.",
    )
    expense_scan_autocrop = fields.Boolean(
        string="Crop the receipt",
        default=True,
        help="Finds the edges of the receipt in the photo and corrects the "
             "perspective before reading.",
    )
    expense_scan_deskew = fields.Boolean(
        string="Straighten the receipt",
        default=True,
        help="Corrects the remaining tilt of the text lines.",
    )
    expense_scan_auto_rotate = fields.Boolean(
        string="Fix quarter turns",
        default=True,
        help="Reads the receipt again after rotating it when the text seems "
             "to run vertically. Makes that scan slower.",
    )
    expense_scan_keep_original = fields.Boolean(
        string="Keep the original photo",
        default=True,
        help="The untouched photo stays attached to the expense as the "
             "receipt; the cropped image is used for display.",
    )
    expense_scan_max_age_days = fields.Integer(
        string="Maximum age (days)",
        default=730,
        help="A date older than this is ignored: it is almost always a "
             "warranty or loyalty card date.",
    )
    expense_scan_text_retention_days = fields.Integer(
        string="Keep the text read for (days)",
        default=3650,
        help="The text read on a receipt (names, addresses, last digits of a "
             "card) is only erased after this delay. The default, 10 years, "
             "is the retention period of accounting records, receipts "
             "included, in France (Commercial Code, art. L123-22) and in "
             "several other countries; check the rule that applies to you. "
             "The receipt image itself is never erased by the module. Zero: "
             "never erase the text either.",
    )
    expense_scan_apply_tax = fields.Boolean(
        string="Use the tax read on the receipt",
        default=True,
        help="Fills the \"Receipt tax\" field with the amount read on the "
             "receipt. That amount then takes precedence over the one the "
             "rate would give, down to the journal entry, so that a receipt "
             "with several rates keeps its exact tax. The rate stays the one "
             "of the expense category.",
    )
    expense_scan_ecb_rates = fields.Boolean(
        string="Exchange rates from the European Central Bank",
        default=True,
        help="Every day, the reference rates of the European Central Bank "
             "(free, no account) are added for the active currencies, over "
             "the last ninety days. Without them, Odoo Community converts a "
             "foreign receipt one for one. Only this public file is "
             "downloaded; nothing leaves the server.",
    )
    expense_scan_reinvoice = fields.Boolean(
        # The label mentions re-invoicing so that the settings search finds it.
        string="Re-invoice expenses to a project",
        default=False,
        help="Adds a \"Re-invoice\" field to expenses and suggests the "
             "employee's project running on the receipt date. The expense is "
             "then booked on the project's analytic account and added to its "
             "sales order, to appear on the next project invoice.\n\n"
             "Off by default: not every organisation works by project.",
    )
    expense_scan_product_id = fields.Many2one(
        comodel_name='product.product',
        string="Default category",
        domain="[('can_be_expensed', '=', True)]",
        help="Category given to expenses created by a scan.\n\n"
             "When empty, Odoo chooses: it looks for the internal reference "
             "\"EXP_GEN\" and otherwise takes the first category in "
             "alphabetical order. Renaming that reference is enough to send "
             "receipts anywhere, hence this setting, which does not depend on "
             "any reference.",
    )
    expense_scan_set_vendor = fields.Boolean(
        string="Look up the vendor",
        default=False,
        help="Links the contact whose name matches the merchant read on the "
             "receipt, only when exactly one contact matches.",
    )
    expense_scan_wide_split = fields.Boolean(
        string="Split view from 768 px",
        default=True,
        help="Odoo only shows the receipt next to the form from 1400 px wide. "
             "This option lowers the threshold for laptop screens, half-screen "
             "windows and tablets, in either orientation.\n\n"
             "Only the width counts: a window taller than it is wide is still "
             "wide enough for two columns. Below the threshold, the preview "
             "becomes a banner above the fields.",
    )

    expense_scan_model_dir = fields.Char(
        string="OCR model folder",
        help="Where the recognition models are downloaded. Leave empty to use "
             "a subfolder of the Odoo data directory, which the service can "
             "always write to.",
    )

    def _expense_scan_default_product(self):
        """Category of an expense the scan has not filed yet.

        The one of the settings; otherwise the one Odoo gives a new expense
        (internal reference "EXP_GEN"). Both are "no category chosen": the
        scan may replace them, and flags them when it cannot.
        """
        self.ensure_one()
        if self.expense_scan_product_id:
            return self.expense_scan_product_id
        return self.env['product.product'].search([
            ('default_code', '=', 'EXP_GEN'), ('can_be_expensed', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.id),
        ], limit=1)

    def _expense_scan_model_dir(self):
        """OCR model folder, created if needed.

        RapidOCR downloads its models into site-packages, where the Odoo
        system user usually cannot write. The Odoo data directory is used
        instead by default.
        """
        self.ensure_one()
        path = self.expense_scan_model_dir or os.path.join(
            config['data_dir'], 'expense_scan_models')
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            _logger.warning("OCR model folder not writable: %s", path)
            return ''
        return path

    def _expense_scan_engine_options(self):
        """Engine options derived from the company settings."""
        self.ensure_one()
        return {
            'threads': self.expense_scan_threads or 0,
            'lang': self.expense_scan_tesseract_lang or 'eng',
            'model_dir': self._expense_scan_model_dir(),
        }
