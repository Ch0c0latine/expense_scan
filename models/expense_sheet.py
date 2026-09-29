# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Expense sheets: PDF summary, Excel export on a template, receipts.

The three outputs share the same numbering: chronological, from 1 to n for
each sheet, and n.1 to n.x when an expense carries several receipts. Flat
rates (mileage, scales) appear in the tables but have no receipt.

One sheet per employee: several employees selected give several files,
delivered together in an archive.
"""
import base64
import io
import logging
import re
import zipfile
from copy import copy
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_date

from ..ocr import preprocess

_logger = logging.getLogger(__name__)

#: Values a line column can receive.
LINE_VALUES = [
    ('n', "Receipt no."),
    ('date', "Date"),
    ('categorie', "Category"),
    ('description', "Description"),
    ('enseigne', "Merchant"),
    ('mission', "Project"),
    ('quantite', "Quantity (nights, km...)"),
    ('prix_unitaire_ht', "Unit amount excl. tax"),
    ('total_ht', "Subtotal excl. tax"),
    ('taux_tva', "VAT rate"),
    ('tva_unitaire', "Unit VAT"),
    ('total_tva', "VAT amount"),
    ('ttc_unitaire', "Unit amount incl. tax"),
    ('total_ttc', "Subtotal incl. tax"),
    ('mode_paiement', "Paid by"),
    ('salarie', "Employee"),
]
#: Values a header cell can receive.
HEADER_VALUES = [
    ('salarie', "Employee"),
    ('prestation', "Service (projects)"),
    ('mois', "Month (date of the first day)"),
    ('periode', "Period (text)"),
    ('date_debut', "Start date"),
    ('date_fin', "End date"),
    ('total_ht', "Total excl. tax"),
    ('total_tva', "Total VAT"),
    ('total_ttc', "Total incl. tax"),
    ('societe', "Company"),
]
#: All values, without duplicates, for the single mapping field.
VALUES = LINE_VALUES + [item for item in HEADER_VALUES
                        if item[0] not in {key for key, _label in LINE_VALUES}]
CELL_RE = re.compile(r"^([A-Z]{1,3})([0-9]*)$")
#: Reference ("$L$70") or range ("L8:L68") in a formula of the same sheet;
#: neither a function name ("LOG10(") nor another sheet ("F2!A1").
CELL_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_!'.$])(\$?[A-Z]{1,3}\$?)(\d+)"
    r"(?::(\$?[A-Z]{1,3}\$?)(\d+))?(?![0-9A-Za-z_(])")
#: Longest side of a receipt photo in the PDFs, in pixels: about 250 dpi on
#: an A4 page, enough to read a photographed receipt again.
IMAGE_MAX_SIDE = 2400
IMAGE_QUALITY = 88


class ExpenseScanExportTemplate(models.Model):
    _name = 'expense.scan.export.template'
    _description = "Excel expense export template"
    _order = 'sequence, name'

    name = fields.Char(string="Name", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string="Company")
    file = fields.Binary(
        string="Excel file", attachment=True,
        help="The workbook as it is filled in by hand, data included: the "
             "expense lines are emptied before being filled.")
    filename = fields.Char(string="File name")
    sheet_name = fields.Char(
        string="Sheet", help="Empty: the first sheet of the workbook.")
    first_row = fields.Integer(string="First expense row", default=8, required=True)
    last_row = fields.Integer(
        string="Last expense row", default=68, required=True,
        help="The exported table has exactly one row per expense: extra rows "
             "are deleted, missing ones added, and the totals below follow.")
    reinvoice_only = fields.Boolean(
        string="Re-invoiced expenses only",
        help="Refuses the export if any expense is not marked "
             "\"Re-invoice: Yes\".")
    single_project = fields.Boolean(
        string="A single project",
        help="Refuses the export if the expenses belong to several projects.")
    column_ids = fields.One2many(
        'expense.scan.export.column', 'template_id', string="Mappings")
    note = fields.Text(string="Notes")

    @api.constrains('first_row', 'last_row')
    def _check_rows(self):
        for template in self:
            if template.first_row < 1 or template.last_row < template.first_row:
                raise UserError(_("The expense rows of template \"%s\" are inconsistent.",
                                  template.name))

    def action_expense_scan_generate_file(self):
        """Blank workbook following the template mappings.

        To download, give the company's look, then upload again. It holds
        the column titles, the header labels, the framed expense rows and
        the totals.
        """
        for template in self:
            content = template._expense_scan_blank_workbook()
            template.write({
                'file': base64.b64encode(content),
                'filename': "%s.xlsx" % re.sub(r'[\\/:*?"<>|]+', '-', template.name),
            })
        return True

    @api.model
    def _expense_scan_fill_blank_files(self):
        """Blank workbook for the bundled templates that have none yet."""
        try:
            import openpyxl  # noqa: F401, PLC0415
        except ImportError:
            _logger.warning("openpyxl missing: bundled export templates have no workbook")
            return
        templates = self.env.ref('expense_scan.export_template_basic',
                                 raise_if_not_found=False)
        if templates and not templates.file:
            templates.action_expense_scan_generate_file()

    def _expense_scan_blank_workbook(self):
        self.ensure_one()
        try:
            import openpyxl  # noqa: PLC0415
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: PLC0415
            from openpyxl.utils import column_index_from_string, get_column_letter  # noqa: PLC0415
        except ImportError as error:
            raise UserError(_("The openpyxl library is missing on the server.")) from error

        labels = dict(VALUES)
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = (self.sheet_name or _("Expenses"))[:31]
        thin = Side(style='thin', color='808080')
        box = Border(left=thin, right=thin, top=thin, bottom=thin)
        title_fill = PatternFill('solid', start_color='DDE7F0')
        bold = Font(bold=True)

        sheet['A1'] = _("Expense sheet")
        sheet['A1'].font = Font(bold=True, size=14)

        for column in self.column_ids.filtered(lambda c: c.kind == 'header' and c.value):
            cell = sheet[column.cell.strip().upper()]
            if cell.column > 1:
                label = sheet.cell(row=cell.row, column=cell.column - 1)
                label.value = dict(HEADER_VALUES).get(column.value, labels[column.value])
                label.font = bold
            cell.border = box
            if column.value in ('mois', 'date_debut', 'date_fin'):
                cell.number_format = 'DD/MM/YYYY'
            elif column.value in ('total_ht', 'total_tva', 'total_ttc'):
                cell.number_format = '#,##0.00'

        first, last = self.first_row, self.last_row
        lines = {column_index_from_string(c.cell.strip().upper()): c.value
                 for c in self.column_ids if c.kind == 'line' and c.value}
        formats = {'date': 'DD/MM/YYYY', 'taux_tva': '0%', 'quantite': '0.##'}
        amounts = {'prix_unitaire_ht', 'total_ht', 'tva_unitaire', 'total_tva',
                   'ttc_unitaire', 'total_ttc'}
        for index, value in lines.items():
            letter = get_column_letter(index)
            if first > 1:
                head = sheet.cell(row=first - 1, column=index, value=labels[value])
                head.font = bold
                head.fill = title_fill
                head.border = box
                head.alignment = Alignment(wrap_text=True, vertical='center')
            for row in range(first, last + 1):
                cell = sheet.cell(row=row, column=index)
                cell.border = box
                cell.number_format = '#,##0.00' if value in amounts \
                    else formats.get(value, 'General')
            total = sheet.cell(row=last + 1, column=index)
            total.font = bold
            total.border = box
            if value in amounts:
                total.value = "=SUM(%s%s:%s%s)" % (letter, first, letter, last)
                total.number_format = '#,##0.00'
            sheet.column_dimensions[letter].width = \
                30 if value in ('description', 'categorie', 'mission') else 14
        if lines:
            sheet.cell(row=last + 1, column=min(lines), value=_("TOTAL"))
        sheet.freeze_panes = sheet.cell(row=first, column=1)

        output = io.BytesIO()
        book.save(output)
        return output.getvalue()


class ExpenseScanExportColumn(models.Model):
    _name = 'expense.scan.export.column'
    _description = "Export template mapping"
    _order = 'kind desc, id'

    template_id = fields.Many2one(
        'expense.scan.export.template', required=True, ondelete='cascade')
    cell = fields.Char(
        string="Column or cell", required=True,
        help="A column letter (\"B\"): each expense's value, row by row. A "
             "cell (\"K4\"): a header value, written once. For a merged cell, "
             "its top-left cell.")
    kind = fields.Selection(
        [('line', "Expenses"), ('header', "Header")],
        string="Type", compute='_compute_kind', store=True)
    value = fields.Selection(
        VALUES, string="Value",
        help="Totals are per row in a column, for the whole sheet in a "
             "header. Service, month, period and company only go in a header.")

    @api.depends('cell')
    def _compute_kind(self):
        for column in self:
            match = CELL_RE.match((column.cell or '').strip().upper())
            column.kind = 'header' if match and match.group(2) else 'line'

    @api.constrains('cell', 'value')
    def _check_cell(self):
        line_keys = {key for key, _label in LINE_VALUES}
        header_keys = {key for key, _label in HEADER_VALUES}
        for column in self:
            if not CELL_RE.match((column.cell or '').strip().upper()):
                raise UserError(_(
                    "\"%(cell)s\": a column letter (B) or a cell (K4).",
                    cell=column.cell))
            allowed = header_keys if column.kind == 'header' else line_keys
            if not column.value:
                raise UserError(_("Choose the value to write in %s.", column.cell))
            if column.value not in allowed:
                raise UserError(_(
                    "\"%(value)s\" cannot go in %(where)s (%(cell)s).",
                    value=dict(VALUES)[column.value], cell=column.cell,
                    where=_("a header") if column.kind == 'header' else _("an expense column")))


class ExpenseScanSheet(models.AbstractModel):
    """Line computation, and file production."""
    _name = 'expense.scan.sheet'
    _description = "Expense sheet production"

    # ------------------------------------------------------------------
    # Lines
    # ------------------------------------------------------------------

    @api.model
    def _is_flat_rate(self, expense):
        """Flat rate or mileage: no receipt expected."""
        product = expense.product_id
        return bool(product.standard_price) or expense._expense_scan_is_distance()

    @api.model
    def _receipts(self, expense):
        """Receipts of the expense, the main one first."""
        if self._is_flat_rate(expense):
            return self.env['ir.attachment']
        attachments = expense.attachment_ids.filtered(
            lambda a: (a.mimetype or '').startswith('image/')
            or a.mimetype == 'application/pdf')
        main = expense.message_main_attachment_id & attachments
        return main | (attachments - main).sorted('id')

    @api.model
    def _tax_rate(self, expense):
        """VAT rate as a fraction (0.1), text when there are several."""
        if expense._expense_scan_product_no_vat(expense.product_id) \
                or not expense.tax_amount:
            return 0.0
        if expense.expense_scan_mixed_rates:
            return _("several rates")
        rates = expense.tax_ids.filtered(
            lambda t: t.amount_type == 'percent').mapped('amount')
        return rates[0] / 100.0 if len(rates) == 1 else ''

    @api.model
    def _lines(self, expenses):
        """Numbered lines, in chronological order."""
        ordered = expenses.sorted(lambda e: (
            e.date or date.min, e.scan_datetime or fields.Datetime.from_string('1970-01-01'), e.id))
        lines = []
        for number, expense in enumerate(ordered, start=1):
            receipts = self._receipts(expense)
            if len(receipts) > 1:
                labels = ["%s.%s" % (number, index) for index in range(1, len(receipts) + 1)]
                label = _("%(first)s to %(last)s", first=labels[0], last=labels[-1])
            else:
                labels = [str(number)] * len(receipts)
                label = str(number)
            if expense.expense_scan_nights_required:
                quantity = expense.expense_scan_nights or 1
            elif self._is_flat_rate(expense):
                quantity = expense.quantity or 1
            else:
                quantity = 1
            rate = self._tax_rate(expense)
            total_ttc = expense.total_amount
            # Zero rate: no recoverable VAT (flat rate, hotel, transport,
            # receipt without readable VAT).
            total_tva = 0.0 if rate == 0.0 else expense.tax_amount
            total_ht = total_ttc - total_tva
            lines.append({
                'expense': expense,
                'number': number,
                'n': label,
                'receipts': list(zip(labels, receipts)),
                'flat_rate': self._is_flat_rate(expense),
                'date': expense.date,
                'categorie': expense.product_id.name or '',
                'description': expense.name or '',
                'enseigne': expense.expense_scan_merchant or '',
                'mission': expense.project_id.name or '',
                'quantite': quantity,
                'prix_unitaire_ht': total_ht / quantity,
                'total_ht': total_ht,
                'taux_tva': rate,
                'taux_label': ("%g %%" % (rate * 100)) if isinstance(rate, float) else rate,
                'quantite_label': "%g" % quantity,
                'tva_unitaire': total_tva / quantity,
                'total_tva': total_tva,
                'ttc_unitaire': total_ttc / quantity,
                'total_ttc': total_ttc,
                'mode_paiement': dict(expense._fields['payment_mode']._description_selection(
                    self.env)).get(expense.payment_mode, ''),
                'salarie': expense.employee_id.name or '',
            })
        return lines

    @api.model
    def _header(self, expenses, lines):
        dates = [line['date'] for line in lines if line['date']]
        start, end = (min(dates), max(dates)) if dates else (False, False)
        # The project is only named when a project filter is applied:
        # expenses picked by hand do not make a service.
        projects = self.env['project.project'].browse(
            self.env.context.get('expense_scan_sheet_project_ids') or [])
        return {
            'salarie': ", ".join(expenses.employee_id.mapped('name')),
            'prestation': (", ".join(projects.mapped('name')) if projects
                           else _("Statement of selected expenses")),
            'vat_rows': self._vat_summary(lines),
            'mois': start.replace(day=1) if start else False,
            'periode': (_("from %(start)s to %(end)s",
                          start=format_date(self.env, start), end=format_date(self.env, end))
                        if start else ''),
            'date_debut': start,
            'date_fin': end,
            'total_ht': sum(line['total_ht'] for line in lines),
            'total_tva': sum(line['total_tva'] for line in lines),
            'total_ttc': sum(line['total_ttc'] for line in lines),
            'societe': expenses.company_id[:1].name or '',
        }

    @api.model
    def _vat_summary(self, lines):
        """Recoverable VAT per rate, for the tax return.

        A rate is grouped whatever the use (10% on services and on goods
        are added up). A receipt with mixed rates, whose detail is not kept,
        is counted at 20%, like VAT of unknown rate.
        """
        groups = {}
        for line in lines:
            rate = line['taux_tva']
            mixed = not isinstance(rate, float)
            if mixed:
                rate = 0.2 if line['total_tva'] else 0.0
            group = groups.setdefault(rate, {
                'rate': rate, 'total_ht': 0.0, 'total_tva': 0.0, 'total_ttc': 0.0,
                'count': 0, 'mixed': False})
            group['total_ht'] += line['total_ht']
            group['total_tva'] += line['total_tva']
            group['total_ttc'] += line['total_ttc']
            group['count'] += 1
            group['mixed'] = group['mixed'] or (mixed and bool(line['total_tva']))
        rows = sorted(groups.values(), key=lambda group: -group['rate'])
        for row in rows:
            if row['rate']:
                row['label'] = "%g %%" % (row['rate'] * 100)
                if row['mixed']:
                    row['label'] += _(" (incl. mixed rates)")
            else:
                row['label'] = _("No recoverable VAT")
        return rows

    # ------------------------------------------------------------------
    # Template checks
    # ------------------------------------------------------------------

    @api.model
    def _check_template(self, template, expenses):
        problems = []
        if template.reinvoice_only:
            wrong = expenses.filtered(lambda e: e.reinvoice_mode != 'project')
            if wrong:
                problems.append(_(
                    "Template \"%(template)s\" only accepts re-invoiced expenses. "
                    "These are not: %(names)s.", template=template.name,
                    names=", ".join("%s (%s)" % (e.name, format_date(self.env, e.date))
                                    for e in wrong)))
        if template.single_project and len(expenses.project_id) > 1:
            problems.append(_(
                "Template \"%(template)s\" expects a single project; the selection "
                "has %(count)s: %(names)s.", template=template.name,
                count=len(expenses.project_id),
                names=", ".join(expenses.project_id.mapped('name'))))
        if problems:
            raise UserError("\n\n".join(problems))

    # ------------------------------------------------------------------
    # Excel
    # ------------------------------------------------------------------

    @api.model
    def _excel(self, template, expenses):
        """The template workbook, filled with one employee's expenses."""
        try:
            import openpyxl  # noqa: PLC0415
            from openpyxl.utils import column_index_from_string  # noqa: PLC0415
            from openpyxl.formula.translate import Translator  # noqa: PLC0415
        except ImportError as error:
            raise UserError(_("The openpyxl library is missing on the server.")) from error
        if not template.file:
            raise UserError(_("Template \"%s\" has no Excel file.", template.name))
        self._check_template(template, expenses)

        lines = self._lines(expenses)
        header = self._header(expenses, lines)
        book = openpyxl.load_workbook(io.BytesIO(base64.b64decode(template.file)))
        sheet = book[template.sheet_name] if template.sheet_name else book.worksheets[0]

        first, last = template.first_row, template.last_row
        # References taken before anything moves: the first row gives the
        # style of the table body, the last one the style of its bottom (end
        # border) and the formulas of an empty row.
        body = {c.column: copy(c._style) for c in sheet[first] if c.has_style}
        bottom = {c.column: copy(c._style) for c in sheet[last] if c.has_style}
        formulas = {c.column: c.value for c in sheet[last]
                    if isinstance(c.value, str) and c.value.startswith('=')}
        height = sheet.row_dimensions[first].height

        # Exactly one row per expense: extra rows go, missing ones are added,
        # and the totals follow.
        self._resize_table(sheet, last, len(lines) - (last - first + 1))
        new_last = first + len(lines) - 1

        columns = {column_index_from_string(c.cell.strip().upper()): c.value
                   for c in template.column_ids if c.kind == 'line' and c.value}

        for index, line in enumerate(lines):
            row = first + index
            dimension = sheet.row_dimensions[row]
            dimension.hidden = False
            dimension.height = height
            for column, style in body.items():
                sheet.cell(row=row, column=column)._style = copy(style)
            for column, formula in formulas.items():
                if column not in columns:
                    sheet.cell(row=row, column=column).value = Translator(
                        formula, origin=sheet.cell(row=last, column=column).coordinate
                    ).translate_formula(sheet.cell(row=row, column=column).coordinate)
            for column, value in columns.items():
                sheet.cell(row=row, column=column).value = line[value] if line[value] != '' else None
        for column, style in bottom.items():
            sheet.cell(row=new_last, column=column)._style = copy(style)

        for column in template.column_ids.filtered(lambda c: c.kind == 'header' and c.value):
            sheet[column.cell.strip().upper()].value = header[column.value] or None

        output = io.BytesIO()
        book.save(output)
        return output.getvalue()

    @api.model
    def _resize_table(self, sheet, last, delta):
        """Add (``delta`` > 0) or remove (< 0) rows at the end of the table.

        openpyxl moves the cells but not the row heights, the hidden state,
        the merges or the ranges of the formulas further down. Those are
        realigned here so that the totals row keeps its look and sums
        exactly the rows of the table.
        """
        if not delta:
            return
        new_last = last + delta
        below = {row: (dim.height, dim.hidden)
                 for row, dim in sheet.row_dimensions.items() if row > last}
        if delta > 0:
            sheet.insert_rows(last + 1, delta)
        else:
            sheet.delete_rows(new_last + 1, -delta)
        for row in [row for row in sheet.row_dimensions if row > new_last]:
            del sheet.row_dimensions[row]
        for row, (height, hidden) in below.items():
            dimension = sheet.row_dimensions[row + delta]
            dimension.height = height
            dimension.hidden = hidden

        for merged in list(sheet.merged_cells.ranges):
            if merged.min_row > last:
                merged.shift(0, delta)
            elif merged.max_row > new_last:
                # Merge inside the removed rows: dropped.
                sheet.merged_cells.remove(merged)
        def shift(match):
            start = int(match.group(2))
            text = "%s%s" % (match.group(1), start + delta if start > last else start)
            if match.group(3):
                end = int(match.group(4))
                if end > last:
                    end += delta
                elif end == last and start <= last:
                    end = new_last  # range running to the bottom of the table
                text += ":%s%s" % (match.group(3), end)
            return text

        # The whole sheet: a total may be reused elsewhere, in a header
        # ("=L70") or in another formula of the table footer.
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith('='):
                    cell.value = CELL_REF_RE.sub(shift, cell.value)

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------

    @api.model
    def _summary_pdf(self, expenses, with_receipts=True):
        report = self.env.ref('expense_scan.action_report_expense_sheet')
        lines = self._lines(expenses)
        pdf, _kind = self.env['ir.actions.report']._render_qweb_pdf(
            report, res_ids=[line['expense'].id for line in lines],
            data={'expense_ids': expenses.ids})
        if not with_receipts:
            return pdf
        receipts = self._receipts_pdf(lines)
        if not receipts:
            return pdf
        from odoo.tools.pdf import merge_pdf  # noqa: PLC0415
        return merge_pdf([pdf, receipts])

    @api.model
    def _receipts_pdf(self, lines):
        """The receipts, one per page, each stamped with its number."""
        from odoo.tools.pdf import PdfFileReader, PdfFileWriter  # noqa: PLC0415
        writer = PdfFileWriter()
        pages = 0
        for line in lines:
            for label, attachment in line['receipts']:
                for page in self._attachment_pages(attachment, label, PdfFileReader):
                    writer.addPage(page)
                    pages += 1
        if not pages:
            return b''
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    @api.model
    def _attachment_pages(self, attachment, label, PdfFileReader):
        raw = attachment.raw or b''
        mimetype = attachment.mimetype or ''
        try:
            if mimetype.startswith('image/'):
                stream = io.BytesIO(self._image_page(raw, label))
                return [PdfFileReader(stream, strict=False).getPage(0)]
            reader = PdfFileReader(io.BytesIO(raw), strict=False)
            count = reader.getNumPages()
            pages = []
            for index in range(count):
                page = reader.getPage(index)
                width = float(abs(page.mediaBox.getWidth()))
                height = float(abs(page.mediaBox.getHeight()))
                text = label if count == 1 else "%s (%s/%s)" % (label, index + 1, count)
                stamp = PdfFileReader(io.BytesIO(self._stamp(text, width, height)), strict=False)
                page.mergePage(stamp.getPage(0))
                pages.append(page)
            return pages
        except Exception:  # noqa: BLE001 - unreadable receipt
            _logger.warning("Unreadable receipt: %s", attachment.name, exc_info=True)
            stream = io.BytesIO(self._unreadable_page(attachment.name, label))
            return [PdfFileReader(stream, strict=False).getPage(0)]

    @api.model
    def _image_page(self, raw, label):
        """An A4 page with the photo, as large as possible."""
        from PIL import Image, ImageOps  # noqa: PLC0415
        from reportlab.lib.pagesizes import A4  # noqa: PLC0415
        from reportlab.lib.utils import ImageReader  # noqa: PLC0415
        from reportlab.pdfgen import canvas  # noqa: PLC0415

        try:
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
        except OSError:
            # Pillow built without WebP: same fallback as the scan.
            decoded = preprocess.decode_with_opencv(raw)
            if decoded is None:
                raise
            image = Image.fromarray(decoded[:, :, ::-1])
        # A phone photo weighs several MB: reduced to a size readable in print,
        # then JPEG, so that the PDF does not carry the raw pixels.
        image.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        jpeg = io.BytesIO()
        image.save(jpeg, format='JPEG', quality=IMAGE_QUALITY, optimize=True)
        jpeg.seek(0)
        width, height = A4
        margin, band = 28, 40
        box_w, box_h = width - 2 * margin, height - 2 * margin - band
        ratio = min(box_w / image.width, box_h / image.height)
        draw_w, draw_h = image.width * ratio, image.height * ratio
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        self._draw_label(pdf, label, width, height)
        pdf.drawImage(ImageReader(jpeg), (width - draw_w) / 2,
                      margin + (box_h - draw_h) / 2, draw_w, draw_h)
        pdf.showPage()
        pdf.save()
        return output.getvalue()

    @api.model
    def _stamp(self, label, width, height):
        from reportlab.pdfgen import canvas  # noqa: PLC0415
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(width, height))
        self._draw_label(pdf, label, width, height)
        pdf.showPage()
        pdf.save()
        return output.getvalue()

    @api.model
    def _unreadable_page(self, name, label):
        from reportlab.lib.pagesizes import A4  # noqa: PLC0415
        from reportlab.pdfgen import canvas  # noqa: PLC0415
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=A4)
        self._draw_label(pdf, label, *A4)
        pdf.setFont('Helvetica', 12)
        pdf.drawString(40, A4[1] / 2, _("Unreadable receipt: %s", name or ''))
        pdf.showPage()
        pdf.save()
        return output.getvalue()

    @api.model
    def _draw_label(self, pdf, label, width, height):
        """Draw the receipt number in a box."""
        text = _("No. %s", label)
        pdf.setFont('Helvetica-Bold', 16)
        text_w = pdf.stringWidth(text, 'Helvetica-Bold', 16)
        pdf.setFillColorRGB(1, 1, 1)
        pdf.setStrokeColorRGB(0, 0, 0)
        pdf.rect(width - text_w - 36, height - 38, text_w + 20, 26, fill=1, stroke=1)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.drawString(width - text_w - 26, height - 30, text)

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    @api.model
    def _file_stem(self, expenses, prefix):
        lines = self._lines(expenses)
        header = self._header(expenses, lines)
        start = header['date_debut']
        period = start.strftime('%Y-%m') if start else ''
        stem = " - ".join(part for part in (prefix, header['salarie'], period) if part)
        return re.sub(r'[\\/:*?"<>|]+', '-', stem)

    @api.model
    def _build(self, expenses, summary=False, excel_template=False, receipts=False):
        """``[(file name, content)]`` for each employee and each output."""
        files = []
        for employee in expenses.employee_id:
            own = expenses.filtered(lambda e: e.employee_id == employee)
            if summary:
                files.append(("%s.pdf" % self._file_stem(own, _("Expense sheet")),
                              self._summary_pdf(own)))
            if excel_template:
                files.append(("%s.xlsx" % self._file_stem(own, excel_template.name),
                              self._excel(excel_template, own)))
            if receipts:
                content = self._receipts_pdf(self._lines(own))
                if content:
                    files.append(("%s.pdf" % self._file_stem(own, _("Receipts")), content))
        return files

    @api.model
    def _zip(self, files):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            seen = set()
            for name, content in files:
                unique, index = name, 2
                while unique in seen:
                    stem, dot, ext = name.rpartition('.')
                    unique = "%s (%s).%s" % (stem, index, ext)
                    index += 1
                seen.add(unique)
                archive.writestr(unique, content)
        return output.getvalue()


class ExpenseSheetReport(models.AbstractModel):
    _name = 'report.expense_scan.report_expense_sheet'
    _description = "Expense sheet (PDF)"

    @api.model
    def _get_report_values(self, docids, data=None):
        ids = (data or {}).get('expense_ids') or docids
        expenses = self.env['hr.expense'].browse(ids)
        Sheet = self.env['expense.scan.sheet']
        lines = Sheet._lines(expenses)
        header = Sheet._header(expenses, lines)
        currency = expenses.company_id[:1].currency_id or self.env.company.currency_id
        return {
            'doc_ids': expenses.ids,
            'doc_model': 'hr.expense',
            'docs': expenses,
            'o': expenses[:1],
            'company': expenses.company_id[:1] or self.env.company,
            'lines': lines,
            'header': header,
            'currency': currency,
            'format_date': lambda value: format_date(self.env, value) if value else '',
        }


class ExpenseScanSheetWizard(models.TransientModel):
    _name = 'expense.scan.sheet.wizard'
    _description = "Expense sheet printing"

    expense_ids = fields.Many2many('hr.expense', string="Selected expenses")
    # Opened from the "Expense sheets" menu, the wizard selects the expenses
    # of a period itself, without going through the list and "Actions".
    period = fields.Selection(
        [('current', "Current month"), ('previous', "Previous month"),
         ('custom', "Other period")],
        string="Period")
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")
    employee_ids = fields.Many2many(
        'hr.employee', string="Employees",
        help="Empty: every employee whose expenses you can see.")
    scope = fields.Selection(
        [('all', "All expenses"), ('reinvoice', "Re-invoiced expenses only")],
        string="Expenses", default='reinvoice', required=True)
    project_ids = fields.Many2many(
        'project.project', string="Projects",
        help="Empty: every project of the selection.")
    available_project_ids = fields.Many2many(
        'project.project', compute='_compute_available_project_ids')
    summary = fields.Boolean(string="PDF summary with receipts", default=True)
    excel = fields.Boolean(string="Excel export")
    template_id = fields.Many2one('expense.scan.export.template', string="Template")
    receipts = fields.Boolean(string="Receipts only (PDF)")
    selected_count = fields.Integer(compute='_compute_selected')
    selected_summary = fields.Char(compute='_compute_selected')
    result_file = fields.Binary(readonly=True, attachment=False)
    result_name = fields.Char(readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'hr.expense':
            values['expense_ids'] = [(6, 0, self.env.context.get('active_ids') or [])]
        elif values.get('period'):
            date_from, date_to = self._period_dates(values['period'])
            values.setdefault('date_from', fields.Date.to_string(date_from))
            values.setdefault('date_to', fields.Date.to_string(date_to))
            employee = self.env.user.employee_id
            if employee and 'employee_ids' not in values:
                values['employee_ids'] = [(6, 0, employee.ids)]
            values['expense_ids'] = [(6, 0, self._period_expenses(
                values['date_from'], values['date_to'],
                employee.ids if employee else []).ids)]
        return values

    @api.model
    def _period_dates(self, period, today=None):
        """First and last day of the current or previous month."""
        today = today or fields.Date.context_today(self)
        first = today.replace(day=1)
        if period == 'previous':
            last = first - timedelta(days=1)
            return last.replace(day=1), last
        next_month = (first + timedelta(days=32)).replace(day=1)
        return first, next_month - timedelta(days=1)

    @api.model
    def _period_expenses(self, date_from, date_to, employee_ids):
        """The period's expenses the user can see, refused ones excepted."""
        if not (date_from and date_to):
            return self.env['hr.expense']
        domain = [('date', '>=', date_from), ('date', '<=', date_to),
                  ('state', '!=', 'refused')]
        if employee_ids:
            domain.append(('employee_id', 'in', employee_ids))
        return self.env['hr.expense'].search(domain)

    @api.onchange('period', 'date_from', 'date_to', 'employee_ids')
    def _onchange_period(self):
        if not self.period:
            return
        if self.period != 'custom':
            self.date_from, self.date_to = self._period_dates(self.period)
        self.expense_ids = self._period_expenses(
            self.date_from, self.date_to, self.employee_ids._origin.ids)

    @api.depends('expense_ids')
    def _compute_available_project_ids(self):
        for wizard in self:
            wizard.available_project_ids = wizard.expense_ids.project_id

    @api.onchange('excel')
    def _onchange_excel(self):
        # Suggest the first template of the list by default (the order is
        # set in the configuration).
        if self.excel and not self.template_id:
            self.template_id = self.env['expense.scan.export.template'].search([], limit=1)

    @api.onchange('template_id')
    def _onchange_template_id(self):
        # A template restricted to re-invoiced expenses imposes that filter.
        if self.template_id.reinvoice_only:
            self.scope = 'reinvoice'

    def _selected(self):
        self.ensure_one()
        # While the form is being edited, the linked records are temporary
        # copies. The comparison is made on the real ones (_origin):
        # otherwise a chosen project would match no expense ("0 expenses").
        expenses = self.expense_ids._origin
        if self.scope == 'reinvoice':
            expenses = expenses.filtered(lambda e: e.reinvoice_mode == 'project')
        project_ids = set(self.project_ids._origin.ids)
        if project_ids:
            expenses = expenses.filtered(lambda e: e.project_id.id in project_ids)
        return expenses

    @api.depends('expense_ids', 'scope', 'project_ids')
    def _compute_selected(self):
        for wizard in self:
            selected = wizard._selected()
            wizard.selected_count = len(selected)
            wizard.selected_summary = _(
                "%(count)s expense(s), %(employees)s employee(s), %(amount).2f incl. tax",
                count=len(selected), employees=len(selected.employee_id),
                amount=sum(selected.mapped('total_amount')))

    def action_generate(self):
        self.ensure_one()
        expenses = self._selected()
        if not expenses:
            raise UserError(_("No expense matches the filters."))
        if not (self.summary or self.excel or self.receipts):
            raise UserError(_("Choose at least one output."))
        if self.excel and not self.template_id:
            raise UserError(_("Choose the Excel template."))
        Sheet = self.env['expense.scan.sheet'].with_context(
            expense_scan_sheet_project_ids=self.project_ids.ids)
        expenses = expenses.with_context(expense_scan_sheet_project_ids=self.project_ids.ids)
        files = Sheet._build(
            expenses, summary=self.summary,
            excel_template=self.excel and self.template_id,
            receipts=self.receipts)
        if not files:
            raise UserError(_("Nothing to produce: none of these expenses has a receipt."))
        if len(files) == 1:
            name, content = files[0]
        else:
            name = "%s.zip" % self.env['expense.scan.sheet']._file_stem(expenses, _("Expense sheets"))
            content = self.env['expense.scan.sheet']._zip(files)
        self.write({'result_file': base64.b64encode(content), 'result_name': name})
        # The client downloads the file, then closes the dialog. A plain link
        # would leave it open, and a new tab would be blocked.
        return {
            'type': 'ir.actions.client',
            'tag': 'expense_scan_download',
            'params': {
                'url': '/web/content/?model=%s&id=%s&field=result_file'
                       '&filename_field=result_name&download=true' % (self._name, self.id),
            },
        }
