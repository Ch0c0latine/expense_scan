# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Expense rules: company good practice and customer requirements.

A rule set applies to every expense (meal and hotel limits, personal
expenses to exclude) or to the projects of some customers, who often impose
their own: travel class, rental car category, their own limits.

The check warns without blocking: going over may be justified and the
reading of a receipt may be wrong. Two levels, the breach ("over") and the
doubt ("to check"), shown at the top of the expense and filterable in the
list.
"""
import re

from odoo import _, api, fields, models
from odoo.tools.misc import formatLang

from ..ocr import lexicon

FAMILIES = [
    ('lodging', "Lodging"),
    ('meal', "Meals"),
    ('train_air', "Train / plane"),
    ('car_rental', "Car rental"),
    ('taxi', "Taxi / urban transport"),
    ('fuel', "Fuel"),
    ('toll_parking', "Tolls and parking"),
    ('telecom', "Communication"),
]
MEAL_PERIODS = [
    ('breakfast', "Breakfast"),
    ('lunch', "Lunch"),
    ('dinner', "Dinner"),
    ('lunch_dinner', "Lunch and dinner of the same day"),
    ('day', "All meals of the same day"),
]
#: ACRISS code of a rental car: its first letter gives the category (M, N, E,
#: H: mini and economy; the others cost more).
ACRISS_RE = re.compile(r"\b([MNEHCDIJSRFGPULWOX])[BCDWVLSTFJXPQZEMRHYNGK][MNCABD][RNDQHIECLSABMFVZUX]\b")
ECONOMY_ACRISS = set("MNEH")
#: Words that, on a rental contract, point to a higher category.
RENTAL_UPGRADE_WORDS = ("premium", "prestige", "luxe", "luxury", "suv", "full size", "fullsize")
#: Heading of the general conditions printed after a ticket or an invoice
#: (folded text). What follows describes every fare, not the purchase: the
#: conditions of a train ticket name the "business" class.
CONDITIONS_RE = re.compile(
    r"^(?:\w+ ){0,3}(?:conditions (?:generales|de vente|d utilisation|tarifaires|de transport)"
    r"|cgv|cgu|terms (?:and )?conditions|terms of (?:sale|use|carriage)|general conditions"
    r"|agb|allgemeine geschaftsbedingungen|beforderungsbedingungen|condiciones generales"
    r"|condizioni generali|algemene voorwaarden|warunki ogolne|regulamin)\b")
#: Signs of the rule findings: a breach, or a point to check.
BREACH_SIGN, CHECK_SIGN = "⚠", "ℹ︎"


class ExpenseScanPolicy(models.Model):
    _name = 'expense.scan.policy'
    _description = "Expense rules"
    _order = 'name'

    name = fields.Char(string="Name", required=True, translate=True)
    active = fields.Boolean(default=True)
    apply_to_all = fields.Boolean(
        string="All expenses",
        help="Checks every expense, with or without a project: the company's "
             "good practice rules. Otherwise, only the projects of the "
             "customers or the projects listed below are concerned.")
    company_id = fields.Many2one(
        'res.company', string="Company",
        help="Empty: the rules apply to every company.")
    partner_ids = fields.Many2many(
        'res.partner', string="Customers",
        help="The rules apply to the projects of these customers and of "
             "their branches.")
    project_ids = fields.Many2many(
        'project.project', string="Projects",
        help="Projects subject to these rules when their customer, in Odoo, "
             "is not the end customer.")
    rule_ids = fields.One2many('expense.scan.policy.rule', 'policy_id', string="Rules")
    note = fields.Text(string="Notes", translate=True)

    @api.model_create_multi
    def create(self, vals_list):
        policies = super().create(vals_list)
        policies._expense_scan_recheck()
        return policies

    def write(self, vals):
        result = super().write(vals)
        self._expense_scan_recheck()
        return result

    def _expense_scan_recheck(self):
        """Recompute the warnings of the expenses still in progress.

        Loading the examples at install time triggers it once, at the end,
        instead of once per rule created.
        """
        if self.env.context.get('expense_scan_no_recheck'):
            return
        self.env['expense.scan.policy']._expense_scan_recheck_all()

    @api.model
    def _expense_scan_recheck_all(self):
        expenses = self.env['hr.expense'].sudo().search([
            ('state', 'in', ('draft', 'submitted', 'approved')),
        ])
        expenses._expense_scan_recompute_policy()


class ExpenseScanPolicyRule(models.Model):
    _name = 'expense.scan.policy.rule'
    _description = "Expense rule"
    _order = 'sequence, id'

    policy_id = fields.Many2one('expense.scan.policy', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Rule", required=True, translate=True,
        help="The text shown to the employee when the rule is broken.")
    family = fields.Selection(
        FAMILIES, string="Expense family",
        help="The expense categories of this family, as the module "
             "recognises them. Ignored when categories are chosen.")
    product_ids = fields.Many2many(
        'product.product', string="Categories",
        domain=[('can_be_expensed', '=', True)])
    rule_type = fields.Selection([
        ('max_amount', "Maximum amount per expense"),
        ('daily_max', "Maximum amount per day"),
        ('forbidden_words', "Forbidden words on the receipt"),
        ('rental_class', "Rental: economy category"),
    ], string="Check", required=True, default='max_amount')
    meal_period = fields.Selection(MEAL_PERIODS, string="Meal")
    amount = fields.Float(string="Limit (tax incl.)", digits='Account')
    per_night = fields.Boolean(
        string="Per night", help="The limit is for one night: the amount is "
                                 "divided by the number of nights.")
    extra_amount = fields.Float(string="Extra", digits='Account')
    extra_cities = fields.Char(
        string="Cities with extra",
        help="The extra is added to the limit when the receipt mentions one "
             "of these cities.")
    words = fields.Text(
        string="Words", help="One per line. Their presence on the receipt or "
                             "in the description triggers the warning.")

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules.policy_id._expense_scan_recheck()
        return rules

    def write(self, vals):
        result = super().write(vals)
        self.policy_id._expense_scan_recheck()
        return result


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_policy_alert = fields.Text(
        string="Expense rules", compute='_compute_expense_scan_policy',
        store=True, readonly=True)
    expense_scan_policy_breach = fields.Boolean(
        string="Outside the rules", compute='_compute_expense_scan_policy',
        store=True, readonly=True)
    #: Shown in the list, to spot the exceptions at a glance.
    expense_scan_policy_status = fields.Selection(
        [('breach', BREACH_SIGN), ('check', CHECK_SIGN)],
        string="Rules", compute='_compute_expense_scan_policy', store=True, readonly=True)

    #: Fields the check of an expense depends on.
    POLICY_FIELDS = ('product_id', 'total_amount', 'total_amount_currency', 'date',
                     'scan_time', 'name', 'expense_scan_nights', 'scan_raw_text',
                     'project_id', 'employee_id', 'expense_scan_merchant', 'currency_id',
                     'expense_scan_todo_codes')

    @api.depends(*POLICY_FIELDS)
    def _compute_expense_scan_policy(self):
        for expense in self:
            # The text is stored: it is written in the employee's language,
            # whoever triggers the check (a manager, a module update).
            findings = expense.with_context(
                lang=expense._expense_scan_policy_lang())._expense_scan_policy_findings()
            expense.expense_scan_policy_alert = "\n".join(
                "%s %s" % (BREACH_SIGN if breach else CHECK_SIGN, message)
                for breach, message in findings) or False
            expense.expense_scan_policy_breach = bool(findings)
            expense.expense_scan_policy_status = (
                'breach' if any(breach for breach, _message in findings)
                else 'check' if findings else False)

    def _expense_scan_recompute_policy(self):
        if not self:
            return
        names = ['expense_scan_policy_alert', 'expense_scan_policy_breach',
                 'expense_scan_policy_status']
        for name in names:
            self.env.add_to_compute(self._fields[name], self)
        self.flush_recordset(names)

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_policy_siblings()._expense_scan_recompute_policy()
        return expenses

    def write(self, vals):
        checked = any(name in vals for name in self.POLICY_FIELDS)
        # The daily limit depends on the other meals of the day: those of the
        # day left, when the date or the employee changes, and those of the
        # new one.
        before = self._expense_scan_policy_siblings() if checked else self.browse()
        result = super().write(vals)
        if checked:
            (before | self._expense_scan_policy_siblings())._expense_scan_recompute_policy()
        return result

    def _expense_scan_policy_siblings(self):
        """Other expenses of the same employee, on the same days."""
        expenses = self.filtered(lambda e: e.employee_id and e.date)
        if not expenses:
            return self.browse()
        domain = ['|'] * (len(expenses) - 1)
        for expense in expenses:
            domain += ['&', ('employee_id', '=', expense.employee_id.id),
                       ('date', '=', expense.date)]
        return self.sudo().search(domain) - expenses

    # ------------------------------------------------------------------
    # Check
    # ------------------------------------------------------------------

    def _expense_scan_policies(self):
        self.ensure_one()
        project = self.sudo().project_id
        domain = [('apply_to_all', '=', True)]
        if project:
            domain = ['|', ('project_ids', 'in', project.id)] + domain
            if project.partner_id:
                domain = ['|', ('partner_ids', 'parent_of', project.partner_id.id)] + domain
        company = self.company_id or self.env.company
        domain += ['|', ('company_id', '=', False), ('company_id', '=', company.id)]
        return self.env['expense.scan.policy'].sudo().search(domain)

    def _expense_scan_family(self):
        self.ensure_one()
        template = self.product_id.product_tmpl_id
        families = self.env['product.template'].sudo()._expense_scan_family_templates()
        return families.get(template)

    def _expense_scan_meal_period(self):
        """Breakfast, lunch or dinner, from the description, then the time."""
        self.ensure_one()
        text = lexicon.fold(self.name)
        if re.search(r"\bpetit\s*dej|\bbreakfast\b|\bcolazione\b|\bfruhstuck", text):
            return 'breakfast'
        if re.search(r"\bdejeuner\b|\bmidi\b|\blunch\b|\bpranzo\b|\bmittag", text):
            return 'lunch'
        if re.search(r"\bdiner\b|\bsoir\b|\bdinner\b|\bcena\b|\babend", text):
            return 'dinner'
        match = re.match(r"(\d{1,2}):(\d{2})", self.scan_time or '')
        if match:
            moment = int(match.group(1)) + int(match.group(2)) / 60.0
            if moment < 10.5:
                return 'breakfast'
            if moment < 16:
                return 'lunch'
            if moment >= 17:
                return 'dinner'
        return None

    def _expense_scan_rule_applies(self, rule, family):
        if rule.product_ids:
            return self.product_id in rule.product_ids
        # Neither family nor category: the rule applies to every expense.
        return not rule.family or rule.family == family

    def _expense_scan_policy_findings(self):
        """``[(breach, message)]`` of the rules that concern this expense."""
        self.ensure_one()
        if not self.product_id:
            return []
        policies = self._expense_scan_policies()
        if not policies:
            return []
        family = self._expense_scan_family()
        period = self._expense_scan_meal_period()
        amount = self.total_amount
        currency = self.company_currency_id
        text = lexicon.fold(" ".join(filter(None, (
            self.name, self.expense_scan_merchant, self._expense_scan_purchase_text()))))
        findings = []
        undecided_limits = []

        def money(value):
            return formatLang(self.env, value, currency_obj=currency)

        # A daily limit that is respected covers the meals of that day: an
        # expensive dinner with a light lunch may stay below it.
        applicable = policies.rule_ids.filtered(
            lambda rule: self._expense_scan_rule_applies(rule, family))
        # An amount not converted to the company currency cannot be compared
        # with its limits: 40 CHF are not 40 EUR.
        unconverted = self._expense_scan_unconverted_note()
        if unconverted:
            amount_rules = applicable.filtered(
                lambda rule: rule.rule_type in ('max_amount', 'daily_max'))
            if amount_rules:
                findings.append((False, unconverted))
            applicable -= amount_rules
        daily_rules = applicable.filtered(lambda rule: rule.rule_type == 'daily_max')
        day_within_limit = bool(daily_rules) and all(
            (total := self._expense_scan_daily_total(rule, family)) is None
            or currency.compare_amounts(total, rule.amount) <= 0
            for rule in daily_rules)

        for rule in applicable:
            if rule.rule_type == 'max_amount':
                if rule.meal_period and day_within_limit:
                    continue
                if rule.meal_period and rule.meal_period not in ('lunch_dinner', 'day'):
                    if period is None:
                        undecided_limits.append((rule.amount, rule.name))
                        continue
                    if rule.meal_period != period:
                        continue
                value = amount
                if rule.per_night:
                    value = amount / max(self.expense_scan_nights or 1, 1)
                limit = rule.amount
                cities = [lexicon.fold(city) for city in (rule.extra_cities or '').split(',')]
                if rule.extra_amount and any(city and re.search(r"\b%s\b" % re.escape(city), text)
                                             for city in cities):
                    limit += rule.extra_amount
                if currency.compare_amounts(value, limit) > 0:
                    findings.append((True, _(
                        "%(rule)s - %(value)s%(unit)s for %(limit)s allowed.",
                        rule=rule.name, value=money(value),
                        unit=_(" per night") if rule.per_night else "", limit=money(limit))))
            elif rule.rule_type == 'daily_max':
                total = self._expense_scan_daily_total(rule, family)
                if total is not None and currency.compare_amounts(total, rule.amount) > 0:
                    findings.append((True, _(
                        "%(rule)s - %(value)s on that day for %(limit)s allowed.",
                        rule=rule.name, value=money(total), limit=money(rule.amount))))
            elif rule.rule_type == 'forbidden_words':
                hits = [word for word in lexicon.split_keywords(rule.words)
                        if re.search(r"\b%s\b" % re.escape(word), text)]
                if hits:
                    findings.append((False, _(
                        "%(rule)s - the receipt mentions \"%(words)s\".",
                        rule=rule.name, words=", ".join(hits))))
            elif rule.rule_type == 'rental_class':
                codes = [m.group(0) for m in ACRISS_RE.finditer((self.scan_raw_text or '').upper())
                         if m.group(1) not in ECONOMY_ACRISS]
                words = [word for word in RENTAL_UPGRADE_WORDS
                         if re.search(r"\b%s\b" % re.escape(word), text)]
                if codes or words:
                    findings.append((False, _(
                        "%(rule)s - category to check (%(hints)s).",
                        rule=rule.name, hints=", ".join(codes + words))))

        if undecided_limits and period is None:
            low = min(undecided_limits)
            high = max(undecided_limits)
            if currency.compare_amounts(amount, high[0]) > 0:
                findings.append((True, _(
                    "Meal of %(value)s: above every limit (%(limit)s at most).",
                    value=money(amount), limit=money(high[0]))))
            elif currency.compare_amounts(amount, low[0]) > 0:
                findings.append((False, _(
                    "Meal of %(value)s without a readable time: above \"%(rule)s\" "
                    "if it is that meal. Say which meal in the description.",
                    value=money(amount), rule=low[1])))
        return findings

    def _expense_scan_policy_lang(self):
        """Language of the rule findings: the employee's, else the company's."""
        employee = self.sudo().employee_id
        return (employee.user_id.lang or employee.work_contact_id.lang
                or (self.company_id or self.env.company).partner_id.lang
                or self.env.lang)

    def _expense_scan_purchase_text(self):
        """Text read on the receipt, without its general conditions."""
        lines = []
        for line in (self.scan_raw_text or '').splitlines():
            if CONDITIONS_RE.match(lexicon.fold(line)):
                break
            lines.append(line)
        return "\n".join(lines)

    def _expense_scan_unconverted_note(self):
        """Why the amount is not in the company currency, or ``False``.

        Either the receipt currency is not active in Odoo (the amount is
        counted in the company currency), or it has no exchange rate (Odoo
        counts one for one).
        """
        self.ensure_one()
        if 'currency' in self._expense_scan_open_codes():
            return _("Receipt in a currency not active in Odoo: "
                     "the amount limits are not checked.")
        if self._expense_scan_missing_rate():
            return _("No exchange rate for %s: the amount limits are not checked.",
                     self.currency_id.name)
        return False

    def _expense_scan_daily_total(self, rule, family):
        """Total of the day's meals covered by the rule, or ``None``."""
        periods = ('lunch', 'dinner') if rule.meal_period == 'lunch_dinner' else None
        if periods and self._expense_scan_meal_period() not in periods:
            return None
        same_day = self.sudo().search([
            ('employee_id', '=', self.employee_id.id),
            ('date', '=', self.date),
            ('id', '!=', self._origin.id or 0),  # 0: expense not created yet
            ('state', '!=', 'refused'),
        ])
        total = self.total_amount
        for other in same_day:
            if not other._expense_scan_rule_applies(rule, other._expense_scan_family()):
                continue
            if periods and other._expense_scan_meal_period() not in periods:
                continue
            if other._expense_scan_unconverted_note():
                continue  # not comparable, reported on that expense
            total += other.total_amount
        return total
