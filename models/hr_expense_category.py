# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Expense category, recognised from the merchant or the words of the receipt.

The clues, from the most to the least reliable:

1. **The shared merchant history.** A submitted expense carries its
   merchant and category, confirmed by the employee. The next expense from
   the same merchant gets the same category, whoever the employee. The
   merchant as the OCR read it is kept apart: a logo is misread, but always
   the same way, so "Rchan" points to Auchan once someone has corrected it.
   This is the heaviest clue, without being final: a receipt that clearly
   points to another category wins.
2. Additional clues:

   * the **activity code** printed on the receipt (APE, MCC) or found in the
     Sirene database from the SIRET number;
   * the **words of the receipt**, declared on each category, in every
     language met while travelling;
   * a **known brand** at the top of the receipt, from OpenStreetMap's Name
     Suggestion Index.

Without certainty, no category is chosen: the default category stays and
the field is flagged for review. Flat rates (mileage, scales) are never
suggested: they do not come from a receipt. They are recognised by their
fixed price or their distance unit; a category merely "without VAT", like
the hotel, can still be suggested.
"""
import base64
import logging
import re
from collections import Counter

from odoo import _, api, fields, models
from odoo.tools import file_open

from ..ocr import categorize, lexicon, parser
from ..ocr.types import OcrLine, OcrWord

_logger = logging.getLogger(__name__)

#: Minimum share of a merchant's expenses filed in a category for that
#: category to apply to the next one.
HISTORY_MAJORITY = 0.6
#: Weight of the history of a merchant read identically, then of a merchant
#: that only looks alike: enough on its own, but beaten by a receipt that
#: clearly points to another category.
HISTORY_WEIGHT = 5.0
HISTORY_FUZZY_WEIGHT = 3.0
#: States where someone has confirmed the category.
CONFIRMED_STATES = ('submitted', 'approved', 'posted', 'in_payment', 'paid')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    expense_scan_keywords = fields.Text(
        string="Receipt words",
        help="Words that, read on a receipt, point to this category: one per "
             "line, in every useful language, merchant names included "
             "(\"Ibis\", \"nocleg\", \"pedaggio\"...). Accents and case are "
             "ignored, and one misread letter is tolerated in words of five "
             "letters or more.\n\n"
             "Merchants already filed are learnt on their own when expenses "
             "are submitted: no need to enter them all here.",
    )

    @api.model
    def _expense_scan_seed_keywords(self):
        """Suggest words for the categories that have none.

        Each expense family (hotel, meal, fuel...) goes to a single category.
        A category that already has words, even one, is left alone.
        """
        to_fill = [(template, family)
                   for template, family in self._expense_scan_family_templates().items()
                   if not (template.expense_scan_keywords or '').strip()]
        for template, family in to_fill:
            template.expense_scan_keywords = "\n".join(
                dict.fromkeys(lexicon.DEFAULT_KEYWORDS[family]))
        return len(to_fill)

    @api.model
    def _expense_scan_icon_key(self, template):
        """Name of the bundled icon that suits this category, or ``None``."""
        labels = (template.name, template.default_code)
        family = lexicon.family_of(*labels)
        if family:
            return {'lodging': 'lodging', 'meal': 'meal', 'train_air': 'train_air',
                    'car_rental': 'car_rental', 'taxi': 'taxi', 'fuel': 'fuel',
                    'toll_parking': 'toll_parking', 'telecom': 'telecom'}.get(family)
        words = lexicon.fold(" ".join(label for label in labels if label)).split()
        starts = lambda prefixes: any(w.startswith(p) for w in words for p in prefixes)  # noqa: E731
        if starts(('invit', 'affaire')):
            return 'invitation'
        if starts(('igd', 'bareme', 'forfait')):
            if starts(('logement', 'hebergement', 'nuit')):
                return 'house'
            if starts(('repas', 'meal')):
                return 'meal'
        return None

    @api.model
    def _expense_scan_seed_icons(self):
        """Give an icon to the categories that have none.

        Same colours and line as the icons Odoo ships for its own categories.
        A category that already has an image, chosen by a user or shipped by
        Odoo, is left alone.
        """
        seeded = 0
        for template in self.search([('can_be_expensed', '=', True), ('image_1920', '=', False)]):
            key = self._expense_scan_icon_key(template)
            if not key:
                continue
            try:
                with file_open('expense_scan/static/img/categories/%s.svg' % key, 'rb') as icon:
                    template.image_1920 = base64.b64encode(icon.read())
                seeded += 1
            except OSError:
                _logger.warning("Category icon not found: %s", key)
        return seeded

    @api.model
    def _expense_scan_add_keywords(self, additions):
        """Add words, per family, to the categories that serve them.

        Only missing words are added, at the end: the changes already made
        on the category are kept.
        """
        for template, family in self._expense_scan_family_templates().items():
            words = additions.get(family)
            current = template.expense_scan_keywords or ''
            if not words or not current.strip():
                continue
            known = set(lexicon.split_keywords(current))
            missing = [word for word in words if lexicon.fold(word) not in known]
            if missing:
                template.expense_scan_keywords = current.rstrip('\n') + '\n' + '\n'.join(missing)

    def _expense_scan_remove_keywords(self, removals):
        """Remove words, per family, from the categories that serve them.

        Only these words are removed; the others keep their spelling and
        their order.
        """
        for template, family in self._expense_scan_family_templates().items():
            words = {lexicon.fold(word) for word in removals.get(family, ())}
            current = template.expense_scan_keywords or ''
            if not words or not current.strip():
                continue
            chunks = [chunk.strip() for chunk in re.split(r"[\n,;]+", current) if chunk.strip()]
            kept = [chunk for chunk in chunks if lexicon.fold(chunk) not in words]
            if len(kept) != len(chunks):
                template.expense_scan_keywords = '\n'.join(kept)

    @api.model
    def _expense_scan_family_templates(self):
        """``{category: family}``: the first active category of each family.

        Archived categories are ignored: otherwise Odoo's former "Travel &
        Accommodation" would take the hotel's place.
        """
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        templates = self.search([('can_be_expensed', '=', True)], order='sequence, id')
        installed = [code for code, _name in self.env['res.lang'].get_installed()]
        result = {}
        taken = set()
        for template in templates:
            # Flat rate: fixed price; mileage: distance unit. A category
            # "without VAT" is not a flat rate (hotel, train: no recoverable
            # VAT, but a receipt).
            if template.standard_price or template.uom_id in distances:
                continue
            # The name in every installed language: a category named in
            # French is recognised as well as its English name. Only
            # installed ones: reading a name in another language fails.
            labels = [template.default_code, template.name]
            labels += [template.with_context(lang=code).name for code in installed]
            family = lexicon.family_of(*labels)
            if not family or family in taken:
                continue
            taken.add(family)
            result[template] = family
        return result


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_merchant = fields.Char(
        string="Merchant",
        copy=False,
        help="Business that issued the receipt. Correct it if the scan misread "
             "it: on submission, the merchant and the category are used to "
             "file the next receipts, for the whole team.",
    )
    expense_scan_merchant_key = fields.Char(
        string="Merchant key",
        compute='_compute_expense_scan_merchant_key',
        store=True,
        index=True,
    )
    expense_scan_merchant_read = fields.Char(
        string="Merchant as read",
        readonly=True,
        copy=False,
        index=True,
        help="The merchant as the OCR read it, before any correction. A logo "
             "reads badly, but the same way on every receipt: this is what "
             "lets it be recognised the next time.",
    )
    expense_scan_guessed_product_id = fields.Many2one(
        comodel_name='product.product',
        string="Suggested category",
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    expense_scan_category_reason = fields.Char(
        string="Category recognised",
        readonly=True,
        copy=False,
    )

    @api.depends('expense_scan_merchant')
    def _compute_expense_scan_merchant_key(self):
        for expense in self:
            expense.expense_scan_merchant_key = (
                lexicon.merchant_key(expense.expense_scan_merchant) or False)

    # ------------------------------------------------------------------
    # Shared history
    # ------------------------------------------------------------------

    def _expense_scan_known_merchants(self):
        """Merchants already filed: ``{key: {'names': ..., 'products': ...}}``.

        Read with elevated rights: the history is shared by the team, while
        an employee only sees their own expenses. Only merchant names and
        categories are returned.

        Limited to the company: one company's history does not influence
        another's filing, even though the access filter is lifted here.

        The expense being scanned is excluded: scanned again after approval,
        it would recognise itself and correct nothing.
        """
        companies = self.company_id or self.env.company
        groups = self.sudo()._read_group(
            [('state', 'in', CONFIRMED_STATES),
             ('company_id', 'in', companies.ids),
             ('id', 'not in', self.ids),
             ('product_id', '!=', False),
             '|', ('expense_scan_merchant_key', '!=', False),
                  ('expense_scan_merchant_read', '!=', False)],
            groupby=['expense_scan_merchant_key', 'expense_scan_merchant_read',
                     'expense_scan_merchant', 'product_id'],
            aggregates=['__count'],
        )
        known = {}
        for key, read_key, name, product, count in groups:
            for found in {key, read_key} - {False, None, ''}:
                entry = known.setdefault(found, {'names': Counter(), 'products': Counter()})
                if name:
                    entry['names'][name] += count
                entry['products'][product.id] += count
        return known

    # ------------------------------------------------------------------
    # Recognition
    # ------------------------------------------------------------------

    def _expense_scan_category_is_free(self, company):
        """Tell whether the scan may still choose the category.

        True as long as nobody chose it: empty, default category, or the one
        the scan suggested. On a new expense a receipt was just attached to,
        true unless the user chose it before uploading.
        """
        self.ensure_one()
        # A category chosen by hand stays, even when it is the default one.
        if 'product_id' in self._expense_scan_kept_fields():
            return False
        if self.env.context.get('expense_scan_new_receipt'):
            return True
        product = self.product_id
        return (not product
                or product == company._expense_scan_default_product()
                or product == self.expense_scan_guessed_product_id)

    def _expense_scan_target_product(self, values):
        """The category the expense will have once these values are written."""
        if values.get('product_id'):
            return self.env['product.product'].browse(values['product_id'])
        return self.product_id

    def _expense_scan_product_no_vat(self, product):
        """Tell whether this category excludes any recoverable VAT."""
        if not product:
            return False
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        return bool(product.expense_scan_no_vat) or product.uom_id in distances

    def _expense_scan_guessable(self, product, company):
        """A category a receipt can point to."""
        if not product or not product.can_be_expensed:
            return False
        if product.company_id and product.company_id != company:
            return False
        if product.standard_price:
            # Flat rate or scale: its price is fixed, no receipt leads to it.
            # A category "without VAT" can still be suggested: the hotel or
            # the train have a receipt, only their VAT is not recoverable.
            return False
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        return product.uom_id not in distances

    def _expense_scan_keyword_categories(self, company):
        """``{category id: folded words}`` of the categories that declare words."""
        products = self.env['product.product'].sudo().search([
            ('can_be_expensed', '=', True),
            ('expense_scan_keywords', '!=', False),
            ('company_id', 'in', (False, company.id)),
        ])
        return {
            product.id: lexicon.split_keywords(product.expense_scan_keywords)
            for product in products
            if self._expense_scan_guessable(product, company)
        }

    def _expense_scan_reason(self, reason, number=None):
        """Sentence explaining the category clue that was kept.

        ``reason``: ``(kind, detail)``, as returned by ``categorize.score``.
        """
        kind, detail = reason
        if kind == categorize.ACTIVITY:
            return _("from the printed activity code %s", detail)
        if kind == categorize.SIRET:
            return _("from SIRET %(number)s (activity %(naf)s)", number=number, naf=detail)
        if kind == categorize.UNIT:
            return _("from a price per litre or per kWh")
        if kind == categorize.BRAND:
            return _("from the brand \"%s\"", detail)
        return _("from the words of the receipt")

    def _expense_scan_recognize(self, result, company):
        """Merchant and category of the receipt.

        Returns ``(merchant, read key, category, reason)``; the category and
        the reason are empty when nothing is certain.
        """
        self.ensure_one()
        lines = [line.text for line in result.lines]
        read_name = result.value('merchant')
        read_key = lexicon.merchant_key(read_name) or False

        known = self._expense_scan_known_merchants()
        key = read_key if read_key in known else lexicon.match_merchant(lines, known)
        merchant = read_name
        if key and known[key]['names']:
            merchant = known[key]['names'].most_common(1)[0][0]

        Product = self.env['product.product'].sudo()
        # All clues add up: merchant history, receipt words, activity code
        # printed or found from the SIRET, known brand.
        family_products = {}
        for template, family in self.env['product.template'].sudo() \
                ._expense_scan_family_templates().items():
            product = template.product_variant_id
            if self._expense_scan_guessable(product, company):
                family_products[family] = product.id
        number = result.value('company_number')
        scores, signals, brand = categorize.score(
            lines, self._expense_scan_keyword_categories(company), family_products,
            activity=result.value('activity'),
            naf_of=lambda: self.env['expense.scan.sirene']._expense_scan_activity(number))
        reasons = {product_id: [(weight, self._expense_scan_reason(reason, number))
                                for weight, reason in found]
                   for product_id, found in signals.items()}

        # The default category says nothing about the merchant: it is the
        # category of an unfiled expense. Counting it in the history would
        # make it repeat itself (a restaurant scanned while recognition
        # failed would stay "Expenses").
        history = Counter(known[key]['products']) if key else Counter()
        history.pop(company._expense_scan_default_product().id, None)
        if history:
            products = history
            product_id, count = products.most_common(1)[0]
            product = Product.browse(product_id).exists()
            share = count / sum(products.values())
            if share >= HISTORY_MAJORITY and self._expense_scan_guessable(product, company):
                # A merchant read identically weighs more than a likeness. In
                # both cases, a receipt that clearly points to another
                # category (activity code, brand, words) wins.
                weight = (HISTORY_WEIGHT if key == read_key else HISTORY_FUZZY_WEIGHT) * share
                scores[product.id] = scores.get(product.id, 0.0) + weight
                reasons.setdefault(product.id, []).append((weight, _(
                    "from the merchant \"%s\", already filed this way", merchant or key)))
        if brand and not key:
            # The recognised brand is preferred to the first line of the
            # receipt (often a misread logo or an address).
            merchant = brand

        product_id = lexicon.pick_category(scores)
        # One log line per scan, to diagnose the choice of a category or the
        # lack of one.
        _logger.info(
            "expense_scan: category scores=%s activity=%s merchant=%s chosen=%s",
            {Product.browse(pid).display_name: round(score, 1) for pid, score in scores.items()},
            result.value('activity'), read_key, product_id)
        if product_id:
            reason = max(reasons[product_id], key=lambda item: item[0])[1]
            return merchant, read_key, Product.browse(product_id), reason
        return merchant, read_key, Product, False

    def _expense_scan_category_values(self, result, company):
        """Merchant and category values to write on the expense."""
        self.ensure_one()
        merchant, read_key, product, reason = self._expense_scan_recognize(result, company)
        values = {'expense_scan_merchant_read': read_key}
        # A merchant corrected by hand stays the employee's, even when the
        # scan runs again.
        corrected = (self.expense_scan_merchant and self.expense_scan_merchant_key
                     != (self.expense_scan_merchant_read or False)
                     and self.expense_scan_merchant_key != lexicon.merchant_key(merchant))
        if not corrected:
            # Without a readable merchant, the previous automatic reading is
            # cleared.
            values['expense_scan_merchant'] = merchant or False
        if self._expense_scan_category_is_free(company):
            if product:
                values.update({
                    'product_id': product.id,
                    'expense_scan_guessed_product_id': product.id,
                    'expense_scan_category_reason': reason,
                })
            else:
                values.update({
                    'expense_scan_guessed_product_id': False,
                    'expense_scan_category_reason': False,
                })
                # A category suggested by a previous reading (of a receipt
                # replaced since, for instance) no longer holds: back to the
                # default category.
                guessed_before = (self.product_id
                                  and self.product_id == self.expense_scan_guessed_product_id)
                default = company._expense_scan_default_product()
                if (not self.product_id or guessed_before) and default:
                    values['product_id'] = default.id
        else:
            # Category chosen by hand: the reason of an earlier automatic
            # choice no longer applies.
            values['expense_scan_category_reason'] = False
        return values

    @api.model
    def _expense_scan_backfill_merchants(self):
        """Extract the merchant from the text already read on past expenses.

        The history is thus fed by every expense scanned since the module
        was installed.
        """
        expenses = self.sudo().search([
            ('scan_raw_text', '!=', False),
            ('expense_scan_merchant_read', '=', False),
        ])
        for expense in expenses:
            lines = [
                OcrLine(words=[OcrWord(text=text, score=1.0, left=0.0,
                                       top=index * 20.0, right=10.0 * len(text),
                                       bottom=index * 20.0 + 14.0)])
                for index, text in enumerate(
                    line for line in expense.scan_raw_text.splitlines() if line.strip())
            ]
            name = parser.extract_merchant(lines).value
            if not name:
                continue
            values = {'expense_scan_merchant_read': lexicon.merchant_key(name)}
            if not expense.expense_scan_merchant:
                values['expense_scan_merchant'] = name
            expense.write(values)
        return len(expenses)
